"""Reglages par defaut de la soufflerie et de l'orientation (clim Atlantic).

Capacites Cozytouch utilisees :
    100801 ROOM_stFanSpeed              (1 basse, 2 moyenne, 3 haute, 5 auto)
    100803 ROOM_stLouverAirFlowDirection (1 a 6)
    100804 ROOM_bLouverSwingMode         (1 = balayage)
"""

import unittest
from datetime import datetime
from unittest import mock

from ecasa.atlantic import AtlanticError, ExecutionTimeout
from ecasa.engine import (
    AIRFLOW_POSITIONS,
    FAN_VALUES,
    Engine,
    detect_caps,
)
from ecasa.storage import MemoryStore

DEVICE_ID = 28129468


class FakeStore:
    def __init__(self, settings=None, device=None):
        self._settings = dict(settings or {})
        self._device = dict(device or DEFAULT_DEVICE)
        self.logs = []

    def settings(self):
        return dict(self._settings)

    def devices(self):
        return [dict(self._device)]

    def device(self, device_id):
        if int(device_id) != int(self._device["device_id"]):
            return None
        return dict(self._device)

    def update_device(self, device_id, changes):
        if int(device_id) != int(self._device["device_id"]):
            return None
        self._device.update(changes)
        return dict(self._device)

    def log(self, kind, message, device_id=None):
        self.logs.append((kind, message, device_id))


class FakeClient:
    def __init__(self):
        self.writes = []

    def write_capability(self, device_id, capability_id, value, wait=True):
        self.writes.append((int(capability_id), str(value)))
        return True


class TimeoutClient(FakeClient):
    """Ordre accepte mais execution non confirmee (cas du 2026-11-12)."""

    def write_capability(self, device_id, capability_id, value, wait=True):
        self.writes.append((int(capability_id), str(value)))
        raise ExecutionTimeout("Execution non confirmee a temps", 4242)


class FailingClient(FakeClient):
    def write_capability(self, device_id, capability_id, value, wait=True):
        self.writes.append((int(capability_id), str(value)))
        raise AtlanticError("Ecriture refusee (HTTP 403)")


DEFAULT_DEVICE = {
    "device_id": DEVICE_ID,
    "name": "Clim",
    "enabled": 1,
    "device_type": "atlantic",
    "cap_mode": 7,
    "cap_setpoint": 40,
    "cap_setpoint_cool": 177,
    "cap_ambient": 117,
    "cap_prog": 184,
    "cap_fan": 100801,
    "cap_louver": 100803,
    "cap_swing": 100804,
}

DESIRED = {"mode": "cool", "temp": 24, "mode_label": "Froid"}


def live_caps(overrides=None):
    caps = {
        7: "3",      # froid
        40: "24",
        177: "24",
        184: "0",
        100801: "5",  # auto
        100803: "4",
        100804: "0",
    }
    caps.update(overrides or {})
    return caps


def make_engine(settings=None, device=None, caps=None, client=None):
    store = FakeStore(settings, device)
    client = client or FakeClient()
    engine = Engine(store, client)
    engine.state = {int(store._device["device_id"]): dict(caps or live_caps())}
    engine.availability = {int(store._device["device_id"]): True}
    return engine, store, client


class DetectCapsTests(unittest.TestCase):
    def test_airflow_capabilities_detected(self):
        caps = detect_caps(
            {7: "3", 40: "24", 100801: "5", 100803: "4", 100804: "0"}
        )
        self.assertEqual(caps["cap_fan"], 100801)
        self.assertEqual(caps["cap_louver"], 100803)
        self.assertEqual(caps["cap_swing"], 100804)

    def test_missing_capabilities_are_zero(self):
        caps = detect_caps({7: "3", 40: "24"})
        self.assertEqual(caps["cap_fan"], 0)
        self.assertEqual(caps["cap_louver"], 0)
        self.assertEqual(caps["cap_swing"], 0)


