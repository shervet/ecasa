"""Tests des endpoints web ajoutés : température annuelle, périodes, liaisons."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ecasa import main  # noqa: E402
from ecasa.storage import MemoryStore  # noqa: E402

DAYS = [
    [
        {"time": "07:00", "mode": "heat", "temp": 20.0},
        {"time": "20:00", "mode": "heat", "temp": 20.0},
    ]
    if index in (0, 4)
    else []
    for index in range(7)
]


class _ApiHandler(main.Handler):
    """Handler HTTP minimal : aucune socket, réponses capturées."""

    def __init__(self, app, method, path, body=b""):
        self.server = SimpleNamespace(app=app)
        self.client_address = ("127.0.0.1", 0)
        self.command = method
        self.path = path
        self.headers = {"Content-Length": str(len(body))}
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.responses = []

    def _json(self, status, payload):
        self.responses.append((status, payload))


class _ApiTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.store = MemoryStore(
            str(root / "state.json"),
            str(root / "history"),
            structural_delay=0.05,
        )
        self.addCleanup(self.store.close)
        self.saved = self.store.set_week_models([
            {"name": "Confort", "days": {"A": DAYS, "B": DAYS}},
        ])
        self.model_id = self.saved[0]["id"]

        self.weather = mock.Mock()
        self.weather.cached_hours.return_value = {
            date.today().isoformat(): {0: 12.0},
        }
        self.app = SimpleNamespace(
            store=self.store,
            engine=SimpleNamespace(tick=mock.Mock()),
            weather=self.weather,
        )

        patcher = mock.patch.object(
            main,
            "load_config",
            return_value={
                "weather_enabled": True,
                "weather_latitude": 48.85,
                "weather_longitude": 2.35,
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, method, path, body=None):
        raw = b"" if body is None else json.dumps(body).encode("utf-8")
        handler = _ApiHandler(self.app, method, path, raw)
        handler._api(method, path.split("?", 1)[0])
        return handler.responses[-1]


class WebApiTests(_ApiTestBase):
    def test_annual_temperature_endpoint(self):
        status, payload = self.call(
            "GET", "/api/stats/temperature?range=year"
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["range"], "year")
        self.assertEqual(payload["avg_c"], 12.0)
        self.assertEqual(payload["coverage_days"], 1)
        self.assertEqual(payload["source"], "weather")

    def test_annual_temperature_rejects_other_range(self):
        status, payload = self.call(
            "GET", "/api/stats/temperature?range=month"
        )
        self.assertEqual(status, 400)
        self.assertIn("year", payload["error"])

    def test_week_stats_endpoint_fills_temperature_buckets(self):
        room = self.store.add_room("Chambre test")
        device_id = 9002
        self.store.upsert_device(device_id, "Chambre (Wiser)")
        self.store.update_room(room["id"], {"actuator_device_id": device_id})
        self.store.update_device(
            device_id,
            {"room_id": room["id"], "device_type": "wiser"},
        )

        # Semaine du lundi 16 au dimanche 22 novembre : le jeudi 20 n'a
        # aucune mesure et doit rester a None.
        rows = []
        hours = {}
        for day in range(16, 23):
            if day == 20:
                continue
            day_key = f"2026-11-{day:02d}"
            hours[day_key] = {8: 10.0 + day, 9: 12.0 + day}
            for hour, ambient in ((8, 2.0 + day), (9, 4.0 + day)):
                rows.append(
                    {
                        "device_id": device_id,
                        "ts": f"{day_key}T{hour:02d}:00:00",
                        "ambient": ambient,
                        "setpoint": 19.0,
                    }
                )
        self.store.add_samples(rows)
        self.weather.hourly.return_value = SimpleNamespace(
            hours=hours,
            source="fake-meteo",
            latitude=48.85,
            longitude=2.35,
        )

        status, payload = self.call(
            "GET",
            f"/api/stats?range=week&room_id={room['id']}&date=2026-11-18",
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["start"], "2026-11-16")
        self.assertEqual(payload["end"], "2026-11-22")
        buckets = {item["date"]: item for item in payload["buckets"]}
        self.assertEqual(len(payload["buckets"]), 7)

        monday = buckets["2026-11-16"]
        self.assertEqual(monday["ambient"], 19.0)
        self.assertEqual(monday["setpoint"], 19.0)
        self.assertEqual(monday["outside"], 27.0)
        self.assertEqual(monday["count"], 2)

        tuesday = buckets["2026-11-17"]
        self.assertEqual(tuesday["ambient"], 20.0)
        self.assertEqual(tuesday["outside"], 28.0)

        empty = buckets["2026-11-20"]
        self.assertIsNone(empty["ambient"])
        self.assertIsNone(empty["setpoint"])
        self.assertIsNone(empty["outside"])
        self.assertEqual(empty["count"], 0)

    def test_energy_rejects_invalid_period_count(self):
        for value in ("6", "0", "13", "abc"):
            status, payload = self.call(
                "GET", f"/api/energy?view=week&periods={value}"
            )
            self.assertEqual(status, 400, value)
            self.assertIn("periods", payload["error"])

    def test_link_endpoint_copies_then_detach_keeps_copy(self):
        status, payload = self.call(
            "PUT", "/api/model-links/1", {"model_id": self.model_id}
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            payload["links"]["1"]["model_id"], self.model_id
        )
        copied = self.store.slots(room_id=1, week="A")
        self.assertEqual(len(copied), 4)
        self.assertTrue(self.store.room_matches_model(1, self.model_id))

        status, payload = self.call("DELETE", "/api/model-links/1")
        self.assertEqual(status, 200)
        self.assertNotIn("1", payload["links"])
        self.assertEqual(self.store.slots(room_id=1, week="A"), copied)


class SchoolApiTests(_ApiTestBase):
    """Endpoints du calendrier scolaire, des vacances et du préchauffage."""

    def test_absence_window_is_saved_and_cleared(self):
        status, payload = self.call(
            "PUT",
            "/api/settings",
            {
                "presence_mode": "present",
                "absence_start": "2026-10-17",
                "absence_until": "2026-11-01",
            },
        )
        self.assertEqual(status, 200)
        settings = payload["settings"]
        self.assertEqual(settings["absence_start"], "2026-10-17")
        self.assertEqual(settings["absence_until"], "2026-11-01")

        status, payload = self.call(
            "PUT", "/api/settings", {"presence_mode": "present"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["settings"]["absence_start"], "")
        self.assertEqual(payload["settings"]["absence_until"], "")

    def test_absence_window_rejects_bad_dates(self):
        status, payload = self.call(
            "PUT", "/api/settings", {"absence_start": "17/10/2026"}
        )
        self.assertEqual(status, 400)
        self.assertIn("date", payload["error"].lower())

        status, payload = self.call(
            "PUT",
            "/api/settings",
            {"absence_start": "2026-11-02", "absence_until": "2026-11-01"},
        )
        self.assertEqual(status, 400)

    def test_preheat_cap_is_validated(self):
        status, payload = self.call(
            "PUT", "/api/settings", {"preheat_max_minutes": "90"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["settings"]["preheat_max_minutes"], "90")

        status, _payload = self.call(
            "PUT", "/api/settings", {"preheat_max_minutes": "999"}
        )
        self.assertEqual(status, 400)

    def test_school_calendar_endpoint(self):
        fake = mock.Mock()
        fake.ensure.return_value = [
            {"name": "Vacances de la Toussaint", "start": "2026-10-17",
             "end": "2026-11-02"},
        ]
        fake.ranges.return_value = fake.ensure.return_value
        fake.next_holiday.return_value = {
            "date": "2026-10-17",
            "name": "Vacances de la Toussaint",
            "kind": "school",
        }
        with mock.patch.object(
            main, "school_calendar_service", return_value=fake
        ):
            status, payload = self.call("GET", "/api/school-calendar?zone=A")
        self.assertEqual(status, 200)
        self.assertEqual(payload["zone"], "A")
        self.assertEqual(len(payload["ranges"]), 1)
        self.assertEqual(payload["next"]["name"], "Vacances de la Toussaint")

    def test_school_calendar_requires_zone(self):
        status, payload = self.call("GET", "/api/school-calendar")
        self.assertEqual(status, 200)
        self.assertEqual(payload["zone"], "")

    def test_room_preheat_toggle_is_persisted(self):
        room = self.store.add_room("Salon test")
        updated = self.store.update_room(room["id"], {"preheat": True})
        self.assertTrue(updated["preheat"])
        updated = self.store.update_room(room["id"], {"preheat": False})
        self.assertFalse(updated["preheat"])


if __name__ == "__main__":
    unittest.main()
