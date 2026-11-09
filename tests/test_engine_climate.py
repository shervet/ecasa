"""Reglages par defaut de la soufflerie et de l'orientation (clim Atlantic).

Capacites Cozytouch utilisees :
    100801 ROOM_stFanSpeed              (1 basse, 2 moyenne, 3 haute, 5 auto)
    100803 ROOM_stLouverAirFlowDirection (1 a 6)
    100804 ROOM_bLouverSwingMode         (1 = balayage)
"""

import unittest

from mecasa.engine import (
    AIRFLOW_POSITIONS,
    FAN_VALUES,
    Engine,
    detect_caps,
)
from mecasa.storage import MemoryStore

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


def make_engine(settings=None, device=None, caps=None):
    store = FakeStore(settings, device)
    client = FakeClient()
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


class ConstantsTests(unittest.TestCase):
    def test_mappings(self):
        self.assertEqual(FAN_VALUES, {"low": "1", "med": "2", "high": "3", "auto": "5"})
        self.assertEqual(
            AIRFLOW_POSITIONS,
            {f"pos{i}": str(i) for i in range(1, 7)},
        )


if __name__ == "__main__":
    unittest.main()