class SettingsTests(unittest.TestCase):
    def test_climate_defaults_are_configurable(self):
        store = MemoryStore(None)
        self.assertEqual(store.settings()["atlantic_fan_default"], "")
        self.assertEqual(store.settings()["atlantic_airflow_default"], "")

        result = store.set_settings(
            {
                "atlantic_fan_default": "auto",
                "atlantic_airflow_default": "swing",
            }
        )
        self.assertEqual(result["atlantic_fan_default"], "auto")
        self.assertEqual(result["atlantic_airflow_default"], "swing")

    def test_device_capability_columns(self):
        store = MemoryStore(None)
        store.upsert_device(DEVICE_ID, "Clim")
        device = store.device(DEVICE_ID)
        self.assertEqual(device["cap_fan"], 0)
        self.assertEqual(device["cap_louver"], 0)
        self.assertEqual(device["cap_swing"], 0)

        device = store.update_device(
            DEVICE_ID,
            {"cap_fan": 100801, "cap_louver": 100803, "cap_swing": 100804},
        )
        self.assertEqual(device["cap_fan"], 100801)
        self.assertEqual(device["cap_louver"], 100803)
        self.assertEqual(device["cap_swing"], 100804)


class ApplyDefaultsTests(unittest.TestCase):
    def test_no_defaults_leaves_remote_values_alone(self):
        engine, _store, client = make_engine()
        self.assertFalse(engine._apply(dict(DEFAULT_DEVICE), DESIRED, force=True))
        self.assertEqual(client.writes, [])

    def test_fan_default_is_applied(self):
        engine, _store, client = make_engine(
            {"atlantic_fan_default": "auto"},
            caps=live_caps({100801: "1"}),
        )
        self.assertTrue(engine._apply(dict(DEFAULT_DEVICE), DESIRED, force=True))
        self.assertEqual(client.writes, [(100801, "5")])
        self.assertIn("soufflerie automatique", engine.last_action)

    def test_fan_already_conforming_is_not_rewritten(self):
        engine, _store, client = make_engine(
            {"atlantic_fan_default": "low"},
            caps=live_caps({100801: "1"}),
        )
        self.assertFalse(engine._apply(dict(DEFAULT_DEVICE), DESIRED, force=True))
        self.assertEqual(client.writes, [])

    def test_swing_default_is_applied(self):
        engine, _store, client = make_engine(
            {"atlantic_airflow_default": "swing"},
        )
        self.assertTrue(engine._apply(dict(DEFAULT_DEVICE), DESIRED, force=True))
        self.assertEqual(client.writes, [(100804, "1")])
        self.assertIn("orientation balayage", engine.last_action)

    def test_fixed_position_disables_swing_first(self):
        engine, _store, client = make_engine(
            {"atlantic_airflow_default": "pos3"},
            caps=live_caps({100804: "1"}),
        )
        self.assertTrue(engine._apply(dict(DEFAULT_DEVICE), DESIRED, force=True))
        self.assertEqual(client.writes, [(100804, "0"), (100803, "3")])

    def test_fixed_position_only_writes_the_louver(self):
        engine, _store, client = make_engine(
            {"atlantic_airflow_default": "pos3"},
        )
        self.assertTrue(engine._apply(dict(DEFAULT_DEVICE), DESIRED, force=True))
        self.assertEqual(client.writes, [(100803, "3")])

    def test_off_mode_does_not_touch_fan_or_louver(self):
        engine, _store, client = make_engine(
            {"atlantic_fan_default": "auto", "atlantic_airflow_default": "swing"},
            caps=live_caps({7: "0"}),
        )
        desired = {"mode": "off", "temp": 24, "mode_label": "ArrÃªt"}
        self.assertFalse(engine._apply(dict(DEFAULT_DEVICE), desired, force=True))
        self.assertEqual(client.writes, [])

    def test_device_without_fan_capability_is_skipped(self):
        device = dict(DEFAULT_DEVICE, cap_fan=0, cap_louver=0, cap_swing=0)
        engine, _store, client = make_engine(
            {"atlantic_fan_default": "high", "atlantic_airflow_default": "swing"},
            device=device,
        )
        self.assertFalse(engine._apply(dict(device), DESIRED, force=True))
        self.assertEqual(client.writes, [])

    def test_current_exposes_fan_and_airflow_labels(self):
        engine, _store, _client = make_engine(
            caps=live_caps({100801: "2", 100803: "3"}),
        )
        current = engine.current(DEVICE_ID)
        self.assertEqual(current["fan"], "med")
        self.assertEqual(current["fan_label"], "Moyenne")
        self.assertEqual(current["airflow"], "pos3")
        self.assertEqual(current["airflow_label"], "Position 3")


