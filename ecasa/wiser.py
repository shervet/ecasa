"""Client HTTP minimal pour la passerelle Schneider Electric Wiser Home (Hub local).

API 100% locale, sans cloud :
    GET   http://<host>/data/domain/       -> tout le système (pièces, thermostats, vannes)
    PATCH http://<host>/data/domain/Room/{id} -> consigne d'une pièce (en dixièmes de degrés)

Authentification :
    En-tête HTTP 'SECRET: <cle_secrete>'
"""

from __future__ import annotations

import http.client
import json
import socket
import urllib.error
import urllib.request

DAY_KEYS = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


class WiserError(RuntimeError):
    """Erreur générique Wiser."""


# --------------------------------------------------------------- planning Wiser
#
# Le Hub expose le planning natif dans /data/domain/ (objet Schedule) et, selon
# les versions, dans /data/domain/Schedule/{id}. Le format varie d'un firmware a
# l'autre : on reste volontairement tolerant.
#
# Variantes connues :
#   {"Monday": {"SetPoints": [{"Time": 450, "DegreesC": 190}, ...]}, ...}
#   {"Days": [{"SetPoints": [...]}, ...]}
#   {"Monday": [{"Time": "07:30", "SetPoint": 19}, ...]}
# Les temperatures sont parfois en dixiemes de degre (190 -> 19.0), parfois en
# degres. Une valeur <= 0 (souvent -200) signifie "arret / hors gel".


def _first(mapping, *keys):
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        if key in mapping:
            return mapping[key]
    lowered = {str(k).lower(): v for k, v in mapping.items()}
    for key in keys:
        if str(key).lower() in lowered:
            return lowered[str(key).lower()]
    return None


def _as_int(value):
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _require_int(value, label: str) -> int:
    """Convertit un identifiant Wiser en entier, sinon leve WiserError."""
    try:
        return int(value)
    except (TypeError, ValueError) as err:
        raise WiserError(
            f"Identifiant {label} Wiser invalide : {value!r}"
        ) from err


def _time_to_minutes(value):
    """Normalise une heure de creneau en minutes depuis minuit."""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if ":" in text:
            parts = text.split(":")
            try:
                hours = int(parts[0])
                minutes = int(parts[1]) if len(parts) > 1 else 0
            except (TypeError, ValueError):
                return None
            return max(0, min(1439, hours * 60 + minutes))
        value = text
    minutes = _as_int(value)
    if minutes is None:
        return None
    if minutes > 1440:  # probablement des secondes
        minutes = int(round(minutes / 60))
    return max(0, min(1439, minutes))


def _setpoint_celsius(value):
    """Renvoie la consigne en °C, ou None si le creneau est un arret/hors gel."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    if number > 50:  # dixiemes de degre (190 -> 19.0)
        number = number / 10.0
    if number <= 6.5:  # consigne hors gel : equivalent d'un arret
        return None
    if number < 5 or number > 35:
        return None
    return round(number, 1)


def _humidity_percent(value):
    """Humidite relative en %, ou None si absente ou hors plage.

    Les thermostats Wiser exposent ``MeasuredHumidity`` en pourcentage. Certains
    firmwares renvoient des dixiemes (450 -> 45 %) : on normalise sans jamais
    afficher une valeur aberrante.
    """
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    if number == 0:
        # Un thermostat muet renvoie 0 : aucune humidité réelle n'est nulle.
        return None
    if number > 100:
        # Certains firmwares renvoient des dixiemes (455 -> 45,5 %) ou des
        # centiemes (4800 -> 48 %). Une valeur brute > 100 % n'a pas de sens.
        if number <= 1000:
            number = number / 10.0
        elif number <= 10000:
            number = number / 100.0
        else:
            return None
    if not 0 <= number <= 100:
        return None
    return round(number, 1)


def _extract_points(raw):
    """Extrait la liste des points d'une journee, quel que soit l'emballage."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        for key in (
            "SetPoints", "setPoints", "Setpoints", "setpoints",
            "Points", "points", "Events", "events",
            "Slots", "slots", "Schedule", "schedule",
        ):
            points = raw.get(key)
            if isinstance(points, list):
                return [item for item in points if isinstance(item, dict)]
        if _first(raw, "Time", "StartTime", "Minutes", "Hour") is not None:
            return [raw]
    return []


