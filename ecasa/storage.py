"""Stockage eCasa : tout en mémoire, écritures disque minimales.

Pourquoi ce module ?
--------------------
Le projet d'origine ouvrait SQLite à chaque opération. Sur une carte SD de
Raspberry Pi, cela représente des milliers d'écritures par jour (mesures,
journal, réglages). eCasa renverse le modèle :

* l'état complet vit en RAM (dict Python) ;
* les **réglages, pièces, appareils, plannings et événements** sont écrits dans
  ``data/state.json``, de façon *débouncée* (un changement = au plus une
  écriture quelques secondes plus tard) ;
* les **mesures** sont agrégées par heure puis ajoutées à un journal
  append-only ``data/history/AAAA-MM.jsonl``, au maximum toutes les
  ``history_interval`` secondes (15 min par défaut).

Ordres de grandeur : ~100 écritures/jour au lieu de plusieurs milliers, et
chaque écriture historique ne fait que quelques centaines d'octets.

Le module expose volontairement la même API que l'ancien ``Store`` : le moteur,
les statistiques et le serveur n'ont presque pas besoin d'être modifiés.
"""

from __future__ import annotations

import copy
import json
import os
import threading
import time
import unicodedata
from datetime import datetime, timedelta

SCHEMA_VERSION = 1

DEFAULT_SETTINGS = {
    "week_mode": "parity",   # parity | explicit | none
    "week_flip": "0",        # inverse A et B
    "default_week": "A",     # utilise si week_mode = explicit
    # Jour de bascule A -> B : "monday" (semaine ISO) ou "friday" (garde alternee).
    "week_switch_day": "monday",
    # Minute (apres minuit) du vendredi ou la semaine bascule si
    # week_switch_day == "friday".
    "friday_switch_minutes": "460",
    "poll_interval": "60",   # secondes entre deux lectures du cloud
    "engine_enabled": "1",   # 0 = pause du moteur de planning
    # Valeurs appliquees a la clim Atlantic quand le moteur pilote l'appareil.
    # Vide = ne pas toucher au reglage fait sur la telecommande.
    "atlantic_fan_default": "",       # "" | low | med | high | auto
    "atlantic_airflow_default": "",   # "" | swing | pos1 ... pos6
}

VALID_MODES = ("off", "auto", "cool", "heat", "dry", "fan")

MAX_EVENTS = 800


