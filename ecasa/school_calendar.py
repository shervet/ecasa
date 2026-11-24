"""Calendrier scolaire francais : vacances par zone et jours feries.

Le calendrier officiel est publie sur data.education.gouv.fr. Il est mis en
cache dans ``data/school_calendar.json`` pour deux raisons :

* ne pas dependre du reseau a chaque import Pronote ;
* continuer a fonctionner si l'API est momentanement indisponible.

Les jours feries sont calcules localement (algorithme gregorien), sans appel
reseau. Seules les vacances scolaires sont telechargees.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

CALENDAR_URL = (
    "https://data.education.gouv.fr/api/explore/v2.1/catalog/datasets/"
    "fr-en-calendrier-scolaire/records"
)
CACHE_MAX_AGE_DAYS = 30
FAILURE_RETRY_SECONDS = 3600
REQUEST_TIMEOUT = 15

ZONES = ("A", "B", "C")
ZONE_LABELS = {
    "A": "Zone A",
    "B": "Zone B",
    "C": "Zone C",
}


def normalize_zone(value) -> str:
    """Renvoie ``A``/``B``/``C`` ou une chaine vide."""
    text = str(value or "").strip().upper()
    return text if text in ZONES else ""


def _local_date(value) -> date | None:
    """Date locale d'un horodatage du calendrier officiel.

    L'API stocke minuit local sous la forme ``22:00Z`` ou ``23:00Z`` (la
    veille). On ajoute donc un jour lorsque l'heure UTC est >= 12 h, sans
    dependre de la base de fuseaux (absente de Windows).
    """
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    # L'API renvoie le minuit local converti en UTC (22:00Z en ete, 23:00Z en
    # hiver, donc la veille). On normalise en UTC pour ne pas dependre du
    # fuseau de la machine, puis on ajoute un jour au-dela de midi.
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc)
    if moment.hour >= 12:
        return moment.date() + timedelta(days=1)
    return moment.date()


def _parse_range(start_value, end_value, name: str) -> dict | None:
    start = _local_date(start_value)
    end = _local_date(end_value)
    if start is None:
        return None
    if end is None or end <= start:
        # Evenement d'une seule journee (ex. pont de l'Ascension).
        end = start + timedelta(days=1)
    return {
        "name": str(name or "Vacances scolaires").strip(),
        "start": start.isoformat(),
        "end": end.isoformat(),  # exclusif
    }


def easter_sunday(year: int) -> date:
    """Dimanche de Paques (algorithme gregorien anonyme)."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def public_holidays(year: int) -> dict[date, str]:
    """Jours feries legaux en France metropolitaine."""
    easter = easter_sunday(year)
    return {
        date(year, 1, 1): "Jour de l'an",
        easter + timedelta(days=1): "Lundi de Pâques",
        date(year, 5, 1): "Fête du Travail",
        date(year, 5, 8): "Victoire 1945",
        easter + timedelta(days=39): "Ascension",
        easter + timedelta(days=50): "Lundi de Pentecôte",
        date(year, 7, 14): "Fête nationale",
        date(year, 8, 15): "Assomption",
        date(year, 11, 1): "Toussaint",
        date(year, 11, 11): "Armistice 1918",
        date(year, 12, 25): "Noël",
    }


def normalize_records(records, years: list[int] | None = None) -> list[dict]:
    """Convertit les enregistrements de l'API en plages locales deduplicquees."""
    result: list[dict] = []
    seen = set()
    for record in records or []:
        if not isinstance(record, dict):
            continue
        population = str(record.get("population") or "").strip().casefold()
        # Les dates des enseignants ne concernent pas l'eleve (ete notamment).
        if population == "enseignants":
            continue
        item = _parse_range(
            record.get("start_date"),
            record.get("end_date"),
            record.get("description"),
        )
        if item is None:
            continue
        if years:
            start = date.fromisoformat(item["start"])
            end = date.fromisoformat(item["end"])
            # Chevauchement avec l'annee civile demandee : la plage de l'API
            # est [start, end) et peut traverser deux annees (Noel, ete).
            if not any(
                start < date(year + 1, 1, 1) and end > date(year, 1, 1)
                for year in years
            ):
                continue
        key = (item["name"], item["start"], item["end"])
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    result.sort(key=lambda item: (item["start"], item["end"], item["name"]))
    return result


