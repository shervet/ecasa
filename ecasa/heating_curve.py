"""Courbe de chauffe : avance à la consigne par pièce (modèle RC du premier ordre).

Module pur et testable, sans accès réseau à l'import.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from .weather import WeatherError


def fit_tau(transitions, counters=None) -> dict | None:
    """Ajuste tau0, tau1 par moindres carrés sur les montées de consigne observées.

    ``transitions`` : liste de dicts {t_out, t_room, target, lag_minutes}.
    ``counters`` reçoit ``{kept, reject_ratio, reject_tau_range, reject_delta,
    total}``, y compris quand le fit échoue.
    Retourne {tau0, tau1, n, r2, debug} ou None si moins de 3 échantillons valides.
    τ est accepté dans ]0, 24 h] (au-delà : rejet compté dans reject_tau_range).
    """
    if counters is None:
        counters = {}
    for key in ("kept", "reject_ratio", "reject_tau_range", "reject_delta", "total"):
        counters[key] = 0

    samples = []
    for item in transitions or []:
        counters["total"] += 1
        try:
            t_out = float(item["t_out"])
            t_room = float(item["t_room"])
            target = float(item["target"])
            lag_minutes = float(item["lag_minutes"])
        except (KeyError, TypeError, ValueError):
            continue

        # Filtres internes comptés dans l'ordre : ratio, τ, écart à la consigne.
        # Le garde-fou sur t_room garde le dénominateur du ratio sûr et conserve
        # le rejet historique des montées trop proches de t_out.
        if not (t_room > t_out + 0.5):
            counters["reject_ratio"] += 1
            continue
        ratio = (target - t_out) / (t_room - t_out)
        if ratio <= 1.0:
            counters["reject_ratio"] += 1
            continue
        tau_hours = (lag_minutes / 60.0) / math.log(ratio)
        # τ_i doit être dans ]0, 24 h].
        if not (0.0 < tau_hours <= 24.0):
            counters["reject_tau_range"] += 1
            continue
        if not (target > t_room + 0.5):
            counters["reject_delta"] += 1
            continue
        samples.append((t_out, tau_hours))

    counters["kept"] = len(samples)
    if len(samples) < 3:
        return None

    n = len(samples)
    xs = [s[0] for s in samples]
    ys = [s[1] for s in samples]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n

    denom = sum((x - mean_x) ** 2 for x in xs)
    if abs(denom) < 1e-12:
        tau1 = 0.0
    else:
        tau1 = sum((x - mean_x) * (y - mean_y) for x, y in samples) / denom
    tau0 = mean_y - tau1 * mean_x

    # Coefficient de détermination R².
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    if abs(ss_tot) < 1e-12:
        r2 = 1.0
    else:
        ss_res = sum((y - (tau0 + tau1 * x)) ** 2 for x, y in samples)
        r2 = 1.0 - ss_res / ss_tot

    return {
        "tau0": float(tau0),
        "tau1": float(tau1),
        "n": n,
        "r2": float(r2),
        "debug": {
            "kept": counters["kept"],
            "reject_ratio": counters["reject_ratio"],
            "reject_tau_range": counters["reject_tau_range"],
            "reject_delta": counters["reject_delta"],
        },
    }


def advance_minutes(
    tau0,
    tau1,
    t_out,
    t_room,
    target,
    eps=0.2,
    cap_minutes=120,
) -> float:
    """Calcule l'avance Δt (minutes) pour atteindre la consigne à l'heure.

    Retourne 0 si la hausse demandée est négligeable. Si t_out est absent,
    tau1 est neutralisé (τ = tau0 seul). Résultat borné dans [0, cap]
    (120 min par défaut, soit 2 h).
    """
    try:
        tau0_f = float(tau0)
        tau1_f = float(tau1)
        t_room_f = float(t_room)
        target_f = float(target)
        eps_f = float(eps)
        cap_f = float(cap_minutes)
    except (TypeError, ValueError):
        return 0.0

    delta_t = target_f - t_room_f
    if delta_t <= eps_f:
        return 0.0

    if t_out is None:
        tau_hours = tau0_f
    else:
        try:
            t_out_f = float(t_out)
        except (TypeError, ValueError):
            tau_hours = tau0_f
        else:
            tau_hours = tau0_f + tau1_f * t_out_f

    # τ doit être positif pour une montée significative.
    if tau_hours <= 0.0:
        return 0.0

    ratio = delta_t / eps_f
    if ratio <= 1.0:
        return 0.0

    minutes = tau_hours * 60.0 * math.log(ratio)
    if minutes < 0.0:
        minutes = 0.0
    if minutes > cap_f:
        minutes = cap_f
    return float(minutes)


def build_table(
    tau0,
    tau1,
    t_out,
    target,
    t_min=13.0,
    t_max=19.0,
    step=0.5,
) -> list[list[float]]:
    """Construit la table d'avance [température pièce, minutes].

    Cible = target si fourni, sinon 19 °C. Si t_out est absent, τ = tau0.
    Les minutes sont arrondies à 0,1.
    """
    if target is None:
        target_f = 19.0
    else:
        try:
            target_f = float(target)
        except (TypeError, ValueError):
            target_f = 19.0

    table = []
    current = float(t_min)
    # Garde-fou contre boucle infinie.
    for _ in range(1000):
        if current > float(t_max) + 1e-9:
            break
        minutes = advance_minutes(tau0, tau1, t_out, current, target_f)
        table.append([round(current, 1), round(minutes, 1)])
        current = round(current + float(step), 10)
    return table


def _as_float(value):
    """Convertit en float ou retourne None."""
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _cached_weather(weather, latitude: float, longitude: float, day):
    """Résultat météo déjà en cache couvrant ``day`` (aucun appel réseau).

    Cherche d'abord la clé d'un jour (ancien chemin), puis les chunks
    multi-jours écrits par les statistiques et le préchauffage du fit
    (``weather.hourly(lat, lon, start, end)``).
    """
    lock = getattr(weather, "_lock", None)
    cache = getattr(weather, "_cache", None)
    if lock is None or not isinstance(cache, dict):
        return None

    import time as _time

    now_ts = _time.time()
    lat = round(latitude, 3)
    lon = round(longitude, 3)
    target = day.isoformat()
    with lock:
        cached = cache.get((lat, lon, target, target))
        if cached and cached[0] > now_ts:
            return cached[1]
        for key, value in list(cache.items()):
            if not isinstance(key, tuple) or len(key) != 4:
                continue
            if key[0] != lat or key[1] != lon:
                continue
            if not value or value[0] <= now_ts:
                continue
            try:
                start_day = datetime.fromisoformat(str(key[2])).date()
                end_day = datetime.fromisoformat(str(key[3])).date()
            except (TypeError, ValueError):
                continue
            if start_day <= day <= end_day:
                return value[1]
    return None


def _outside_temp_at(weather, config, moment: datetime):
    """Température extérieure à l'heure de ``moment`` via le cache météo passé.

    Réutilise l'accès météo en cache ; ne force pas de nouvel appel réseau.
    Tout échec vaut « météo indisponible » pour cette transition seulement :
    une erreur météo ne doit pas invalider les transitions suivantes.
    """
    try:
        if not weather or not config:
            return None
        if not config.get("weather_enabled", True):
            return None
        try:
            latitude = float(config.get("weather_latitude"))
            longitude = float(config.get("weather_longitude"))
        except (TypeError, ValueError):
            return None

        day = moment.date()
        # Cache en mémoire uniquement (jour seul ou chunk multi-jours).
        result = _cached_weather(weather, latitude, longitude, day)

        # 2) Donnée absente du cache : pas de nouvel appel réseau.
        if result is None:
            return None

        hours = (result.hours or {}).get(day.isoformat(), {}) if result else {}
        hour = moment.hour
        temp = hours.get(hour)
        if temp is not None:
            return float(temp)
        # Repli : dernière heure connue avant l'instant demandé.
        past = {h: v for h, v in hours.items() if h <= hour}
        if past:
            return float(past[max(past)])
        return None
    except WeatherError:
        # Panne météo ponctuelle : on passe à la transition suivante sans
        # toucher au cache positif déjà réutilisé.
        return None
    except Exception:  # noqa: BLE001
        return None


def _room_device_ids(store, room_id: int) -> list[int]:
    """Identifiants des appareils rattachés à la pièce."""
    try:
        room_id = int(room_id)
    except (TypeError, ValueError):
        return []
    ids = []
    for device in store.devices():
        if int(device.get("room_id") or 0) == room_id:
            try:
                ids.append(int(device["device_id"]))
            except (KeyError, TypeError, ValueError):
                continue
    return ids


def _room_primary_device_id(store, room_id: int) -> int | None:
    """Appareil principal de la pièce (actuateur prioritaire)."""
    room = store.room(room_id)
    if not room:
        return None
    for key in ("actuator_device_id", "thermostat_device_id"):
        value = room.get(key)
        if value in (None, "", 0, "0"):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    device_ids = _room_device_ids(store, room_id)
    return device_ids[0] if device_ids else None


def _sample_series(store, device_id: int, start: datetime, end: datetime):
    """Série chronologique des échantillons d'un appareil."""
    try:
        rows = store.samples(int(device_id), start, end)
    except Exception:  # noqa: BLE001
        return []
    series = []
    for row in rows:
        try:
            ts = datetime.fromisoformat(str(row["ts"]))
        except (TypeError, ValueError):
            continue
        series.append(
            {
                "ts": ts,
                "ambient": _as_float(row.get("ambient")),
                "setpoint": _as_float(row.get("setpoint")),
                "mode": row.get("mode"),
            }
        )
    series.sort(key=lambda item: item["ts"])
    return series