class MemoryStore:
    """État applicatif en mémoire, persisté par snapshots et journal.

    Paramètres
    ----------
    state_path:
        Fichier JSON de l'état structurel. ``None`` = pas de persistance
        (utile pour les tests).
    history_dir:
        Dossier du journal horaire. ``None`` = pas d'historique.
    history_days:
        Durée de conservation de l'historique (180 jours par défaut).
    history_interval:
        Intervalle minimal entre deux ajouts au journal (900 s par défaut).
    structural_delay:
        Délai de regroupement des écritures de l'état (3 s par défaut).
    """

    def __init__(
        self,
        state_path: str | None,
        history_dir: str | None = None,
        history_days: int = 180,
        history_interval: float = 900.0,
        structural_delay: float = 3.0,
    ):
        self.state_path = os.path.abspath(state_path) if state_path else None
        self.history_dir = os.path.abspath(history_dir) if history_dir else None
        self.history_days = max(7, int(history_days))
        self.history_interval = max(60.0, float(history_interval))
        self.structural_delay = max(0.5, float(structural_delay))

        self._lock = threading.RLock()
        self._closed = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self._settings: dict[str, str] = dict(DEFAULT_SETTINGS)
        self._rooms: list[dict] = []
        self._devices: dict[int, dict] = {}
        self._slots: list[dict] = []
        self._week_overrides: dict[str, str] = {}
        self._overrides: dict[int, dict] = {}
        self._events: list[dict] = []
        self._week_models: list[dict] = []
        # Liaisons pièce -> modèle : {"<room_id>": {"model_id": "...", "pending": bool}}
        self._model_links: dict[str, dict] = {}
        self._samples: dict[int, dict[str, dict[str, dict]]] = {}
        self._pending_history: set[tuple[int, str, str]] = set()
        self._last_sample_at: dict[int, datetime] = {}

        self._next_room_id = 1
        self._next_slot_id = 1
        self._next_event_id = 1

        self._structural_dirty = False
        self._structural_since = 0.0
        self._last_state_save = 0.0
        self._last_history_save = 0.0
        self.state_writes = 0
        self.history_writes = 0
        self.load_error: str | None = None

        self._load()
        self._seed_settings()
        self._seed_rooms()

        if self.state_path or self.history_dir:
            self._thread = threading.Thread(
                target=self._writer_loop, name="ecasa-storage", daemon=True
            )
            self._thread.start()

    # --------------------------------------------------------------- cycle de vie

    def _writer_loop(self) -> None:
        while not self._stop.wait(2.0):
            try:
                self._maybe_flush()
            except Exception:  # noqa: BLE001 - le pilotage ne doit jamais tomber
                pass

    def _maybe_flush(self) -> None:
        now = time.monotonic()
        if (
            self._structural_dirty
            and now - self._structural_since >= self.structural_delay
        ):
            self.flush_state()
        if (
            self._pending_history
            and now - self._last_history_save >= self.history_interval
        ):
            self.flush_history()

    def close(self) -> None:
        """Arrête le writer et force une dernière sauvegarde."""
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        try:
            self.flush(force=True)
        except Exception:  # noqa: BLE001
            pass

    def flush(self, force: bool = False) -> None:
        if force or self._structural_dirty:
            self.flush_state()
        if force or self._pending_history:
            self.flush_history()

    # --------------------------------------------------------------- chargement

    def _load(self) -> None:
        self._load_state()
        self._load_history()
        self._prune_history_files()

    def _load_state(self) -> None:
        if not self.state_path or not os.path.isfile(self.state_path):
            return
        try:
            with open(self.state_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError) as err:
            self.load_error = f"state.json illisible : {err}"
            return
        if not isinstance(payload, dict):
            self.load_error = "state.json invalide : objet attendu"
            return

        settings = payload.get("settings")
        if isinstance(settings, dict):
            self._settings.update({str(k): str(v) for k, v in settings.items()})

        rooms = payload.get("rooms")
        if isinstance(rooms, list):
            self._rooms = [dict(item) for item in rooms if isinstance(item, dict)]
            self._next_room_id = max(
                (int(item.get("id", 0)) for item in self._rooms), default=0
            ) + 1

        devices = payload.get("devices")
        if isinstance(devices, list):
            for item in devices:
                if not isinstance(item, dict):
                    continue
                try:
                    device_id = int(item["device_id"])
                except (KeyError, TypeError, ValueError):
                    continue
                device = dict(item)
                if "temp_min" not in device or device.get("temp_min") is None:
                    device["temp_min"] = 0.0 if device.get("device_type") == "wiser" else 16.0
                if "temp_max" not in device or device.get("temp_max") is None:
                    device["temp_max"] = 30.0
                self._devices[device_id] = device

        slots = payload.get("slots")
        if isinstance(slots, list):
            self._slots = [dict(item) for item in slots if isinstance(item, dict)]
            self._next_slot_id = max(
                (int(item.get("id", 0)) for item in self._slots), default=0
            ) + 1

        weeks = payload.get("week_overrides")
        if isinstance(weeks, dict):
            self._week_overrides = {
                str(k): str(v) for k, v in weeks.items() if v in ("A", "B")
            }

        overrides = payload.get("overrides")
        if isinstance(overrides, dict):
            for key, value in overrides.items():
                try:
                    self._overrides[int(key)] = dict(value)
                except (TypeError, ValueError):
                    continue

        events = payload.get("events")
        if isinstance(events, list):
            self._events = [dict(item) for item in events if isinstance(item, dict)]
            self._events = self._events[-MAX_EVENTS:]
            self._next_event_id = max(
                (int(item.get("id", 0)) for item in self._events), default=0
            ) + 1

        models = payload.get("week_models")
        if isinstance(models, list):
            self._week_models = [
                copy.deepcopy(item) for item in models if isinstance(item, dict)
            ]
            for model in self._week_models:
                model["days"] = self._clean_model_days(model.get("days"), strict=False)
            self._ensure_model_ids()

        links = payload.get("model_links")
        if isinstance(links, dict):
            known = {str(model.get("id")) for model in self._week_models}
            for key, value in links.items():
                if not isinstance(value, dict):
                    continue
                model_id = str(value.get("model_id") or "").strip()
                if model_id not in known:
                    continue
                try:
                    room_id = str(int(key))
                except (TypeError, ValueError):
                    continue
                self._model_links[room_id] = {
                    "model_id": model_id,
                    "pending": bool(value.get("pending")),
                }
        self._prune_model_links()

    def _load_history(self) -> None:
        if not self.history_dir or not os.path.isdir(self.history_dir):
            return
        cutoff = (datetime.now() - timedelta(days=self.history_days)).date()
        try:
            names = sorted(
                name for name in os.listdir(self.history_dir)
                if name.startswith("history-") and name.endswith(".jsonl")
            )
        except OSError:
            return

        for name in names:
            path = os.path.join(self.history_dir, name)
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    for line in handle:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            item = json.loads(line)
                        except ValueError:
                            continue
                        self._apply_history_item(item, cutoff)
            except OSError:
                continue

    def _apply_history_item(self, item: dict, cutoff) -> None:
        if not isinstance(item, dict):
            return
        try:
            device_id = int(item["d"])
            day, hour = str(item["t"]).split("T", 1)
            moment = datetime.fromisoformat(f"{day}T{hour}:00:00")
        except (KeyError, TypeError, ValueError):
            return
        if moment.date() < cutoff:
            return
        bucket = self._samples.setdefault(device_id, {}).setdefault(day, {}).setdefault(
            hour, {"a_sum": 0.0, "a_n": 0, "s_sum": 0.0, "s_n": 0, "mode": None}
        )
        ambient = item.get("a")
        if isinstance(ambient, list) and len(ambient) == 2:
            bucket["a_sum"] = float(ambient[0])
            bucket["a_n"] = int(ambient[1])
        setpoint = item.get("s")
        if isinstance(setpoint, list) and len(setpoint) == 2:
            bucket["s_sum"] = float(setpoint[0])
            bucket["s_n"] = int(setpoint[1])
        if item.get("m") is not None:
            bucket["mode"] = item.get("m")

    def _prune_history_files(self) -> None:
        if not self.history_dir or not os.path.isdir(self.history_dir):
            return
        cutoff = datetime.now() - timedelta(days=self.history_days)
        cutoff_name = f"history-{cutoff:%Y-%m}.jsonl"
        try:
            for name in os.listdir(self.history_dir):
                if not (name.startswith("history-") and name.endswith(".jsonl")):
                    continue
                if name < cutoff_name:
                    try:
                        os.remove(os.path.join(self.history_dir, name))
                    except OSError:
                        pass
        except OSError:
            pass

    # --------------------------------------------------------------- écritures

    def mark_structure(self) -> None:
        with self._lock:
            if not self._structural_dirty:
                self._structural_since = time.monotonic()
            self._structural_dirty = True

    def flush_state(self) -> None:
        if not self.state_path:
            return
        with self._lock:
            if not self._structural_dirty:
                return
            payload = {
                "version": SCHEMA_VERSION,
                "saved_at": datetime.now().isoformat(timespec="seconds"),
                "settings": dict(self._settings),
                "rooms": copy.deepcopy(self._rooms),
                "devices": [copy.deepcopy(item) for item in self._devices.values()],
                "slots": copy.deepcopy(self._slots),
                "week_overrides": dict(self._week_overrides),
                "overrides": copy.deepcopy(self._overrides),
                "events": copy.deepcopy(self._events[-MAX_EVENTS:]),
                "week_models": copy.deepcopy(self._week_models),
                "model_links": copy.deepcopy(self._model_links),
            }
            self._structural_dirty = False
            self._last_state_save = time.monotonic()

        directory = os.path.dirname(self.state_path) or "."
        os.makedirs(directory, exist_ok=True)
        tmp_path = f"{self.state_path}.tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp_path, self.state_path)
            self.state_writes += 1
        except OSError:
            # On remet le drapeau : une prochaine tentative aura lieu.
            with self._lock:
                self._structural_dirty = True
                self._structural_since = time.monotonic()
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass

    def flush_history(self) -> None:
        if not self.history_dir:
            return
        with self._lock:
            pending = list(self._pending_history)
            if not pending:
                return
            lines: dict[str, list[str]] = {}
            for device_id, day, hour in pending:
                bucket = (
                    self._samples.get(device_id, {}).get(day, {}).get(hour)
                )
                if not bucket:
                    continue
                item = {
                    "d": device_id,
                    "t": f"{day}T{hour}",
                    "a": [round(bucket.get("a_sum", 0.0), 3), int(bucket.get("a_n", 0))],
                    "s": [round(bucket.get("s_sum", 0.0), 3), int(bucket.get("s_n", 0))],
                    "m": bucket.get("mode"),
                }
                month = f"history-{day[:7]}.jsonl"
                lines.setdefault(month, []).append(
                    json.dumps(item, ensure_ascii=False, separators=(",", ":"))
                )
            self._pending_history.clear()
            self._last_history_save = time.monotonic()

        if not lines:
            return
        os.makedirs(self.history_dir, exist_ok=True)
        try:
            for month, rows in lines.items():
                with open(
                    os.path.join(self.history_dir, month), "a", encoding="utf-8"
                ) as handle:
                    handle.write("\n".join(rows) + "\n")
                self.history_writes += 1
        except OSError:
            # Les lignes non écrites seront ré-émises au prochain passage :
            # le bucket reste en mémoire et sera re-marqué par add_sample.
            with self._lock:
                self._pending_history.update(pending)

    # --------------------------------------------------------------- recherche

    def _seed_settings(self) -> None:
        with self._lock:
            for key, value in DEFAULT_SETTINGS.items():
                self._settings.setdefault(key, value)

    def _seed_rooms(self) -> None:
        with self._lock:
            if self._rooms:
                return
            for position, name in enumerate(("Salon", "Chambre", "Bureau")):
                self._rooms.append(
                    {
                        "id": self._next_room_id,
                        "name": name,
                        "position": position,
                        "thermostat_device_id": None,
                        "actuator_device_id": None,
                        "thermostat_cap_id": None,
                        "actuator_cap_id": None,
                    }
                )
                self._next_room_id += 1

            rooms = [
                {"id": room["id"], "name": room["name"]} for room in self._rooms
            ]
            fallback = rooms[0]["id"] if rooms else None
            for device in self._devices.values():
                if device.get("room_id") is not None:
                    continue
                match = _match_room(device.get("name") or "", rooms)
                device["room_id"] = match["id"] if match else fallback
            self.mark_structure()

    # --------------------------------------------------------------- pièces

    def rooms(self) -> list[dict]:
        with self._lock:
            return sorted(
                (copy.deepcopy(item) for item in self._rooms),
                key=lambda item: (int(item.get("position", 0) or 0), int(item["id"])),
            )

    def room(self, room_id: int) -> dict | None:
        with self._lock:
            for item in self._rooms:
                if int(item["id"]) == int(room_id):
                    return copy.deepcopy(item)
        return None

    def add_room(self, name: str) -> dict:
        clean = _clean_room_name(name)
        with self._lock:
            self._ensure_room_name_free(clean)
            room = {
                "id": self._next_room_id,
                "name": clean,
                "position": max(
                    (int(item.get("position", 0) or 0) for item in self._rooms),
                    default=-1,
                ) + 1,
                "thermostat_device_id": None,
                "actuator_device_id": None,
                "thermostat_cap_id": None,
                "actuator_cap_id": None,
            }
            self._next_room_id += 1
            self._rooms.append(room)
            self.mark_structure()
            return copy.deepcopy(room)

    def update_room(self, room_id: int, changes: dict) -> dict | None:
        allowed = (
            "name", "position", "thermostat_device_id", "actuator_device_id",
            "thermostat_cap_id", "actuator_cap_id", "heating_curve", "curve_fit",
        )
        with self._lock:
            room = next(
                (item for item in self._rooms if int(item["id"]) == int(room_id)),
                None,
            )
            if room is None:
                return None
            for key in allowed:
                if key not in changes:
                    continue
                if key == "name":
                    clean = _clean_room_name(changes["name"])
                    self._ensure_room_name_free(clean, room_id)
                    room["name"] = clean
                elif key == "position":
                    try:
                        room["position"] = int(changes["position"])
                    except (TypeError, ValueError) as err:
                        raise ValueError("Position de pièce invalide") from err
                elif key == "heating_curve":
                    value = changes[key]
                    room[key] = bool(value) if not isinstance(value, str) else value.lower() in ("1", "true", "on", "yes")
                elif key == "curve_fit":
                    # Champ serveur uniquement : persistance du fit de courbe de chauffe.
                    value = changes[key]
                    if value in (None, "", False):
                        room.pop("curve_fit", None)
                    elif isinstance(value, dict):
                        room["curve_fit"] = copy.deepcopy(value)
                    else:
                        raise ValueError("curve_fit invalide")
                else:
                    value = changes[key]
                    if value in (None, "", 0, "0"):
                        room[key] = None
                    else:
                        try:
                            room[key] = int(value)
                        except (TypeError, ValueError) as err:
                            raise ValueError(f"{key} invalide") from err
            self.mark_structure()
            return copy.deepcopy(room)

    def delete_room(self, room_id: int) -> bool:
        with self._lock:
            before = len(self._rooms)
            self._rooms = [
                item for item in self._rooms if int(item["id"]) != int(room_id)
            ]
            if len(self._rooms) == before:
                return False
            for device in self._devices.values():
                if device.get("room_id") == int(room_id):
                    device["room_id"] = None
            self._model_links.pop(str(int(room_id)), None)
            self.mark_structure()
            return True

    def room_devices(self, room_id: int) -> list[dict]:
        with self._lock:
            devices = [
                copy.deepcopy(item)
                for item in self._devices.values()
                if item.get("room_id") == int(room_id)
            ]
        return sorted(
            devices,
            key=lambda item: (
                int(item.get("position", 0) or 0),
                int(item["device_id"]),
            ),
        )

    def _ensure_room_name_free(
        self, name: str, exclude_id: int | None = None
    ) -> None:
        for room in self._rooms:
            if exclude_id is not None and int(room["id"]) == int(exclude_id):
                continue
            if str(room["name"]).casefold() == name.casefold():
                raise ValueError(f"Une pièce s'appelle déjà « {name} »")

    # --------------------------------------------------------------- réglages

    def settings(self) -> dict:
        with self._lock:
            values = dict(DEFAULT_SETTINGS)
            values.update(self._settings)
            return values

    def set_settings(self, changes: dict) -> dict:
        allowed = set(DEFAULT_SETTINGS)
        with self._lock:
            for key, value in changes.items():
                if key in allowed:
                    self._settings[key] = str(value)
            self.mark_structure()
        return self.settings()

    def flag(self, key: str) -> bool:
        return self.settings().get(key, "0") in ("1", "true", "True", "on")

    # ------------------------------------------------------------- week models

    def _ensure_model_ids(self) -> None:
        """Donne un identifiant stable à chaque modèle (migration des états existants)."""
        used: set[str] = set()
        for model in self._week_models:
            raw = str(model.get("id") or "").strip()
            if raw and raw not in used:
                model["id"] = raw
                used.add(raw)
            else:
                model["id"] = self._next_model_id(used)
                used.add(model["id"])

    @staticmethod
    def _next_model_id(used: set[str]) -> str:
        index = 1
        while f"model-{index}" in used:
            index += 1
        return f"model-{index}"

    def _prune_model_links(self) -> None:
        """Supprime les liaisons orphelines (pièce ou modèle disparu)."""
        rooms = {str(int(room["id"])) for room in self._rooms}
        models = {str(model.get("id")) for model in self._week_models}
        for key in list(self._model_links):
            link = self._model_links[key]
            if key not in rooms or str(link.get("model_id")) not in models:
                self._model_links.pop(key, None)

    def week_models(self) -> list[dict]:
        with self._lock:
            return copy.deepcopy(self._week_models)

    def week_model(self, model_id) -> dict | None:
        target = str(model_id or "")
        with self._lock:
            model = next(
                (item for item in self._week_models if str(item.get("id")) == target),
                None,
            )
            return copy.deepcopy(model) if model else None

    def room_model_links(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._model_links)

    def link_for_room(self, room_id) -> dict | None:
        with self._lock:
            link = self._model_links.get(str(int(room_id)))
            return copy.deepcopy(link) if link else None

    def set_room_model_link(self, room_id, model_id, pending: bool = False) -> dict:
        """Lie (ou délie si ``model_id`` est vide) une pièce à un modèle."""
        key = str(int(room_id))
        if not model_id:
            with self._lock:
                self._model_links.pop(key, None)
                self.mark_structure()
            return self.room_model_links()
        if self.week_model(model_id) is None:
            raise ValueError("Modèle inconnu")
        with self._lock:
            self._model_links[key] = {
                "model_id": str(model_id),
                "pending": bool(pending),
            }
            self.mark_structure()
            return self.room_model_links()

    def set_model_link_pending(self, room_id, pending: bool) -> None:
        with self._lock:
            link = self._model_links.get(str(int(room_id)))
            if link and bool(link.get("pending")) != bool(pending):
                link["pending"] = bool(pending)
                self.mark_structure()

    @staticmethod
    def _clean_model_days(days, strict: bool = True) -> dict:
        """Normalise les 7 jours A/B d'un modèle.

        ``strict`` valide les modes (sinon ``ValueError``) : indispensable avant
        toute écriture, un mode invalide ferait échouer ``set_slots`` en cours
        de route et laisserait un planning tronqué. En mode non strict (lecture
        d'un état existant), les créneaux invalides sont simplement ignorés.
        """
        days = days if isinstance(days, dict) else {}
        cleaned: dict[str, list[list[dict]]] = {}
        for week_key in ("A", "B"):
            raw = days.get(week_key)
            week: list[list[dict]] = []
            for index in range(7):
                slots = (
                    raw[index]
                    if isinstance(raw, list) and index < len(raw) and isinstance(raw[index], list)
                    else []
                )
                day_slots = []
                for slot in slots:
                    if not isinstance(slot, dict):
                        continue
                    time = str(slot.get("time") or "").strip()
                    if not time:
                        continue
                    mode = str(slot.get("mode") or "heat")
                    if mode not in VALID_MODES:
                        if strict:
                            raise ValueError(f"Mode inconnu : {mode}")
                        continue
                    try:
                        temp = float(slot.get("temp", 20))
                    except (TypeError, ValueError):
                        temp = 20.0
                    day_slots.append({"time": time, "mode": mode, "temp": round(temp, 1)})
                day_slots.sort(key=lambda s: s.get("time", "00:00"))
                week.append(day_slots)
            cleaned[week_key] = week
        return cleaned

    def set_week_models(self, models, locked_days=None,
                        propagate: bool = True) -> list[dict]:
        if not isinstance(models, list):
            raise ValueError("Liste de modèles invalide")

        cleaned: list[dict] = []
        used: set[str] = set()
        for model in models:
            if not isinstance(model, dict):
                continue
            name = str(model.get("name") or "Sans nom").strip() or "Sans nom"
            model_id = str(model.get("id") or "").strip()
            if not model_id or model_id in used:
                model_id = self._next_model_id(used)
            used.add(model_id)
            cleaned.append({
                "id": model_id,
                "name": name,
                "days": self._clean_model_days(model.get("days")),
            })

        with self._lock:
            previous = {
                str(item.get("id")): copy.deepcopy(item.get("days"))
                for item in self._week_models
            }
            changed = [
                item["id"] for item in cleaned
                if previous.get(item["id"]) != item["days"]
            ]
            self._week_models = cleaned
            self._prune_model_links()
            self.mark_structure()

        # Un modèle modifié met à jour le planning des pièces liées, en
        # conservant leurs jours de cours Pronote (transmis par main.py).
        if propagate and changed:
            for room_key, link in self.room_model_links().items():
                if link.get("model_id") not in changed:
                    continue
                locks = (locked_days or {}).get(room_key)
                try:
                    self.apply_model_to_room(int(room_key), link["model_id"], locks)
                except (TypeError, ValueError):
                    continue
        return self.week_models()

    # --------------------------------------------------- liaisons modèles/pièces

    def _has_room_slots(self, room_id) -> bool:
        """Vrai si la pièce possède au moins un créneau qui lui est rattaché."""
        try:
            target = int(room_id)
        except (TypeError, ValueError):
            return False
        with self._lock:
            return any(
                int(item.get("room_id") or 0) == target for item in self._slots
            )

    def _room_slot_days(self, room_id, week: str) -> list[list[dict]]:
        """Planning d'une pièce sous forme de 7 listes de créneaux {time, mode, temp}."""
        rows = self.slots(room_id=int(room_id), week=week)
        if not rows and not self._has_room_slots(room_id):
            # Anciennes données : créneaux rattachés à l'appareil actionneur.
            # Le repli ne s'applique que si la pièce n'a AUCUN créneau propre
            # (toutes semaines), sinon une semaine volontairement vidée ferait
            # réapparaître d'anciens créneaux d'appareil.
            with self._lock:
                room = next(
                    (item for item in self._rooms if int(item["id"]) == int(room_id)),
                    None,
                )
                related = set()
                if room:
                    for key in ("actuator_device_id", "thermostat_device_id"):
                        value = room.get(key)
                        if value not in (None, "", 0, "0"):
                            try:
                                related.add(int(value))
                            except (TypeError, ValueError):
                                pass
                rows = [
                    copy.deepcopy(item) for item in self._slots
                    if item.get("week") == week
                    and item.get("device_id") in related
                    and item.get("room_id") in (None, "", 0, "0")
                ]
        days: list[list[dict]] = [[] for _ in range(7)]
        for slot in rows:
            index = int(slot.get("weekday", 0))
            if not 0 <= index < 7:
                continue
            days[index].append({
                "time": _from_minutes(int(slot.get("minutes", 0))),
                "mode": str(slot.get("mode") or "off"),
                "temp": round(float(slot.get("temp", 20)), 1),
            })
        for day in days:
            day.sort(key=lambda item: item["time"])
        return days

    @staticmethod
    def _model_days(model: dict, week: str) -> list[list[dict]]:
        raw = (model.get("days") or {}).get(week) or []
        days: list[list[dict]] = []
        for index in range(7):
            slots = raw[index] if isinstance(raw, list) and index < len(raw) else []
            days.append([
                {
                    "time": str(slot.get("time")),
                    "mode": str(slot.get("mode") or "off"),
                    "temp": round(float(slot.get("temp", 20)), 1),
                }
                for slot in slots
                if isinstance(slot, dict) and slot.get("time")
            ])
        return days

    @staticmethod
    def _day_key(day) -> list[tuple]:
        items = []
        for slot in day or []:
            if not isinstance(slot, dict) or not slot.get("time"):
                continue
            try:
                temp = round(float(slot.get("temp", 20)), 1)
            except (TypeError, ValueError):
                temp = 20.0
            items.append((
                _to_minutes(slot.get("time")),
                str(slot.get("mode") or "off"),
                temp,
            ))
        items.sort()
        return items

    def room_matches_model(self, room_id, model_id, ignore_days=None) -> bool:
        """Vrai si le planning de la pièce est identique au modèle.

        ``ignore_days`` permet d'ignorer les jours verrouillés par Pronote
        (``{"A": [bool]*7, "B": [...]}``), qui diffèrent par nature.
        """
        model = self.week_model(model_id)
        if not model:
            return False
        ignore_days = ignore_days or {}
        for week in ("A", "B"):
            room_days = self._room_slot_days(room_id, week)
            model_days = self._model_days(model, week)
            ignore = ignore_days.get(week) or []
            for index in range(7):
                if index < len(ignore) and ignore[index]:
                    continue
                if self._day_key(room_days[index]) != self._day_key(model_days[index]):
                    return False
        return True

    def apply_model_to_room(self, room_id, model_id, locked_days=None) -> bool:
        """Remplace le planning de la pièce par le modèle, jours verrouillés exclus."""
        model = self.week_model(model_id)
        if not model or self.room(int(room_id)) is None:
            return False
        locked_days = locked_days or {}
        for week in ("A", "B"):
            current = self._room_slot_days(room_id, week)
            model_days = self._model_days(model, week)
            locked = locked_days.get(week) or []
            days = []
            for index in range(7):
                if index < len(locked) and locked[index]:
                    days.append([dict(slot) for slot in current[index]])
                else:
                    days.append([dict(slot) for slot in model_days[index]])
            self.set_slots(room_id=int(room_id), week=week, days=days)
        # `set_slots` borne les consignes à la plage de l'appareil : l'état réel
        # de synchronisation est donc recalculé, jamais supposé.
        matches = self.room_matches_model(room_id, model_id, locked_days)
        with self._lock:
            link = self._model_links.get(str(int(room_id)))
            if link and str(link.get("model_id")) == str(model_id):
                link["pending"] = not matches
            self.mark_structure()
        return True

    def update_model_from_room(self, room_id, model_id, skip_days=None) -> dict | None:
        """Copie le planning de la pièce dans son modèle (jours Pronote ignorés)."""
        skip_days = skip_days or {}
        with self._lock:
            model = next(
                (item for item in self._week_models if str(item.get("id")) == str(model_id)),
                None,
            )
        if model is None:
            return None
        days = {}
        for week in ("A", "B"):
            room_days = self._room_slot_days(room_id, week)
            old_days = self._model_days(model, week)
            skip = skip_days.get(week) or []
            merged = []
            for index in range(7):
                if index < len(skip) and skip[index]:
                    merged.append([dict(slot) for slot in old_days[index]])
                else:
                    merged.append([dict(slot) for slot in room_days[index]])
            days[week] = merged
        with self._lock:
            for item in self._week_models:
                if str(item.get("id")) == str(model_id):
                    item["days"] = days
                    break
            self.mark_structure()
        # Le modèle vient d'être aligné sur la pièce : seul un écart résiduel
        # (arrondi, verrou Pronote mal déclaré) doit rester « à synchroniser ».
        matches = self.room_matches_model(room_id, model_id, skip_days)
        with self._lock:
            link = self._model_links.get(str(int(room_id)))
            if link and str(link.get("model_id")) == str(model_id):
                link["pending"] = not matches
            self.mark_structure()
        return self.week_model(model_id)

    # --------------------------------------------------------------- appareils

    def devices(self) -> list[dict]:
        with self._lock:
            devices = [copy.deepcopy(item) for item in self._devices.values()]
        return sorted(
            devices,
            key=lambda item: (
                int(item.get("position", 0) or 0),
                int(item["device_id"]),
            ),
        )

    def device(self, device_id: int) -> dict | None:
        with self._lock:
            item = self._devices.get(int(device_id))
            return copy.deepcopy(item) if item else None

    def upsert_device(self, device_id: int, name: str) -> None:
        with self._lock:
            device = self._devices.get(int(device_id))
            if device is None:
                device = {
                    "device_id": int(device_id),
                    "name": name,
                    "enabled": 1,
                    "cap_mode": 7,
                    "cap_setpoint": 17,
                    "cap_setpoint_cool": 0,
                    "cap_ambient": 40,
                    "cap_prog": 0,
                    "cap_fan": 0,
                    "cap_louver": 0,
                    "cap_swing": 0,
                    "position": len(self._devices),
                    "room_id": None,
                    "device_type": "atlantic",
                    "wiser_room_id": 0,
                    "temp_sensor_device_id": 0,
                 "temp_min": 16.0,
                 "temp_max": 30.0,
                }
                self._devices[int(device_id)] = device
            elif device.get("name") != name:
                device["name"] = name
            if device.get("room_id") is None:
                rooms = [
                    {"id": item["id"], "name": item["name"]} for item in self._rooms
                ]
                match = _match_room(name, rooms)
                if match:
                    device["room_id"] = match["id"]
            self.mark_structure()

    def delete_device(self, device_id: int) -> None:
        device_id = int(device_id)
        with self._lock:
            self._devices.pop(device_id, None)
            self._slots = [
                item for item in self._slots
                if int(item.get("device_id") or 0) != device_id
            ]
            self._overrides.pop(device_id, None)
            self._samples.pop(device_id, None)
            self._pending_history = {
                item for item in self._pending_history if item[0] != device_id
            }
            for item in self._devices.values():
                if item.get("temp_sensor_device_id") == device_id:
                    item["temp_sensor_device_id"] = 0
            self.mark_structure()

    def update_device(self, device_id: int, changes: dict) -> dict | None:
        allowed = (
            "name", "enabled", "cap_mode", "cap_setpoint", "cap_setpoint_cool",
            "cap_ambient", "cap_prog", "cap_fan", "cap_louver", "cap_swing",
            "position", "device_type",
            "wiser_room_id", "temp_sensor_device_id", "room_id",
            "temp_min", "temp_max",
        )
        with self._lock:
            device = self._devices.get(int(device_id))
            if device is None:
                return None
            for key, value in changes.items():
                if key not in allowed:
                    continue
                if key == "room_id":
                    if value in (None, "", 0, "0"):
                        device["room_id"] = None
                    else:
                        try:
                            room_id = int(value)
                        except (TypeError, ValueError) as err:
                            raise ValueError("Pièce invalide") from err
                        if not any(int(item["id"]) == room_id for item in self._rooms):
                            raise ValueError("Pièce introuvable")
                        device["room_id"] = room_id
                elif key in ("name", "device_type"):
                    device[key] = str(value)
                    # Plage par défaut selon le type, si non fournie explicitement.
                    if key == "device_type" and "temp_min" not in changes and "temp_max" not in changes:
                        if str(value) == "wiser":
                            device["temp_min"] = 0.0
                            device["temp_max"] = 30.0
                        else:
                            device["temp_min"] = 16.0
                            device["temp_max"] = 30.0
                elif key == "enabled":
                    device[key] = 1 if value in (1, "1", True, "true", "on") else 0
                elif key == "position":
                    device[key] = int(value)
                elif key in ("temp_min", "temp_max"):
                    try:
                        number = float(value)
                    except (TypeError, ValueError) as err:
                        raise ValueError("Plage de température invalide") from err
                    if key == "temp_min":
                        device[key] = round(max(-10.0, min(30.0, number)), 1)
                    else:
                        device[key] = round(max(0.0, min(40.0, number)), 1)
                    if device.get("temp_min", 0) > device.get("temp_max", 30):
                        device["temp_min"], device["temp_max"] = device["temp_max"], device["temp_min"]
                else:
                    device[key] = int(value or 0)
            self.mark_structure()
            return copy.deepcopy(device)

    # --------------------------------------------------------------- plannings

    def slots(
        self,
        device_id: int | None = None,
        week: str = "A",
        room_id: int | None = None,
    ) -> list[dict]:
        if week not in ("A", "B"):
            return []
        with self._lock:
            if room_id is not None:
                rows = [
                    copy.deepcopy(item) for item in self._slots
                    if item.get("room_id") == int(room_id) and item.get("week") == week
                ]
            elif device_id is not None:
                rows = [
                    copy.deepcopy(item) for item in self._slots
                    if item.get("device_id") == int(device_id)
                    and item.get("week") == week
                ]
            else:
                return []
        return sorted(rows, key=lambda item: (int(item["weekday"]), int(item["minutes"])))

    def set_slots(
        self,
        device_id: int | None = None,
        week: str = "A",
        days: list[list[dict]] | None = None,
        room_id: int | None = None,
    ) -> None:
        if days is None and isinstance(device_id, list):
            days = device_id  # compat appel positionnel
            device_id = None
        if week not in ("A", "B"):
            raise ValueError("Semaine invalide")
        if not days or len(days) != 7:
            raise ValueError("Il faut exactement 7 jours")
        if room_id is None and device_id is None:
            raise ValueError("room_id ou device_id requis")

        with self._lock:
            if room_id is not None:
                # Les créneaux peuvent être rattachés à la pièce OU à l'appareil
                # qui en est l'actionneur (cas des anciennes données migrées).
                # On supprime les deux pour éviter les doublons fantômes.
                room = next(
                    (item for item in self._rooms if int(item["id"]) == int(room_id)),
                    None,
                )
                related_devices = set()
                if room:
                    for key in ("thermostat_device_id", "actuator_device_id"):
                        value = room.get(key)
                        if value not in (None, "", 0, "0"):
                            try:
                                related_devices.add(int(value))
                            except (TypeError, ValueError):
                                pass
                self._slots = [
                    item for item in self._slots
                    if not (
                        item.get("week") == week
                        and (
                            item.get("room_id") == int(room_id)
                            or (
                                item.get("room_id") in (None, "", 0, "0")
                                and item.get("device_id") in related_devices
                            )
                        )
                    )
                ]
            else:
                self._slots = [
                    item for item in self._slots
                    if not (
                        item.get("device_id") == int(device_id)
                        and item.get("week") == week
                    )
                ]

            # Plage de température selon l'appareil concerné (défaut 16-30).
            _device = None
            if device_id is not None:
                _device = self._devices.get(int(device_id))
            elif room_id is not None:
                _room = next(
                    (item for item in self._rooms if int(item["id"]) == int(room_id)),
                    None,
                )
                if _room:
                    for _key in ("actuator_device_id", "thermostat_device_id"):
                        _value = _room.get(_key)
                        if _value not in (None, "", 0, "0"):
                            try:
                                _device = self._devices.get(int(_value))
                                if _device:
                                    break
                            except (TypeError, ValueError):
                                pass
            # Attention : temp_min peut valoir 0.0 (Wiser) — 0.0 est falsy en
            # Python, il ne faut donc tester que None (sinon → repli 16 °C).
            _raw_tmin = (_device or {}).get("temp_min")
            _raw_tmax = (_device or {}).get("temp_max")
            _tmin = 16.0 if _raw_tmin is None else float(_raw_tmin)
            _tmax = 30.0 if _raw_tmax is None else float(_raw_tmax)
            if _tmin > _tmax:
                _tmin, _tmax = _tmax, _tmin

            for weekday, day in enumerate(days):
                for slot in day or []:
                    mode = str(slot.get("mode", "off"))
                    if mode not in VALID_MODES:
                        raise ValueError(f"Mode inconnu : {mode}")
                    minutes = _to_minutes(slot.get("time", "00:00"))
                    temp = max(_tmin, min(_tmax, float(slot.get("temp", 20))))
                    self._slots.append(
                        {
                            "id": self._next_slot_id,
                            "device_id": int(device_id) if device_id is not None else None,
                            "room_id": int(room_id) if room_id is not None else None,
                            "week": week,
                            "weekday": weekday,
                            "minutes": minutes,
                            "mode": mode,
                            "temp": round(temp, 1),
                            "fan": slot.get("fan"),
                        }
                    )
                    self._next_slot_id += 1
            self.mark_structure()

    # --------------------------------------------------------------- semaines

    def week_overrides(self) -> dict:
        with self._lock:
            return dict(self._week_overrides)

    def set_week_override(self, monday: str, week: str | None) -> None:
        with self._lock:
            if week is None:
                self._week_overrides.pop(str(monday), None)
            elif week in ("A", "B"):
                self._week_overrides[str(monday)] = week
            else:
                raise ValueError("Semaine invalide")
            self.mark_structure()

    # --------------------------------------------------------------- overrides

    def override(self, device_id: int) -> dict | None:
        with self._lock:
            item = self._overrides.get(int(device_id))
            return copy.deepcopy(item) if item else None

    def overrides(self) -> dict:
        with self._lock:
            return {
                int(key): copy.deepcopy(value)
                for key, value in self._overrides.items()
            }

    def set_override(
        self, device_id: int, mode: str, temp: float, until: datetime
    ) -> None:
        if mode not in VALID_MODES:
            raise ValueError("Mode inconnu")
        device = self._devices.get(int(device_id))
        # Même piège que set_slots : 0.0 (Wiser) est falsy → tester None seul.
        _raw_tmin = (device or {}).get("temp_min")
        _raw_tmax = (device or {}).get("temp_max")
        _tmin = 16.0 if _raw_tmin is None else float(_raw_tmin)
        _tmax = 30.0 if _raw_tmax is None else float(_raw_tmax)
        if _tmin > _tmax:
            _tmin, _tmax = _tmax, _tmin
        temp = max(_tmin, min(_tmax, float(temp)))
        with self._lock:
            self._overrides[int(device_id)] = {
                "device_id": int(device_id),
                "mode": mode,
                "temp": round(temp, 1),
                "until": until.isoformat(timespec="seconds"),
            }
            self.mark_structure()

    def clear_override(self, device_id: int) -> None:
        with self._lock:
            if self._overrides.pop(int(device_id), None) is not None:
                self.mark_structure()

    # --------------------------------------------------------------- mesures

    def add_sample(
        self,
        device_id: int,
        ambient: float | None,
        setpoint: float | None,
        mode: str | None = None,
        ts: datetime | None = None,
        min_interval: float = 15.0,
    ) -> bool:
        if ambient is None and setpoint is None:
            return False
        moment = (ts or datetime.now()).replace(microsecond=0)
        device_id = int(device_id)
        with self._lock:
            previous = self._last_sample_at.get(device_id)
            if previous and (moment - previous).total_seconds() < min_interval:
                return False
            self._last_sample_at[device_id] = moment
            self._accumulate(device_id, moment, ambient, setpoint, mode)
        return True

    def add_samples(self, rows: list[dict]) -> None:
        if not rows:
            return
        with self._lock:
            for row in rows:
                moment = row.get("ts")
                if isinstance(moment, str):
                    try:
                        moment = datetime.fromisoformat(moment)
                    except ValueError:
                        continue
                if not isinstance(moment, datetime):
                    continue
                try:
                    device_id = int(row["device_id"])
                except (KeyError, TypeError, ValueError):
                    continue
                self._accumulate(
                    device_id,
                    moment.replace(microsecond=0),
                    row.get("ambient"),
                    row.get("setpoint"),
                    row.get("mode"),
                )

    def _accumulate(
        self,
        device_id: int,
        moment: datetime,
        ambient,
        setpoint,
        mode,
    ) -> None:
        day = moment.strftime("%Y-%m-%d")
        hour = f"{moment.hour:02d}"
        bucket = self._samples.setdefault(device_id, {}).setdefault(day, {}).setdefault(
            hour, {"a_sum": 0.0, "a_n": 0, "s_sum": 0.0, "s_n": 0, "mode": None}
        )
        ambient_value = _float_or_none(ambient)
        if ambient_value is not None:
            bucket["a_sum"] = float(bucket["a_sum"]) + ambient_value
            bucket["a_n"] = int(bucket["a_n"]) + 1
        setpoint_value = _float_or_none(setpoint)
        if setpoint_value is not None:
            bucket["s_sum"] = float(bucket["s_sum"]) + setpoint_value
            bucket["s_n"] = int(bucket["s_n"]) + 1
        if mode:
            bucket["mode"] = mode
        self._pending_history.add((device_id, day, hour))

    def samples(
        self, device_id: int, start: datetime, end: datetime
    ) -> list[dict]:
        device_id = int(device_id)
        with self._lock:
            days = self._samples.get(device_id, {})
            rows = []
            for day, hours in days.items():
                try:
                    day_date = datetime.fromisoformat(f"{day}T00:00:00")
                except ValueError:
                    continue
                if day_date.date() < start.date() or day_date.date() > end.date():
                    continue
                for hour, bucket in hours.items():
                    try:
                        moment = datetime.fromisoformat(f"{day}T{hour}:00:00")
                    except ValueError:
                        continue
                    if moment < start or moment > end:
                        continue
                    ambient = (
                        round(bucket["a_sum"] / bucket["a_n"], 1)
                        if bucket.get("a_n") else None
                    )
                    setpoint = (
                        round(bucket["s_sum"] / bucket["s_n"], 1)
                        if bucket.get("s_n") else None
                    )
                    rows.append(
                        {
                            "ts": moment.isoformat(timespec="seconds"),
                            "ambient": ambient,
                            "setpoint": setpoint,
                            "mode": bucket.get("mode"),
                        }
                    )
        return sorted(rows, key=lambda item: item["ts"])

    def sample_bounds(self, device_id: int) -> tuple[str | None, str | None]:
        with self._lock:
            days = self._samples.get(int(device_id), {})
            if not days:
                return None, None
            valid_days = sorted(days)
            first_day = valid_days[0]
            last_day = valid_days[-1]
            first_hours = sorted(days[first_day])
            last_hours = sorted(days[last_day])
            first = (
                f"{first_day}T{first_hours[0]}:00:00" if first_hours else None
            )
            last = (
                f"{last_day}T{last_hours[-1]}:00:00" if last_hours else None
            )
            return first, last

    def prune_samples(self, keep_days: int = 180) -> None:
        cutoff = (datetime.now() - timedelta(days=max(1, int(keep_days)))).strftime(
            "%Y-%m-%d"
        )
        with self._lock:
            for device_id, days in list(self._samples.items()):
                for day in list(days):
                    if day < cutoff:
                        days.pop(day, None)
                if not days:
                    self._samples.pop(device_id, None)
            self._prune_history_files()

    # --------------------------------------------------------------- journal

    def log(self, level: str, message: str, device_id: int | None = None) -> None:
        with self._lock:
            self._events.append(
                {
                    "id": self._next_event_id,
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "level": level,
                    "device_id": int(device_id) if device_id is not None else None,
                    "message": message,
                }
            )
            self._next_event_id += 1
            if len(self._events) > MAX_EVENTS:
                self._events = self._events[-MAX_EVENTS:]
            self.mark_structure()

    def events(self, limit: int = 120) -> list[dict]:
        with self._lock:
            return [copy.deepcopy(item) for item in reversed(self._events[-int(limit):])]

    def prune_events(self, keep: int = MAX_EVENTS) -> None:
        with self._lock:
            keep = max(1, int(keep))
            if len(self._events) > keep:
                self._events = self._events[-keep:]

    def clear_events(self) -> int:
        """Vide le journal. Retourne le nombre d'évènements supprimés."""
        with self._lock:
            removed = len(self._events)
            if removed:
                self._events = []
                self.mark_structure()
            return removed

    # --------------------------------------------------------------- export

    def export(self) -> dict:
        with self._lock:
            return {
                "settings": self.settings(),
                "rooms": copy.deepcopy(self._rooms),
                "devices": [copy.deepcopy(item) for item in self._devices.values()],
                "slots": copy.deepcopy(self._slots),
                "week_overrides": [
                    {"monday": monday, "week": week}
                    for monday, week in self._week_overrides.items()
                ],
            }

    def import_data(self, payload: dict) -> None:
        if not isinstance(payload, dict):
            raise ValueError("Sauvegarde invalide")
        with self._lock:
            if payload.get("settings"):
                self.set_settings(payload["settings"])

            if "rooms" in payload:
                self._rooms = []
                for room in payload.get("rooms") or []:
                    name = _clean_room_name(room.get("name"))
                    self._rooms.append(
                        {
                            "id": int(room["id"]),
                            "name": name,
                            "position": int(room.get("position", 0) or 0),
                            "thermostat_device_id": room.get("thermostat_device_id"),
                            "actuator_device_id": room.get("actuator_device_id"),
                            "thermostat_cap_id": room.get("thermostat_cap_id"),
                            "actuator_cap_id": room.get("actuator_cap_id"),
                        }
                    )
                self._next_room_id = max(
                    (int(item["id"]) for item in self._rooms), default=0
                ) + 1
                if not self._rooms:
                    self._seed_rooms()

            valid_rooms = {int(room["id"]) for room in self._rooms}
            for device in payload.get("devices", []) or []:
                if not isinstance(device, dict):
                    continue
                try:
                    device_id = int(device["device_id"])
                except (KeyError, TypeError, ValueError):
                    continue
                changes = dict(device)
                if changes.get("room_id") not in valid_rooms:
                    changes["room_id"] = None
                self.upsert_device(device_id, device.get("name", "Appareil"))
                self.update_device(device_id, changes)

            grouped: dict[tuple[str, int, str], list[list[dict]]] = {}
            for slot in payload.get("slots", []) or []:
                if not isinstance(slot, dict):
                    continue
                try:
                    weekday = int(slot["weekday"])
                    week = str(slot["week"])
                except (KeyError, TypeError, ValueError):
                    continue
                if not 0 <= weekday <= 6 or week not in ("A", "B"):
                    continue
                if str(slot.get("mode")) not in VALID_MODES:
                    continue
                try:
                    minutes = int(slot["minutes"])
                except (KeyError, TypeError, ValueError):
                    continue
                room_id = slot.get("room_id")
                device_id = slot.get("device_id")
                if room_id not in (None, "", 0, "0"):
                    try:
                        key = ("room", int(room_id), week)
                    except (TypeError, ValueError):
                        continue
                elif device_id not in (None, "", 0, "0"):
                    try:
                        key = ("device", int(device_id), week)
                    except (TypeError, ValueError):
                        continue
                else:
                    continue
                grouped.setdefault(key, [[] for _ in range(7)])
                grouped[key][weekday].append(
                    {
                        "time": _from_minutes(minutes),
                        "mode": slot["mode"],
                        "temp": slot["temp"],
                        "fan": slot.get("fan"),
                    }
                )
            known_rooms = {int(item["id"]) for item in self._rooms}
            for (kind, target_id, week), days in grouped.items():
                if kind == "room":
                    if target_id not in known_rooms:
                        continue
                    self.set_slots(room_id=target_id, week=week, days=days)
                else:
                    self.set_slots(target_id, week, days)

            self._week_overrides = {}
            for row in payload.get("week_overrides", []) or []:
                if isinstance(row, dict) and row.get("week") in ("A", "B"):
                    self._week_overrides[str(row["monday"])] = row["week"]
            self.mark_structure()

    # --------------------------------------------------------------- diagnostic

    def storage_info(self) -> dict:
        with self._lock:
            state_size = 0
            if self.state_path and os.path.isfile(self.state_path):
                try:
                    state_size = os.path.getsize(self.state_path)
                except OSError:
                    state_size = 0
            history_files = 0
            history_size = 0
            if self.history_dir and os.path.isdir(self.history_dir):
                try:
                    for name in os.listdir(self.history_dir):
                        if name.startswith("history-") and name.endswith(".jsonl"):
                            history_files += 1
                            try:
                                history_size += os.path.getsize(
                                    os.path.join(self.history_dir, name)
                                )
                            except OSError:
                                pass
                except OSError:
                    pass
            buckets = sum(
                len(hours)
                for days in self._samples.values()
                for hours in days.values()
            )
            return {
                "mode": "mémoire + snapshots",
                "state_path": self.state_path,
                "history_dir": self.history_dir,
                "state_size": state_size,
                "history_files": history_files,
                "history_size": history_size,
                "history_buckets": buckets,
                "pending_history": len(self._pending_history),
                "state_writes": self.state_writes,
                "history_writes": self.history_writes,
                "structural_delay": self.structural_delay,
                "history_interval": self.history_interval,
                "history_days": self.history_days,
                "dirty": self._structural_dirty,
            }