class ExecutionTimeoutTests(unittest.TestCase):
    """Un ordre accepte mais non confirme ne doit pas etre une erreur rouge."""

    def test_unconfirmed_write_is_a_warning(self):
        engine, store, client = make_engine(
            caps=live_caps({177: "20"}),
            client=TimeoutClient(),
        )
        self.assertTrue(engine._apply(dict(DEFAULT_DEVICE), DESIRED, force=True))
        self.assertEqual(client.writes, [(177, "24")])
        self.assertIsNone(engine.last_error)
        # Etat optimiste : pas de reecriture au tick suivant.
        self.assertEqual(engine.state[DEVICE_ID][177], "24")
        self.assertIn("confirmation non reçue", engine.last_action)
        levels = [level for level, _message, _device in store.logs]
        self.assertIn("avertissement", levels)
        self.assertNotIn("erreur", levels)

    def test_sequence_continues_after_unconfirmed_write(self):
        engine, _store, client = make_engine(
            caps=live_caps({177: "20", 184: "1"}),
            client=TimeoutClient(),
        )
        self.assertTrue(engine._apply(dict(DEFAULT_DEVICE), DESIRED, force=True))
        self.assertEqual(client.writes, [(184, "0"), (177, "24")])

    def test_real_write_error_still_fails(self):
        engine, store, client = make_engine(
            caps=live_caps({177: "20"}),
            client=FailingClient(),
        )
        self.assertFalse(engine._apply(dict(DEFAULT_DEVICE), DESIRED, force=True))
        self.assertEqual(client.writes, [(177, "24")])
        self.assertIn("consigne (capacite 177 = 24)", engine.last_error)
        levels = [level for level, _message, _device in store.logs]
        self.assertIn("erreur", levels)


class PollBackfillTests(unittest.TestCase):
    def test_poll_completes_missing_capability_columns(self):
        store = MemoryStore(None)
        store.upsert_device(DEVICE_ID, "Clim")
        store.update_device(
            DEVICE_ID,
            {"device_type": "atlantic", "cap_mode": 7, "cap_setpoint": 40},
        )

        class SetupClient:
            def get_setup(self):
                return {
                    "devices": [
                        {
                            "deviceId": DEVICE_ID,
                            "customName": "Clim",
                            "isAvailable": True,
                            "capabilities": [
                                {"capabilityId": 7, "value": "3"},
                                {"capabilityId": 40, "value": "24"},
                                {"capabilityId": 100801, "value": "5"},
                                {"capabilityId": 100803, "value": "4"},
                                {"capabilityId": 100804, "value": "0"},
                            ],
                        }
                    ]
                }

        engine = Engine(store, SetupClient())
        engine.poll()

        device = store.device(DEVICE_ID)
        self.assertEqual(device["cap_fan"], 100801)
        self.assertEqual(device["cap_louver"], 100803)
        self.assertEqual(device["cap_swing"], 100804)


