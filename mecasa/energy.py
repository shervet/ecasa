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


class EnergyError(RuntimeError):
    """MyElectricalData injoignable ou réponse inexploitable."""


class EnergyHTTPError(EnergyError):
    """Erreur HTTP renvoyée par MyElectricalData."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


class EnergyService:
    """Client MyElectricalData avec cache mémoire et repli de chemin."""

    def __init__(self, fetcher=None):
        self._fetcher = fetcher or _http_json
        self._cache: dict[tuple, tuple[float, dict[str, float]]] = {}
        self._lock = threading.Lock()
        self._errors: dict[tuple, tuple[float, str]] = {}

    def daily(self, base_url: str, pdl: str, token: str, start: date,
              end: date, refresh: bool = False) -> dict[str, float]:
        """Consommation journalière en kWh entre deux dates incluses.

        Le résultat est indexé par date ISO (``YYYY-MM-DD``). Les années sont
        demandées séparément : le cache reste utile d'un mois à l'autre et une
        seule année manquante ne fait pas tout recharger. La clé de cache
        inclut une empreinte du token pour qu'une rotation de token ne serve
        pas d'anciennes valeurs.

        ``refresh=True`` ignore le cache (synchro manuelle) tout en le
        réécrivant avec les valeurs fraîches.
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

        token_key = _token_key(token)
        values: dict[str, float] = {}
        for chunk_start, chunk_end in _year_chunks(start, end):
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
                    raise EnergyError(failure[1])

            try:
                chunk = self._fetch(base, pdl, token, chunk_start, chunk_end)
            except EnergyError as err:
                with self._lock:
                    self._errors[error_key] = (time.time(), str(err))
                raise

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
        return values

    # ------------------------------------------------------------------ réseau

    def _fetch(self, base: str, pdl: str, token: str, start: date,
               end: date) -> dict[str, float]:
        headers = {
            "Authorization": token,
            "Accept": "application/json",
            "User-Agent": "MeCasa/1.0 (consommation electrique locale)",
        }
        quoted_pdl = urllib.parse.quote(pdl, safe="")
        url = (
            f"{base}/daily_consumption/{quoted_pdl}/start/"
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

    En vue ``week``, ``day`` désigne un jour au sein de la semaine affichée
    (seuls le mois et le jour du mois comptent) : la fenêtre est la semaine
    ISO complète (lundi → dimanche) qui le contient, comparée d'année en
    année sur les mêmes jours calendaires. ``days`` est ignoré.
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
    _fill_view(payload, points, view, month, today, window)
    payload["summary"] = _summary(points, view, month, today, window)
    return payload


def test_energy(config: dict, service=None, today: date | None = None) -> dict:
    """Teste le token et le PDL sans rien enregistrer."""
    today = today or date.today()
    token = str(config.get("med_token") or "").strip()
    pdl = str(config.get("med_pdl") or "").strip()
    base_url = str(config.get("med_url") or MED_DEFAULT_URL).strip() or MED_DEFAULT_URL

    if not token or not pdl:
        return {
            "ok": False,
            "message": "Renseigne le token et le PDL MyElectricalData.",
        }

    service = service or EnergyService()
    try:
        values = service.daily(base_url, pdl, token, today - timedelta(days=7), today)
    except EnergyError as err:
        return {"ok": False, "message": str(err)}
    except Exception as err:  # noqa: BLE001
        return {"ok": False, "message": f"Connexion impossible : {err}"}

    if not values:
        return {
            "ok": False,
            "message": (
                "Connexion réussie, mais aucune consommation reçue sur les "
                "7 derniers jours."
            ),
            "pdl": pdl,
        }
    return {
        "ok": True,
        "message": f"{len(values)} jour(s) de consommation reçus.",
        "pdl": pdl,
        "samples": len(values),
        "last_date": max(values),
    }


# --------------------------------------------------------------------- outils


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
    selected = [day for day in points if day.month == month]
    years = {day.year for day in selected}
    if years:
        max_day = max(calendar.monthrange(year, month)[1] for year in years)
    else:
        max_day = calendar.monthrange(today.year, month)[1]
    limit = max_day
    if today.month == month:
        limit = min(limit, today.day)
        if selected:
            # Mois en cours : on s'arrête à la dernière donnée de cette
            # année. Une donnée ancienne (ex. le 30 novembre de l'an dernier)
            # ne doit pas laisser apparaître un jour encore sans relevé
            # (ex. le 10 novembre alors que la consommation du 10 n'est pas
            # encore disponible).
            current = [day.day for day in selected if day.year == today.year]
            if current:
                limit = min(limit, max(current))
    elif selected:
        # Mois passé : la comparaison porte sur le dernier jour disponible,
        # toutes années confondues.
        limit = min(limit, max(day.day for day in selected))
    limit = max(1, limit)
    end = min(max(ref_day or limit, 1), limit)
    start = max(1, end - size + 1)
    return {
        "month": month,
        "start": start,
        "end": end,
        "limit": limit,
        "size": size,
        "days": list(range(start, end + 1)),
    }


def _week_window(points: dict[date, float], month: int, ref_day: int | None,
                 today: date) -> dict:
    """Semaine ISO (lundi → dimanche) affichée par la vue ``week``.

    La date de référence est reconstruite dans l'année courante à partir du
    mois sélectionné et du jour du mois (``ref_day``) : comme pour la vue
    ``day``, seuls le mois et le jour comptent, car la comparaison porte sur
    les **mêmes jours calendaires de chaque année**. Sans ``ref_day``, la
    semaine courante s'applique.

    Bornage : la référence ne dépasse jamais ``today`` ni le dernier jour
    avec donnée de l'année en cours — impossible d'afficher une semaine
    entièrement future ou sans donnée récente. La semaine affichée reste
    entière (7 jours, lundi → dimanche), même si elle court après le jour
    borné : les jours sans donnée sont simplement vides.

    La fenêtre est une liste de paires ``(mois, jour)`` — et non de dates
    pleines — pour être comparée d'année en année. Une semaine ISO peut
    chevaucher deux mois (30/11 → 06/12), voire deux ans (29/12 → 04/01) :
    les paires le gèrent nativement, et le 29 février d'une année bissextile
    tombe sur ``None`` les autres années.

    Clés renvoyées :

    - ``mdays`` : les 7 paires ``(mois, jour)`` de la fenêtre ;
    - ``days`` : les 7 dates pleines dans l'année de référence ;
    - ``iso_week`` : numéro de semaine ISO du lundi ;
    - ``first`` / ``limit`` : lundis (dates) de la première et de la dernière
      semaine navigables dans l'année de référence ;
    - ``label`` : libellé court sans année, ex. ``Semaine 46 · 09–15
      novembre`` (ou ``Semaine 49 · 30/11–06/12`` à cheval sur deux mois).
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
    monday = ref - timedelta(days=ref.weekday())
    days = [monday + timedelta(days=offset) for offset in range(7)]

    limit_bound = min(today, last) if last.year == today.year else today
    limit = limit_bound - timedelta(days=limit_bound.weekday())
    jan_first = date(today.year, 1, 1)
    first = jan_first - timedelta(days=jan_first.weekday())

    iso_week = monday.isocalendar()[1]
    start_day, end_day = days[0], days[-1]
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
    return {
        "month": month,
        "iso_week": iso_week,
        "size": 7,
        "mdays": [(day.month, day.day) for day in days],
        "days": days,
        "first": first,
        "limit": limit,
        "label": label,
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


def _fill_view(payload: dict, points: dict[date, float], view: str, month: int,
               today: date, window: dict | None = None) -> None:
    if view == "year":
        years = sorted({day.year for day in points})
        payload["years"] = years
        payload["labels"] = [str(year) for year in years]
        # Totaux à date (comme le tableau) : l'année en cours est partielle,
        # on borne donc chaque année au même jour de l'année.
        totals = []
        for year in years:
            try:
                limit = date(year, today.month, today.day)
            except ValueError:
                limit = date(year, today.month, 28)
            totals.append(
                _round(
                    sum(
                        value for day, value in points.items()
                        if day.year == year and day <= limit
                    )
                )
            )
        payload["series"] = [
            {"key": "total", "label": "Consommation", "values": totals}
        ]
        return

    if view == "week":
        # Fenêtre de 7 jours (lundi → dimanche) comparée d'année en année :
        # une barre par jour calendaire, une série par année avec donnée dans
        # la fenêtre. ``window`` expose des repères JJ/MM (affichage) et ISO
        # (navigation côté client : comparaison lexicographique fiable).
        window = window or _week_window(points, month, None, today)
        mdays = window["mdays"]
        mdays_set = set(mdays)
        years = sorted(
            {day.year for day in points if (day.month, day.day) in mdays_set}
        )
        payload["years"] = years
        payload["labels"] = [
            f"{day_number:02d}/{month_number:02d}"
            for month_number, day_number in mdays
        ]
        payload["window"] = {
            "start": f"{window['days'][0].day:02d}/{window['days'][0].month:02d}",
            "end": f"{window['days'][-1].day:02d}/{window['days'][-1].month:02d}",
            "start_iso": window["days"][0].isoformat(),
            "end_iso": window["days"][-1].isoformat(),
            "first_iso": window["first"].isoformat(),
            "limit_iso": window["limit"].isoformat(),
            "month": window["month"],
            "iso_week": window["iso_week"],
            "size": window["size"],
            "label": window["label"],
        }
        series = []
        for year in years:
            values = []
            for month_number, day_number in mdays:
                try:
                    key = date(year, month_number, day_number)
                except ValueError:
                    # 29 février hors année bissextile.
                    values.append(None)
                    continue
                values.append(_round(points[key]) if key in points else None)
            series.append({"key": str(year), "label": str(year), "values": values})
        payload["series"] = series
        return

    if view == "day":
        window = window or _day_window(points, month, None, 7, today)
        day_numbers = set(window["days"])
        selected = {
            day: value
            for day, value in points.items()
            if day.month == month
        }
        years = sorted({day.year for day in selected})
        payload["years"] = years
        payload["labels"] = [
            f"{day_number:02d}/{month:02d}" for day_number in window["days"]
        ]
        payload["window"] = {
            key: window[key]
            for key in ("month", "start", "end", "limit", "size")
        }
        grouped = {}
        for day, value in selected.items():
            if day.day not in day_numbers:
                continue
            grouped[(day.year, day.day)] = grouped.get((day.year, day.day), 0.0) + value
        series = []
        for year in years:
            values = [
                _round(grouped[(year, day_number)])
                if (year, day_number) in grouped else None
                for day_number in window["days"]
            ]
            series.append({"key": str(year), "label": str(year), "values": values})
        payload["series"] = series
        return

    # Vue mensuelle : 12 libellés, une série par année.
    years = sorted({day.year for day in points})
    payload["years"] = years
    payload["labels"] = list(MONTH_SHORT)
    grouped = {}
    for day, value in points.items():
        grouped[(day.year, day.month)] = grouped.get((day.year, day.month), 0.0) + value
    series = []
    for year in years:
        values = [None] * 12
        for month_number in range(1, 13):
            total = grouped.get((year, month_number))
            if total is not None:
                values[month_number - 1] = _round(total)
        series.append({"key": str(year), "label": str(year), "values": values})
    payload["series"] = series


def _period_totals(points: dict[date, float], view: str, month: int,
                   today: date, window: dict | None):
    """Renvoie (années concernées, total_par_année) pour la période de la vue.

    ``total(year)`` -> ``(kwh, jours)`` calculé sur la période comparable :

    - ``day``   : les jours de la fenêtre affichée (mêmes jours du mois) ;
    - ``week``  : les 7 jours de la fenêtre affichée (mêmes jours
      calendaires, la semaine pouvant chevaucher deux mois) ;
    - ``year``  : l'année complète (l'année en cours reste partielle) ;
    - ``month`` : l'année à date (même date pour les années passées).
    """
    if view == "day":
        day_numbers = set(window["days"]) if window else None

        def total(year: int) -> tuple[float, int]:
            kwh = 0.0
            count = 0
            for day, value in points.items():
                if (
                    day.year == year
                    and day.month == month
                    and (day_numbers is None or day.day in day_numbers)
                ):
                    kwh += value
                    count += 1
            return kwh, count

        years = {
            day.year
            for day in points
            if day.month == month
            and (day_numbers is None or day.day in day_numbers)
        }
        return years, total

    if view == "week":
        # Les 7 jours de la fenêtre affichée (paires mois/jour), comparés
        # d'année en année — exactement la période du graphique.
        mdays = set(window["mdays"]) if window else set()

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
        # chaque année au même jour de l'année pour que l'écart N-1 ait un sens.
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

        return {day.year for day in points}, total

    # month : année à date
    def total(year: int) -> tuple[float, int]:
        try:
            limit = date(year, today.month, today.day)
        except ValueError:
            limit = date(year, today.month, 28)
        kwh = 0.0
        count = 0
        for day, value in points.items():
            if day.year == year and day <= limit:
                kwh += value
                count += 1
        return kwh, count

    return {day.year for day in points}, total


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
    - ``week`` : la semaine affichée (7 jours calendaires), dernière année
      avec données dans la fenêtre ;
    - ``year`` : la dernière année avec données ;
    - ``month`` : l'année en cours, à date.

    ``by_year`` décline le même calcul pour chaque année disponible.
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
            label = (
                f"{window['start']:02d}–{window['end']:02d} "
                f"{MONTH_LONG[month - 1].capitalize()} {ref}"
            )
            previous_label = (
                f"{window['start']:02d}–{window['end']:02d} "
                f"{MONTH_LONG[month - 1].capitalize()} {ref - 1}"
            )
        else:
            label = f"{MONTH_LONG[month - 1].capitalize()} {ref}"
            previous_label = f"{MONTH_LONG[month - 1].capitalize()} {ref - 1}"
    elif view == "week":
        # La semaine affichée n'a pas toujours de données pour l'année en
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
    else:  # mois : année en cours, à date
        ref = today.year
        label = f"Année {ref}"
        previous_label = f"Année {ref - 1}"

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
        raise EnergyHTTPError(
            f"MyElectricalData HTTP {err.code}"
            f"{(' : ' + detail) if detail else ''}",
            err.code,
        ) from err
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
