"""Tests de la veille des mises à jour eCasa (sans git ni réseau).

Les fonctions de vérification sont simulées : on valide le cache disque,
l'invalidation après application d'une mise à jour et l'avertissement unique
dans le journal.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ecasa import main  # noqa: E402


class FakeStore:
    def __init__(self):
        self.entries = []

    def log(self, level, message, device_id=None):  # noqa: ARG002
        self.entries.append((level, message))


class UpdateWatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "update-check.json"
        main._UPDATE_STATE = None
        self.addCleanup(setattr, main, "_UPDATE_STATE", None)

    @staticmethod
    def result(**kwargs):
        base = {
            "available": False,
            "behind": 0,
            "current": "aaaa111",
            "remote": "",
            "branch": "main",
            "error": None,
        }
        base.update(kwargs)
        return base

    def test_warning_logged_once_per_remote(self):
        store = FakeStore()
        res = self.result(available=True, behind=2, remote="bbbb222")
        main._record_update_state(res, store=store, path=self.path)
        main._record_update_state(res, store=store, path=self.path)
        self.assertEqual(len(store.entries), 1)
        level, message = store.entries[0]
        self.assertEqual(level, "avertissement")
        self.assertIn("Mise à jour disponible", message)
        self.assertIn("2 commit", message)
        self.assertIn("Réglages", message)

    def test_new_remote_logs_again(self):
        store = FakeStore()
        main._record_update_state(
            self.result(available=True, behind=1, remote="bbbb222"),
            store=store, path=self.path,
        )
        main._record_update_state(
            self.result(available=True, behind=3, remote="cccc333"),
            store=store, path=self.path,
        )
        self.assertEqual(len(store.entries), 2)

    def test_up_to_date_clears_notification(self):
        store = FakeStore()
        main._record_update_state(
            self.result(available=True, behind=1, remote="bbbb222"),
            store=store, path=self.path,
        )
        state = main._record_update_state(
            self.result(), store=store, path=self.path
        )
        self.assertFalse(state["available"])
        self.assertEqual(state["notified_remote"], "")
        self.assertEqual(len(store.entries), 1)

    def test_snapshot_exposes_status(self):
        main._record_update_state(
            self.result(available=True, behind=4, remote="bbbb222"),
            path=self.path,
        )
        snap = main.update_state_snapshot()
        self.assertTrue(snap["available"])
        self.assertEqual(snap["behind"], 4)
        self.assertEqual(snap["remote"], "bbbb222")
        self.assertEqual(snap["current"], "aaaa111")
        self.assertEqual(snap["branch"], "main")
        self.assertTrue(snap["checked_at"])

    def test_refresh_uses_fresh_cache(self):
        main._record_update_state(self.result(), path=self.path)
        with mock.patch.object(main, "ecasa_current_commit", return_value="aaaa111"), \
                mock.patch.object(
                    main, "ecasa_update_check",
                    side_effect=AssertionError("git ne doit pas être appelé"),
                ):
            state = main.refresh_update_state(path=self.path)
        self.assertFalse(state["available"])

    def test_refresh_checks_when_stale(self):
        main._record_update_state(
            self.result(), now=datetime.now() - timedelta(days=2), path=self.path
        )
        with mock.patch.object(main, "ecasa_current_commit", return_value="aaaa111"), \
                mock.patch.object(
                    main, "ecasa_update_check",
                    return_value=self.result(available=True, behind=1, remote="bbbb222"),
                ) as check:
            state = main.refresh_update_state(path=self.path)
        self.assertEqual(check.call_count, 1)
        self.assertTrue(state["available"])
        self.assertEqual(state["behind"], 1)

    def test_refresh_invalidates_cache_after_update(self):
        main._record_update_state(
            self.result(available=True, behind=1, remote="bbbb222"),
            path=self.path,
        )
        with mock.patch.object(main, "ecasa_current_commit", return_value="dddd444"), \
                mock.patch.object(
                    main, "ecasa_update_check",
                    return_value=self.result(current="dddd444"),
                ) as check:
            state = main.refresh_update_state(path=self.path)
        self.assertEqual(check.call_count, 1)
        self.assertFalse(state["available"])

    def test_refresh_survives_network_error(self):
        with mock.patch.object(main, "ecasa_current_commit", return_value="aaaa111"), \
                mock.patch.object(
                    main, "ecasa_update_check", side_effect=OSError("réseau")
                ):
            state = main.refresh_update_state(path=self.path)
        self.assertFalse(state["available"])
        self.assertIn("réseau", state["error"] or "")


if __name__ == "__main__":
    unittest.main()