class ModeSupportTests(unittest.TestCase):
    """Modes applicables par type d'appareil et délégation Wiser → clim."""

    def test_wiser_only_heats(self):
        self.assertTrue(Engine.mode_supported({"device_type": "wiser"}, "heat"))
        self.assertTrue(Engine.mode_supported({"device_type": "wiser"}, "off"))
        self.assertFalse(Engine.mode_supported({"device_type": "wiser"}, "cool"))
        self.assertFalse(Engine.mode_supported({"device_type": "wiser"}, "dry"))

    def test_atlantic_cool_requires_cool_capability(self):
        clim = {"device_type": "atlantic", "cap_setpoint_cool": 177}
        heat_only = {"device_type": "atlantic", "cap_setpoint_cool": 0}
        self.assertTrue(Engine.mode_supported(clim, "cool"))
        self.assertTrue(Engine.mode_supported(clim, "dry"))
        self.assertFalse(Engine.mode_supported(heat_only, "cool"))
        self.assertFalse(Engine.mode_supported(heat_only, "dry"))
        self.assertTrue(Engine.mode_supported(heat_only, "heat"))

    def _store_with_room(self):
        store = MemoryStore(None)
        store.upsert_device(9001, "Radiateur")
        store.upsert_device(1001, "Clim")
        room = store.add_room("Séjour test")
        store.update_room(room["id"], {"actuator_device_id": 9001})
        store.update_device(9001, {"room_id": room["id"], "device_type": "wiser"})
        store.update_device(
            1001,
            {
                "room_id": room["id"],
                "device_type": "atlantic",
                "cap_setpoint_cool": 177,
            },
        )
        days = [[] for _ in range(7)]
        days[0] = [{"time": "00:00", "mode": "cool", "temp": 24}]
        for week in ("A", "B"):
            store.set_slots(room_id=room["id"], week=week, days=days)
        engine = Engine(store, FakeClient())
        engine.state = {}
        engine.availability = {}
        return store, engine

    def test_wiser_delegates_cool_to_clim(self):
        store, engine = self._store_with_room()
        now = datetime(2026, 11, 9, 10, 0)  # lundi
        desired, source, _until = engine.desired_for(store.device(9001), now)
        self.assertIsNone(desired)
        self.assertEqual(source, "délégué")

        desired, source, _until = engine.desired_for(store.device(1001), now)
        self.assertIsNotNone(desired)
        self.assertEqual(desired["mode"], "cool")
        self.assertEqual(desired["temp"], 24)
        self.assertTrue(source.startswith("planning"))

    def test_unsupported_mode_is_ignored_without_other_device(self):
        store = MemoryStore(None)
        store.upsert_device(9001, "Radiateur")
        room = store.add_room("Pièce seule")
        store.update_room(room["id"], {"actuator_device_id": 9001})
        store.update_device(9001, {"room_id": room["id"], "device_type": "wiser"})
        days = [[] for _ in range(7)]
        days[0] = [{"time": "00:00", "mode": "cool", "temp": 24}]
        for week in ("A", "B"):
            store.set_slots(room_id=room["id"], week=week, days=days)
        engine = Engine(store, FakeClient())
        engine.state = {}
        engine.availability = {}
        desired, source, _until = engine.desired_for(
            store.device(9001), datetime(2026, 11, 9, 10, 0)
        )
        self.assertIsNone(desired)
        self.assertIn("non pris en charge", source)


class ConstantsTests(unittest.TestCase):
    def test_mappings(self):
        self.assertEqual(FAN_VALUES, {"low": "1", "med": "2", "high": "3", "auto": "5"})
        self.assertEqual(
            AIRFLOW_POSITIONS,
            {f"pos{i}": str(i) for i in range(1, 7)},
        )


class HeatingCurveAdvanceTests(unittest.TestCase):
    """Avance à la consigne sur une montée imminente (courbe de chauffe)."""

    def test_advance_uses_next_setpoint_then_reverts_when_disabled(self):
        store = MemoryStore(None)
        store.upsert_device(9001, "Radiateur chambre")
        room = store.add_room("Chambre courbe")
        store.update_room(
            room["id"],
            {
                "actuator_device_id": 9001,
                "heating_curve": True,
                "curve_fit": {"tau0": 4.0, "tau1": 0.0, "n": 10},
            },
        )
        store.update_device(9001, {"room_id": room["id"], "device_type": "wiser"})

        # Lundi : 17 °C jusqu'à 06:30, puis montée à 20 °C.
        days = [[] for _ in range(7)]
        days[0] = [
            {"time": "00:00", "mode": "heat", "temp": 17},
            {"time": "06:30", "mode": "heat", "temp": 20},
        ]
        for week in ("A", "B"):
            store.set_slots(room_id=room["id"], week=week, days=days)

        engine = Engine(store, FakeClient())
        engine.wiser_rooms = {1: {"setpoint": 17.0, "temp": 16.0}}

        # 06:10 : la montée est dans 20 min, l'avance (τ=4 h, bornée à 120 min)
        # démarre donc immédiatement avec la consigne suivante.
        now = datetime(2026, 11, 9, 6, 10)
        desired, source, _until = engine.desired_for(store.device(9001), now)
        self.assertIsNotNone(desired)
        self.assertEqual(desired["temp"], 20.0)
        self.assertIn("avance", source)

        # Toggle off : retour au comportement d'avant (consigne courante).
        store.update_room(room["id"], {"heating_curve": False})
        desired, source, _until = engine.desired_for(store.device(9001), now)
        self.assertIsNotNone(desired)
        self.assertEqual(desired["temp"], 17.0)
        self.assertNotIn("avance", source)


