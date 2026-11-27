"""Agrégation des mesures pour l'onglet Statistiques.

Les mesures sont enregistrées par le moteur (une toutes les 60 s environ) dans
la table ``samples``. Ce module les regroupe par tranche de 15 min (jour),
par heure (semaine / mois) et y ajoute la température extérieure moyenne issue
de ``weather.py``.
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

ANNUAL_DAYS = 365


def annual_temperature(store, weather, rooms, config: dict,
                       today: date | None = None) -> dict:
    """Température moyenne sur les 365 derniers jours.

    Deux sources sont comparées, sans nouvel appel réseau :

    - le cache météo déjà présent (chunks horaires de ``WeatherService``) ;
    - les échantillons horaires des pièces (``store.samples``).

    La source qui couvre le plus de jours est retenue ; à couverture égale,
    la météo est prioritaire car elle ne dépend pas des pièces équipées.
    """
    today = today or datetime.now().date()
    start = today - timedelta(days=ANNUAL_DAYS - 1)

    weather_hours = {}
    if config.get("weather_enabled", True):
        try:
            latitude = float(config.get("weather_latitude"))
            longitude = float(config.get("weather_longitude"))
        except (TypeError, ValueError):
            latitude = longitude = None
        if latitude is not None and longitude is not None:
            cached_hours = getattr(weather, "cached_hours", None)
            if callable(cached_hours):
                try:
                    weather_hours = cached_hours(
                        latitude, longitude, start, today
                    ) or {}
                except Exception:  # noqa: BLE001 - statistique facultative
                    weather_hours = {}

    weather_values = [
        value
        for day_values in weather_hours.values()
        for value in day_values.values()
        if value is not None
    ]
    weather_days = sum(
        1
        for day_values in weather_hours.values()
        if any(value is not None for value in day_values.values())
    )
    weather_avg = _average(weather_values)

    sample_values: list[float] = []
    sample_days: set[str] = set()
    start_dt = datetime.combine(start, time.min)
    end_dt = datetime.combine(today, time(23, 59, 59))
    for room in rooms or []:
        for device in room.get("devices") or []:
            try:
                device_id = int(device["device_id"])
            except (TypeError, ValueError):
                continue
            try:
                rows = store.samples(device_id, start_dt, end_dt)
            except Exception:  # noqa: BLE001 - une pièce ne bloque pas l'ensemble
                continue
            for row in rows:
                ambient = _as_float(row.get("ambient"))
                if ambient is None:
                    continue
                try:
                    moment = datetime.fromisoformat(str(row["ts"]))
                except (TypeError, ValueError):
                    continue
                if not start <= moment.date() <= today:
                    continue
                sample_values.append(ambient)
                sample_days.add(moment.date().isoformat())

    sample_avg = _average(sample_values)
    sample_coverage = len(sample_days)

    if weather_avg is not None and (
        sample_avg is None or weather_days >= sample_coverage
    ):
        avg_c, coverage_days, source, source_label = (
            weather_avg, weather_days, "weather", "météo"
        )
    elif sample_avg is not None:
        avg_c, coverage_days, source, source_label = (
            sample_avg, sample_coverage, "rooms", "pièces"
        )
    else:
        avg_c, coverage_days, source, source_label = (
            None, 0, None, None
        )

    return {
        "range": "year",
        "start": start.isoformat(),
        "end": today.isoformat(),
        "window_days": ANNUAL_DAYS,
        "avg_c": avg_c,
        "coverage_days": coverage_days,
        "coverage_pct": round(coverage_days * 100 / ANNUAL_DAYS, 1),
        "source": source,
        "source_label": source_label,
        "message": None if avg_c is not None else "pas de relevés sur l'année",
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

    # La consigne d'une pièce est appliquée par son actuateur : les autres
    # appareils (thermostat sans planning, sonde) ne doivent pas diluer la
    # moyenne en y mêlant leur propre « consigne » de repli.
    actuator_id = None
    try:
        actuator_id = int(room.get("actuator_device_id"))
    except (TypeError, ValueError):
        actuator_id = None

    # Un actionneur qui n est pas un appareil de la piece ne remonte jamais de
    # releve : sans cet avertissement, toutes les statistiques de consigne
    # (moyenne, ecart, consigne par tranche) devenaient silencieusement vides.
    if actuator_id is not None and not any(
        int(item.get("device_id")) == actuator_id
        for item in devices
        if str(item.get("device_id") or "").lstrip("-").isdigit()
    ):
        logger = getattr(store, "log", None)
        if callable(logger):
            logger(
                "avertissement",
                "L actionnaire de la piece (appareil %d) n est pas rattache a la "
                "piece : consigne indisponible dans les statistiques."
                % actuator_id,
                actuator_id,
            )
        actuator_id = None

    today = datetime.now().date()
    if anchor > today:
        anchor = today

    start_date, end_date = period_bounds(range_name, anchor)
    now = datetime.now()
    end_dt = min(datetime.combine(end_date, time(23, 59, 59)), now)
    start_dt = datetime.combine(start_date, time.min)

    rows = []
    actuator_rows = []
    if end_dt >= start_dt:
        for device in devices:
            try:
                device_id = int(device["device_id"])
            except (TypeError, ValueError):
                continue
            device_rows = store.samples(device_id, start_dt, end_dt)
            rows.extend(device_rows)
            if actuator_id is not None and device_id == actuator_id:
                actuator_rows = device_rows

    # Sous-ensemble utilisé pour la consigne : l'actuateur seul s'il est
    # défini (pas de repli sur les autres appareils), sinon tous les
    # appareils de la pièce (comportement historique, pièce mono-appareil).
    setpoint_rows = actuator_rows if actuator_id is not None else rows

    buckets = _buckets(range_name, start_date, end_date)
    sums = [
        {"ambient": [], "setpoint": [], "count": 0}
        for _ in buckets
    ]

    # Température d'ambiance et compteurs : tous les appareils de la pièce.
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
        if ambient is not None:
            bucket["ambient"].append(ambient)

    # Consigne : uniquement le sous-ensemble retenu (actuateur ou tous).
    for row in setpoint_rows:
        try:
            moment = datetime.fromisoformat(str(row["ts"]))
        except (TypeError, ValueError):
            continue
        index = _bucket_index(range_name, moment, start_date, len(buckets))
        if index is None:
            continue
        setpoint = _as_float(row.get("setpoint"))
        if setpoint is not None:
            sums[index]["setpoint"].append(setpoint)

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
        for row in setpoint_rows
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
        "setpoint_device_id": actuator_id,
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
        # 96 buckets: 15-min intervals (00:00, 00:15, ..., 23:45)
        buckets = []
        for hour in range(24):
            for quarter in (0, 15, 30, 45):
                buckets.append(
                    {
                        "key": f"{start.isoformat()}T{hour:02d}:{quarter:02d}",
                        "label": f"{hour:02d}:{quarter:02d}",
                        "short": f"{hour:02d}h{quarter:02d}",
                        "date": start.isoformat(),
                        "hour": hour,
                        "quarter": quarter,
                    }
                )
        return buckets

    if range_name == "week":
        # 168 buckets: hourly for 7 days (24h × 7)
        buckets = []
        day = start
        for day_index in range(7):
            for hour in range(24):
                current = start + timedelta(days=day_index)
                label = f"{WEEKDAY_NAMES[current.weekday()]} {current.day:02d}/{current.month:02d} {hour:02d}h"
                short = f"{WEEKDAY_NAMES[current.weekday()][:3]}.{hour:02d}h"
                buckets.append(
                    {
                        "key": f"{current.isoformat()}T{hour:02d}",
                        "label": label,
                        "short": short,
                        "date": current.isoformat(),
                        "hour": hour,
                    }
                )
        return buckets

    # month: hourly per day (24 × days in month)
    buckets = []
    day = start
    while day <= end:
        for hour in range(24):
            label = f"{day.day:02d}/{day.month:02d} {hour:02d}h"
            short = f"{day.day:02d} {hour:02d}h"
            buckets.append(
                {
                    "key": f"{day.isoformat()}T{hour:02d}",
                    "label": label,
                    "short": short,
                    "date": day.isoformat(),
                    "hour": hour,
                }
            )
        day += timedelta(days=1)
    return buckets


def _bucket_index(range_name: str, moment: datetime, start: date,
                  bucket_count: int) -> int | None:
    if range_name == "day":
        if moment.date() != start:
            return None
        # 96 buckets: 4 per hour
        return min(95, max(0, moment.hour * 4 + moment.minute // 15))

    if range_name == "week":
        day_index = (moment.date() - start).days
        if 0 <= day_index < 7:
            return day_index * 24 + moment.hour
        return None

    # month: hourly per day
    day_index = (moment.date() - start).days
    if 0 <= day_index < bucket_count // 24:
        return day_index * 24 + moment.hour
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
        # 96 buckets: 15-min intervals - use the same hour value for all 4 quarters
        for hour in range(24):
            hour_val = _round(day_values.get(hour))
            if start == today and hour > now.hour:
                values.extend([None] * 4)
            else:
                values.extend([hour_val] * 4)
        return values

    if range_name == "week":
        for bucket in buckets:
            day = bucket["date"]
            hour = bucket["hour"]
            day_values = hours.get(day, {})
            if day == today.isoformat() and hour > now.hour:
                values.append(None)
            else:
                values.append(_round(day_values.get(hour)))
        return values

    # month: hourly per day
    for bucket in buckets:
        day = bucket["date"]
        hour = bucket["hour"]
        day_values = hours.get(day, {})
        if day == today.isoformat() and hour > now.hour:
            values.append(None)
        else:
            values.append(_round(day_values.get(hour)))
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


def temperature_series(range_name: str, buckets: list[dict],
                       weather_hours: dict[str, dict[int, float]] | None,
                       config: dict) -> list[dict[str, float | None]]:
    """Return temperature series aligned on consumption buckets.

    For each bucket, returns ``{"label": str, "avg_c": float|None}``.
    If a bucket has fewer than 50 % of its hours with valid temperature data,
    ``avg_c`` is ``None`` (rendered as a hole in the curve).

    The function handles ``day`` (96 15-min buckets), ``week`` (168 hourly buckets),
    ``month`` (hourly per day) and ``year`` (12 monthly buckets).
    """
    if weather_hours is None:
        return [{"label": bucket["label"], "avg_c": None} for bucket in buckets]

    result: list[dict[str, float | None]] = []
    today = datetime.now().date()
    start = today - timedelta(days=365 - 1)

    # ---- day : 96 buckets (15-min intervals) ----
    if range_name == "day":
        for bucket in buckets:
            hour = bucket["hour"]  # 0..23
            quarter = bucket["quarter"]  # 0, 15, 30, 45
            day_str = bucket["date"]
            day_values = weather_hours.get(day_str, {})
            hour_val = day_values.get(hour)
            if hour_val is not None and isinstance(hour_val, (int, float)):
                result.append({"label": bucket["label"], "avg_c": _round(hour_val)})
            else:
                # Count coverage across the whole window
                covered = 0
                total = 0
                for ds, hv in weather_hours.items():
                    try:
                        d = datetime.fromisoformat(ds).date()
                    except ValueError:
                        continue
                    if start <= d <= today:
                        total += 1
                        if hour in hv:
                            covered += 1
                if total > 0 and covered / total >= 0.5:
                    # average over covered days for this hour
                    vals = [hv[hour] for ds, hv in weather_hours.items()
                            if _in_range(datetime.fromisoformat(ds).date(), start, today)
                            and hour in hv]
                    if vals:
                        result.append({"label": bucket["label"], "avg_c": _round(sum(vals) / len(vals))})
                    else:
                        result.append({"label": bucket["label"], "avg_c": None})
                else:
                    result.append({"label": bucket["label"], "avg_c": None})
        return result

    # ---- week : 168 buckets (hourly for 7 days) ----
    if range_name == "week":
        for bucket in buckets:
            day_str = bucket["date"]
            hour = bucket["hour"]
            day_values = weather_hours.get(day_str, {})
            hour_val = day_values.get(hour)
            if hour_val is not None and isinstance(hour_val, (int, float)):
                result.append({"label": bucket["label"], "avg_c": _round(hour_val)})
            else:
                # Count coverage across the whole window for this specific hour
                covered = 0
                total = 0
                for ds, hv in weather_hours.items():
                    try:
                        d = datetime.fromisoformat(ds).date()
                    except ValueError:
                        continue
                    if start <= d <= today and d.weekday() == datetime.fromisoformat(day_str).weekday():
                        total += 1
                        if hour in hv:
                            covered += 1
                if total > 0 and covered / total >= 0.5:
                    vals = [hv[hour] for ds, hv in weather_hours.items()
                            if _in_range(datetime.fromisoformat(ds).date(), start, today)
                            and datetime.fromisoformat(ds).weekday() == datetime.fromisoformat(day_str).weekday()
                            and hour in hv]
                    if vals:
                        result.append({"label": bucket["label"], "avg_c": _round(sum(vals) / len(vals))})
                    else:
                        result.append({"label": bucket["label"], "avg_c": None})
                else:
                    result.append({"label": bucket["label"], "avg_c": None})
        return result

    # ---- month : hourly per day ----
    if range_name == "month":
        for bucket in buckets:
            day_str = bucket["date"]
            hour = bucket["hour"]
            day_values = weather_hours.get(day_str, {})
            hour_val = day_values.get(hour)
            if hour_val is not None and isinstance(hour_val, (int, float)):
                result.append({"label": bucket["label"], "avg_c": _round(hour_val)})
            else:
                # Count coverage across the whole window for this specific hour
                covered = 0
                total = 0
                for ds, hv in weather_hours.items():
                    try:
                        d = datetime.fromisoformat(ds).date()
                    except ValueError:
                        continue
                    if start <= d <= today and d.day == datetime.fromisoformat(day_str).day and d.month == datetime.fromisoformat(day_str).month:
                        total += 1
                        if hour in hv:
                            covered += 1
                if total > 0 and covered / total >= 0.5:
                    vals = [hv[hour] for ds, hv in weather_hours.items()
                            if _in_range(datetime.fromisoformat(ds).date(), start, today)
                            and datetime.fromisoformat(ds).day == datetime.fromisoformat(day_str).day
                            and datetime.fromisoformat(ds).month == datetime.fromisoformat(day_str).month
                            and hour in hv]
                    if vals:
                        result.append({"label": bucket["label"], "avg_c": _round(sum(vals) / len(vals))})
                    else:
                        result.append({"label": bucket["label"], "avg_c": None})
                else:
                    result.append({"label": bucket["label"], "avg_c": None})
        return result

    # ---- year : 12 monthly buckets ----
    if range_name == "year":
        # Group all hourly temps by month (1-12)
        months_temps: dict[int, list[float]] = {m: [] for m in range(1, 13)}
        # Jours reellement presents par mois : le seuil de couverture doit se
        # baser sur les heures existantes, pas sur 31*24 (un mois court ou une
        # fenetre tronquee echouait artificiellement au seuil des 50 %).
        months_days: dict[int, set[str]] = {m: set() for m in range(1, 13)}
        for day_str, hour_vals in weather_hours.items():
            try:
                d = datetime.fromisoformat(day_str).date()
            except ValueError:
                continue
            if d > today or d < start:
                continue
            month = d.month
            months_days[month].add(day_str)
            for h in range(24):
                if h in hour_vals:
                    months_temps[month].append(hour_vals[h])
        for i, bucket in enumerate(buckets):
            month = i + 1  # buckets ordered Jan..Dec
            vals = months_temps.get(month, [])
            # 50% des heures de ce mois sur les 365 derniers jours
            total_hours = len(months_days.get(month, ())) * 24
            if total_hours and len(vals) >= 0.5 * total_hours:
                avg = _average(vals) if vals else None
                result.append({"label": bucket["label"], "avg_c": avg})
            else:
                result.append({"label": bucket["label"], "avg_c": None})
        return result

    # unknown range
    return [{"label": bucket["label"], "avg_c": None} for bucket in buckets]


def _in_range(d: datetime.date, start: datetime.date, today: datetime.date) -> bool:
    return start <= d <= today


def _average(values: list[float]) -> float | None:
    clean = [value for value in values if value is not None]
    if not clean:
        return None
    return round(sum(clean) / len(clean), 1)