def _school_years(years: list[int]) -> list[str]:
    values: list[str] = []
    for year in years:
        for label in (f"{year - 1}-{year}", f"{year}-{year + 1}"):
            if label not in values:
                values.append(label)
    return values


def fetch_school_holidays(zone: str, years: list[int],
                          timeout: float = REQUEST_TIMEOUT) -> list[dict]:
    """Telecharge les vacances d'une zone pour les annees demandees.

    L'API renvoie une ligne par academie : les doublons sont supprimes par
    ``normalize_records``. Une annee en echec n'empeche pas les autres.
    """
    zone = normalize_zone(zone)
    if not zone:
        raise ValueError("Zone scolaire invalide (A, B ou C)")
    years = sorted({int(year) for year in years})
    if not years:
        return []

    labels = _school_years(years)
    conditions = " or ".join(
        f'annee_scolaire="{label}"' for label in labels
    )
    where = f'zones="{ZONE_LABELS[zone]}" and ({conditions})'
    select = "description,population,start_date,end_date,zones,annee_scolaire"

    # L'API plafonne « limit » a 100 : une zone compte une ligne par academie
    # et par periode, il faut donc paginer.
    records: list[dict] = []
    offset = 0
    while offset <= 2000:
        params = urllib.parse.urlencode({
            "where": where,
            "limit": 100,
            "offset": offset,
            "select": select,
        })
        request = urllib.request.Request(
            f"{CALENDAR_URL}?{params}",
            headers={
                "User-Agent": "ecasa/1.0",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        page = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(page, list) or not page:
            break
        records.extend(page)
        if len(page) < 100:
            break
        offset += 100

    return normalize_records(records, years)


class SchoolCalendar:
    """Cache memoire + disque des vacances scolaires."""

    def __init__(self, cache_path=None, fetcher=None):
        self.cache_path = Path(cache_path) if cache_path else None
        self._fetcher = fetcher or fetch_school_holidays
        self._lock = threading.Lock()
        self._data: dict = {"zones": {}}
        self._retry_at = 0.0
        self._load()

    # --------------------------------------------------------------- cache

    def _load(self) -> None:
        if not self.cache_path or not self.cache_path.is_file():
            return
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(payload, dict) and isinstance(payload.get("zones"), dict):
            self._data = payload

    def _save(self) -> None:
        if not self.cache_path:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_name(self.cache_path.name + ".tmp")
            tmp.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
            tmp.replace(self.cache_path)
        except OSError:
            pass

    def _zone_data(self, zone: str) -> dict:
        zones = self._data.setdefault("zones", {})
        return zones.setdefault(zone, {
            "fetched_at": "",
            "years": [],
            "ranges": [],
        })

    def _fresh(self, item: dict) -> bool:
        try:
            fetched = datetime.fromisoformat(str(item.get("fetched_at") or ""))
        except ValueError:
            return False
        return (datetime.now() - fetched).days < CACHE_MAX_AGE_DAYS

    def ensure(self, zone: str, years: list[int]) -> list[dict]:
        """Garantit un cache utilisable pour la zone et les annees demandees."""
        zone = normalize_zone(zone)
        if not zone:
            return []
        years = sorted({int(year) for year in years})
        with self._lock:
            item = self._zone_data(zone)
            known = {int(year) for year in item.get("years") or []}
            if years and set(years) <= known and self._fresh(item):
                return list(item.get("ranges") or [])
            # Un cache ancien mais complet reste meilleur qu'un ecran vide :
            # on tente le rafraichissement, puis on retombe dessus en cas d'echec.
            fallback = list(item.get("ranges") or [])
            if time.time() < self._retry_at:
                return fallback

        try:
            ranges = self._fetcher(zone, years)
        except Exception:  # noqa: BLE001 - reseau/API indisponible
            with self._lock:
                self._retry_at = time.time() + FAILURE_RETRY_SECONDS
            return fallback

        with self._lock:
            item = self._zone_data(zone)
            merged = {
                (entry["name"], entry["start"], entry["end"]): entry
                for entry in (item.get("ranges") or [])
                if isinstance(entry, dict) and entry.get("start") and entry.get("end")
            }
            for entry in ranges:
                merged[(entry["name"], entry["start"], entry["end"])] = entry
            item["ranges"] = sorted(
                merged.values(),
                key=lambda entry: (entry["start"], entry["end"], entry["name"]),
            )
            item["years"] = sorted(known | set(years))
            item["fetched_at"] = datetime.now().isoformat(timespec="seconds")
            self._retry_at = 0.0
            self._save()
            return list(item["ranges"])

    def refresh(self, zone: str, years: list[int]) -> list[dict]:
        """Force un telechargement (bouton de l'interface).

        Contrairement a ``ensure``, une erreur reseau est propagee : l'appelant
        peut ainsi afficher un message au lieu de garder silencieusement
        l'ancien cache.
        """
        zone = normalize_zone(zone)
        if not zone:
            raise ValueError("Zone scolaire invalide (A, B ou C)")
        years = sorted({int(year) for year in years})
        ranges = self._fetcher(zone, years)
        with self._lock:
            item = self._zone_data(zone)
            known = {int(year) for year in item.get("years") or []}
            merged = {
                (entry["name"], entry["start"], entry["end"]): entry
                for entry in (item.get("ranges") or [])
                if isinstance(entry, dict) and entry.get("start") and entry.get("end")
            }
            for entry in ranges:
                merged[(entry["name"], entry["start"], entry["end"])] = entry
            item["ranges"] = sorted(
                merged.values(),
                key=lambda entry: (entry["start"], entry["end"], entry["name"]),
            )
            item["years"] = sorted(known | set(years))
            item["fetched_at"] = datetime.now().isoformat(timespec="seconds")
            self._retry_at = 0.0
            self._save()
            return list(item["ranges"])

    # ------------------------------------------------------------- lecture

    def ranges(self, zone: str) -> list[dict]:
        zone = normalize_zone(zone)
        if not zone:
            return []
        with self._lock:
            return list(self._zone_data(zone).get("ranges") or [])

    def day_info(self, day: date, zone: str) -> dict | None:
        """Nom et type de fermeture pour une date, ou ``None``."""
        zone = normalize_zone(zone)
        if not zone:
            return None
        for entry in self.ranges(zone):
            try:
                start = date.fromisoformat(str(entry["start"]))
                end = date.fromisoformat(str(entry["end"]))
            except (KeyError, ValueError):
                continue
            if start <= day < end:
                return {
                    "name": str(entry.get("name") or "Vacances scolaires"),
                    "kind": "school",
                }
        holiday = public_holidays(day.year).get(day)
        if holiday:
            return {"name": holiday, "kind": "public"}
        return None

    def week(self, week_start: date, zone: str) -> list[dict | None]:
        """Sept entrées ``day_info`` pour la semaine (lundi -> dimanche)."""
        return [
            self.day_info(week_start + timedelta(days=offset), zone)
            for offset in range(7)
        ]

    def next_holiday(self, day: date, zone: str) -> dict | None:
        """Prochaine fermeture a partir de ``day`` (inclus)."""
        zone = normalize_zone(zone)
        if not zone:
            return None
        for offset in range(0, 370):
            candidate = day + timedelta(days=offset)
            info = self.day_info(candidate, zone)
            if info:
                return {"date": candidate.isoformat(), **info}
        return None
