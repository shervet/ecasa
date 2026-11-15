"""Consommation électrique Enedis via MyElectricalData.

Le module interroge l'API ``daily_consumption`` de MyElectricalData et agrège
les mesures journalières pour l'onglet Statistiques. Aucune donnée n'est
écrite sur le disque : le cache vit en mémoire jusqu'au prochain passage de
la synchro quotidienne (3 h), ce qui limite les appels (quotas
MyElectricalData). Une synchro manuelle peut le forcer.

Deux formats de réponse sont acceptés :

1. **Enedis / MyElectricalData** :
   ``{"meter_reading": {"interval_reading": [{"date": ..., "value": ...}]}}``
   où ``value`` est une énergie en Wh.
2. **Instance auto-hébergée** :
   ``{"unit": "w", "data": {"2024-01-01": 12540}}``
   où la valeur est aussi une énergie en Wh.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

MED_DEFAULT_URL = "https://www.myelectricaldata.fr"

# Enedis limite chaque appel ``daily_consumption`` à 1095 jours consécutifs
# (36 mois d'historique). On borne donc la période demandée à cette limite.
MAX_HISTORY_DAYS = 1095

# Les données journalières n'évoluent qu'une fois par jour : le cache vit
# jusqu'au prochain passage de la synchro quotidienne (3 h), ce qui limite les
# appels MyElectricalData au strict nécessaire. Une synchro manuelle peut
# forcer le rafraîchissement.
SYNC_HOUR = 3
ERROR_RETRY_SECONDS = 60


def next_sync_time(now: datetime | None = None) -> datetime:
    """Prochain passage de la synchro quotidienne (3 h du matin)."""
    now = now or datetime.now()
    target = now.replace(hour=SYNC_HOUR, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def cache_ttl(now: datetime | None = None) -> int:
    """Durée de vie du cache : jusqu'au prochain passage de 3 h.

    Le relevé est préchargé à 3 h par la synchro quotidienne, puis réutilisé
    toute la journée. La valeur minimale évite un cache expiré dans la
    seconde qui suit un chargement juste avant 3 h.
    """
    now = now or datetime.now()
    return max(60, int((next_sync_time(now) - now).total_seconds()))

ENERGY_VIEWS = ("day", "week", "month", "year")

ENERGY_VIEW_LABELS = {
    "day": "Jour",
    "week": "Semaine",
    "month": "Mois",
    "year": "Année",
}

MONTH_SHORT = (
    "Jan", "Fév", "Mar", "Avr", "Mai", "Juin",
    "Juil", "Août", "Sep", "Oct", "Nov", "Déc",
)

MONTH_LONG = (
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
)

# Palette des séries (définie dans ``web/css/style.css``) : une année =
# une couleur fixe, quel que soit le graphique ou la période choisie.
SERIES_COLORS = tuple(f"var(--series-{index})" for index in range(1, 7))


def _year_color(year: int, today: date) -> str:
    """Couleur stable d'une année : N → ``--series-1``, N-1 → ``--series-2``…"""
    return SERIES_COLORS[(today.year - year) % len(SERIES_COLORS)]


class EnergyError(RuntimeError):
    """MyElectricalData injoignable ou réponse inexploitable."""


