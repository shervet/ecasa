"""Tests de l'API santé serveur (/api/system/health)."""

from __future__ import annotations

import io
import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ecasa import main  # noqa: E402


class _HealthHandler(main.Handler):
    """Handler HTTP minimal : pas de socket, réponses capturées."""

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


class HealthApiTests(unittest.TestCase):
    def setUp(self):
        self.app = SimpleNamespace(
            store=SimpleNamespace(storage_info=lambda: {"ok": True}),
            engine=SimpleNamespace(
                _thread=None,
                mock=False,
                last_poll=datetime(2026, 11, 24, 12, 0, 0),
                client=SimpleNamespace(token_expires_in=lambda: 123),
            ),
        )
        main._POWER_STATUS_CACHE["ts"] = 0.0
        main._POWER_STATUS_CACHE["data"] = None

    def call(self, method: str, path: str):
        handler = _HealthHandler(self.app, method, path)
        handler._api(method, path)
        return handler.responses[-1]

    @mock.patch.object(main, "update_state_snapshot", return_value={"available": False})
    @mock.patch.object(main, "load_sync_state", return_value={"pronote": "2026-11-24"})
    @mock.patch.object(
        main,
        "load_config",
        return_value={
            "pronote_enabled": True,
            "pronote_auto_enabled": True,
            "pronote_sync_minutes": 180,
        },
    )
    @mock.patch("ecasa.main.subprocess.run")
    def test_power_includes_undervoltage_history_bit16(
        self,
        run_mock,
        _load_config,
        _load_sync_state,
        _update_snapshot,
    ):
        run_mock.return_value = SimpleNamespace(returncode=0, stdout="throttled=0x10000\n")

        status, payload = self.call("GET", "/api/system/health")

        self.assertEqual(status, 200)
        self.assertTrue(payload["power"]["supported"])
        self.assertTrue(payload["power"]["undervoltage_history"])
        self.assertIn("Historique : sous-tension détectée", payload["power"]["incidents"])

    @mock.patch.object(main, "update_state_snapshot", return_value={"available": False})
    @mock.patch.object(main, "load_sync_state", return_value={})
    @mock.patch.object(main, "load_config", return_value={})
    @mock.patch(
        "ecasa.main.subprocess.run",
        side_effect=TimeoutError("vcgencmd timeout"),
    )
    def test_power_unsupported_when_vcgencmd_fails(
        self,
        _run_mock,
        _load_config,
        _load_sync_state,
        _update_snapshot,
    ):
        status, payload = self.call("GET", "/api/system/health")

        self.assertEqual(status, 200)
        self.assertFalse(payload["power"]["supported"])
        self.assertIn("power", payload)

    def test_legacy_health_format_is_kept(self):
        status, payload = self.call("GET", "/api/health")

        self.assertEqual(status, 200)
        self.assertEqual(payload["ok"], True)
        self.assertIn("app", payload)
        self.assertIn("version", payload)


if __name__ == "__main__":
    unittest.main()
