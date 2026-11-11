"""Moteur : lit le cloud Atlantic et le Hub local Wiser, calcule l'etat voulu, l'applique.

Le planning vit ici, pas dans la clim ni dans le thermostat. On ecrit donc un etat (mode + consigne)
a chaque changement de creneau, et on le reaffirme si besoin.
"""

from __future__ import annotations

import threading
import time
import traceback
from datetime import datetime, timedelta

from .atlantic import AtlanticError, RateLimited
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
MIN_POLL_SECONDS = 30

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
    def __init__(self, store, client, wiser_client=None):
        self.store = store
        self.client = client
        self.wiser_client = wiser_client
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
                        int(_as_float(self.store.settings().get("poll_interval"), 60)),
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
        wiser_sensor_name = None
        sensor_dev_id = device.get("temp_sensor_device_id")
        if sensor_dev_id:
            sensor_dev = self.store.device(sensor_dev_id)
            if sensor_dev:
                wiser_room_id = sensor_dev.get("wiser_room_id") or (sensor_dev_id - 9000)
                with self._lock:
                    w_room = self.wiser_rooms.get(wiser_room_id, {})
                wiser_ambient = w_room.get("temp")
                wiser_sensor_name = sensor_dev.get("name")

        return {
            "device_type": "atlantic",
            "mode": mode_name,
            "mode_label": MODE_LABELS.get(mode_name, mode_name) if mode_name else None,
            "mode_raw": mode,
            "temp": _as_float(caps.get(self.setpoint_cap(device, mode))),
            "ambient": ambient_split,
            "wiser_ambient": wiser_ambient,
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
        # Check if this device is an actuator for any room
        for room in self.store.rooms():
            if room.get("actuator_device_id") == device["device_id"] or room.get("id") and device.get("room_id") == room.get("id") and room.get("actuator_device_id") == device["device_id"]:
                slots = self.store.slots(room_id=room["id"], week=week)
                break
        if not slots:
            # Also check if room has actuator pointing to this device
            for room in self.store.rooms():
                if room.get("actuator_device_id") == device["device_id"]:
                    slots = self.store.slots(room_id=room["id"], week=week)
                    break
        if not slots:
            slots = self.store.slots(device["device_id"], week)
        slot = find_slot(slots, now)
        if not slot:
            return None, "vide", None

        mode = slot["mode"]
        return (
            {
                "mode": mode,
                "mode_label": self.effective_label(device, mode),
                "temp": self.effective_temp(device, mode, slot["temp"]),
            },
            f"planning {week}",
            None,
        )

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
            upcoming = next_slot(slots_other, now)
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
                target_upcoming = next_slot(slots_target, now)
                if target_upcoming:
                    return Upcoming(target_upcoming, target_week, wiser)

        return Upcoming(upcoming, week, wiser)

    def tick(self, force: bool = False) -> None:
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

        def write(cap_id: int, value: str, label: str) -> None:
            """Ecrit une capacite en gardant le contexte dans le message d'erreur."""
            try:
                self.client.write_capability(device_id, int(cap_id), str(value))
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
        devices = []

        for device in self.store.devices():
            desired, source, until = self.desired_for(device, now)
            upcoming = self.next_change(device, now)

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