def room_transitions(
    store,
    room_id,
    weather=None,
    config=None,
    days=120,
    counters=None,
    t_out_fallback=None,
) -> list[dict]:
    """Extrait les montées de consigne observées pour une pièce.

    Source : historique des consignes désirées (échantillons), fiable et horodaté.
    Réutilise l'accès météo passée mis en cache (sans nouvel appel réseau dédié).
    ``counters`` reçoit ``{rises, out_of_range, no_t_out, no_lag, valid}``.
    ``t_out_fallback`` (ex. 10.0) remplace toutes les températures extérieures,
    y compris celles qui avaient une météo valide : x constant, tau1 = 0.
    """
    if counters is None:
        counters = {}
    for key in ("rises", "out_of_range", "no_t_out", "no_lag", "valid"):
        counters[key] = 0
    fallback = None
    if t_out_fallback is not None:
        try:
            fallback = float(t_out_fallback)
        except (TypeError, ValueError):
            fallback = None

    device_id = _room_primary_device_id(store, room_id)
    if device_id is None:
        return []

    now = datetime.now()
    start = now - timedelta(days=max(1, int(days)))
    series = _sample_series(store, device_id, start, now)
    if len(series) < 2:
        return []

    transitions = []
    i = 1
    while i < len(series):
        prev = series[i - 1]
        cur = series[i]
        prev_sp = prev.get("setpoint")
        cur_sp = cur.get("setpoint")
        if prev_sp is None or cur_sp is None:
            i += 1
            continue

        # Montée de consigne observée : changement >= +0,5 °C vers le haut.
        if cur_sp - prev_sp < 0.5:
            i += 1
            continue
        counters["rises"] += 1

        start_ts = cur["ts"]
        t_room = cur.get("ambient")
        if t_room is None:
            t_room = prev.get("ambient")
        target = cur_sp

        # Filtres de base sur les températures.
        if t_room is None:
            counters["out_of_range"] += 1
            i += 1
            continue
        if not (10.0 <= float(t_room) <= 24.0):
            counters["out_of_range"] += 1
            i += 1
            continue
        if not (15.0 <= float(target) <= 24.0):
            counters["out_of_range"] += 1
            i += 1
            continue

        t_out = _outside_temp_at(weather, config, start_ts)
        if fallback is not None:
            # Mode repli : x constant pour toutes les transitions, y compris
            # celles qui avaient une météo valide (tau1 = 0 sans mélange).
            t_out = fallback
        elif t_out is None:
            counters["no_t_out"] += 1
            i += 1
            continue

        # Recherche du lag : premier relevé >= target - 0,2 °C dans les 12 h,
        # avant toute descente de consigne.
        deadline = start_ts + timedelta(hours=12)
        lag_minutes = None
        j = i
        while j < len(series):
            point = series[j]
            if point["ts"] > deadline:
                break
            sp = point.get("setpoint")
            if sp is not None and sp < target - 0.1:
                # Descente de consigne avant d'atteindre la cible.
                lag_minutes = None
                break
            amb = point.get("ambient")
            if amb is not None and amb >= target - 0.2:
                lag_minutes = (point["ts"] - start_ts).total_seconds() / 60.0
                break
            j += 1

        if lag_minutes is None or lag_minutes < 0:
            counters["no_lag"] += 1
            i += 1
            continue
        transitions.append(
            {
                "t_out": float(t_out),
                "t_room": float(t_room),
                "target": float(target),
                "lag_minutes": float(lag_minutes),
                "at": start_ts.isoformat(timespec="seconds"),
            }
        )
        counters["valid"] += 1

        i += 1

    return transitions
