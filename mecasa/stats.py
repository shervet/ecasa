"""Agrégation des mesures pour l'onglet Statistiques.

Les mesures sont enregistrées par le moteur (une toutes les 60 s environ) dans
la table ``samples``. Ce module les regroupe par heure (jour), par jour
(semaine / mois) et y ajoute la température extérieure moyenne issue de
``weather.py``.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from .schedule import WEEKDAY_NAMES
from .weather import WeatherError

RANGES = ("day", "week", "month")

RANGE_LABELS = {
    "day": "Jour",
    "week": "Semaine",
    "month": "Mois",
}


def parse_anchor(value) -> date:
    """Date de référence (aujourd'hui si absente ou invalide)."""
    if not value:
        return datetime.now().date()
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except ValueError:
        return datetime.now().date()


def period_bounds(range_name: str, anchor: date) -> tuple[date, date]:
    """Bornes incluses de la période demandée."""
    if range_name == "day":
        return anchor, anchor

    if range_name == "week":
        start = anchor - timedelta(days=anchor.weekday())
        return start, start + timedelta(days=6)

    if range_name == "month":
        start = anchor.replace(day=1)
        if start.month == 12:
            next_month = start.replace(year=start.year + 1, month=1, day=1)
        else:
            next_month = start.replace(month=start.month + 1, day=1)
        return start, next_month - timedelta(days=1)

    raise ValueError("Période invalide (jour, semaine ou mois attendu)")


def build_stats(store, weather, room: dict, range_name: str, anchor: date,
                config: dict) -> dict:
    """Statistiques d'une piece, agregees sur tous ses equipements."""
    if range_name not in RANGES:
        raise ValueError("Période invalide (jour, semaine ou mois attendu)")

    devices = [
        device
        for device in (room.get("devices") or [])
        if device.get("device_id") is not None
    ]

    today = datetime.now().date()
    if anchor > today:
        anchor = today

    start_date, end_date = period_bounds(range_name, anchor)
    now = datetime.now()
    end_dt = min(datetime.combine(end_date, time(23, 59, 59)), now)
    start_dt = datetime.combine(start_date, time.min)

    rows = []
    if end_dt >= start_dt:
        for device in devices:
            try:
                device_id = int(device["device_id"])
            except (TypeError, ValueError):
                continue
            rows.extend(store.samples(device_id, start_dt, end_dt))

    buckets = _buckets(range_name, start_date, end_date)
    sums = [
        {"ambient": [], "setpoint": [], "count": 0}
        for _ in buckets
    ]

    for row in rows:
        try:
            moment = datetime.fromisoformat(str(row["ts"]))
        except (TypeError, ValueError):
            continue
        index = _bucket_index(range_name, moment, start_date, len(buckets))
        if index is None:
            continue
        bucket = sums[index]
        bucket["count"] += 1
        ambient = _as_float(row.get("ambient"))
        setpoint = _as_float(row.get("setpoint"))
        if ambient is not None:
            bucket["ambient"].append(ambient)
        if setpoint is not None:
            bucket["setpoint"].append(setpoint)

    hours, weather_error, weather_result = _weather_values(
        weather, config, start_date, end_date, today, now
    )

    outside_by_bucket = _outside_by_bucket(
        range_name, buckets, hours, start_date, today, now
    )

    for index, bucket in enumerate(buckets):
        values = sums[index]
        bucket["ambient"] = _average(values["ambient"])
        bucket["setpoint"] = _average(values["setpoint"])
        bucket["outside"] = outside_by_bucket[index]
        bucket["count"] = values["count"]

    all_ambient = [
        _as_float(row.get("ambient"))
        for row in rows
        if _as_float(row.get("ambient")) is not None
    ]
    all_setpoint = [
        _as_float(row.get("setpoint"))
        for row in rows
        if _as_float(row.get("setpoint")) is not None
    ]
    all_outside = [
        value
        for day_values in hours.values()
        for value in day_values.values()
    ] if hours else []

    ambient_avg = _average(all_ambient)
    setpoint_avg = _average(all_setpoint)

    first_sample = None
    last_sample = None
    for device in devices:
        try:
            first, last = store.sample_bounds(int(device["device_id"]))
        except (TypeError, ValueError):
            continue
        if first and (first_sample is None or first < first_sample):
            first_sample = first
        if last and (last_sample is None or last > last_sample):
            last_sample = last

    return {
        "room_id": room.get("id"),
        "room_name": room.get("name") or "Ma maison",
        "devices": [
            {
                "device_id": int(device["device_id"]),
                "name": device.get("name") or f"Appareil {device['device_id']}",
                "device_type": device.get("device_type", "atlantic"),
            }
            for device in devices
        ],
        "range": range_name,
        "range_label": RANGE_LABELS[range_name],
        "anchor": anchor.isoformat(),
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "buckets": buckets,
        "summary": {
            "ambient_avg": ambient_avg,
            "ambient_min": _min(all_ambient),
            "ambient_max": _max(all_ambient),
            "setpoint_avg": setpoint_avg,
            "outside_avg": _average(all_outside),
            "outside_min": _min(all_outside),
            "outside_max": _max(all_outside),
            "gap_avg": (
                round(ambient_avg - setpoint_avg, 1)
                if ambient_avg is not None and setpoint_avg is not None
                else None
            ),
            "samples": len(rows),
            "buckets_with_data": sum(1 for item in buckets if item["count"]),
            "first_sample": first_sample,
            "last_sample": last_sample,
        },
        "weather": {
            "ok": not weather_error,
            "source": weather_result.source if weather_result else None,
            "error": weather_error,
            "latitude": weather_result.latitude if weather_result else None,
            "longitude": weather_result.longitude if weather_result else None,
        },
    }


# --------------------------------------------------------------------- outils


def _buckets(range_name: str, start: date, end: date) -> list[dict]:
    if range_name == "day":
        return [
            {
                "key": f"{start.isoformat()}T{hour:02d}",
                "label": f"{hour:02d}:00",
                "short": f"{hour:02d}h",
                "date": start.isoformat(),
                "hour": hour,
            }
            for hour in range(24)
        ]

    buckets = []
    day = start
    while day <= end:
        if range_name == "week":
            label = f"{WEEKDAY_NAMES[day.weekday()]} {day.day:02d}/{day.month:02d}"
            short = WEEKDAY_NAMES[day.weekday()][:3] + "."
        else:
            label = f"{day.day:02d}/{day.month:02d}"
            short = f"{day.day:02d}"
        buckets.append(
            {
                "key": day.isoformat(),
                "label": label,
                "short": short,
                "date": day.isoformat(),
                "hour": None,
            }
        )
        day += timedelta(days=1)
    return buckets


def _bucket_index(range_name: str, moment: datetime, start: date,
                  bucket_count: int) -> int | None:
    if range_name == "day":
        if moment.date() != start:
            return None
        return min(23, max(0, moment.hour))

    index = (moment.date() - start).days
    if 0 <= index < bucket_count:
        return index
    return None


def _weather_values(weather, config: dict, start: date, end: date,
                    today: date, now: datetime):
    """Renvoie ({date: {heure: temp}}, erreur, WeatherResult)."""
    if not config.get("weather_enabled", True):
        return {}, "Météo extérieure désactivée dans les réglages.", None

    try:
        latitude = float(config.get("weather_latitude"))
        longitude = float(config.get("weather_longitude"))
    except (TypeError, ValueError):
        return {}, "Coordonnées météo non configurées dans les réglages.", None

    effective_end = min(end, today)
    if effective_end < start:
        return {}, None, None

    try:
        result = weather.hourly(latitude, longitude, start, effective_end)
    except WeatherError as err:
        return {}, str(err), None
    except Exception as err:  # noqa: BLE001 - l'UI ne doit pas casser
        return {}, f"Météo indisponible : {err}", None

    # Pour aujourd'hui, on ne garde que les heures déjà passées : le reste
    # n'a pas encore de mesure intérieure.
    hours = result.hours
    if effective_end == today:
        today_values = hours.get(today.isoformat())
        if today_values:
            hours = dict(hours)
            hours[today.isoformat()] = {
                hour: value
                for hour, value in today_values.items()
                if hour <= now.hour
            }
    return hours, None, result


def _outside_by_bucket(range_name: str, buckets: list[dict],
                       hours: dict[str, dict[int, float]], start: date,
                       today: date, now: datetime) -> list[float | None]:
    values: list[float | None] = []
    if range_name == "day":
        day_values = hours.get(start.isoformat(), {})
        for hour in range(24):
            if start == today and hour > now.hour:
                values.append(None)
            else:
                values.append(_round(day_values.get(hour)))
        return values

    for bucket in buckets:
        day = bucket["date"]
        day_values = hours.get(day, {})
        if day == today.isoformat():
            day_values = {
                hour: value for hour, value in day_values.items()
                if hour <= now.hour
            }
        values.append(_average(list(day_values.values())))
    return values


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _average(values: list[float]) -> float | None:
    clean = [value for value in values if value is not None]
    if not clean:
        return None
    return round(sum(clean) / len(clean), 1)


def _min(values: list[float]) -> float | None:
    clean = [value for value in values if value is not None]
    return round(min(clean), 1) if clean else None


def _max(values: list[float]) -> float | None:
    clean = [value for value in values if value is not None]
    return round(max(clean), 1) if clean else None


def _round(value) -> float | None:
    return round(value, 1) if value is not None else None