class PresenceTests(unittest.TestCase):
    """Mode Présent/Absent : l'absence domine override et planning."""

    NOW = datetime(2026, 11, 9, 10, 0)  # lundi

    def _engine(self, device, settings=None):
        store = FakeStore(settings, device)
        return Engine(store, FakeClient()), store

    def test_absent_wiser_defaults_to_off_at_10(self):
        device = dict(DEFAULT_DEVICE, device_id=9001, device_type="wiser")
        engine, _store = self._engine(device, {"presence_mode": "absent"})
        desired, source, until = engine.desired_for(device, self.NOW)
        self.assertEqual(desired, {"mode": "off", "mode_label": "Hors gel", "temp": 10.0})
        self.assertEqual(source, "absence")
        self.assertIsNone(until)

    def test_absent_wiser_uses_custom_frost_temp_not_wiser_frost_temp(self):
        device = dict(DEFAULT_DEVICE, device_id=9001, device_type="wiser", frost_temp=12)
        engine, _store = self._engine(device, {"presence_mode": "absent"})
        desired, _source, _until = engine.desired_for(device, self.NOW)
        self.assertEqual(desired["temp"], 12.0)
        self.assertNotEqual(desired["temp"], 6.0)

    def test_absent_atlantic_defaults_to_heat_at_16(self):
        device = dict(DEFAULT_DEVICE, frost_temp=None, frost_mode=None)
        engine, _store = self._engine(device, {"presence_mode": "absent"})
        desired, source, _until = engine.desired_for(device, self.NOW)
        self.assertEqual(desired, {"mode": "heat", "mode_label": "Chaud", "temp": 16.0})
        self.assertEqual(source, "absence")

    def test_absent_atlantic_honours_custom_frost_mode_and_temp(self):
        device = dict(DEFAULT_DEVICE, frost_temp=24, frost_mode="cool")
        engine, _store = self._engine(device, {"presence_mode": "absent"})
        desired, _source, _until = engine.desired_for(device, self.NOW)
        self.assertEqual(desired, {"mode": "cool", "mode_label": "Froid", "temp": 24.0})

    def test_absence_dominates_active_override_then_present_restores_it(self):
        store = MemoryStore(None)
        store.upsert_device(9001, "Radiateur")
        store.update_device(9001, {"device_type": "wiser"})
        store.set_override(9001, "heat", 21, datetime(2027, 1, 1))
        engine = Engine(store, FakeClient())

        store.set_settings({"presence_mode": "absent"})
        desired, source, _until = engine.desired_for(store.device(9001), self.NOW)
        self.assertEqual(desired["temp"], 10.0)
        self.assertEqual(source, "absence")

        store.set_settings({"presence_mode": "present"})
        desired, source, _until = engine.desired_for(store.device(9001), self.NOW)
        self.assertEqual(desired["temp"], 21.0)
        self.assertEqual(source, "override")

    def test_absence_then_present_restores_planning(self):
        store = MemoryStore(None)
        store.upsert_device(9001, "Radiateur")
        store.update_device(9001, {"device_type": "wiser"})
        days = [[] for _ in range(7)]
        days[0] = [{"time": "00:00", "mode": "heat", "temp": 19}]
        for week in ("A", "B"):
            store.set_slots(device_id=9001, week=week, days=days)
        engine = Engine(store, FakeClient())

        store.set_settings({"presence_mode": "absent"})
        desired, source, _until = engine.desired_for(store.device(9001), self.NOW)
        self.assertEqual(desired["temp"], 10.0)
        self.assertEqual(source, "absence")

        store.set_settings({"presence_mode": "present"})
        desired, source, _until = engine.desired_for(store.device(9001), self.NOW)
        self.assertEqual(desired["temp"], 19.0)
        self.assertTrue(source.startswith("planning"))

    def test_default_settings_leave_planning_unchanged(self):
        store = MemoryStore(None)
        store.upsert_device(9001, "Radiateur")
        store.update_device(9001, {"device_type": "wiser"})
        self.assertEqual(store.settings()["presence_mode"], "present")
        days = [[] for _ in range(7)]
        days[0] = [{"time": "00:00", "mode": "heat", "temp": 19}]
        for week in ("A", "B"):
            store.set_slots(device_id=9001, week=week, days=days)
        engine = Engine(store, FakeClient())
        desired, source, _until = engine.desired_for(store.device(9001), self.NOW)
        self.assertEqual(desired["temp"], 19.0)
        self.assertTrue(source.startswith("planning"))

    def test_snapshot_next_is_none_during_absence_only(self):
        store = MemoryStore(None)
        store.upsert_device(9001, "Radiateur")
        store.update_device(9001, {"device_type": "wiser"})
        engine = Engine(store, FakeClient())

        class Upcoming:
            def as_dict(self):
                return {"describe": "Chaud 20 °C"}

        with mock.patch.object(engine, "next_change", return_value=Upcoming()) as upcoming:
            store.set_settings({"presence_mode": "absent"})
            snapshot = engine.snapshot()
            self.assertIsNone(snapshot["devices"][0]["next"])
            self.assertEqual(snapshot["devices"][0]["desired"]["temp"], 10.0)
            upcoming.assert_not_called()

            store.set_settings({"presence_mode": "present"})
            snapshot = engine.snapshot()
            self.assertEqual(snapshot["devices"][0]["next"], {"describe": "Chaud 20 °C"})
            upcoming.assert_called_once()


