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


class WebApiTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