# --------------------------------------------------------------------- outils


def _float_or_none(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_room_name(value) -> str:
    name = " ".join(str(value or "").split())
    if not name:
        raise ValueError("Le nom de la pièce ne peut pas être vide")
    if len(name) > 60:
        raise ValueError("Le nom de la pièce est trop long (60 caractères max)")
    return name


def _normalize_room_text(value) -> str:
    text = str(value or "").lower().replace("œ", "oe")
    text = unicodedata.normalize("NFKD", text)
    return "".join(char for char in text if not unicodedata.combining(char))


def _match_room(device_name: str, rooms: list[dict]) -> dict | None:
    haystack = _normalize_room_text(device_name)
    best = None
    best_position = None
    best_length = 0
    for room in rooms:
        needle = _normalize_room_text(room.get("name"))
        if not needle:
            continue
        position = haystack.find(needle)
        if position < 0:
            continue
        if (
            best_position is None
            or position < best_position
            or (position == best_position and len(needle) > best_length)
        ):
            best = room
            best_position = position
            best_length = len(needle)
    return best


def _to_minutes(value: str) -> int:
    hours, _, minutes = str(value).partition(":")
    try:
        return max(0, min(23, int(hours))) * 60 + max(0, min(59, int(minutes)))
    except (TypeError, ValueError):
        return 0


def _from_minutes(minutes: int) -> str:
    return f"{int(minutes) // 60:02d}:{int(minutes) % 60:02d}"


def dumps(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)