class NextChangeWeekBoundaryTests(unittest.TestCase):
    """Le prochain changement ne melange pas les semaines au passage A/B."""

    NOW = datetime(2026, 11, 22, 13, 12)  # dimanche, semaine A (ISO 47)

    def _store(self):
        store = MemoryStore(None)
        store.set_settings({"week_mode": "parity"})
        store.upsert_device(9003, "Clim séjour")
        room = store.add_room("Séjour semaine")
        store.update_room(room["id"], {"actuator_device_id": 9003})
        store.update_device(
            9003,
            {"room_id": room["id"], "device_type": "atlantic", "temp_min": 12},
        )
        return store, room

    @staticmethod
    def _days(sunday, monday=None):
        days = [[] for _ in range(7)]
        days[6] = list(sunday)
        days[0] = list(monday or [])
        return days

    def _grid_b(self, store, room):
        store.set_slots(
            room_id=room["id"],
            week="B",
            days=self._days(
                [
                    {"time": "09:00", "mode": "cool", "temp": 19},
                    {"time": "23:30", "mode": "cool", "temp": 16.5},
                ],
                [{"time": "00:00", "mode": "cool", "temp": 13}],
            ),
        )

    def test_sunday_evening_takes_next_week_monday_not_sunday(self):
        """Repro live : dimanche 13:12 A -> lundi 00:00 B (13 °C)."""
        store, room = self._store()
        store.set_slots(
            room_id=room["id"],
            week="A",
            days=self._days(
                [{"time": "00:00", "mode": "cool", "temp": 13}],
                [
                    {"time": "00:00", "mode": "cool", "temp": 17},
                    {"time": "06:30", "mode": "cool", "temp": 19},
                ],
            ),
        )
        self._grid_b(store, room)

        upcoming = Engine(store, FakeClient()).next_change(store.device(9003), self.NOW)

        self.assertIsNotNone(upcoming)
        self.assertEqual(upcoming.week, "B")
        self.assertEqual(upcoming.slot["at"], datetime(2026, 11, 23, 0, 0))
        self.assertEqual(upcoming.slot["temp"], 13.0)
        self.assertNotEqual(upcoming.slot["at"], datetime(2026, 11, 22, 23, 30))
        self.assertNotEqual(upcoming.slot["temp"], 16.5)

    def test_no_slot_left_this_week_still_validates_other_week(self):
        """Branche « plus de créneau cette semaine » : dimanche B ignore."""
        store, room = self._store()
        self._grid_b(store, room)

        upcoming = Engine(store, FakeClient()).next_change(store.device(9003), self.NOW)

        self.assertIsNotNone(upcoming)
        self.assertEqual(upcoming.week, "B")
        self.assertEqual(upcoming.slot["at"], datetime(2026, 11, 23, 0, 0))
        self.assertEqual(upcoming.slot["temp"], 13.0)

    def test_valid_slot_later_today_is_kept(self):
        """Garde-fou : un créneau valide de la semaine courante reste servi."""
        store, room = self._store()
        store.set_slots(
            room_id=room["id"],
            week="A",
            days=self._days([{"time": "23:00", "mode": "cool", "temp": 15}]),
        )
        self._grid_b(store, room)

        upcoming = Engine(store, FakeClient()).next_change(store.device(9003), self.NOW)

        self.assertIsNotNone(upcoming)
        self.assertEqual(upcoming.week, "A")
        self.assertEqual(upcoming.slot["at"], datetime(2026, 11, 22, 23, 0))
        self.assertEqual(upcoming.slot["temp"], 15.0)


if __name__ == "__main__":
    unittest.main()