def parse_schedule_days(schedule) -> list[list[dict]]:
    """Convertit un planning Wiser en 7 listes de creneaux {time, mode, temp}."""
    days: list[list[dict]] = [[] for _ in range(7)]
    if not isinstance(schedule, dict):
        return days

    containers = [None] * 7
    for index, name in enumerate(DAY_KEYS):
        containers[index] = _first(
            schedule, name, name.lower(), name[:3], name[:3].lower()
        )

    if all(container is None for container in containers):
        raw_days = _first(schedule, "Days", "days", "Week", "week")
        if isinstance(raw_days, list) and len(raw_days) == 7:
            containers = list(raw_days)
        elif isinstance(raw_days, dict):
            for index, name in enumerate(DAY_KEYS):
                containers[index] = _first(
                    raw_days, name, name.lower(), str(index), str(index + 1)
                )

    for index, raw in enumerate(containers):
        by_minutes: dict[int, dict] = {}
        for point in _extract_points(raw):
            minutes = _time_to_minutes(
                _first(
                    point,
                    "Time", "time", "StartTime", "startTime",
                    "Minutes", "minutes", "Hour", "hour",
                )
            )
            if minutes is None:
                continue
            celsius = _setpoint_celsius(
                _first(
                    point,
                    "DegreesC", "degreesC", "Degrees", "degrees",
                    "SetPoint", "setPoint", "Setpoint", "setpoint",
                    "Temperature", "temperature", "Temp", "temp",
                    "Value", "value",
                )
            )
            by_minutes[minutes] = {
                "time": f"{minutes // 60:02d}:{minutes % 60:02d}",
                "mode": "heat" if celsius is not None else "off",
                "temp": celsius if celsius is not None else 6.0,
            }
        days[index] = [by_minutes[minutes] for minutes in sorted(by_minutes)]

    return days


def _schedule_list(raw) -> list[dict]:
    """Normalise la collection de plannings renvoyee par le Hub."""
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        # Certains firmwares renvoient directement un planning unique.
        if any(key in raw for key in DAY_KEYS) or _first(raw, "Days", "days") is not None:
            return [raw]
        return [item for item in raw.values() if isinstance(item, dict)]
    return []


def resolve_room_schedule(domain, room_id: int, fetch=None):
    """Retrouve et convertit le planning natif associe a une piece Wiser."""
    if not isinstance(domain, dict):
        return None

    schedules = _schedule_list(
        domain.get("Schedule") or domain.get("Schedules") or []
    )

    rooms = domain.get("Room") or []
    if isinstance(rooms, dict):
        rooms = list(rooms.values())
    room = next(
        (
            item for item in rooms
            if isinstance(item, dict) and _as_int(item.get("id")) == int(room_id)
        ),
        None,
    )

    schedule_id = None
    if room:
        schedule_id = _as_int(
            _first(
                room,
                "ScheduleId", "ScheduleID", "scheduleId",
                "HeatingScheduleId", "heatingScheduleId",
                "CurrentScheduleId", "currentScheduleId",
            )
        )

    chosen = None
    if schedule_id is not None:
        chosen = next(
            (
                item for item in schedules
                if _as_int(_first(item, "id", "Id", "ID")) == schedule_id
            ),
            None,
        )
        if chosen is None and fetch is not None:
            chosen = fetch(schedule_id)

    if chosen is None:
        for item in schedules:
            room_ids = _first(item, "RoomIds", "roomIds", "Rooms", "rooms") or []
            if isinstance(room_ids, list) and any(
                _as_int(value) == int(room_id) for value in room_ids
            ):
                chosen = item
                break

    if chosen is None:
        heating = [
            item for item in schedules
            if str(_first(item, "Type", "type") or "").lower()
            in ("heating", "chauffage", "")
        ]
        if len(heating) == 1:
            chosen = heating[0]

    if chosen is None:
        return None

    return {
        "schedule_id": _as_int(_first(chosen, "id", "Id", "ID")),
        "name": _first(chosen, "Name", "name"),
        "days": parse_schedule_days(chosen),
    }


