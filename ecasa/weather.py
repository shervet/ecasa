"""Température extérieure gratuite pour l'onglet Statistiques.

Deux sources, sans clé d'API :

1. **Météo-France via Open-Meteo** pour les 3 derniers mois environ. Open-Meteo
   expose les modèles AROME/ARPEGE de Météo-France avec un accès gratuit et
   sans inscription.
2. **Réanalyse ERA5 via Open-Meteo** pour les dates plus anciennes, quand les
   modèles Météo-France ne remontent pas assez loin.

Les résultats sont mis en cache en mémoire quelques minutes : l'onglet
Statistiques peut être rechargé sans marteler le service.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

METEOFRANCE_URL = "https://api.open-meteo.com/v1/meteofrance"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

# Open-Meteo accepte past_days jusqu'à 92 ; on garde une marge.
MAX_METEOFRANCE_PAST_DAYS = 88
CACHE_SECONDS = 600
DAILY_CACHE_SECONDS = 1800
ERROR_RETRY_SECONDS = 60


class WeatherError(RuntimeError):
    """Service météo injoignable ou réponse inexploitable."""


class WeatherResult:
    def __init__(self, hours: dict[str, dict[int, float]], source: str,
                 latitude: float, longitude: float):
        self.hours = hours
        self.source = source
        self.latitude = latitude
        self.longitude = longitude


class WeatherService:
    def __init__(self):
        self._cache: dict[tuple, tuple[float, WeatherResult]] = {}
        self._lock = threading.Lock()
        self._last_error: str | None = None
        self._last_error_at = 0.0
        self._daily_error: str | None = None
        self._daily_error_at = 0.0

    def hourly(
        self, latitude: float, longitude: float, start: date, end: date
    ) -> WeatherResult:
        """Températures horaires extérieures entre deux dates incluses.

        Le résultat est indexé par date ISO (``YYYY-MM-DD``) puis par heure
        locale (0–23). Les heures sans mesure sont absentes du dictionnaire.
        """
        if start > end:
            raise WeatherError("Période météo invalide")

        try:
            latitude = float(latitude)
            longitude = float(longitude)
        except (TypeError, ValueError) as err:
            raise WeatherError("Coordonnées météo invalides") from err

        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise WeatherError("Coordonnées météo hors limites")

        key = (
            round(latitude, 3),
            round(longitude, 3),
            start.isoformat(),
            end.isoformat(),
        )
        now = time.time()
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached[0] > now:
                return cached[1]
            if self._last_error and now - self._last_error_at < ERROR_RETRY_SECONDS:
                raise WeatherError(self._last_error)

        try:
            result = self._download(latitude, longitude, start, end)
        except WeatherError as err:
            with self._lock:
                self._last_error = str(err)
                self._last_error_at = time.time()
            raise

        with self._lock:
            self._cache[key] = (time.time() + CACHE_SECONDS, result)
            self._last_error = None
            # Un cache trop gros n'a aucun intérêt : on garde les entrées
            # récentes seulement.
            if len(self._cache) > 64:
                threshold = time.time() - CACHE_SECONDS * 2
                self._cache = {
                    item: value
                    for item, value in self._cache.items()
                    if value[0] > threshold
                }
        return result

    def daily(self, latitude: float, longitude: float, days: int = 7):
        """Prévisions journalières Météo-France (via Open-Meteo).

        Retourne ``(jours, source)`` où chaque jour est un dictionnaire
        ``{date, code, tmax, tmin}``. Les jours sans prévision sont ignorés.
        """
        try:
            latitude = float(latitude)
            longitude = float(longitude)
        except (TypeError, ValueError) as err:
            raise WeatherError("Coordonnées météo invalides") from err

        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise WeatherError("Coordonnées météo hors limites")

        try:
            days = max(1, min(int(days), 16))
        except (TypeError, ValueError):
            days = 7

        key = (
            "daily",
            round(latitude, 3),
            round(longitude, 3),
            days,
        )
        now = time.time()
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached[0] > now:
                return cached[1]
            if self._daily_error and now - self._daily_error_at < ERROR_RETRY_SECONDS:
                raise WeatherError(self._daily_error)

        params = {
            "latitude": f"{latitude:.4f}",
            "longitude": f"{longitude:.4f}",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min",
            "forecast_days": str(days),
            "timezone": "Europe/Paris",
        }

        # Météo-France (AROME) couvre ~4 jours : on complète la fin de semaine
        # avec le modèle « best match » d'Open-Meteo, qui agrège Météo-France
        # et les autres modèles.
        try:
            result = _parse_daily(self._http_json(METEOFRANCE_URL, params))
            source = "Météo-France (AROME via Open-Meteo)"
            if len(result) < days:
                try:
                    fallback = _parse_daily(
                        self._http_json(FORECAST_URL, params)
                    )
                except WeatherError:
                    fallback = []
                known = {item["date"] for item in result}
                for item in fallback:
                    if item["date"] not in known:
                        result.append(item)
                        known.add(item["date"])
                result.sort(key=lambda item: item["date"])
                if fallback:
                    source = "Météo-France + Open-Meteo (fin de semaine)"

            if not result:
                raise WeatherError("Aucune prévision journalière reçue")
        except WeatherError as err:
            with self._lock:
                self._daily_error = str(err)
                self._daily_error_at = time.time()
            raise

        with self._lock:
            self._cache[key] = (time.time() + DAILY_CACHE_SECONDS, (result, source))
            self._daily_error = None
        return result, source

    # ------------------------------------------------------------------ réseau

    def _download(self, latitude: float, longitude: float, start: date,
                  end: date) -> WeatherResult:
        today = datetime.now().date()
        # Une fin future (mois en cours) est rejetée par l'API : on la ramène
        # à aujourd'hui avant tout appel réseau.
        end = min(end, today)
        if start > end:
            raise WeatherError("Période météo invalide")

        limit = today - timedelta(days=MAX_METEOFRANCE_PAST_DAYS)
        if start < limit and end > limit:
            # La plage chevauche la limite des deux sources : archive pour la
            # partie ancienne, Météo-France pour la partie récente. Les heures
            # récentes écrasent les valeurs d'archive sur les jours communs.
            hours, _, res_lat, res_lon = self._download_range(
                latitude, longitude, start, limit
            )
            recent, _, rec_lat, rec_lon = self._download_range(
                latitude, longitude, limit + timedelta(days=1), end
            )
            for day, bucket in recent.items():
                hours.setdefault(day, {}).update(bucket)
            if not hours:
                raise WeatherError(
                    "Aucune température extérieure reçue du service météo"
                )
            return WeatherResult(
                hours,
                "Réanalyse ERA5 + Météo-France",
                rec_lat or res_lat,
                rec_lon or res_lon,
            )

        hours, source, res_lat, res_lon = self._download_range(
            latitude, longitude, start, end
        )
        return WeatherResult(hours, source, res_lat, res_lon)

    def _download_range(
        self, latitude: float, longitude: float, start: date, end: date
    ) -> tuple[dict[str, dict[int, float]], str, float, float]:
        """Télécharge une plage homogène et renvoie ``(heures, source, lat, lon)``."""
        today = datetime.now().date()
        use_meteofrance = start >= today - timedelta(days=MAX_METEOFRANCE_PAST_DAYS)

        if use_meteofrance:
            past_days = max(0, (today - start).days)
            params = {
                "latitude": f"{latitude:.4f}",
                "longitude": f"{longitude:.4f}",
                "hourly": "temperature_2m",
                "past_days": str(past_days),
                "forecast_days": "1",
                "timezone": "Europe/Paris",
            }
            url = METEOFRANCE_URL
            source = "Météo-France (AROME/ARPEGE via Open-Meteo)"
        else:
            params = {
                "latitude": f"{latitude:.4f}",
                "longitude": f"{longitude:.4f}",
                "hourly": "temperature_2m",
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "timezone": "Europe/Paris",
            }
            url = ARCHIVE_URL
            source = "Réanalyse ERA5 via Open-Meteo"

        payload = self._http_json(url, params)
        hours = _parse_hourly(payload, start, end)
        if not hours:
            raise WeatherError("Aucune température extérieure reçue du service météo")
        resolved_lat = float(payload.get("latitude") or latitude)
        resolved_lon = float(payload.get("longitude") or longitude)
        return hours, source, resolved_lat, resolved_lon

    @staticmethod
    def _http_json(url: str, params: dict) -> dict:
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(
            f"{url}?{query}",
            headers={
                "User-Agent": "CozytouchPlanner/1.0 (statistiques locales)",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                raw = response.read()
        except urllib.error.HTTPError as err:
            detail = ""
            try:
                detail = err.read().decode("utf-8", "replace")[:200]
            except OSError:
                pass
            raise WeatherError(
                f"Service météo HTTP {err.code}{(' : ' + detail) if detail else ''}"
            ) from err
        except urllib.error.URLError as err:
            raise WeatherError(f"Service météo injoignable : {err.reason}") from err
        except OSError as err:
            raise WeatherError(f"Service météo injoignable : {err}") from err

        try:
            payload = json.loads(raw.decode("utf-8"))
        except ValueError as err:
            raise WeatherError("Réponse météo illisible") from err

        if isinstance(payload, dict) and payload.get("error"):
            raise WeatherError(str(payload.get("reason") or "Erreur météo"))
        if not isinstance(payload, dict):
            raise WeatherError("Réponse météo inattendue")
        return payload


def _parse_daily(payload: dict) -> list[dict]:
    """Extrait les jours valides d'une réponse journalière Open-Meteo."""
    block = payload.get("daily")
    if not isinstance(block, dict):
        return []

    times = block.get("time") or []
    codes = block.get("weather_code") or []
    highs = block.get("temperature_2m_max") or []
    lows = block.get("temperature_2m_min") or []

    result = []
    for stamp, code, high, low in zip(times, codes, highs, lows):
        if high is None or low is None:
            continue
        try:
            high_value = round(float(high), 1)
            low_value = round(float(low), 1)
        except (TypeError, ValueError):
            continue
        try:
            code_value = int(code) if code is not None else None
        except (TypeError, ValueError):
            code_value = None
        result.append({
            "date": str(stamp),
            "code": code_value,
            "tmax": high_value,
            "tmin": low_value,
        })
    return result


def _parse_hourly(payload: dict, start: date, end: date) -> dict[str, dict[int, float]]:
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        return {}

    times = hourly.get("time") or []
    values = hourly.get("temperature_2m") or []
    result: dict[str, dict[int, float]] = {}

    for stamp, value in zip(times, values):
        if value is None:
            continue
        try:
            moment = datetime.fromisoformat(str(stamp))
            number = float(value)
        except (TypeError, ValueError):
            continue
        day = moment.date()
        if day < start or day > end:
            continue
        result.setdefault(day.isoformat(), {})[moment.hour] = round(number, 1)

    return result
