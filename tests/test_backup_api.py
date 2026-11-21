"""Tests de l'API de sauvegarde/restauration complète (zip)."""

from __future__ import annotations

import base64
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ecasa import main  # noqa: E402


class _BackupHandler(main.Handler):
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
        self.downloads = []

    def _json(self, status, payload):
        self.responses.append((status, payload))

    def _download(self, status, body, content_type, filename):
        self.downloads.append({
            "status": status,
            "body": body,
            "content_type": content_type,
            "filename": filename,
        })

    def _schedule_shutdown(self, restart):
        self.responses.append(("restart", restart))


class BackupApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "data" / "history").mkdir(parents=True)
        self.config = {
            "email": "test@example.com",
            "password": "secret-api",
            "energy_api_key": "clef-enedis",
        }
        (self.root / "config.json").write_text(
            json.dumps(self.config), encoding="utf-8"
        )
        (self.root / "data" / "state.json").write_text(
            json.dumps({
                "settings": {"week_mode": "explicit"},
                "rooms": [],
            }),
            encoding="utf-8",
        )
        (self.root / "data" / "energy.json").write_text(
            json.dumps({"days": []}), encoding="utf-8"
        )
        (self.root / "data" / "history" / "history-2026-01.jsonl").write_text(
            '{"t": 1}\n', encoding="utf-8"
        )

        self.original_cache = main._config_cache
        main._config_cache = None
        self.addCleanup(setattr, main, "_config_cache", self.original_cache)
        for patch in (
            mock.patch.object(main, "ROOT", self.root),
            mock.patch.object(main, "CONFIG_PATH", self.root / "config.json"),
            mock.patch.object(main, "DATA_DIR", self.root / "data"),
        ):
            patch.start()
            self.addCleanup(patch.stop)

        self.store = mock.Mock()
        self.app = SimpleNamespace(
            store=self.store,
            engine=SimpleNamespace(stop=mock.Mock(), _thread=None),
            daily_sync=SimpleNamespace(stop=mock.Mock()),
        )

    def get_backup(self):
        handler = _BackupHandler(self.app, "GET", "/api/config/backup")
        handler._api("GET", "/api/config/backup")
        return handler

    def post_restore(self, archive):
        body = json.dumps({
            "data": base64.b64encode(archive).decode("ascii"),
        }).encode("utf-8")
        handler = _BackupHandler(
            self.app, "POST", "/api/config/restore", body
        )
        handler._api("POST", "/api/config/restore")
        return handler

    def test_backup_zip_contains_config_state_and_history(self):
        handler = self.get_backup()

        self.assertEqual(len(handler.downloads), 1)
        download = handler.downloads[0]
        self.assertEqual(download["status"], 200)
        self.assertEqual(download["content_type"], "application/zip")
        self.assertRegex(
            download["filename"],
            r"^ecasa-config-\d{4}-\d{2}-\d{2}-\d{6}\.zip$",
        )

        with zipfile.ZipFile(io.BytesIO(download["body"])) as archive:
            names = set(archive.namelist())
            self.assertIn("config.json", names)
            self.assertIn("data/state.json", names)
            self.assertIn("data/energy.json", names)
            self.assertTrue(
                any(name.startswith("data/history/") for name in names)
            )
            payload = json.loads(archive.read("config.json"))
            self.assertEqual(payload["password"], "secret-api")
            self.assertEqual(payload["energy_api_key"], "clef-enedis")

    def test_restore_replaces_files_and_creates_backup(self):
        exported = self.get_backup().downloads[0]["body"]

        (self.root / "config.json").write_text(
            json.dumps({"email": "changed@example.com"}), encoding="utf-8"
        )
        (self.root / "data" / "state.json").write_text(
            json.dumps({"settings": {"week_mode": "none"}}), encoding="utf-8"
        )
        (self.root / "data" / "history" / "history-2026-01.jsonl").write_text(
            "changed\n", encoding="utf-8"
        )

        handler = self.post_restore(exported)

        self.assertEqual(handler.responses[0][0], 200)
        payload = handler.responses[0][1]
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["restart_required"])
        self.assertEqual(handler.responses[-1], ("restart", True))

        backup_dir = self.root / payload["backup_dir"]
        self.assertTrue((backup_dir / "config.json").is_file())
        self.assertEqual(
            json.loads((backup_dir / "config.json").read_text())["email"],
            "changed@example.com",
        )

        restored_config = json.loads(
            (self.root / "config.json").read_text()
        )
        self.assertEqual(restored_config["email"], "test@example.com")
        self.assertEqual(restored_config["password"], "secret-api")
        restored_state = json.loads(
            (self.root / "data" / "state.json").read_text()
        )
        self.assertEqual(
            restored_state["settings"]["week_mode"], "explicit"
        )
        self.assertEqual(
            (self.root / "data" / "history" / "history-2026-01.jsonl").read_text(),
            '{"t": 1}\n',
        )
        self.assertEqual(
            main.load_config(force=True)["email"], "test@example.com"
        )

    def test_restore_rejects_invalid_archive(self):
        handler = self.post_restore(b"ceci n'est pas un zip")

        self.assertEqual(handler.responses[0][0], 400)
        self.assertIn("error", handler.responses[0][1])
        self.assertNotIn(
            "restart", [item[0] for item in handler.responses]
        )

    def test_restore_rejects_too_large_body(self):
        with mock.patch.object(main, "BACKUP_MAX_BYTES", 10):
            handler = self.post_restore(b"x" * 11)

        self.assertEqual(handler.responses[0][0], 413)
        self.assertIn("error", handler.responses[0][1])


if __name__ == "__main__":
    unittest.main()
