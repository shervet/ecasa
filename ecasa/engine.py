"""Moteur : lit le cloud Atlantic et le Hub local Wiser, calcule l'etat voulu, l'applique.

Le planning vit ici, pas dans la clim ni dans le thermostat. On ecrit donc un etat (mode + consigne)
a chaque changement de creneau, et on le reaffirme si besoin.
"""

from __future__ import annotations

import math
import threading
import time
import traceback
from datetime import date, datetime, timedelta

from . import heating_curve

from .atlantic import AtlanticError, ExecutionTimeout, RateLimited
from .wiser import WiserError
from .schedule import (
    MODE_LABELS,
    MODE_VALUES,
    VALUE_LABELS,
    VALUE_MODES,
    describe,
    find_slot,
    next_slot,
    week_type,
)

TICK_SECONDS = 10
ANTI_FLAP_SECONDS = 45
MIN_POLL_SECONDS = 300

# Fenêtre d'apprentissage de la courbe de chauffe et préchauffage météo.
CURVE_FIT_DAYS = 120
WEATHER_WARM_CHUNK_DAYS = 30
WEATHER_WARM_PAUSE_SECONDS = 0.2

# Le Hub Wiser n'a pas de vrai mode "off" : on envoie une consigne hors gel.
# 6 °C est la valeur utilisee par les thermostats Wiser pour la protection gel.
WISER_FROST_TEMP = 6.0

# Soufflerie et orientation du flux d'air (catalogue Cozytouch) :
#   100800 ROOM_stFanModeSequence      (sequence de vitesses disponibles)
#   100801 ROOM_stFanSpeed             (1 basse, 2 moyenne, 3 haute, 5 auto)
#   100803 ROOM_stLouverAirFlowDirection (1 a 6, position des volets)
#   100804 ROOM_bLouverSwingMode       (1 = balayage)
FAN_CAP = 100801
LOUVER_CAP = 100803
SWING_CAP = 100804

FAN_VALUES = {"low": "1", "med": "2", "high": "3", "auto": "5"}
FAN_LABELS = {"low": "Basse", "med": "Moyenne", "high": "Haute", "auto": "Automatique"}
AIRFLOW_POSITIONS = {f"pos{index}": str(index) for index in range(1, 7)}


def _iso_date(value) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def absence_window(settings: dict, now: datetime | None = None) -> dict | None:
    """Fenetre de vacances active, ou ``None``.

    Les dates sont incluses : ``absence_until`` est le dernier jour d'absence.
    Une date de debut ou de fin absente laisse la fenetre ouverte de ce cote.
    """
    start = _iso_date(settings.get("absence_start"))
    until = _iso_date(settings.get("absence_until"))
    if start is None and until is None:
        return None
    today = (now or datetime.now()).date()
    if start is not None and today < start:
        return None
    if until is not None and today > until:
        return None
    return {
        "start": start.isoformat() if start else None,
        "until": until.isoformat() if until else None,
    }


def presence_status(settings: dict, now: datetime | None = None) -> dict:
    """Etat de presence effectif : manuel ou fenetre de vacances."""
    window = absence_window(settings, now)
    if window:
        return {
            "active": True,
            "mode": "absent",
            "reason": "vacation",
            **window,
        }
    if str(settings.get("presence_mode", "present")).strip().lower() == "absent":
        return {
            "active": True,
            "mode": "absent",
            "reason": "manual",
            "start": None,
            "until": None,
        }
    return {
        "active": False,
        "mode": "present",
        "reason": "present",
        "start": None,
        "until": None,
    }


def absence_active(settings: dict, now: datetime | None = None) -> bool:
    return bool(presence_status(settings, now).get("active"))


def _caps(device: dict) -> dict:
    """Normalise les capacites quel que soit le format renvoye par l'API."""
    caps = device.get("capabilities")

    if isinstance(caps, list):
        return {
            int(item["capabilityId"]): item.get("value")
            for item in caps
            if "capabilityId" in item
        }

    if isinstance(caps, dict):
        if isinstance(caps.get("values"), dict):
            return {int(key): value for key, value in caps["values"].items()}
        return {int(key): value for key, value in caps.items() if str(key).isdigit()}

    return {}


def _as_float(value, default=None):
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default


def _mode_value(value) -> str:
    """Ramene '3', '3.0' ou 3 a '3'."""
    text = str(value if value is not None else "").strip()
    return text.split(".")[0]


def device_name(device: dict, caps: dict) -> str:
    room = caps.get(154)
    for key in ("customName", "name", "longName"):
        value = device.get(key)
        if not value or value in ("---", ""):
            continue
        text = str(value)
        if room and text.startswith(("ROOM_", "THZONE_")):
            continue
        return text

    if room:
        return str(room)

    return f"Appareil {device.get('deviceId')}"


def detect_caps(caps: dict) -> dict:
    if 40 in caps:
        setpoint = 40
    elif 17 in caps:
        setpoint = 17
    else:
        setpoint = 40

    return {
        "cap_mode": 7,
        "cap_setpoint": setpoint,
        "cap_setpoint_cool": 177 if 177 in caps else 0,
        "cap_ambient": 117 if 117 in caps else 40,
        "cap_prog": 184 if 184 in caps else 0,
        "cap_fan": FAN_CAP if FAN_CAP in caps else 0,
        "cap_louver": LOUVER_CAP if LOUVER_CAP in caps else 0,
        "cap_swing": SWING_CAP if SWING_CAP in caps else 0,
    }


