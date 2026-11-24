"""Tests du prechauffage intelligent.

``_advance_desired`` n'est pas reecrit ici : on isole la decision (montee
imminente, plafond d'avance) en remplacant la lecture capteur et la meteo.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ecasa import heating_curve  # noqa: E402
from ecasa.engine import Engine  # noqa: E402

DEVICE_ID = 28129468
NOW = datetime(2026, 11, 24, 6, 30)

DEVICE = {
    "device_id": DEVICE_ID,
    "name": "Radiateur",
    "device_type": "atlantic",
    "enabled": 1,
    "cap_mode": 7,
    "cap_setpoint": 40,
}

ROOM = {"id": 1, "name": "Salon", "actuator_device_id": DEVICE_ID, "preheat": True}


class StubStore:
    def __init__(self, settings=None, rooms=None, slots=None):
        self._settings = dict(settings or {})
        self._rooms = [dict(room) for room in (rooms or [])]
        self._slots = slots or {}

    def settings(self):
        return dict(self._settings)

    def rooms(self):
        return [dict(room) for room in self._rooms]

    def slots(self, device_id=None, week=None, room_id=None):
        key = ("room", room_id, week) if room_id is not None else (device_id, week)
        return [dict(slot) for slot in self._slots.get(key, [])]

    def override(self, device_id):
        return None

    def week_overrides(self):
        return {}

    def devices(self):
        return [dict(DEVICE)]

    def log(self, *args, **kwargs):
        pass


class FakeClient:
    def write_capability(self, *args, **kwargs):
        return True


def make_engine(settings=None, rooms=None, slots=None):
    store = StubStore(settings, rooms, slots)
    return Engine(store, FakeClient()), store


def rise(in_minutes, temp=20.0, mode="heat"):
    return {"in_minutes": in_minutes, "temp": temp, "mode": mode, "upcoming": None}


class AdvanceDesiredTests(unittest.TestCase):
    def call(self, engine, room=None, rise_value=None, t_room=18.0, t_out=5.0,
             delta=120.0):
        room = room or dict(ROOM)

        def fake_advance(tau0, tau1, outside, inside, target, eps=0.2,
                         cap_minutes=120):
            return min(delta, float(cap_minutes))

        with mock.patch.object(engine, "_next_rise", return_value=rise_value), \
             mock.patch.object(engine, "_room_current_temp", return_value=t_room), \
             mock.patch.object(engine, "_outside_temp_at", return_value=t_out), \
             mock.patch.object(
                 heating_curve, "advance_minutes", side_effect=fake_advance
             ) as advance:
            result = engine._advance_desired(
                DEVICE, room, {"mode": "heat", "temp": 18.0}, [], NOW
            )
        return result, advance

    def test_advances_before_an_upcoming_rise(self):
        engine, _store = make_engine({"preheat_max_minutes": "180"})
        result, _advance = self.call(engine, rise_value=rise(60))
        self.assertEqual(result["mode"], "heat")
        self.assertEqual(result["temp"], 20.0)

    def test_no_advance_when_rise_is_far(self):
        engine, _store = make_engine({"preheat_max_minutes": "180"})
        result, _advance = self.call(engine, rise_value=rise(240))
        self.assertIsNone(result)

    def test_max_advance_caps_the_lead(self):
        engine, _store = make_engine({"preheat_max_minutes": "30"})
        result, advance = self.call(engine, rise_value=rise(60), delta=120.0)
        self.assertIsNone(result)
        self.assertEqual(advance.call_args.kwargs["cap_minutes"], 30)

    def test_zero_max_advance_disables_preheat(self):
        engine, _store = make_engine({"preheat_max_minutes": "0"})
        result, advance = self.call(engine, rise_value=rise(10), delta=120.0)
        self.assertIsNone(result)
        self.assertEqual(advance.call_args.kwargs["cap_minutes"], 0)

    def test_invalid_max_advance_falls_back_to_default(self):
        engine, _store = make_engine({"preheat_max_minutes": "nope"})
        result, _advance = self.call(engine, rise_value=rise(60))
        self.assertIsNotNone(result)

    def test_missing_room_temp_blocks_advance(self):
        engine, _store = make_engine({})
        result, _advance = self.call(engine, rise_value=rise(10), t_room=None)
        self.assertIsNone(result)

    def test_no_rise_means_no_advance(self):
        engine, _store = make_engine({})
        result, _advance = self.call(engine, rise_value=None)
        self.assertIsNone(result)

    def test_default_model_is_used_without_curve(self):
        engine, _store = make_engine({})
        _result, advance = self.call(engine, rise_value=rise(60))
        args = advance.call_args.args
        self.assertEqual(args[0], 3.0)  # tau0 par defaut
        self.assertEqual(args[1], 0.0)  # tau1 par defaut


class DesiredForPreheatTests(unittest.TestCase):
    def build(self, room):
        slots = {("room", 1, "A"): [{"weekday": 0, "minutes": 0, "mode": "heat", "temp": 18.0}],
                 ("room", 1, "B"): [{"weekday": 0, "minutes": 0, "mode": "heat", "temp": 18.0}]}
        engine, store = make_engine({"week_match": "parity"}, rooms=[room], slots=slots)
        return engine, store

    def test_preheat_room_uses_advance_path(self):
        engine, _store = self.build(dict(ROOM))
        advanced = {"mode": "heat", "mode_label": "Chauffage", "temp": 20.0}
        with mock.patch.object(engine, "_advance_desired", return_value=advanced) as fn:
            desired, source, _until = engine.desired_for(DEVICE, NOW)
        fn.assert_called_once()
        self.assertEqual(desired["temp"], 20.0)
        self.assertTrue(source.endswith("(avance)"))

    def test_plain_room_skips_advance(self):
        room = {"id": 1, "name": "Salon", "actuator_device_id": DEVICE_ID}
        engine, _store = self.build(room)
        with mock.patch.object(engine, "_advance_desired") as fn:
            desired, source, _until = engine.desired_for(DEVICE, NOW)
        fn.assert_not_called()
        self.assertEqual(desired["temp"], 18.0)
        self.assertNotIn("avance", source)


if __name__ == "__main__":
    unittest.main()
