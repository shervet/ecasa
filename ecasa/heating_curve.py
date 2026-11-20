"""Courbe de chauffe : avance à la consigne par pièce (modèle RC du premier ordre).

Module pur et testable, sans accès réseau à l'import.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta


def fit_tau(transitions) -> dict | None:
    """Ajuste tau0, tau1 par moindres carrés sur les montées de consigne observées.

    ``transitions`` : liste de dicts {t_out, t_room, target, lag_minutes}.
    Retourne {tau0, tau1, n, r2} ou None si moins de 5 échantillons valides.
    """
    samples = []
    for item in transitions or []:
        try:
            t_out = float(item["t_out"])
            t_room = float(item["t_room"])
            target = float(item["target"])
            lag_minutes = float(item["lag_minutes"])
        except (KeyError, TypeError, ValueError):
            continue

        # Échantillon valide : conditions de montée réaliste.
        if not (
            target > t_out + 0.5
            and t_room > t_out + 0.5
            and target > t_room + 0.5
        ):
            continue

        ratio = (target - t_out) / (t_room - t_out)
        if ratio <= 1.0:
            continue
        tau_hours = (lag_minutes / 60.0) / math.log(ratio)
        # τ_i doit être dans ]0, 12 h].
        if not (0.0 < tau_hours <= 12.0):
            continue
        samples.append((t_out, tau_hours))

    if len(samples) < 5:
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
    }


def advance_minutes(
    tau0,
    tau1,
    t_out,
    t_room,
    target,
    eps=0.2,
    cap_minutes=240,
) -> float:
    """Calcule l'avance Δt (minutes) pour atteindre la consigne à l'heure.

    Retourne 0 si la hausse demandée est négligeable. Si t_out est absent,
    tau1 est neutralisé (τ = tau0 seul). Résultat borné dans [0, cap_minutes].
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


def _outside_temp_at(weather, config, moment: datetime):
    """Température extérieure à l'heure de ``moment`` via le cache météo passé.

    Réutilise l'accès météo en cache ; ne force pas de nouvel appel réseau.
    """
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
    result = None
    # 1) Lecture directe du cache en mémoire si disponible (aucun appel réseau).
    try:
        key = (
            round(float(latitude), 3),
            round(float(longitude), 3),
            day.isoformat(),
            day.isoformat(),
        )
        lock = getattr(weather, "_lock", None)
        cache = getattr(weather, "_cache", None)
        if lock is not None and isinstance(cache, dict):
            import time as _time

            with lock:
                cached = cache.get(key)
                if cached and cached[0] > _time.time():
                    result = cached[1]
    except Exception:  # noqa: BLE001
        result = None

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


def room_transitions(store, room_id, weather=None, config=None, days=120) -> list[dict]:
    """Extrait les montées de consigne observées pour une pièce.

    Source : historique des consignes désirées (échantillons), fiable et horodaté.
    Réutilise l'accès météo passée mis en cache (sans nouvel appel réseau dédié).
    """
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

        start_ts = cur["ts"]
        t_room = cur.get("ambient")
        if t_room is None:
            t_room = prev.get("ambient")
        target = cur_sp

        # Filtres de base sur les températures.
        if t_room is None:
            i += 1
            continue
        if not (10.0 <= float(t_room) <= 24.0):
            i += 1
            continue
        if not (15.0 <= float(target) <= 24.0):
            i += 1
            continue

        t_out = _outside_temp_at(weather, config, start_ts)
        if t_out is None:
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

        if lag_minutes is not None and lag_minutes >= 0:
            transitions.append(
                {
                    "t_out": float(t_out),
                    "t_room": float(t_room),
                    "target": float(target),
                    "lag_minutes": float(lag_minutes),
                    "at": start_ts.isoformat(timespec="seconds"),
                }
            )

        i += 1

    return transitions