class EnergyHTTPError(EnergyError):
    """Erreur HTTP renvoyée par MyElectricalData."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


class EnergyService:
    """Client MyElectricalData avec cache mémoire et cache disque optionnel.

    Si ``cache_path`` est fourni, les mesures journalières déjà récupérées sont
    persistées localement : seules les dates manquantes sont redemandées à
    l'API (en mode ``refresh``, la semaine récente est revalidée). En cas
    d'erreur réseau/quota, les données locales sont servies si disponibles et
    ``last_warning`` est renseigné.
    """

    def __init__(self, fetcher=None, cache_path: str | None = None,
                 provider: str = "med"):
        self.provider = (provider or "med").strip().lower()
        self._fetcher = fetcher or _http_json
        self._cache: dict[tuple, tuple[float, dict[str, float]]] = {}
        self._lock = threading.Lock()
        self._errors: dict[tuple, tuple[float, str]] = {}
        self.cache_path = os.path.abspath(cache_path) if cache_path else None
        self._days: dict[str, dict[str, float]] = {}
        self._days_loaded = False
        self.last_warning: str | None = None

    def daily(self, base_url: str, pdl: str, token: str, start: date,
              end: date, refresh: bool = False) -> dict[str, float]:
        """Consommation journalière en kWh entre deux dates incluses.

        Le résultat est indexé par date ISO (``YYYY-MM-DD``). Avec cache disque,
        seules les dates manquantes sont demandées ; ``refresh=True`` revalide
        les 7 derniers jours (synchro manuelle/quotidienne).
        """
        if start > end:
            raise EnergyError("Période de consommation invalide")

        base = str(base_url or MED_DEFAULT_URL).strip().rstrip("/")
        pdl = str(pdl or "").strip()
        token = str(token or "").strip()
        if not pdl:
            raise EnergyError("PDL MyElectricalData manquant")
        if not token:
            raise EnergyError("Token MyElectricalData manquant")

        self.last_warning = None
        source = _source_key(base, pdl)
        token_key = _token_key(token)

        values: dict[str, float] = {}
        stored = self._stored_days(source)
        if stored:
            for raw_day, raw_value in stored.items():
                day = _parse_day(raw_day)
                number = _as_float(raw_value)
                if day is None or number is None or not (start <= day <= end):
                    continue
                values[day.isoformat()] = number

        if refresh and stored is not None:
            wanted = {
                start + timedelta(days=offset)
                for offset in range((end - start).days + 1)
                if start + timedelta(days=offset) >= end - timedelta(days=6)
            }
        else:
            wanted = {
                start + timedelta(days=offset)
                for offset in range((end - start).days + 1)
                if (start + timedelta(days=offset)).isoformat() not in values
            }

        errors: list[EnergyError] = []
        for chunk_start, chunk_end in _contiguous_ranges(wanted):
            key = (
                base, pdl, token_key,
                chunk_start.isoformat(), chunk_end.isoformat(),
            )
            error_key = (base, pdl, token_key)
            now = time.time()
            with self._lock:
                cached = None if refresh else self._cache.get(key)
                if cached and cached[0] > now:
                    values.update(cached[1])
                    continue
                failure = self._errors.get(error_key)
                if failure and now - failure[0] < ERROR_RETRY_SECONDS:
                    errors.append(EnergyError(failure[1]))
                    continue

            try:
                chunk = self._fetch(base, pdl, token, chunk_start, chunk_end)
            except EnergyError as err:
                with self._lock:
                    self._errors[error_key] = (time.time(), str(err))
                errors.append(err)
                continue

            with self._lock:
                self._cache[key] = (time.time() + cache_ttl(), chunk)
                self._errors.pop(error_key, None)
                if len(self._cache) > 64:
                    horizon = time.time()
                    self._cache = {
                        item: value
                        for item, value in self._cache.items()
                        if value[0] > horizon
                    }
                if len(self._errors) > 64:
                    cutoff = time.time() - ERROR_RETRY_SECONDS
                    self._errors = {
                        item: value
                        for item, value in self._errors.items()
                        if value[0] > cutoff
                    }
            values.update(chunk)
            self._merge_days(source, chunk)

        values = {
            key: value
            for key, value in values.items()
            if (day := _parse_day(key)) is not None and start <= day <= end
        }

        if errors:
            if not values:
                raise errors[0]
            self.last_warning = str(errors[0])
        return values

    # ------------------------------------------------------- cache disque

    def _stored_days(self, source: str) -> dict[str, float] | None:
        if not self.cache_path:
            return None
        with self._lock:
            if not self._days_loaded:
                self._load_days_locked()
            return self._days.get(source)

    def _merge_days(self, source: str, chunk: dict[str, float]) -> None:
        if not self.cache_path or not chunk:
            return
        with self._lock:
            if not self._days_loaded:
                self._load_days_locked()
            days = self._days.setdefault(source, {})
            added = False
            for raw_day, raw_value in chunk.items():
                day = _parse_day(raw_day)
                number = _as_float(raw_value)
                if day is None or number is None:
                    continue
                key = day.isoformat()
                if key not in days:
                    days[key] = _round(number) or 0.0
                    added = True
            if added:
                self._save_days_locked()

    def _load_days_locked(self) -> None:
        self._days_loaded = True
        if not self.cache_path or not os.path.isfile(self.cache_path):
            return
        try:
            with open(self.cache_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            return
        sources = payload.get("sources") if isinstance(payload, dict) else None
        if not isinstance(sources, dict):
            return
        for source, block in sources.items():
            days = block.get("days") if isinstance(block, dict) else None
            if not isinstance(days, dict):
                continue
            clean: dict[str, float] = {}
            for raw_day, raw_value in days.items():
                day = _parse_day(raw_day)
                number = _as_float(raw_value)
                if day is None or number is None or number < 0:
                    continue
                clean[day.isoformat()] = number
            if clean:
                self._days[str(source)] = clean

    def _save_days_locked(self) -> None:
        if not self.cache_path:
            return
        payload = {
            "version": 1,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "sources": {
                source: {"days": dict(sorted(days.items()))}
                for source, days in self._days.items()
                if days
            },
        }
        directory = os.path.dirname(self.cache_path) or "."
        try:
            os.makedirs(directory, exist_ok=True)
            tmp_path = f"{self.cache_path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp_path, self.cache_path)
        except OSError:
            pass

    # ------------------------------------------------------------------ réseau

    def _fetch(self, base: str, pdl: str, token: str, start: date,
               end: date) -> dict[str, float]:
        if self.provider == "consoapi":
            headers = {
                "Authorization": "Bearer " + str(token or "").strip(),
                "Accept": "application/json",
                "User-Agent": "eCasa/1.0 (consommation electrique locale)",
            }
            # ConsoAPI : dateFin exclusive -> on demande end+1 jour.
            api_end = end + timedelta(days=1)
            url = (
                f"{base.rstrip('/')}/consommation_quotidienne"
                f"?pointId={urllib.parse.quote(str(pdl or '').strip(), safe='')}"
                f"&dateDebut={start.isoformat()}&dateFin={api_end.isoformat()}"
            )
        else:
            headers = {
                "Authorization": str(token or "").strip(),
                "Accept": "application/json",
                "User-Agent": "eCasa/1.0 (consommation electrique locale)",
            }
            quoted_pdl = urllib.parse.quote(str(pdl or "").strip(), safe="")
            url = (
                f"{base.rstrip('/')}/daily_consumption/{quoted_pdl}/start/"
                f"{start.isoformat()}/end/{end.isoformat()}"
            )
        payload = self._fetcher(url, headers, 20)
        return _parse_daily(payload)


def build_energy(service, config: dict, view: str = "month", month=None,
                 today: date | None = None, day=None, days=None,
                 refresh: bool = False) -> dict:
    """Construit la réponse de ``GET /api/energy``.

    ``view`` vaut ``day``, ``week``, ``month`` ou ``year``. Les séries sont
    toujours indexées par année afin que l'interface compare les mêmes
    périodes d'une année sur l'autre.

    En vue ``day``, ``day`` (date ISO) fixe le jour de référence de la
    fenêtre glissante et ``days`` sa taille (7 par défaut).

    En vue ``week``, ``day`` désigne un jour au sein de la semaine de
    référence (seuls le mois et le jour du mois comptent) : le bloc affiché
    est formé des 7 semaines ISO (lundi → dimanche) terminées par cette
    semaine, comparées d'année en année sur les mêmes jours calendaires.
    ``days`` est ignoré.

    En vue ``month``, le bloc affiché est formé des 7 mois terminés par le
    mois sélectionné ; ``day`` (date ISO) fournit alors l'année de
    référence (année courante par défaut) pour la navigation.

    Toutes les vues renvoient une série par année (avec une ``color``
    stable : N → ``--series-1``, N-1 → ``--series-2``…) et un bloc
    ``tooltip`` : pour chaque période affichée, une ligne par année avec
    la valeur, le nombre de jours mesurés et la couleur de la série.
    """
    today = today or date.today()
    view = view if view in ENERGY_VIEWS else "month"
    month = parse_energy_month(month, today)
    ref_day = parse_energy_day(day, month)
    window_size = parse_energy_days(days)

    enabled = bool(config.get("med_enabled"))
    token = str(config.get("med_token") or "").strip()
    pdl = str(config.get("med_pdl") or "").strip()
    base_url = str(config.get("med_url") or MED_DEFAULT_URL).strip() or MED_DEFAULT_URL

    payload = {
        "ok": False,
        "enabled": enabled,
        "configured": bool(token and pdl),
        "view": view,
        "view_label": ENERGY_VIEW_LABELS[view],
        "month": month,
        "month_label": MONTH_LONG[month - 1],
        "unit": "kWh",
        "labels": [],
        "series": [],
        "years": [],
        "source": "MyElectricalData",
        "start": None,
        "end": today.isoformat(),
        "days": 0,
        "last_date": None,
        "summary": {},
        "tooltip": None,
    }

    if not enabled:
        payload["error"] = "Suivi électrique désactivé dans les réglages."
        return payload

    if not token or not pdl:
        payload["error"] = (
            "Renseigne le token et le PDL MyElectricalData dans les réglages."
        )
        return payload

    start = parse_med_start(config.get("med_start"), today)
    payload["start"] = start.isoformat()

    try:
        daily = service.daily(base_url, pdl, token, start, today, refresh=refresh)
    except EnergyError as err:
        payload["error"] = str(err)
        return payload
    except Exception as err:  # noqa: BLE001 - l'UI ne doit pas casser
        payload["error"] = f"Consommation indisponible : {err}"
        return payload

    warning = getattr(service, "last_warning", None)
    payload["warning"] = warning
    payload["local"] = bool(warning)

    points: dict[date, float] = {}
    for key, value in daily.items():
        day = _parse_day(key)
        if day is None or not start <= day <= today:
            continue
        number = _as_float(value)
        if number is not None:
            points[day] = number

    if not points:
        payload["error"] = "Aucune donnée de consommation sur la période."
        return payload

    payload["ok"] = True
    payload["days"] = len(points)
    payload["last_date"] = max(points).isoformat()
    window = None
    if view == "day":
        window = _day_window(points, month, ref_day, window_size, today)
    elif view == "week":
        window = _week_window(points, month, ref_day, today)
    elif view == "month":
        window = _month_window(
            points, month, parse_energy_year(day, today), today
        )
    _fill_view(payload, points, view, month, today, window)
    payload["summary"] = _summary(points, view, month, today, window)
    return payload


def test_energy(config: dict, service=None, today: date | None = None) -> dict:
    """Teste le token et le PDL sans rien enregistrer."""
    today = today or date.today()
    provider = str(config.get("energy_provider") or "myelectricaldata").strip().lower()
    if provider == "consoapi":
        token = str(config.get("consoapi_token") or "").strip()
        pdl = str(config.get("consoapi_pdl") or "").strip()
        base_url = str(config.get("consoapi_url") or "https://conso.boris.sh/api").strip() or "https://conso.boris.sh/api"
        label = "ConsoAPI"
        provider_key = "consoapi"
    else:
        token = str(config.get("med_token") or "").strip()
        pdl = str(config.get("med_pdl") or "").strip()
        base_url = str(config.get("med_url") or MED_DEFAULT_URL).strip() or MED_DEFAULT_URL
        label = "MyElectricalData"
        provider_key = "myelectricaldata"

    if not token or not pdl:
        return {
            "ok": False,
            "message": "Renseigne le token et le PDL %s." % label,
        }

    if service is None:
        service = EnergyService(provider=provider_key)
    try:
        values = service.daily(
            base_url, pdl, token, today - timedelta(days=7), today, refresh=True
        )
    except EnergyError as err:
        return {"ok": False, "message": str(err)}
    except Exception as err:  # noqa: BLE001
        return {"ok": False, "message": "Connexion impossible : %s" % err}

    warning = getattr(service, "last_warning", None)
    if warning:
        return {
            "ok": False,
            "message": (
                "%s Données locales conservées%s."
                % (warning, (" (dernière mesure : " + max(values) + ")") if values else "")
            ),
            "pdl": pdl,
            "samples": len(values),
            "last_date": max(values) if values else None,
        }

    if not values:
        return {
            "ok": False,
            "message": "Aucune donnée reçue de %s pour ce PDL." % label,
            "pdl": pdl,
        }

    return {
        "ok": True,
        "message": "%d jour(s) de consommation reçus." % len(values),
        "pdl": pdl,
        "samples": len(values),
        "last_date": max(values) if values else None,
    }
def parse_energy_month(value, today: date | None = None) -> int:
    """Mois sélectionné (1–12), mois courant par défaut."""
    today = today or date.today()
    try:
        month = int(value)
    except (TypeError, ValueError):
        return today.month
    return month if 1 <= month <= 12 else today.month


def parse_energy_day(value, month: int) -> int | None:
    """Jour de référence (fin de fenêtre) de la vue ``day``, en jour du mois.

    Le paramètre est une date ISO ; seul le jour du mois compte, car la vue
    compare le même jour de chaque année. Une date d'un autre mois (ou
    invalide) est ignorée : la fenêtre par défaut s'applique.
    """
    if not value:
        return None
    try:
        ref = datetime.fromisoformat(str(value)[:10]).date()
    except ValueError:
        return None
    if ref.month != month:
        return None
    return ref.day


def parse_energy_days(value, default: int = 7) -> int:
    """Taille de la fenêtre de la vue ``day`` (1 à 31 jours, 7 par défaut)."""
    try:
        size = int(value)
    except (TypeError, ValueError):
        return default
    return min(31, max(1, size))


def parse_energy_year(value, today: date | None = None) -> int:
    """Année de référence de la vue ``month`` (année courante par défaut).

    Le paramètre est une date ISO dont seule l'année compte : la navigation
    par flèches peut franchir le 1er janvier, mais jamais aller dans le
    futur (le sélecteur de mois suffit pour viser un mois à venir).
    """
    today = today or date.today()
    if not value:
        return today.year
    try:
        ref = datetime.fromisoformat(str(value)[:10]).date()
    except ValueError:
        return today.year
    return min(max(ref.year, 2000), today.year)


def _day_window(points: dict[date, float], month: int, ref_day: int | None,
                size: int, today: date) -> dict:
    """Fenêtre glissante de ``size`` jours sur le mois sélectionné.

    Les séries comparent le même jour du mois d'une année sur l'autre : la
    fenêtre porte donc sur les numéros de jour (1–31). Elle est clippée sur
    les données disponibles : jamais au-delà d'aujourd'hui pour le mois
    courant, jamais au-delà du dernier jour relevé (celui de cette année pour
    le mois en cours, toutes années pour un mois passé), et elle commence au
    1er si moins de ``size`` jours sont écoulés.
    """
    # Référence : jour du mois sélectionné, ou dernier jour avec donnée.
    if ref_day is None:
        selected = [day for day in points if day.month == month]
        if today.month == month:
            ref_day = min(today.day, calendar.monthrange(today.year, month)[1])
            if selected:
                current = [day.day for day in selected if day.year == today.year]
                if current:
                    ref_day = min(ref_day, max(current))
        else:
            if selected:
                ref_day = max(day.day for day in selected)
            else:
                ref_day = calendar.monthrange(today.year, month)[1]
    ref_day = max(1, min(ref_day, calendar.monthrange(today.year, month)[1]))

    try:
        ref_date = date(today.year, month, ref_day)
    except ValueError:
        ref_date = date(today.year, month, calendar.monthrange(today.year, month)[1])
    if ref_date > today:
        ref_date = today
    # Ne pas reculer avant la plus ancienne donnée disponible : la
    # navigation s'arrête au début de l'historique.
    if points:
        earliest = min(points)
        if ref_date < earliest:
            ref_date = earliest

    # size jours consécutifs, pouvant traverser les mois.
    pairs = []
    for offset in range(size - 1, -1, -1):
        d = ref_date - timedelta(days=offset)
        pairs.append((d.month, d.day))

    start_month, start_day = pairs[0]
    end_month, end_day = pairs[-1]

    return {
        "month": month,
        "start": start_day,
        "end": end_day,
        "limit": end_day,
        "size": size,
        "days": pairs,
        "start_month": start_month,
        "end_month": end_month,
        "start_iso": (ref_date - timedelta(days=size - 1)).isoformat(),
        "end_iso": ref_date.isoformat(),
        "first_iso": min(points).isoformat() if points else None,
        "limit_iso": today.isoformat(),
    }


def _week_window(points: dict[date, float], month: int, ref_day: int | None,
                 today: date) -> dict:
    """Bloc de 7 semaines ISO (lundi → dimanche) affiché par la vue ``week``.

    La date de référence est reconstruite dans l'année courante à partir du
    mois sélectionné et du jour du mois (``ref_day``) : comme pour la vue
    ``day``, seuls le mois et le jour comptent, car la comparaison porte sur
    les **mêmes jours calendaires de chaque année**. Sans ``ref_day``, la
    semaine courante s'applique.

    Le bloc se termine sur la semaine de référence et remonte six semaines
    en arrière. Les semaines de tête sans aucune donnée (quelle que soit
    l'année) sont retirées : pas de barres vides, le bloc est simplement
    plus court quand l'historique débute.

    Bornage : la référence ne dépasse jamais ``today`` ni le dernier jour
    avec donnée de l'année en cours — impossible d'afficher une semaine
    entièrement future sans donnée récente. Un mois futur reste en revanche
    navigable : les séries viennent alors des années précédentes.

    Clés renvoyées :

    - ``weeks`` : les semaines affichées, chacune avec ``mdays`` (7 paires
      ``(mois, jour)``), ``label`` (``S46``), ``start`` (lundi) et ``end``
      (dimanche) ;
    - ``ref`` / ``first`` / ``limit`` : lundis (dates) de la semaine de
      référence et des bornes de navigation ;
    - ``iso_week`` : numéro ISO de la semaine de référence ;
    - ``label`` : libellé du bloc (ex. ``Semaines 7 → 11 · 10/02 →
      16/03``, ou le libellé détaillé d'une semaine unique) ;
    - ``can_prev`` / ``can_next`` : navigation possible en arrière / en
      avant (le bloc voisin contient des données).
    """
    ref = today
    last = max(points) if points else today
    if ref_day is not None:
        try:
            ref = date(today.year, month, ref_day)
        except ValueError:
            ref = today
        # Une date passée ou courante ne dépasse pas la dernière donnée
        # (ex. le 14 mars alors que le relevé s'arrête le 9). En revanche,
        # un mois futur reste navigable : la semaine est alors comparée aux
        # mêmes jours calendaires des années précédentes.
        if ref <= today and last.year == ref.year:
            ref = min(ref, last)
    elif last.year == ref.year:
        ref = min(ref, last)
    ref_monday = ref - timedelta(days=ref.weekday())

    years = sorted({day.year for day in points})

    def week_has_data(monday: date) -> bool:
        mdays = {
            (day.month, day.day)
            for day in (monday + timedelta(days=offset) for offset in range(7))
        }
        for year in years:
            for month_number, day_number in mdays:
                try:
                    if date(year, month_number, day_number) in points:
                        return True
                except ValueError:
                    # 29 février hors année bissextile.
                    continue
        return False

    # 7 semaines consécutives terminées par la semaine de référence ; on
    # retire les semaines de tête entièrement vides (première donnée).
    mondays = [
        ref_monday - timedelta(weeks=offset) for offset in range(6, -1, -1)
    ]
    while len(mondays) > 1 and not week_has_data(mondays[0]):
        mondays.pop(0)

    weeks = []
    for monday in mondays:
        days = [monday + timedelta(days=offset) for offset in range(7)]
        weeks.append({
            "mdays": [(day.month, day.day) for day in days],
            "label": f"S{monday.isocalendar()[1]}",
            "start": monday,
            "end": days[-1],
        })

    limit_bound = min(today, last) if last.year == today.year else today
    limit = limit_bound - timedelta(days=limit_bound.weekday())
    jan_first = date(today.year, 1, 1)
    first = jan_first - timedelta(days=jan_first.weekday())

    start_day, end_day = weeks[0]["start"], weeks[-1]["end"]
    if len(weeks) == 1:
        iso_week = start_day.isocalendar()[1]
        if start_day.month == end_day.month:
            label = (
                f"Semaine {iso_week} · {start_day.day:02d}–{end_day.day:02d} "
                f"{MONTH_LONG[start_day.month - 1].capitalize()}"
            )
        else:
            label = (
                f"Semaine {iso_week} · "
                f"{start_day.day:02d}/{start_day.month:02d}–"
                f"{end_day.day:02d}/{end_day.month:02d}"
            )
    else:
        label = (
            f"Semaines {start_day.isocalendar()[1]} → "
            f"{weeks[-1]['start'].isocalendar()[1]} · "
            f"{start_day.day:02d}/{start_day.month:02d} → "
            f"{end_day.day:02d}/{end_day.month:02d}"
        )

    can_prev = ref_monday > first and any(
        week_has_data(ref_monday - timedelta(weeks=offset))
        for offset in range(1, 8)
    )
    can_next = ref_monday < limit

    return {
        "month": month,
        "iso_week": ref_monday.isocalendar()[1],
        "size": 7,
        "weeks": weeks,
        "ref": ref_monday,
        "first": first,
        "limit": limit,
        "label": label,
        "can_prev": can_prev,
        "can_next": can_next,
    }


def _months_back(end_key: tuple[int, int]) -> list[tuple[int, int]]:
    """Les 7 paires ``(année, mois)`` consécutives terminées par ``end_key``."""
    months = []
    year, month = end_key
    for _ in range(7):
        months.append((year, month))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    months.reverse()
    return months


def _month_window(points: dict[date, float], month: int, ref_year: int,
                  today: date) -> dict:
    """Bloc de 7 mois consécutifs affiché par la vue ``month``.

    Le mois de référence est le mois sélectionné, placé dans l'année
    ``ref_year`` (année courante par défaut ; la navigation par flèches
    peut franchir le 1er janvier). Chaque colonne compare le **même mois
    calendaire** d'une année sur l'autre ; l'année du libellé est celle de
    la position dans le calendrier de référence.

    Le bloc remonte six mois en arrière ; les mois de tête sans aucune
    donnée (quelle que soit l'année) sont retirés, comme pour la vue
    semaine.

    Clés renvoyées :

    - ``months`` : les colonnes affichées, chacune avec ``year``, ``month``,
      ``key`` (``YYYY-MM``) et ``label`` (``Nov``, ou ``Nov 25`` quand le
      bloc franchit une année) ;
    - ``ref`` / ``first`` / ``limit`` : bornes de navigation ``YYYY-MM`` ;
    - ``label`` : libellé du bloc (ex. ``Déc 24 → Mar 25``) ;
    - ``range_label`` : mois sans années (ex. ``Décembre → Mars``), pour le
      résumé qui ajoute l'année comparée ;
    - ``can_prev`` / ``can_next`` : navigation possible (bloc voisin non
      vide).
    """
    ref_key = (ref_year, month)
    months = _months_back(ref_key)

    def month_has_data(number: int) -> bool:
        return any(day.month == number for day in points)

    while len(months) > 1 and not month_has_data(months[0][1]):
        months.pop(0)

    first_data = min(points) if points else today
    first_key = (first_data.year, first_data.month)
    limit_key = (today.year, today.month)
    crossed = months[0][0] != months[-1][0]
    columns = [
        {
            "year": year,
            "month": number,
            "key": f"{year:04d}-{number:02d}",
            "label": (
                f"{MONTH_SHORT[number - 1]} {year % 100:02d}"
                if crossed
                else MONTH_SHORT[number - 1]
            ),
        }
        for year, number in months
    ]

    label = (
        f"{MONTH_SHORT[months[0][1] - 1]} {months[0][0] % 100:02d} → "
        f"{MONTH_SHORT[months[-1][1] - 1]} {months[-1][0] % 100:02d}"
    )
    range_label = (
        f"{MONTH_LONG[months[0][1] - 1].capitalize()} → "
        f"{MONTH_LONG[months[-1][1] - 1].capitalize()}"
    )

    previous_key = ref_key[0], ref_key[1] - 1
    if previous_key[1] == 0:
        previous_key = ref_key[0] - 1, 12
    can_prev = ref_key > first_key and any(
        month_has_data(number)
        for _, number in _months_back(previous_key)
    )
    can_next = ref_key < limit_key

    return {
        "month": month,
        "size": 7,
        "months": columns,
        "ref": f"{ref_key[0]:04d}-{ref_key[1]:02d}",
        "first": f"{first_key[0]:04d}-{first_key[1]:02d}",
        "limit": f"{limit_key[0]:04d}-{limit_key[1]:02d}",
        "label": label,
        "range_label": range_label,
        "can_prev": can_prev,
        "can_next": can_next,
    }


def parse_med_start(value, today: date | None = None) -> date:
    """Début de l'historique, borné aux 1095 jours autorisés par Enedis.

    Une valeur vide (ou invalide) demande les trois dernières années. Une date
    plus ancienne est ramenée à la limite basse : au-delà, l'API Enedis refuse
    l'appel.
    """
    today = today or date.today()
    oldest = today - timedelta(days=MAX_HISTORY_DAYS - 1)
    if value:
        try:
            start = datetime.fromisoformat(str(value)[:10]).date()
        except ValueError:
            start = None
        if start is not None:
            return min(max(start, oldest), today)
    return oldest


def _tooltip_row(year: int, total: float | None, count: int,
                 today: date, variation: float | None = None) -> dict:
    """Une ligne du tableau flottant (clic sur une barre) pour une année."""
    return {
        "label": str(year),
        "year": year,
        "value": _round(total) if count else None,
        "days": count,
        "variation": _round(variation),
        "color": _year_color(year, today),
    }


def _fill_view(payload: dict, points: dict[date, float], view: str, month: int,
               today: date, window: dict | None = None) -> None:
    if view == "year":
        # Une barre par année : uniquement les années avec au moins une
        # donnée sur la période comparée (année à date, l'année en cours
        # reste partielle), 7 maximum — pas d'année vide ni de placeholder.
        totals: dict[int, float] = {}
        counts: dict[int, int] = {}
        years = []
        for year in sorted({day.year for day in points}):
            try:
                limit = date(year, today.month, today.day)
            except ValueError:
                limit = date(year, today.month, 28)
            count = sum(
                1 for day in points if day.year == year and day <= limit
            )
            if not count:
                continue
            years.append(year)
            counts[year] = count
            totals[year] = _round(
                sum(
                    value for day, value in points.items()
                    if day.year == year and day <= limit
                )
            )
        years = years[-7:]
        payload["years"] = years
        payload["labels"] = [str(year) for year in years]
        series = []
        for index, year in enumerate(years):
            values = [None] * len(years)
            values[index] = totals[year]
            series.append({
                "key": str(year),
                "label": str(year),
                "values": values,
                "color": _year_color(year, today),
            })
        payload["series"] = series
        tooltip_rows = []
        for i, year in enumerate(years):
            if i > 0 and counts[year]:
                prev_total = totals[years[i - 1]]
                prev_count = counts[years[i - 1]]
                if prev_count and prev_total:
                    variation = (totals[year] - prev_total) / prev_total * 100
                else:
                    variation = None
            else:
                variation = None
            tooltip_rows.append(
                [_tooltip_row(year, totals[year], counts[year], today, variation)]
            )
        payload["tooltip"] = {
            "labels": payload["labels"],
            "rows": tooltip_rows,
        }
        return

    if view == "week":
        # Une barre par semaine ISO (total de la semaine), une série par
        # année avec donnée dans le bloc affiché. ``window`` expose des
        # repères JJ/MM (affichage) et ISO (navigation côté client :
        # comparaison lexicographique fiable).
        window = window or _week_window(points, month, None, today)
        weeks = window["weeks"]
        mdays_set = {mday for week in weeks for mday in week["mdays"]}
        years = sorted(
            {day.year for day in points if (day.month, day.day) in mdays_set}
        )
        payload["years"] = years
        payload["labels"] = [week["label"] for week in weeks]
        first_monday, last_sunday = weeks[0]["start"], weeks[-1]["end"]
        payload["window"] = {
            "start": f"{first_monday.day:02d}/{first_monday.month:02d}",
            "end": f"{last_sunday.day:02d}/{last_sunday.month:02d}",
            "start_iso": first_monday.isoformat(),
            "end_iso": last_sunday.isoformat(),
            "ref_iso": window["ref"].isoformat(),
            "first_iso": window["first"].isoformat(),
            "limit_iso": window["limit"].isoformat(),
            "month": window["month"],
            "iso_week": window["iso_week"],
            "size": window["size"],
            "label": window["label"],
            "can_prev": window["can_prev"],
            "can_next": window["can_next"],
        }
        week_stats = []
        for week in weeks:
            stats = {}
            for year in years:
                total = 0.0
                count = 0
                for month_number, day_number in week["mdays"]:
                    try:
                        key = date(year, month_number, day_number)
                    except ValueError:
                        # 29 février hors année bissextile.
                        continue
                    if key in points:
                        total += points[key]
                        count += 1
                stats[year] = (total, count)
            week_stats.append(stats)
        payload["series"] = [
            {
                "key": str(year),
                "label": str(year),
                "values": [
                    _round(stats[year][0]) if stats[year][1] else None
                    for stats in week_stats
                ],
                "color": _year_color(year, today),
            }
            for year in years
        ]
        tooltip_rows = []
        for stats in week_stats:
            row_list = []
            for i, year in enumerate(years):
                total, count = stats[year]
                if i > 0 and count:
                    prev_total, prev_count = stats[years[i - 1]]
                    if prev_count and prev_total:
                        variation = (total - prev_total) / prev_total * 100
                    else:
                        variation = None
                else:
                    variation = None
                row_list.append(
                    _tooltip_row(year, total, count, today, variation)
                )
            tooltip_rows.append(row_list)
        payload["tooltip"] = {
            "labels": payload["labels"],
            "rows": tooltip_rows,
        }
        return

    if view == "day":
        window = window or _day_window(points, month, None, 7, today)
        day_pairs = window["days"]  # list of (month, day) tuples
        day_keys = set(day_pairs)
        selected = {
            day: value
            for day, value in points.items()
            if (day.month, day.day) in day_keys
        }
        years = sorted({day.year for day in selected})
        payload["years"] = years
        _FRENCH_DAY_ABBR = ("Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim")
        # La fenêtre peut chevaucher deux années : on calcule le jour de la
        # semaine à partir de la date réelle de chaque case (ancre + index),
        # pas de l'année courante, pour rester cohérent avec les données.
        try:
            _anchor = datetime.fromisoformat(str(window.get("start_iso"))).date()
        except (TypeError, ValueError):
            _anchor = date(today.year, day_pairs[0][0], day_pairs[0][1])
        payload["labels"] = [
            f"{day_number:02d}/{month_number:02d}|"
            f"{_FRENCH_DAY_ABBR[(_anchor + timedelta(days=index)).weekday()]}"
            for index, (month_number, day_number) in enumerate(day_pairs)
        ]
        payload["window"] = {
            key: window[key]
            for key in (
                "month", "start", "end", "limit", "size",
                "start_month", "end_month", "start_iso", "end_iso",
                "first_iso", "limit_iso",
            )
        }
        grouped = {}
        for day, value in selected.items():
            grouped[(day.year, day.month, day.day)] = grouped.get(
                (day.year, day.month, day.day), 0.0
            ) + value
        payload["series"] = [
            {
                "key": str(year),
                "label": str(year),
                "values": [
                    _round(grouped[(year, month_number, day_number)])
                    if (year, month_number, day_number) in grouped else None
                    for month_number, day_number in day_pairs
                ],
                "color": _year_color(year, today),
            }
            for year in years
        ]
        tooltip_rows = []
        for day_pair in window["days"]:
            month_number, day_number = day_pair
            row_list = []
            for i, year in enumerate(years):
                key = (year, month_number, day_number)
                total = grouped.get(key)
                count = 1 if key in grouped else 0
                if i > 0 and total is not None and count:
                    prev_key = (years[i - 1], month_number, day_number)
                    prev_total = grouped.get(prev_key)
                    prev_count = 1 if prev_key in grouped else 0
                    if prev_count and prev_total:
                        variation = (total - prev_total) / prev_total * 100
                    else:
                        variation = None
                else:
                    variation = None
                row_list.append(
                    _tooltip_row(year, total, count, today, variation)
                )
            tooltip_rows.append(row_list)
        payload["tooltip"] = {
            "labels": payload["labels"],
            "rows": tooltip_rows,
        }
        return

    # Vue mois : une barre par mois (total mensuel), une série par année.
    window = window or _month_window(points, month, today.year, today)
    columns = window["months"]
    month_numbers = {column["month"] for column in columns}
    years = sorted({day.year for day in points if day.month in month_numbers})
    payload["years"] = years
    payload["labels"] = [column["label"] for column in columns]
    payload["window"] = window
    month_stats = []
    for column in columns:
        number = column["month"]
        stats = {}
        for year in years:
            total = 0.0
            count = 0
            for day, value in points.items():
                if day.year == year and day.month == number:
                    total += value
                    count += 1
            stats[year] = (total, count)
        month_stats.append(stats)
    payload["series"] = [
        {
            "key": str(year),
            "label": str(year),
            "values": [
                _round(stats[year][0]) if stats[year][1] else None
                for stats in month_stats
            ],
            "color": _year_color(year, today),
        }
        for year in years
    ]
    tooltip_rows = []
    for stats in month_stats:
        row_list = []
        for i, year in enumerate(years):
            total, count = stats[year]
            if i > 0 and count:
                prev_total, prev_count = stats[years[i - 1]]
                if prev_count and prev_total:
                    variation = (total - prev_total) / prev_total * 100
                else:
                    variation = None
            else:
                variation = None
            row_list.append(
                _tooltip_row(year, total, count, today, variation)
            )
        tooltip_rows.append(row_list)
    payload["tooltip"] = {
        "labels": payload["labels"],
        "rows": tooltip_rows,
    }


def _period_totals(points: dict[date, float], view: str, month: int,
                   today: date, window: dict | None):
    """Renvoie (années concernées, total_par_année) pour la période de la vue.

    ``total(year)`` -> ``(kwh, jours)`` calculé sur la période affichée :

    - ``day``   : les jours de la fenêtre affichée (mêmes jours du mois) ;
    - ``week``  : les semaines du bloc affiché (mêmes jours calendaires,
      la semaine pouvant chevaucher deux mois) ;
    - ``month`` : les mois du bloc affiché (mêmes mois calendaires) ;
    - ``year``  : l'année à date (l'année en cours reste partielle).
    """
    if view == "day":
        day_keys = set(window["days"]) if window else None  # set of (month, day) tuples

        def total(year: int) -> tuple[float, int]:
            kwh = 0.0
            count = 0
            for day, value in points.items():
                if (
                    day.year == year
                    and (day_keys is None or (day.month, day.day) in day_keys)
                ):
                    kwh += value
                    count += 1
            return kwh, count

        years = {
            day.year
            for day in points
            if day_keys is None or (day.month, day.day) in day_keys
        }
        return years, total

    if view == "week":
        # Les semaines du bloc affiché (paires mois/jour), comparées
        # d'année en année — exactement la période du graphique.
        mdays = set()
        if window:
            mdays = {mday for week in window["weeks"] for mday in week["mdays"]}

        def total(year: int) -> tuple[float, int]:
            kwh = 0.0
            count = 0
            for day, value in points.items():
                if day.year == year and (day.month, day.day) in mdays:
                    kwh += value
                    count += 1
            return kwh, count

        years = {
            day.year
            for day in points
            if (day.month, day.day) in mdays
        }
        return years, total

    if view == "year":
        # Comparaison à date : l'année en cours est partielle, on borne donc
        # chaque année au même jour de l'année pour que l'écart N-1 ait un
        # sens. Seules les années avec données à date (comme les barres)
        # sont retenues, 7 maximum.
        def total(year: int) -> tuple[float, int]:
            try:
                limit = date(year, today.month, today.day)
            except ValueError:
                limit = date(year, today.month, 28)
            kwh = sum(
                value for day, value in points.items()
                if day.year == year and day <= limit
            )
            count = sum(
                1 for day in points if day.year == year and day <= limit
            )
            return kwh, count

        years = {
            year
            for year in {day.year for day in points}
            if total(year)[1]
        }
        return set(sorted(years)[-7:]), total

    # month : les mois du bloc affiché (mêmes mois calendaires), d'année en
    # année — exactement la période du graphique.
    month_numbers = set()
    if window:
        month_numbers = {column["month"] for column in window["months"]}

    def total(year: int) -> tuple[float, int]:
        kwh = 0.0
        count = 0
        for day, value in points.items():
            if day.year == year and day.month in month_numbers:
                kwh += value
                count += 1
        return kwh, count

    return {day.year for day in points if day.month in month_numbers}, total


def _delta_pct(total: float, prev_total: float, prev_count: int) -> float | None:
    """Écart en % vs la même période l'année N-1 (None sans référence)."""
    if not prev_count:
        return None
    if prev_total > 0:
        return round((total - prev_total) / prev_total * 100, 1)
    # Consommation nulle l'an passé : tout est une hausse.
    return 100.0 if total > 0 else 0.0


def _by_year(years: set[int], total_for) -> list[dict]:
    """Totaux par année (décroissant), écart vs N-1 sur la même période."""
    rows = []
    for year in sorted(years, reverse=True):
        total, count = total_for(year)
        if not count:
            continue
        prev_total, prev_count = total_for(year - 1)
        rows.append({
            "year": year,
            "label": str(year),
            "kwh": _round(total),
            "delta_pct": _delta_pct(total, prev_total, prev_count),
            "days": count,
        })
    return rows


def _summary(points: dict[date, float], view: str, month: int,
             today: date, window: dict | None = None) -> dict:
    """Compare la période affichée à la même période de l'année précédente.

    - ``day`` : la fenêtre affichée du mois sélectionné, dernière année
      avec données dans la fenêtre ;
    - ``week`` : le bloc de semaines affiché, dernière année avec données
      dans le bloc ;
    - ``month`` : le bloc de mois affiché, dernière année avec données
      dans le bloc ;
    - ``year`` : la dernière année avec données.

    ``by_year`` décline le même calcul pour chaque année affichée : les
    années retenues et les totaux sont ceux du graphique.
    """
    empty = {
        "year": None,
        "label": "",
        "previous_year": None,
        "previous_label": "",
        "total_kwh": None,
        "previous_kwh": None,
        "delta_pct": None,
        "days": 0,
        "partial": False,
        "last_date": None,
        "by_year": [],
    }
    if not points:
        return empty

    years_scope, total_for = _period_totals(points, view, month, today, window)

    if view == "day":
        month_years = sorted({day.year for day in points if day.month == month})
        ref = (
            max(years_scope)
            if years_scope
            else (month_years[-1] if month_years else today.year)
        )
        if window:
            start_month = window.get("start_month", month)
            end_month = window.get("end_month", month)
            if start_month == end_month:
                label = (
                    f"{window['start']:02d}–{window['end']:02d} "
                    f"{MONTH_LONG[end_month - 1].capitalize()} {ref}"
                )
                previous_label = (
                    f"{window['start']:02d}–{window['end']:02d} "
                    f"{MONTH_LONG[end_month - 1].capitalize()} {ref - 1}"
                )
            else:
                label = (
                    f"{window['start']:02d}/{start_month:02d}–"
                    f"{window['end']:02d}/{end_month:02d} {ref}"
                )
                previous_label = (
                    f"{window['start']:02d}/{start_month:02d}–"
                    f"{window['end']:02d}/{end_month:02d} {ref - 1}"
                )
        else:
            label = f"{MONTH_LONG[month - 1].capitalize()} {ref}"
            previous_label = f"{MONTH_LONG[month - 1].capitalize()} {ref - 1}"
    elif view == "week":
        # Le bloc affiché n'a pas toujours de données pour l'année en
        # cours (elle ne fait que commencer) : on compare alors la dernière
        # année qui en a. Le libellé reprend celui de la fenêtre.
        if years_scope:
            ref = max(years_scope)
        else:
            ref = today.year
        base = window["label"] if window else (
            f"Semaine {today.isocalendar()[1]}"
        )
        label = f"{base} {ref}"
        previous_label = f"{base} {ref - 1}"
    elif view == "year":
        ref = max({day.year for day in points})
        label = f"Année {ref}"
        previous_label = f"Année {ref - 1}"
    else:  # mois : bloc affiché, dernière année avec données dans le bloc
        ref = max(years_scope) if years_scope else today.year
        base = window["range_label"] if window else "Année"
        label = f"{base} {ref}"
        previous_label = f"{base} {ref - 1}"

    total, count = total_for(ref)
    prev_total, prev_count = total_for(ref - 1)

    # Les totaux sont « à date » quand l'année de référence n'est pas
    # terminée : l'interface doit le signaler. En vue semaine, la fenêtre
    # affichée peut courir après aujourd'hui (jours futurs sans donnée).
    partial = (
        count > 0 and ref == today.year and view in ("week", "month", "year")
    )

    return {
        "year": ref,
        "label": label,
        "previous_year": ref - 1,
        "previous_label": previous_label,
        # Aucun jour mesuré sur la période : « — » plutôt qu'un 0 trompeur.
        "total_kwh": _round(total) if count else None,
        "previous_kwh": _round(prev_total) if prev_count else None,
        "delta_pct": _delta_pct(total, prev_total, prev_count) if count else None,
        "days": count,
        "partial": partial,
        "last_date": max(points).isoformat() if points else None,
        "by_year": _by_year(years_scope, total_for),
    }


def _source_key(base: str, pdl: str) -> str:
    """Clé du cache disque (URL + PDL), indépendante du token."""
    return f"{base}|{pdl}"


def _contiguous_ranges(days) -> list[tuple[date, date]]:
    """Regroupe des jours en plages consécutives, bornées par année."""
    ranges: list[tuple[date, date]] = []
    first = prev = None
    for day in sorted(days):
        if first is None:
            first = prev = day
            continue
        if day == prev + timedelta(days=1) and day.year == prev.year:
            prev = day
            continue
        ranges.append((first, prev))
        first = prev = day
    if first is not None:
        ranges.append((first, prev))
    return ranges


def _year_chunks(start: date, end: date) -> list[tuple[date, date]]:
    chunks = []
    current = start
    while current <= end:
        last_day = date(current.year, 12, 31)
        chunk_end = min(last_day, end)
        chunks.append((current, chunk_end))
        current = chunk_end + timedelta(days=1)
    return chunks


def _token_key(token: str) -> str:
    """Empreinte courte du token, pour la clé de cache (jamais le token)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _parse_daily(payload) -> dict[str, float]:
    """Extrait les couples date → kWh d'une réponse MyElectricalData."""
    if isinstance(payload, list):
        return _parse_interval(payload)
    if not isinstance(payload, dict):
        raise EnergyError("Réponse MyElectricalData inattendue")

    for key in ("meter_reading", "reading", "consumption"):
        block = payload.get(key)
        if isinstance(block, dict):
            for field in ("interval_reading", "interval_readings"):
                items = block.get(field)
                if isinstance(items, list):
                    return _parse_interval(items)

    items = payload.get("interval_reading")
    if isinstance(items, list):
        return _parse_interval(items)

    data = payload.get("data")
    if isinstance(data, dict):
        return _parse_data_map(data)

    results = payload.get("results")
    if isinstance(results, list):
        return _parse_interval(results)

    return {}


def _parse_interval(items: list) -> dict[str, float]:
    values: dict[str, float] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        day = _parse_day(
            item.get("date") or item.get("day") or item.get("timestamp")
        )
        kwh = _wh_to_kwh(
            item.get("value")
            if item.get("value") is not None
            else item.get("consumption")
        )
        if day is None or kwh is None:
            continue
        key = day.isoformat()
        values[key] = values.get(key, 0.0) + kwh
    return values


def _parse_data_map(data: dict) -> dict[str, float]:
    values: dict[str, float] = {}
    for raw_day, raw_value in data.items():
        day = _parse_day(raw_day)
        kwh = _wh_to_kwh(raw_value)
        if day is None or kwh is None:
            continue
        key = day.isoformat()
        values[key] = values.get(key, 0.0) + kwh
    return values


def _parse_day(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if len(text) < 10:
        return None
    try:
        return datetime.fromisoformat(text[:19].replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _wh_to_kwh(value) -> float | None:
    number = _as_float(value)
    if number is None or number < 0:
        return None
    return number / 1000.0


def _as_float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def _round(value) -> float | None:
    if value is None:
        return None
    return round(float(value), 2)


def _http_json(url: str, headers: dict, timeout: int = 20) -> dict:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as err:
        detail = ""
        try:
            detail = err.read().decode("utf-8", "replace")[:300]
        except OSError:
            pass
        if err.code == 429:
            message = (
                "Quota Enedis dépassé chez MyElectricalData : "
                "réessaie plus tard (les données locales restent affichées si disponibles)."
            )
        elif err.code >= 500:
            message = (
                "MyElectricalData/Enedis : erreur technique ou quota dépassé. "
                "Réessaie plus tard."
            )
            if detail:
                message += f" ({detail[:120].strip()})"
        else:
            message = f"MyElectricalData HTTP {err.code}"
            if detail:
                message += f" : {detail}"
        raise EnergyHTTPError(message, err.code) from err
    except urllib.error.URLError as err:
        raise EnergyError(
            f"MyElectricalData injoignable : {err.reason}"
        ) from err
    except OSError as err:
        raise EnergyError(f"MyElectricalData injoignable : {err}") from err

    try:
        payload = json.loads(raw.decode("utf-8"))
    except ValueError as err:
        raise EnergyError("Réponse MyElectricalData illisible") from err

    if isinstance(payload, dict) and payload.get("error"):
        message = payload.get("error")
        if isinstance(message, dict):
            message = message.get("message") or message.get("detail")
        raise EnergyError(
            str(message or payload.get("message") or "Erreur MyElectricalData")
        )
    return payload