class WiserClient:
    """Client pour le Hub local Schneider Wiser Home."""

    def __init__(self, host: str, secret: str, timeout: int = 8):
        self.host = host.strip().rstrip("/")
        self.secret = secret.strip()
        self.timeout = timeout

    def _url(self, path: str) -> str:
        base = self.host
        if not base.startswith("http://") and not base.startswith("https://"):
            base = f"http://{base}"
        return f"{base}{path}"

    def _headers(self) -> dict:
        return {
            "SECRET": self.secret,
            "Content-Type": "application/json;charset=UTF-8",
            "Accept": "application/json",
        }

    def _request(self, method: str, path: str, payload=None) -> dict:
        url = self._url(path)
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                status = resp.status
                body = resp.read()
                if status not in (200, 201, 202, 204):
                    raise WiserError(f"Wiser HTTP {status}")
                return json.loads(body.decode("utf-8")) if body else {}
        except urllib.error.HTTPError as err:
            if err.code in (401, 403):
                raise WiserError("Clé secrète Wiser refusée par le Hub") from err
            raise WiserError(f"Erreur Hub Wiser HTTP {err.code}") from err
        except urllib.error.URLError as err:
            raise WiserError(f"Hub Wiser injoignable ({self.host}) : {err.reason}") from err
        except (OSError, http.client.HTTPException, socket.timeout) as err:
            # Timeout de lecture du corps ou connexion coupee en cours de
            # reponse : on les ramene dans WiserError pour que l'engine garde
            # son message « Hub Wiser injoignable » et son backoff.
            raise WiserError(f"Hub Wiser injoignable ({self.host}) : {err}") from err

    def get_domain(self) -> dict:
        """Lit l'état complet du système Wiser."""
        return self._request("GET", "/data/domain/")

    def get_rooms(self) -> list[dict]:
        """Renvoie la liste normalisée des pièces Wiser."""
        domain = self.get_domain()
        raw_rooms = domain.get("Room") or []
        devices = domain.get("Device") or []
        if isinstance(raw_rooms, dict):
            raw_rooms = list(raw_rooms.values())
        if isinstance(devices, dict):
            devices = list(devices.values())

        # Les thermostats d'ambiance vivent dans une collection dédiée
        # (« RoomStat »). C'est là que se trouvent MeasuredTemperature et
        # MeasuredHumidity ; les entrées « Device » n'en portent pas.
        raw_stats = domain.get("RoomStat") or []
        if isinstance(raw_stats, dict):
            raw_stats = list(raw_stats.values())
        room_stats = {
            _require_int(stat["id"], "RoomStat"): stat
            for stat in raw_stats
            if isinstance(stat, dict) and stat.get("id") is not None
        }
        # Repli pour les firmwares qui exposent la mesure sur le Device.
        for device in devices:
            if device.get("ProductType") != "RoomStat":
                continue
            try:
                stat_id = _require_int(device.get("id"), "RoomStat")
            except WiserError:
                continue
            room_stats.setdefault(stat_id, device)

        rooms = []
        for r in raw_rooms:
            room_id = _require_int(r.get("id"), "Room")
            calc_temp = r.get("CalculatedTemperature")
            setpoint = r.get("CurrentSetPoint")
            stat_id = r.get("RoomStatId")
            stat_key = (
                _require_int(stat_id, "RoomStat") if stat_id is not None else None
            )
            stat = room_stats.get(stat_key) if stat_key is not None else None
            stat = stat or {}

            # L'humidite vient du thermostat d'ambiance quand il en expose une ;
            # sinon certains firmwares la placent directement sur la piece.
            humidity = _humidity_percent(
                _first(stat, "MeasuredHumidity", "Humidity", "RelativeHumidity")
            )
            if humidity is None:
                humidity = _humidity_percent(
                    _first(r, "Humidity", "RelativeHumidity", "MeasuredHumidity")
                )

            rooms.append({
                "id": room_id,
                "name": r.get("Name", f"Pièce {room_id}"),
                "temp": round(calc_temp / 10.0, 1) if calc_temp and calc_temp != -32768 else None,
                "setpoint": round(setpoint / 10.0, 1) if setpoint and setpoint != -32768 else None,
                "humidity": humidity,
                "mode": r.get("Mode", "Auto"),
                "has_room_stat": stat_key is not None and stat_key in room_stats,
                "heating_rate": r.get("HeatingRate", 0),
            })
        return rooms

    def _fetch_schedule(self, schedule_id: int) -> dict | None:
        """Lit un planning precis quand le Hub ne l'inclut pas dans /data/domain/."""
        try:
            data = self._request(
                "GET", f"/data/domain/Schedule/{int(schedule_id)}"
            )
        except WiserError:
            return None
        if isinstance(data, list):
            return next((item for item in data if isinstance(item, dict)), None)
        if isinstance(data, dict):
            for key in ("Schedule", "schedule", "Data", "data"):
                value = data.get(key)
                if isinstance(value, dict):
                    return value
                if isinstance(value, list) and value and isinstance(value[0], dict):
                    return value[0]
            return data
        return None

    def get_schedule(self, room_id: int) -> dict | None:
        """Planning natif d'une piece : {schedule_id, name, days}."""
        return resolve_room_schedule(
            self.get_domain(), int(room_id), self._fetch_schedule
        )

    def get_schedules(self) -> list[dict]:
        """Tous les plannings natifs du Hub, convertis au format de l'application.

        Inclut les plannings classiques (``Schedule``) ainsi que les scènes /
        automatisations nommées (``Scene``, ``Automation``, ``ActionGroup``)
        lorsqu'elles contiennent un planning hebdomadaire.
        """
        domain = self.get_domain()
        items = []
        for key in ("Schedule", "Schedules", "Scene", "Scenes",
                    "Automation", "Automations", "ActionGroup", "ActionGroups"):
            raw = domain.get(key)
            if raw is None:
                continue
            items.extend(_schedule_list(raw))
        # Déduplique par id.
        seen = set()
        unique = []
        for item in items:
            sid = _as_int(_first(item, "id", "Id", "ID"))
            if sid is not None and sid in seen:
                continue
            if sid is not None:
                seen.add(sid)
            unique.append(item)

        schedules = []
        for item in unique:
            days = parse_schedule_days(item)
            if not any(day for day in days):
                # Pas de planning hebdomadaire exploitable : on ignore
                # (scène purement conditionnelle).
                continue
            schedules.append({
                "schedule_id": _as_int(_first(item, "id", "Id", "ID")),
                "name": _first(item, "Name", "name", "ScheduleName", "scheduleName",
                                 "schedule_name", "HeatingScheduleName", "heatingScheduleName",
                                 "DisplayName", "displayName", "Label", "label",
                                 "Title", "title", "Description", "description"),
                "type": _first(item, "Type", "type"),
                "days": days,
            })
        return schedules

    def set_room_temperature(self, room_id: int, temp_celsius: float) -> bool:
        """Définit la consigne d'une pièce Wiser (ex: 19.5 °C -> 195).

        On utilise RequestOverride (API Hub v1) : sur certains firmwares,
        Mode/ManualSetPoint seuls sont acceptés sans effet alors que
        RequestOverride applique réellement la consigne.
        """
        temp_wiser = int(round(temp_celsius * 10))
        temp_wiser = max(50, min(300, temp_wiser))  # 5°C à 30°C
        self._request(
            "PATCH",
            f"/data/domain/Room/{int(room_id)}",
            {"RequestOverride": {"Type": "Manual", "SetPoint": temp_wiser}},
        )
        return True