class Engine:
    def __init__(self, store, client, wiser_client=None, weather=None, config_getter=None):
        self.store = store
        self.client = client
        self.wiser_client = wiser_client
        self.weather = weather
        self._config_getter = config_getter
        self.state: dict[int, dict] = {}
        self.availability: dict[int, bool] = {}
        self.wiser_rooms: dict[int, dict] = {}
        self.last_poll: datetime | None = None
        self.last_error: str | None = None
        self.last_action: str | None = None
        # Vrai seulement apres une lecture cloud reussie : evite d'envoyer des
        # ordres a des appareils qui ne sont plus sur le compte (ou sans auth).
        self._cloud_ok = False
        self.mock = client.__class__.__name__.startswith("Mock")
        self.wiser_mock = bool(
            wiser_client and wiser_client.__class__.__name__.startswith("Mock")
        )

        self._last_attempt: dict[int, float] = {}
        self._missing: dict[int, int] = {}
        self._next_prune = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._next_poll = 0.0
        # Courbe de chauffe : recalcul du fit une fois par jour (par pièce).
        self._next_curve_fit: dict[int, float] = {}
        self._curve_fit_dirty: set[int] = set()

    def _config(self) -> dict:
        getter = self._config_getter
        if getter is None:
            return {}
        try:
            return getter() or {}
        except Exception:  # noqa: BLE001
            return {}

    # ------------------------------------------------------------------ cycle

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if time.time() >= self._next_poll:
                    self.poll()
                    interval = max(
                        MIN_POLL_SECONDS,
                        int(_as_float(self.store.settings().get("poll_interval"), 300)),
                    )
                    self._next_poll = time.time() + interval

                self.tick()
            except RateLimited as err:
                self.last_error = str(err)
                self._next_poll = time.time() + max(30, err.retry_after)
            except AtlanticError as err:
                self.last_error = str(err)
                self._next_poll = time.time() + 60
            except WiserError as err:
                self.last_error = f"Wiser : {err}"
                self._next_poll = time.time() + 30
            except Exception:  # noqa: BLE001
                self.last_error = traceback.format_exc(limit=2)
                self._next_poll = time.time() + 60

            self._stop.wait(TICK_SECONDS)

    # ------------------------------------------------------------------ lecture

    def poll(self) -> None:
        errors: list[str] = []
        cloud_ok = False

        # 1. Lecture Cloud Atlantic
        try:
            setup = self.client.get_setup()
            devices = setup.get("devices") or []
            fresh: dict[int, dict] = {}
            avail: dict[int, bool] = {}

            for device in devices:
                caps = _caps(device)
                device_id = int(device.get("deviceId", 0))
                if not device_id:
                    continue
                fresh[device_id] = caps
                avail[device_id] = bool(device.get("isAvailable", True))

                stored = self.store.device(device_id)
                if stored is None:
                    if _mode_value(caps.get(7)) == "":
                        continue
                    self.store.upsert_device(device_id, device_name(device, caps))
                    self.store.update_device(device_id, {
                        **detect_caps(caps),
                        "device_type": "atlantic",
                    })
                else:
                    # Les appareils deja enregistres n'ont pas forcement les
                    # colonnes des nouvelles capacites (soufflerie, orientation).
                    # On complete uniquement ce qui a change : pas d'ecriture
                    # disque a chaque lecture.
                    detected = detect_caps(caps)
                    changes = {
                        key: value
                        for key, value in detected.items()
                        if stored.get(key) != value
                    }
                    if changes:
                        self.store.update_device(device_id, changes)

            with self._lock:
                self.state = fresh
                self.availability = avail

            # Un appareil absent du compte (demo, ancien module) ne doit pas
            # rester dans l'interface : on le retire apres 3 lectures
            # consecutives sans le voir.
            if fresh:
                self._track_missing("atlantic", set(fresh))
            cloud_ok = True
        except AtlanticError as err:
            errors.append(str(err))

        with self._lock:
            self._cloud_ok = cloud_ok

        # 2. Lecture Hub Schneider Wiser Home
        if self.wiser_client:
            try:
                rooms = self.wiser_client.get_rooms()
                rooms_dict = {}
                for r in rooms:
                    room_id = int(r["id"])
                    rooms_dict[room_id] = r
                    dev_id = 9000 + room_id

                    stored = self.store.device(dev_id)
                    if stored is None:
                        self.store.upsert_device(dev_id, f"{r['name']} (Wiser)")
                        self.store.update_device(dev_id, {
                            "device_type": "wiser",
                            "wiser_room_id": room_id,
                        })

                with self._lock:
                    self.wiser_rooms = rooms_dict

                # Hub reel : les pieces disparues du Hub sont retirees.
                if not self.wiser_mock and rooms_dict:
                    self._track_missing(
                        "wiser", {9000 + room_id for room_id in rooms_dict}
                    )

                # Association automatique de la température du salon si présent
                # Si un thermostat Wiser s'appelle 'Salon', on l'associe comme capteur de référence
                for d in self.store.devices():
                    if d.get("device_type") == "atlantic" and not d.get("temp_sensor_device_id"):
                        for r in rooms:
                            if "salon" in r.get("name", "").lower():
                                self.store.update_device(d["device_id"], {
                                    "temp_sensor_device_id": 9000 + r["id"]
                                })
                                break

            except WiserError as err:
                errors.append(f"Wiser : {err}")

        self.last_poll = datetime.now()
        # Une lecture reussie efface les erreurs precedentes : sinon l'ancien
        # message d'authentification resterait affiche indefiniment.
        self.last_error = " ; ".join(errors) if errors else None

        # Historique pour l'onglet Statistiques : une mesure par cycle.
        try:
            self._record_samples(self.last_poll)
        except Exception:  # noqa: BLE001 - ne jamais bloquer le pilotage
            pass

        # Le journal et l'historique ne grossissent pas indefiniment.
        try:
            self.store.prune_events(800)
        except Exception:  # noqa: BLE001
            pass

        if time.time() >= self._next_prune:
            try:
                self.store.prune_samples(180)
            except Exception:  # noqa: BLE001
                pass
            self._next_prune = time.time() + 3600

    def _record_samples(self, moment: datetime | None = None) -> None:
        """Enregistre température mesurée et consigne attendue de chaque zone."""
        moment = moment or datetime.now()
        for device in self.store.devices():
            try:
                current = self.current(device["device_id"])
            except Exception:  # noqa: BLE001
                current = {}

            # Sur une clim Atlantic, on privilégie la sonde Wiser associée
            # (temp_sensor_device_id) quand elle existe : c'est la température
            # réellement représentative de la pièce.
            ambient = current.get("wiser_ambient")
            if ambient is None:
                ambient = current.get("ambient")

            desired, _, _ = self.desired_for(device, moment)
            if desired:
                setpoint = desired.get("temp")
                mode = desired.get("mode")
            else:
                setpoint = current.get("temp")
                mode = current.get("mode")

            self.store.add_sample(
                int(device["device_id"]), ambient, setpoint, mode, moment
            )

    def _track_missing(self, device_type: str, seen: set[int]) -> None:
        """Retire les appareils absents depuis plusieurs lectures d'affilee."""
        for device in self.store.devices():
            if device.get("device_type", "atlantic") != device_type:
                continue
            device_id = int(device["device_id"])
            if device_id in seen:
                self._missing.pop(device_id, None)
                continue

            count = self._missing.get(device_id, 0) + 1
            self._missing[device_id] = count
            # Seuil volontairement haut : evite de supprimer un appareil reel
            # lors d'une panne cloud passagere (3 lectures = 3 min etait trop court).
            if count >= 10:
                self._missing.pop(device_id, None)
                name = device.get("name", device_id)
                self.store.delete_device(device_id)
                self.store.log("info", f"Appareil retire : {name}", device_id)

    def setpoint_cap(self, device: dict, mode: str | None) -> int:
        if mode in ("3", "8") and device.get("cap_setpoint_cool"):
            return int(device["cap_setpoint_cool"])
        return int(device["cap_setpoint"])

    def current(self, device_id: int) -> dict:
        device = self.store.device(device_id)
        if not device:
            return {}

        dev_type = device.get("device_type", "atlantic")

        # PIÈCE / RADIATEUR SCHNEIDER WISER
        if dev_type == "wiser":
            room_id = device.get("wiser_room_id") or (device_id - 9000)
            with self._lock:
                room_data = self.wiser_rooms.get(room_id, {})

            temp = room_data.get("setpoint")
            ambient = room_data.get("temp")
            return {
                "device_type": "wiser",
                "mode": "heat" if (temp or 0) > 6 else "off",
                "mode_label": "Chauffage Wiser" if (temp or 0) > 6 else "Hors gel Wiser",
                "mode_raw": "4",
                "temp": temp,
                "ambient": ambient,
                "humidity": room_data.get("humidity"),
                "has_room_stat": room_data.get("has_room_stat", False),
                "available": bool(self.wiser_rooms),
            }

        # CLIMATISATION ATLANTIC COZYTOUCH
        with self._lock:
            caps = self.state.get(device_id, {})
            available = self.availability.get(device_id, True) if self.state else False

        mode = _mode_value(caps.get(device["cap_mode"]))
        mode_name = next(
            (name for name, value in MODE_VALUES.items() if value == mode), None
        )
        prog_cap = device.get("cap_prog") or 0
        ambient_split = _as_float(caps.get(device["cap_ambient"]))

        # Soufflerie et orientation du flux d'air (si l'appareil les expose).
        fan_cap = int(device.get("cap_fan") or 0)
        louver_cap = int(device.get("cap_louver") or 0)
        swing_cap = int(device.get("cap_swing") or 0)
        fan_raw = _mode_value(caps.get(fan_cap)) if fan_cap else ""
        fan_name = next(
            (name for name, value in FAN_VALUES.items() if value == fan_raw), None
        )
        swing_raw = _mode_value(caps.get(swing_cap)) if swing_cap else ""
        louver_raw = _mode_value(caps.get(louver_cap)) if louver_cap else ""
        if swing_raw == "1":
            airflow, airflow_label = "swing", "Balayage"
        elif louver_raw in AIRFLOW_POSITIONS.values():
            airflow = f"pos{louver_raw}"
            airflow_label = f"Position {louver_raw}"
        else:
            airflow, airflow_label = None, None

        # Vérification si un thermostat d'ambiance Wiser est associé pour la température
        wiser_ambient = None
        wiser_humidity = None
        wiser_sensor_name = None
        sensor_dev_id = device.get("temp_sensor_device_id")
        if sensor_dev_id:
            sensor_dev = self.store.device(sensor_dev_id)
            if sensor_dev:
                wiser_room_id = sensor_dev.get("wiser_room_id") or (sensor_dev_id - 9000)
                with self._lock:
                    w_room = self.wiser_rooms.get(wiser_room_id, {})
                wiser_ambient = w_room.get("temp")
                wiser_humidity = w_room.get("humidity")
                wiser_sensor_name = sensor_dev.get("name")

        return {
            "device_type": "atlantic",
            "mode": mode_name,
            "mode_label": MODE_LABELS.get(mode_name, mode_name) if mode_name else None,
            "mode_raw": mode,
            "temp": _as_float(caps.get(self.setpoint_cap(device, mode))),
            "ambient": ambient_split,
            "wiser_ambient": wiser_ambient,
            "wiser_humidity": wiser_humidity,
            "wiser_sensor_name": wiser_sensor_name,
            "prog": _mode_value(caps.get(prog_cap)) if prog_cap else None,
            "fan": fan_name,
            "fan_label": FAN_LABELS.get(fan_name) if fan_name else None,
            "airflow": airflow,
            "airflow_label": airflow_label,
            "available": available,
        }

    # ------------------------------------------------------------------ decision

    def desired_for(self, device: dict, now: datetime | None = None):
        now = now or datetime.now()
        settings = self.store.settings()

        if absence_active(settings, now):
            return self._absent_desired(device), "absence", None

        override = self.store.override(device["device_id"])
        if override:
            try:
                until = datetime.fromisoformat(override["until"])
            except ValueError:
                until = None
            if until is None or until > now:
                mode = override["mode"]
                return (
                    {
                        "mode": mode,
                        "mode_label": self.effective_label(device, mode),
                        "temp": self.effective_temp(device, mode, override["temp"]),
                    },
                    "override",
                    until,
                )
            self.store.clear_override(device["device_id"])

        week = week_type(now, settings, self.store.week_overrides())
        slots = []
        control_room = None
        # Check if this device is an actuator for any room
        for room in self.store.rooms():
            if room.get("actuator_device_id") == device["device_id"] or room.get("id") and device.get("room_id") == room.get("id") and room.get("actuator_device_id") == device["device_id"]:
                slots = self.store.slots(room_id=room["id"], week=week)
                control_room = room
                break
        if not slots:
            # Also check if room has actuator pointing to this device
            for room in self.store.rooms():
                if room.get("actuator_device_id") == device["device_id"]:
                    slots = self.store.slots(room_id=room["id"], week=week)
                    control_room = room
                    break
        if not slots:
            # Appareil non actionnaire (ex. climatisation) qui prend en charge
            # le mode d'une pièce que son actionnaire ne sait pas appliquer.
            slots = self._delegated_slots(device, week, now)
        if not slots:
            slots = self.store.slots(device["device_id"], week)
        slot = find_slot(slots, now)
        if not slot:
            return None, "vide", None

        mode = slot["mode"]
        if not self.mode_supported(device, mode):
            # Un autre appareil de la pièce (climatisation) sait peut-être
            # appliquer ce mode : on lui laisse la main.
            if self._delegates_to(device, mode):
                return None, "délégué", None
            self.store.log(
                "avertissement",
                f"Mode {MODE_LABELS.get(mode, mode)} non pris en charge par "
                f"{device['name']} : créneau ignoré.",
                device["device_id"],
            )
            return None, "mode non pris en charge", None

        desired = {
            "mode": mode,
            "mode_label": self.effective_label(device, mode),
            "temp": self.effective_temp(device, mode, slot["temp"]),
        }

        # Préchauffage : avance sur la prochaine montée de consigne.
        # La courbe de chauffe fournit le modèle appris ; le mode « préchauffage
        # intelligent » utilise le même calcul avec un modèle par défaut.
        if control_room and (
            control_room.get("heating_curve") or control_room.get("preheat")
        ):
            advanced = self._advance_desired(device, control_room, desired, slots, now)
            if advanced:
                return advanced, f"planning {week} (avance)", None

        return desired, f"planning {week}", None

    def _absent_desired(self, device: dict) -> dict:
        """Consigne d'absence : hors gel paramétrable, sans passer par le planning."""
        dev_type = device.get("device_type", "atlantic")
        if dev_type == "atlantic":
            frost_temp = device.get("frost_temp") or 16
            frost_mode = device.get("frost_mode") or "heat"
            return {
                "mode": frost_mode,
                "mode_label": MODE_LABELS.get(frost_mode, frost_mode),
                "temp": self.effective_temp(device, frost_mode, frost_temp),
            }
        # Wiser (et types non-atlantic) : ne pas utiliser effective_temp, qui
        # transformerait un mode off en WISER_FROST_TEMP (6 °C) et écraserait
        # la température hors gel configurée.
        return {
            "mode": "off",
            "mode_label": "Hors gel",
            "temp": float(device.get("frost_temp") or 10),
        }

    # ----------------------------------------------------------------- courbe

    def _curve_control_room(self, device: dict) -> dict | None:
        """Pièce contrôlée par cet appareil (actionneur), pour la courbe de chauffe."""
        try:
            device_id = int(device.get("device_id", 0))
        except (TypeError, ValueError):
            return None
        for room in self.store.rooms():
            if int(room.get("actuator_device_id") or 0) == device_id:
                return room
        return None

    def _current_outside_temp(self) -> float | None:
        """Température extérieure actuelle (meilleur effort, sans casser le moteur)."""
        config = self._config()
        if not config or not config.get("weather_enabled", True):
            return None
        try:
            latitude = float(config.get("weather_latitude"))
            longitude = float(config.get("weather_longitude"))
        except (TypeError, ValueError):
            return None
        # Préfère la météo horaire déjà en cache (accès réutilisé), sinon rien.
        if self.weather is not None:
            try:
                now = datetime.now()
                result = self.weather.hourly(latitude, longitude, now.date(), now.date())
                hours = (result.hours or {}).get(now.date().isoformat(), {})
                temp = hours.get(now.hour)
                if temp is None:
                    past = {h: v for h, v in hours.items() if h <= now.hour}
                    if past:
                        temp = past[max(past)]
                if temp is not None:
                    return float(temp)
            except Exception:  # noqa: BLE001
                pass
        # Repli léger : cache local main._current_weather si importable.
        try:
            from .main import _current_weather

            payload = _current_weather(latitude, longitude)
            if payload and payload.get("temp") is not None:
                return float(payload["temp"])
        except Exception:  # noqa: BLE001
            pass
        return None

    def _room_current_temp(self, room: dict, device: dict | None = None) -> float | None:
        """Température actuelle de la pièce (ambiance mesurée)."""
        candidates = []
        if device is not None:
            try:
                candidates.append(int(device["device_id"]))
            except (TypeError, ValueError):
                pass
        for key in ("actuator_device_id", "thermostat_device_id"):
            value = room.get(key)
            if value in (None, "", 0, "0"):
                continue
            try:
                candidates.append(int(value))
            except (TypeError, ValueError):
                continue
        # Évite les doublons en conservant l'ordre.
        seen = set()
        ordered = []
        for item in candidates:
            if item in seen:
                continue
            seen.add(item)
            ordered.append(item)
        for device_id in ordered:
            current = self.current(device_id)
            ambient = current.get("wiser_ambient")
            if ambient is None:
                ambient = current.get("ambient")
            if ambient is not None:
                try:
                    return float(ambient)
                except (TypeError, ValueError):
                    continue
        return None

    def _curve_params(self, room: dict) -> tuple[float, float, bool]:
        """(tau0, tau1, learning) pour la pièce."""
        fit = room.get("curve_fit") if isinstance(room.get("curve_fit"), dict) else None
        if fit and isinstance(fit.get("tau0"), (int, float)):
            tau0 = float(fit.get("tau0", 3.0))
            tau1 = float(fit.get("tau1", 0.0)) if fit.get("tau1") is not None else 0.0
            try:
                n = int(fit.get("n") or 0)
            except (TypeError, ValueError):
                n = 0
            return tau0, tau1, n < 3
        # Défaut sans échantillons suffisants.
        return 3.0, 0.0, True

    # Scanner de montée : au plus 10 changements successifs, dans 8 jours.
    RISE_SCAN_CHANGES = 10
    RISE_SCAN_HORIZON = timedelta(days=8)

    def _next_rise(self, device: dict, current_desired: dict, now: datetime):
        """Prochaine VRAIE montée de consigne dans l'horizon du scanner.

        Le prochain changement n'est pas forcément une montée : minuit peut
        reconduire exactement la consigne courante. On scanne alors les
        changements suivants (au plus 10, dans 8 jours) jusqu'à une consigne
        strictement supérieure à la consigne courante. ``in_minutes`` reste
        mesuré depuis ``now`` (et non depuis le curseur du scan).
        """
        try:
            cur_temp_f = float(current_desired.get("temp"))
        except (TypeError, ValueError):
            return None

        horizon = now + self.RISE_SCAN_HORIZON
        cursor = now
        for _ in range(self.RISE_SCAN_CHANGES):
            upcoming = self.next_change(device, cursor)
            if not upcoming:
                return None
            slot = upcoming.slot
            at = slot.get("at")
            if at is None or at > horizon:
                return None
            mode = slot.get("mode")
            try:
                next_temp_f = float(
                    self.effective_temp(device, mode, slot.get("temp"))
                )
            except (TypeError, ValueError):
                return None
            if next_temp_f > cur_temp_f + 0.05:
                return {
                    "upcoming": upcoming,
                    "temp": next_temp_f,
                    "mode": mode,
                    "in_minutes": (at - now).total_seconds() / 60.0,
                }
            # Changement sans montée (même consigne ou descente) : on continue
            # juste après cet instant.
            cursor = at + timedelta(minutes=1)
        return None

    def _outside_temp_at(self, moment: datetime) -> float | None:
        """Température extérieure prévue à ``moment`` (meilleur effort).

        Pour aujourd'hui, la météo horaire est utilisée. Pour les jours
        suivants, on interpole la prévision journalière (min vers 5 h, max
        vers 15 h) afin d'anticiper un épisode froid. Sans donnée, repli sur
        la température extérieure actuelle.
        """
        config = self._config()
        if not config or not config.get("weather_enabled", True):
            return None
        try:
            latitude = float(config.get("weather_latitude"))
            longitude = float(config.get("weather_longitude"))
        except (TypeError, ValueError):
            return None
        if self.weather is None:
            return self._current_outside_temp()

        today = datetime.now().date()
        try:
            if moment.date() <= today:
                result = self.weather.hourly(latitude, longitude, today, today)
                hours = (result.hours or {}).get(today.isoformat(), {})
                temp = hours.get(moment.hour)
                if temp is None:
                    past = {h: v for h, v in hours.items() if h <= moment.hour}
                    if past:
                        temp = past[max(past)]
                if temp is not None:
                    return float(temp)
        except Exception:  # noqa: BLE001 - météo facultative
            pass

        # Prévision journalière : interpolation simple entre le minimum
        # nocturne et le maximum de l'après-midi.
        try:
            days, _source = self.weather.daily(latitude, longitude, 8)
            forecast = next(
                (item for item in days if item.get("date") == moment.date().isoformat()),
                None,
            )
            if forecast:
                tmin = float(forecast["tmin"])
                tmax = float(forecast["tmax"])
                hour = moment.hour + moment.minute / 60.0
                if 5 <= hour <= 15:
                    ratio = (hour - 5) / 10.0
                elif hour < 5:
                    ratio = 0.0
                else:
                    ratio = max(0.0, 1.0 - (hour - 15) / 14.0)
                # Courbe douce plutôt qu'une rampe linéaire.
                ratio = (1 - math.cos(ratio * math.pi)) / 2
                return round(tmin + (tmax - tmin) * ratio, 1)
        except Exception:  # noqa: BLE001 - météo facultative
            pass
        return self._current_outside_temp()

    def _advance_desired(self, device: dict, room: dict, current_desired: dict, slots, now: datetime):
        """Retourne l'état désiré avancé si une montée imminente le justifie."""
        rise = self._next_rise(device, current_desired, now)
        if not rise or rise["in_minutes"] is None:
            return None

        t_room = self._room_current_temp(room, device)
        if t_room is None:
            return None

        # Température extérieure au moment de la montée (et non maintenant) :
        # un redoux ou un coup de froid prévu change l'avance nécessaire.
        upcoming = rise.get("upcoming")
        rise_at = upcoming.slot.get("at") if upcoming is not None else None
        if rise_at is None:
            rise_at = now + timedelta(minutes=float(rise["in_minutes"]))
        t_out = self._outside_temp_at(rise_at)

        try:
            max_advance = int(self.store.settings().get("preheat_max_minutes", 120))
        except (TypeError, ValueError):
            max_advance = 120
        max_advance = max(0, min(360, max_advance))

        tau0, tau1, _learning = self._curve_params(room)
        delta = heating_curve.advance_minutes(
            tau0, tau1, t_out, t_room, rise["temp"], cap_minutes=max_advance
        )
        if rise["in_minutes"] <= delta and delta > 0:
            mode = rise["mode"]
            return {
                "mode": mode,
                "mode_label": self.effective_label(device, mode),
                "temp": self.effective_temp(device, mode, rise["temp"]),
            }
        return None

    def _warm_weather_cache(self) -> None:
        """Préchauffe le cache météo de la fenêtre du fit (T_ext des montées).

        Réutilise l'appel multi-jours des statistiques
        (``weather.hourly(lat, lon, start, end)``), par chunks mensuels : la
        rafale d'appels quotidiens déclenchait le disjoncteur météo et forçait
        le repli à 10 °C tous les jours. Un chunk en échec n'interrompt pas les
        suivants ni le fit ; le repli existant reste le secours.
        """
        weather = self.weather
        if weather is None:
            return
        config = self._config()
        if not config or not config.get("weather_enabled", True):
            return
        try:
            latitude = float(config.get("weather_latitude"))
            longitude = float(config.get("weather_longitude"))
        except (TypeError, ValueError):
            return

        today = datetime.now().date()
        start = today - timedelta(days=CURVE_FIT_DAYS)
        chunks = []
        cursor = start
        while cursor <= today:
            chunk_end = min(cursor + timedelta(days=WEATHER_WARM_CHUNK_DAYS - 1), today)
            chunks.append((cursor, chunk_end))
            cursor = chunk_end + timedelta(days=1)
        # Du plus récent au plus ancien : les montées proches de now d'abord.
        chunks.reverse()

        for index, (chunk_start, chunk_end) in enumerate(chunks):
            try:
                weather.hourly(latitude, longitude, chunk_start, chunk_end)
            except Exception:  # noqa: BLE001 - un chunk en échec ne bloque rien
                continue
            if index < len(chunks) - 1:
                # Limite de débit Open-Meteo : petite pause entre les chunks.
                time.sleep(WEATHER_WARM_PAUSE_SECONDS)

    def refresh_room_curve_fit(self, room_id: int, force: bool = False) -> None:
        """Recalcule et persiste le fit de courbe de chauffe pour une pièce."""
        try:
            room_id = int(room_id)
        except (TypeError, ValueError):
            return
        room = self.store.room(room_id)
        if not room or not room.get("heating_curve"):
            return
        if not force:
            with self._lock:
                next_at = self._next_curve_fit.get(room_id, 0.0)
            if time.time() < next_at:
                return

        # La dépendance à la température extérieure vient du cache météo :
        # on le réchauffe avant d'extraire les transitions.
        self._warm_weather_cache()

        counters: dict = {}
        fit_counters: dict = {}
        transitions = heating_curve.room_transitions(
            self.store,
            room_id,
            weather=self.weather,
            config=self._config(),
            days=CURVE_FIT_DAYS,
            counters=counters,
        )
        fit = heating_curve.fit_tau(transitions, counters=fit_counters)
        source = "meteo"
        if fit is None and counters.get("no_t_out", 0) > 0:
            # Météo absente : on retente avec une référence fixe, sans toucher
            # aux transitions qui avaient une température extérieure valide.
            fallback_counters: dict = {}
            fit_counters = {}
            transitions = heating_curve.room_transitions(
                self.store,
                room_id,
                weather=self.weather,
                config=self._config(),
                counters=fallback_counters,
                t_out_fallback=10.0,
            )
            fit = heating_curve.fit_tau(transitions, counters=fit_counters)
            counters = fallback_counters
            source = "fallback_10c"
        if fit is None:
            fit = {"tau0": 3.0, "tau1": 0.0, "n": 0, "r2": 0.0}
        fit["fit_at"] = datetime.now().isoformat(timespec="seconds")
        fit["debug"] = {
            **counters,
            "source": source,
            "fit": dict(fit_counters),
            "transitions": [
                {
                    "t_out": round(float(item["t_out"]), 2),
                    "t_room": round(float(item["t_room"]), 2),
                    "target": round(float(item["target"]), 2),
                    "lag_minutes": round(float(item["lag_minutes"]), 2),
                }
                for item in (transitions or [])[:20]
            ],
        }
        if source == "fallback_10c":
            fit["t_out_fallback"] = True
        try:
            self.store.update_room(room_id, {"curve_fit": fit})
        except Exception:  # noqa: BLE001
            pass
        with self._lock:
            self._next_curve_fit[room_id] = time.time() + 24 * 3600
            self._curve_fit_dirty.discard(room_id)

    def mark_room_curve_fit_dirty(self, room_id: int) -> None:
        """Demande un recalcul immédiat du fit (ex. activation du toggle)."""
        try:
            room_id = int(room_id)
        except (TypeError, ValueError):
            return
        with self._lock:
            self._curve_fit_dirty.add(room_id)

    def _tick_curve_fits(self) -> None:
        """Recalcule les fits dus (quotidien) ou demandés."""
        now_ts = time.time()
        for room in self.store.rooms():
            if not room.get("heating_curve"):
                continue
            try:
                room_id = int(room["id"])
            except (TypeError, ValueError):
                continue
            with self._lock:
                dirty = room_id in self._curve_fit_dirty
                due = now_ts >= self._next_curve_fit.get(room_id, 0.0)
            if dirty or due:
                self.refresh_room_curve_fit(room_id, force=dirty or due)

    # Modes réellement applicables selon le type d'appareil.
    ATLANTIC_MODES = ("off", "cool", "heat", "dry", "fan", "auto")
    WISER_MODES = ("off", "heat")

    @classmethod
    def mode_supported(cls, device: dict, mode: str) -> bool:
        """Vrai si l'appareil sait appliquer ce mode.

        - Wiser : « Arrêt » (hors gel) et « Chauffage » uniquement.
        - Atlantic : tous les modes, à condition d'exposer la consigne de froid
          pour « Froid » et « Déshumidification ».
        """
        if not device or not mode:
            return False
        if device.get("device_type", "atlantic") == "wiser":
            return mode in cls.WISER_MODES
        if mode in ("cool", "dry"):
            return bool(int(device.get("cap_setpoint_cool") or 0))
        return mode in cls.ATLANTIC_MODES

    def _room_of(self, device: dict):
        """Pièce à laquelle l'appareil est rattaché (ou ``None``)."""
        room_id = device.get("room_id")
        if room_id in (None, "", 0, "0"):
            return None
        for room in self.store.rooms():
            if int(room.get("id", 0)) == int(room_id):
                return room
        return None

    def _delegates_to(self, device: dict, mode: str) -> bool:
        """Vrai si un autre appareil de la pièce sait appliquer ce mode."""
        room = self._room_of(device)
        if not room:
            return False
        return any(
            int(other.get("device_id", 0)) != int(device.get("device_id", 0))
            and int(other.get("room_id") or 0) == int(room.get("id", 0))
            and self.mode_supported(other, mode)
            for other in self.store.devices()
        )

    def _delegated_slots(self, device: dict, week: str, now: datetime):
        """Créneaux d'une pièce que cet appareil applique à la place de
        l'actionnaire (cas d'une climatisation Atlantic dans une pièce dont
        le radiateur Wiser ne sait que chauffer)."""
        room = self._room_of(device)
        if not room:
            return []
        actuator_id = room.get("actuator_device_id")
        if actuator_id in (None, "", 0, "0"):
            return []
        actuator = self.store.device(int(actuator_id))
        if not actuator or int(actuator.get("device_id", 0)) == int(
            device.get("device_id", 0)
        ):
            return []
        slots = self.store.slots(room_id=room["id"], week=week)
        if not slots:
            return []
        slot = find_slot(slots, now)
        if not slot or self.mode_supported(actuator, slot["mode"]):
            return []
        if not self.mode_supported(device, slot["mode"]):
            return []
        return slots

    @staticmethod
    def effective_temp(device: dict, mode: str, temp) -> float:
        """Consigne réellement envoyée à l'appareil.

        Wiser ne connaît pas le mode Arrêt : on applique une vraie consigne de
        protection hors gel au lieu de la température du créneau.
        """
        if device.get("device_type", "atlantic") == "wiser" and mode == "off":
            return WISER_FROST_TEMP
        return float(temp)

    @staticmethod
    def effective_label(device: dict, mode: str) -> str:
        if device.get("device_type", "atlantic") == "wiser" and mode == "off":
            return "Hors gel"
        return MODE_LABELS.get(mode, mode)

    @staticmethod
    def _next_valid_slot(
        slots: list[dict],
        week: str,
        cursor: datetime,
        settings: dict,
        overrides: dict,
    ):
        """Prochain créneau qui appartient réellement à `week`.

        ``next_slot`` boucle sur 7 jours : au passage de semaine il peut donc
        renvoyer un créneau de la semaine suivante (le dimanche soir de la
        grille B reste dans la semaine A). On écarte ces candidats.
        """
        for _ in range(7):
            candidate = next_slot(slots, cursor)
            if not candidate:
                return None
            at = candidate.get("at")
            if not at or week_type(at, settings, overrides) == week:
                return candidate
            cursor = at + timedelta(minutes=1)
        return None

    def next_change(self, device: dict, now: datetime | None = None):
        now = now or datetime.now()
        settings = self.store.settings()
        week = week_type(now, settings, self.store.week_overrides())
        slots = []
        for room in self.store.rooms():
            if room.get("actuator_device_id") == device["device_id"]:
                slots = self.store.slots(room_id=room["id"], week=week)
                break
        if not slots:
            slots = self.store.slots(device["device_id"], week)
        upcoming = next_slot(slots, now)
        wiser = device.get("device_type", "atlantic") == "wiser"

        if not upcoming:
            other_week = "B" if week == "A" else "A"
            slots_other = []
            for room in self.store.rooms():
                if room.get("actuator_device_id") == device["device_id"]:
                    slots_other = self.store.slots(room_id=room["id"], week=other_week)
                    break
            if not slots_other:
                slots_other = self.store.slots(device["device_id"], other_week)
            upcoming = self._next_valid_slot(
                slots_other,
                other_week,
                now,
                settings,
                self.store.week_overrides(),
            )
            if not upcoming:
                return None
            return Upcoming(upcoming, other_week, wiser)

        target_at = upcoming.get("at")
        if target_at:
            target_week = week_type(target_at, settings, self.store.week_overrides())
            if target_week != week:
                slots_target = []
                for room in self.store.rooms():
                    if room.get("actuator_device_id") == device["device_id"]:
                        slots_target = self.store.slots(room_id=room["id"], week=target_week)
                        break
                if not slots_target:
                    slots_target = self.store.slots(device["device_id"], target_week)
                target_upcoming = self._next_valid_slot(
                    slots_target,
                    target_week,
                    now,
                    settings,
                    self.store.week_overrides(),
                )
                if target_upcoming:
                    return Upcoming(target_upcoming, target_week, wiser)

        return Upcoming(upcoming, week, wiser)

    def tick(self, force: bool = False) -> None:
        # Courbe de chauffe : recalcul des fits (quotidien / à l'activation).
        self._tick_curve_fits()

        if not self.store.flag("engine_enabled"):
            return

        for device in self.store.devices():
            if not device["enabled"]:
                continue

            if device.get("device_type", "atlantic") == "atlantic":
                with self._lock:
                    cloud_ok = self._cloud_ok
                    known = int(device["device_id"]) in self.state
                if not cloud_ok or not known:
                    # Cloud injoignable ou appareil absent de la derniere
                    # lecture (anciens appareils de demonstration) : on
                    # n'envoie rien, sinon Atlantic repond 403 en boucle.
                    continue

            desired, source, _ = self.desired_for(device)
            if desired is None:
                continue

            self._apply(device, desired, force=force)

    def _apply(self, device: dict, desired: dict, force: bool = False) -> bool:
        device_id = device["device_id"]
        dev_type = device.get("device_type", "atlantic")

        # APPLICATION SCHNEIDER WISER HOME
        if dev_type == "wiser":
            if not self.wiser_client:
                return False
            room_id = device.get("wiser_room_id") or (device_id - 9000)
            want_temp = float(desired["temp"])
            with self._lock:
                current_room = self.wiser_rooms.get(room_id, {})
            current_temp = current_room.get("setpoint")

            # Si la consigne est déjà celle voulue, on ne renvoie rien.
            # Sauf en mode forcé (action utilisateur/override) : on réapplique
            # pour rattraper un hub désynchronisé.
            if not force and current_temp is not None and abs(current_temp - want_temp) < 0.2:
                return False

            if not force:
                elapsed = time.time() - self._last_attempt.get(device_id, 0.0)
                if elapsed < ANTI_FLAP_SECONDS:
                    return False
            self._last_attempt[device_id] = time.time()

            try:
                self.wiser_client.set_room_temperature(room_id, want_temp)
                with self._lock:
                    if room_id in self.wiser_rooms:
                        self.wiser_rooms[room_id]["setpoint"] = want_temp
                self.last_action = f"{datetime.now():%H:%M:%S} Wiser {device['name']} -> {want_temp:g} °C"
                self.store.log("action", f"Appliqué Wiser : {device['name']} -> {want_temp:g} °C", device_id)
                return True
            except WiserError as err:
                self.last_error = f"Wiser : {err}"
                self.store.log("erreur", f"Wiser {device['name']} : {err}", device_id)
                return False

        # APPLICATION CLIMATISATION ATLANTIC COZYTOUCH
        current = self.current(device_id)
        settings = self.store.settings()
        want_mode = MODE_VALUES.get(desired["mode"], "0")
        want_temp = float(desired["temp"])
        temp_cap = self.setpoint_cap(device, want_mode)
        prog_cap = int(device.get("cap_prog") or 0)

        # Valeurs par defaut choisies dans les reglages (vides = ne pas toucher).
        fan_choice = str(settings.get("atlantic_fan_default") or "").strip()
        airflow_choice = str(settings.get("atlantic_airflow_default") or "").strip()
        fan_cap = int(device.get("cap_fan") or 0)
        louver_cap = int(device.get("cap_louver") or 0)
        swing_cap = int(device.get("cap_swing") or 0)
        fan_target = FAN_VALUES.get(fan_choice)
        airflow_swing = airflow_choice == "swing"
        airflow_pos = AIRFLOW_POSITIONS.get(airflow_choice)
        # Inutile de regler la soufflerie ou les volets quand la clim est arretee.
        ac_active = want_mode != "0"

        with self._lock:
            live = dict(self.state.get(device_id, {}))

        mode_ok = current.get("mode_raw") == want_mode
        temp_now = _as_float(live.get(temp_cap))
        temp_ok = temp_now is not None and abs(temp_now - want_temp) < 0.05
        prog_ok = not prog_cap or _mode_value(live.get(prog_cap)) != "1"

        fan_ok = not (ac_active and fan_cap and fan_target) or (
            _mode_value(live.get(fan_cap)) == fan_target
        )
        swing_ok = True
        louver_ok = True
        if ac_active:
            if airflow_swing and swing_cap:
                swing_ok = _mode_value(live.get(swing_cap)) == "1"
            elif airflow_pos and louver_cap:
                # Une position fixe suppose le balayage desactive.
                swing_ok = not swing_cap or _mode_value(live.get(swing_cap)) == "0"
                louver_ok = _mode_value(live.get(louver_cap)) == airflow_pos
        airflow_ok = swing_ok and louver_ok

        temp_relevant = want_mode not in ("0", "7")
        if mode_ok and prog_ok and fan_ok and airflow_ok and (
            temp_ok or not temp_relevant
        ):
            return False

        if not force:
            elapsed = time.time() - self._last_attempt.get(device_id, 0.0)
            if elapsed < ANTI_FLAP_SECONDS:
                return False

        self._last_attempt[device_id] = time.time()

        warnings: list[str] = []

        def write(cap_id: int, value: str, label: str) -> None:
            """Ecrit une capacite en gardant le contexte dans le message d'erreur."""
            try:
                self.client.write_capability(device_id, int(cap_id), str(value))
            except ExecutionTimeout as err:
                # Atlantic a accepte l'ordre (HTTP 201) mais la confirmation
                # n'est pas arrivee dans la fenetre de suivi : la passerelle
                # applique souvent la consigne quelques dizaines de secondes
                # plus tard. On ne bloque pas le reste de la sequence et on ne
                # reecrit pas tout de suite (anti-flap).
                warnings.append(f"{label} non confirmée à temps")
                self.store.log(
                    "avertissement",
                    f"{label} (capacite {cap_id} = {value}) : {err}",
                    device_id,
                )
            except AtlanticError as err:
                raise AtlanticError(
                    f"{label} (capacite {cap_id} = {value}) : {err}"
                ) from err

        try:
            changed = False

            if not prog_ok:
                write(prog_cap, "0", "programme natif")
                with self._lock:
                    self.state.setdefault(device_id, {})[prog_cap] = "0"
                changed = True

            if temp_relevant and not temp_ok:
                write(temp_cap, f"{want_temp:g}", "consigne")
                with self._lock:
                    self.state.setdefault(device_id, {})[temp_cap] = f"{want_temp:g}"
                changed = True

            if not mode_ok:
                write(device["cap_mode"], want_mode, "mode")
                with self._lock:
                    self.state.setdefault(device_id, {})[device["cap_mode"]] = want_mode
                changed = True

            # Apres le mode : certaines telecommandes reinitialisent la
            # soufflerie et les volets quand le mode change.
            if ac_active and fan_cap and fan_target and not fan_ok:
                write(fan_cap, fan_target, "soufflerie")
                with self._lock:
                    self.state.setdefault(device_id, {})[fan_cap] = fan_target
                changed = True

            if ac_active and not airflow_ok:
                if airflow_swing and swing_cap:
                    write(swing_cap, "1", "orientation (balayage)")
                    with self._lock:
                        self.state.setdefault(device_id, {})[swing_cap] = "1"
                elif airflow_pos and louver_cap:
                    if swing_cap and not swing_ok:
                        write(swing_cap, "0", "orientation (balayage)")
                        with self._lock:
                            self.state.setdefault(device_id, {})[swing_cap] = "0"
                    if louver_cap and not louver_ok:
                        write(louver_cap, airflow_pos, "orientation")
                        with self._lock:
                            self.state.setdefault(device_id, {})[louver_cap] = airflow_pos
                changed = True

            if not changed:
                return False

            parts = []
            if not prog_ok:
                parts.append("programme natif désactivé")
            if not mode_ok:
                if want_mode == "0":
                    parts.append("Arrêt")
                elif want_mode == "7":
                    parts.append("Ventilation")
                else:
                    label_name = VALUE_LABELS.get(want_mode, desired.get("mode_label", desired["mode"]))
                    parts.append(f"{label_name} {want_temp:g} °C")
            elif temp_relevant and not temp_ok:
                label_name = VALUE_LABELS.get(want_mode, desired.get("mode_label", desired["mode"]))
                parts.append(f"{label_name} {want_temp:g} °C")
            if ac_active and fan_cap and fan_target and not fan_ok:
                parts.append(f"soufflerie {FAN_LABELS[fan_choice].lower()}")
            if ac_active and not airflow_ok:
                if airflow_swing and swing_cap:
                    parts.append("orientation balayage")
                elif airflow_pos and louver_cap:
                    parts.append(f"orientation position {airflow_pos}")

            label = " ; ".join(parts) or "état déjà conforme"
            if warnings:
                label = f"{label} (confirmation non reçue)"
            self.last_action = (
                f"{datetime.now():%H:%M:%S} {device['name']} -> {label}"
            )
            self.store.log("action", f"Appliqué : {label}", device_id)
            self.last_error = None
            return True

        except (AtlanticError, RateLimited) as err:
            self.last_error = str(err)
            self.store.log("erreur", str(err), device_id)
            return False

    # ------------------------------------------------------------------ etat

    def snapshot(self) -> dict:
        now = datetime.now()
        settings = self.store.settings()
        week = week_type(now, settings, self.store.week_overrides())
        presence = presence_status(settings, now)
        presence_absent = bool(presence.get("active"))
        devices = []

        for device in self.store.devices():
            desired, source, until = self.desired_for(device, now)
            # Pendant l'absence, aucune promesse de changement planifié :
            # seule la consigne hors gel est active.
            upcoming = None if presence_absent else self.next_change(device, now)

            # Check if this device is actuator for a room
            room_slots_a = []
            room_slots_b = []
            for room in self.store.rooms():
                if room.get("actuator_device_id") == device["device_id"]:
                    room_slots_a = self.store.slots(room_id=room["id"], week="A")
                    room_slots_b = self.store.slots(room_id=room["id"], week="B")
                    break
            if room_slots_a or room_slots_b:
                programs = {
                    "A": _group(room_slots_a) if room_slots_a else _group(self.store.slots(device["device_id"], "A")),
                    "B": _group(room_slots_b) if room_slots_b else _group(self.store.slots(device["device_id"], "B")),
                }
            else:
                programs = {
                    "A": _group(self.store.slots(device["device_id"], "A")),
                    "B": _group(self.store.slots(device["device_id"], "B")),
                }

            devices.append(
                {
                    **device,
                    "current": self.current(device["device_id"]),
                    "desired": desired,
                    "source": source,
                    "until": until.isoformat(timespec="seconds") if until else None,
                    "next": upcoming.as_dict() if upcoming else None,
                    "programs": programs,
                }
            )

        return {
            "now": now.isoformat(timespec="seconds"),
            "week": week,
            "iso_week": now.isocalendar().week,
            "settings": settings,
            "presence": presence,
            "week_overrides": self.store.week_overrides(),
            "rooms": self.store.rooms(),
            "devices": devices,
            "wiser_status": {
                "enabled": bool(self.wiser_client),
                "connected": bool(self.wiser_rooms),
                "rooms_count": len(self.wiser_rooms),
            },
            "status": {
                "mock": self.mock,
                "last_poll": self.last_poll.isoformat(timespec="seconds")
                if self.last_poll
                else None,
                "last_error": self.last_error,
                "last_action": self.last_action,
                "running": bool(self._thread and self._thread.is_alive()),
            },
            "storage": (
                self.store.storage_info()
                if hasattr(self.store, "storage_info")
                else None
            ),
            "logs": self.store.events(80),
        }

    def seed_demo(self, include_history: bool = True) -> None:
        if not self.mock:
            return

        for device in self.store.devices():
            if any(self.store.slots(device["device_id"], week) for week in ("A", "B")):
                continue

            for week in ("A", "B"):
                days = []
                for weekday in range(7):
                    days.append(
                        [
                            {"time": "07:30", "mode": "heat" if device.get("device_type") == "wiser" else "cool", "temp": 20},
                            {"time": "09:00", "mode": "heat" if device.get("device_type") == "wiser" else "off", "temp": 17},
                            {"time": "18:00", "mode": "heat" if device.get("device_type") == "wiser" else "cool", "temp": 20},
                            {"time": "23:00", "mode": "heat" if device.get("device_type") == "wiser" else "off", "temp": 16},
                        ]
                    )
                self.store.set_slots(device["device_id"], week, days)

        # L'historique fictif n'a de sens que pour une installation neuve en
        # simulation : jamais pour des appareils migrés d'une vraie base.
        if include_history:
            self._seed_demo_samples()

    def _seed_demo_samples(self) -> None:
        """Historique fictif de 31 jours pour que l'onglet Statistiques soit utile."""
        import math
        import random

        now = datetime.now().replace(minute=0, second=0, microsecond=0)
        rng = random.Random(42)
        settings = self.store.settings()
        overrides = self.store.week_overrides()

        for index, device in enumerate(self.store.devices()):
            first, _ = self.store.sample_bounds(int(device["device_id"]))
            start = now - timedelta(days=31)
            end = now

            if first:
                try:
                    first_moment = datetime.fromisoformat(first).replace(
                        minute=0, second=0, microsecond=0
                    )
                except ValueError:
                    first_moment = None
                # Un historique d'au moins 30 jours existe deja : rien a faire.
                if first_moment and first_moment <= start + timedelta(hours=1):
                    continue
                if first_moment and first_moment > start:
                    # On complete uniquement la partie manquante, sans ecraser
                    # les mesures deja enregistrees par le moteur.
                    end = first_moment - timedelta(minutes=1)

            if end < start:
                continue

            is_wiser = device.get("device_type", "atlantic") == "wiser"
            base = (20.4, 21.2, 19.3)[index % 3]
            rows = []
            hours = int((end - start).total_seconds() // 3600)
            for step in range(hours, -1, -1):
                moment = end - timedelta(hours=step)
                week = week_type(moment, settings, overrides)
                slot = find_slot(self.store.slots(device["device_id"], week), moment)

                if slot:
                    mode = slot["mode"]
                    setpoint = self.effective_temp(device, mode, slot["temp"])
                else:
                    mode = "heat" if is_wiser else "cool"
                    setpoint = 20.0

                # Variation douce au fil de la journée + petit bruit de mesure.
                daily = 0.55 * math.sin((moment.hour - 4) / 24 * 2 * math.pi)
                ambient = base + daily + rng.uniform(-0.15, 0.15)

                rows.append({
                    "device_id": device["device_id"],
                    "ts": moment,
                    "ambient": ambient,
                    "setpoint": setpoint,
                    "mode": mode,
                })

            self.store.add_samples(rows)


class Upcoming:
    def __init__(self, slot: dict, week: str, wiser: bool = False):
        self.slot = slot
        self.week = week
        self.wiser = wiser

    def as_dict(self) -> dict:
        mode = self.slot["mode"]
        temp = self.slot["temp"]
        label = MODE_LABELS.get(mode, mode)
        description = describe(self.slot)

        # Sur Wiser, "Arrêt" est appliqué par une consigne hors gel.
        if self.wiser and mode == "off":
            temp = WISER_FROST_TEMP
            label = "Hors gel"
            description = f"Hors gel {WISER_FROST_TEMP:g} °C"

        return {
            "mode": mode,
            "mode_label": label,
            "temp": temp,
            "weekday": self.slot["weekday"],
            "time": f"{int(self.slot['minutes']) // 60:02d}:"
                    f"{int(self.slot['minutes']) % 60:02d}",
            "in_minutes": self.slot.get("in_minutes"),
            "at": self.slot.get("at").isoformat(timespec="minutes")
            if self.slot.get("at")
            else None,
            "week": self.week,
            "describe": description,
        }


def _group(slots: list[dict]) -> list[list[dict]]:
    days = [[] for _ in range(7)]
    for slot in slots:
        days[int(slot["weekday"])].append(
            {
                "time": f"{int(slot['minutes']) // 60:02d}:"
                        f"{int(slot['minutes']) % 60:02d}",
                "mode": slot["mode"],
                "temp": slot["temp"],
            }
        )
    return days