class MockWiserClient:
    """Client simulé pour tester Wiser sans matériel physique."""

    def __init__(self):
        self.rooms = [
            {
                "id": 1,
                "name": "Salon (Thermostat Wiser)",
                "temp": 21.4,
                "setpoint": 20.0,
                "humidity": 52.0,
                "mode": "Auto",
                "has_room_stat": True,
                "schedule_id": 1,
            },
            {
                "id": 2,
                "name": "Chambre Parentale",
                "temp": 19.1,
                "setpoint": 18.0,
                "humidity": 47.0,
                "mode": "Auto",
                "has_room_stat": False,
                "schedule_id": 2,
            },
            {
                "id": 3,
                "name": "Bureau / Chambre 2",
                "temp": 19.6,
                "setpoint": 19.0,
                "humidity": 55.0,
                "mode": "Auto",
                "has_room_stat": False,
                "schedule_id": 3,
            },
        ]
        # Planning natif simule, au format Drayton/Schneider :
        # Time en minutes depuis minuit, DegreesC en dixiemes de degre.
        self.schedules = {
            1: {
                "id": 1,
                "Name": "Confort",
                "Type": "Heating",
                "Monday": {"SetPoints": [
                    {"Time": 390, "DegreesC": 190},
                    {"Time": 480, "DegreesC": 200},
                    {"Time": 1350, "DegreesC": 170},
                ]},
                "Tuesday": {"SetPoints": [
                    {"Time": 390, "DegreesC": 190},
                    {"Time": 480, "DegreesC": 200},
                    {"Time": 1350, "DegreesC": 170},
                ]},
                "Wednesday": {"SetPoints": [
                    {"Time": 390, "DegreesC": 190},
                    {"Time": 480, "DegreesC": 200},
                    {"Time": 1350, "DegreesC": 170},
                ]},
                "Thursday": {"SetPoints": [
                    {"Time": 390, "DegreesC": 190},
                    {"Time": 480, "DegreesC": 200},
                    {"Time": 1350, "DegreesC": 170},
                ]},
                "Friday": {"SetPoints": [
                    {"Time": 390, "DegreesC": 190},
                    {"Time": 480, "DegreesC": 200},
                    {"Time": 1350, "DegreesC": 170},
                ]},
                "Saturday": {"SetPoints": [
                    {"Time": 480, "DegreesC": 200},
                    {"Time": 1380, "DegreesC": 170},
                ]},
                "Sunday": {"SetPoints": [
                    {"Time": 480, "DegreesC": 200},
                    {"Time": 1380, "DegreesC": 170},
                ]},
            },
            2: {
                "id": 2,
                "Name": "Éco",
                "Type": "Heating",
                "Monday": {"SetPoints": [
                    {"Time": 360, "DegreesC": 190},
                    {"Time": 480, "DegreesC": 170},
                    {"Time": 1230, "DegreesC": 190},
                    {"Time": 1350, "DegreesC": -200},
                ]},
                "Tuesday": {"SetPoints": [
                    {"Time": 360, "DegreesC": 190},
                    {"Time": 480, "DegreesC": 170},
                    {"Time": 1230, "DegreesC": 190},
                    {"Time": 1350, "DegreesC": -200},
                ]},
                "Wednesday": {"SetPoints": [
                    {"Time": 360, "DegreesC": 190},
                    {"Time": 480, "DegreesC": 170},
                    {"Time": 1230, "DegreesC": 190},
                    {"Time": 1350, "DegreesC": -200},
                ]},
                "Thursday": {"SetPoints": [
                    {"Time": 360, "DegreesC": 190},
                    {"Time": 480, "DegreesC": 170},
                    {"Time": 1230, "DegreesC": 190},
                    {"Time": 1350, "DegreesC": -200},
                ]},
                "Friday": {"SetPoints": [
                    {"Time": 360, "DegreesC": 190},
                    {"Time": 480, "DegreesC": 170},
                    {"Time": 1230, "DegreesC": 190},
                    {"Time": 1350, "DegreesC": -200},
                ]},
                "Saturday": {"SetPoints": [
                    {"Time": 510, "DegreesC": 190},
                    {"Time": 1380, "DegreesC": -200},
                ]},
                "Sunday": {"SetPoints": [
                    {"Time": 510, "DegreesC": 190},
                    {"Time": 1380, "DegreesC": -200},
                ]},
            },
            3: {
                "id": 3,
                "Name": "Absence",
                "Type": "Heating",
                "Monday": {"SetPoints": [
                    {"Time": 420, "DegreesC": 190},
                    {"Time": 540, "DegreesC": 170},
                    {"Time": 1020, "DegreesC": 200},
                    {"Time": 1380, "DegreesC": -200},
                ]},
                "Tuesday": {"SetPoints": [
                    {"Time": 420, "DegreesC": 190},
                    {"Time": 540, "DegreesC": 170},
                    {"Time": 1020, "DegreesC": 200},
                    {"Time": 1380, "DegreesC": -200},
                ]},
                "Wednesday": {"SetPoints": [
                    {"Time": 420, "DegreesC": 190},
                    {"Time": 540, "DegreesC": 170},
                    {"Time": 1020, "DegreesC": 200},
                    {"Time": 1380, "DegreesC": -200},
                ]},
                "Thursday": {"SetPoints": [
                    {"Time": 420, "DegreesC": 190},
                    {"Time": 540, "DegreesC": 170},
                    {"Time": 1020, "DegreesC": 200},
                    {"Time": 1380, "DegreesC": -200},
                ]},
                "Friday": {"SetPoints": [
                    {"Time": 420, "DegreesC": 190},
                    {"Time": 540, "DegreesC": 170},
                    {"Time": 1020, "DegreesC": 200},
                    {"Time": 1380, "DegreesC": -200},
                ]},
                "Saturday": {"SetPoints": [
                    {"Time": 540, "DegreesC": 190},
                    {"Time": 1380, "DegreesC": -200},
                ]},
                "Sunday": {"SetPoints": [
                    {"Time": 540, "DegreesC": 190},
                    {"Time": 1380, "DegreesC": -200},
                ]},
            },
        }

    def get_domain(self) -> dict:
        return {
            "System": {"Name": "Wiser Mock Hub"},
            "Room": [
                {
                    "id": r["id"],
                    "Name": r["name"],
                    "CalculatedTemperature": int(r["temp"] * 10),
                    "CurrentSetPoint": int(r["setpoint"] * 10),
                    "Mode": r["mode"],
                    "RoomStatId": 100 if r["has_room_stat"] else None,
                    "ScheduleId": r["schedule_id"],
                }
                for r in self.rooms
            ],
            "Device": [{"id": 100, "ProductType": "RoomStat"}],
            "Schedule": list(self.schedules.values()),
        }

    def get_rooms(self) -> list[dict]:
        return list(self.rooms)

    def get_schedule(self, room_id: int) -> dict | None:
        return resolve_room_schedule(self.get_domain(), int(room_id))

    def get_schedules(self) -> list[dict]:
        items = list(self.schedules.values()) + [
            {
                "id": 10,
                "Name": "Hugo pas là",
                "Type": "Heating",
                "Monday": {"SetPoints": [{"Time": 420, "DegreesC": 170}, {"Time": 1380, "DegreesC": 160}]},
                "Tuesday": {"SetPoints": [{"Time": 420, "DegreesC": 170}, {"Time": 1380, "DegreesC": 160}]},
                "Wednesday": {"SetPoints": [{"Time": 420, "DegreesC": 170}, {"Time": 1380, "DegreesC": 160}]},
                "Thursday": {"SetPoints": [{"Time": 420, "DegreesC": 170}, {"Time": 1380, "DegreesC": 160}]},
                "Friday": {"SetPoints": [{"Time": 420, "DegreesC": 170}, {"Time": 1380, "DegreesC": 160}]},
                "Saturday": {"SetPoints": [{"Time": 480, "DegreesC": 170}, {"Time": 1380, "DegreesC": 160}]},
                "Sunday": {"SetPoints": [{"Time": 480, "DegreesC": 170}, {"Time": 1380, "DegreesC": 160}]},
            },
            {
                "id": 11,
                "Name": "Hugo vacances",
                "Type": "Heating",
                "Monday": {"SetPoints": [{"Time": 480, "DegreesC": 160}, {"Time": 1380, "DegreesC": 150}]},
                "Tuesday": {"SetPoints": [{"Time": 480, "DegreesC": 160}, {"Time": 1380, "DegreesC": 150}]},
                "Wednesday": {"SetPoints": [{"Time": 480, "DegreesC": 160}, {"Time": 1380, "DegreesC": 150}]},
                "Thursday": {"SetPoints": [{"Time": 480, "DegreesC": 160}, {"Time": 1380, "DegreesC": 150}]},
                "Friday": {"SetPoints": [{"Time": 480, "DegreesC": 160}, {"Time": 1380, "DegreesC": 150}]},
                "Saturday": {"SetPoints": [{"Time": 480, "DegreesC": 160}, {"Time": 1380, "DegreesC": 150}]},
                "Sunday": {"SetPoints": [{"Time": 480, "DegreesC": 160}, {"Time": 1380, "DegreesC": 150}]},
            },
        ]
        seen = set()
        unique = []
        for item in items:
            sid = item.get("id")
            if sid is not None and sid in seen:
                continue
            if sid is not None:
                seen.add(sid)
            unique.append(item)
        result = []
        for item in unique:
            days = parse_schedule_days(item)
            if not any(day for day in days):
                continue
            result.append({
                "schedule_id": item.get("id"),
                "name": item.get("Name") or item.get("name"),
                "type": item.get("Type") or item.get("type"),
                "days": days,
            })
        return result

    def set_room_temperature(self, room_id: int, temp_celsius: float) -> bool:
        for r in self.rooms:
            if r["id"] == int(room_id):
                r["setpoint"] = round(temp_celsius, 1)
                return True
        return False
