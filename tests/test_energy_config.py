"""Tests de la configuration MyElectricalData (validation, secrets)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mecasa import main  # noqa: E402
from mecasa.energy import MAX_HISTORY_DAYS  # noqa: E402

TODAY = date.today()


class ConfigTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "config.json"
        self.path.write_text(
            json.dumps({
                "med_token": "ancien-token",
                "med_pdl": "11111111111111",
            }),
            encoding="utf-8",
        )
        self.original_path = main.CONFIG_PATH
        self.original_cache = main._config_cache
        main.CONFIG_PATH = self.path
        main._config_cache = None
        self.addCleanup(self._restore)

    def _restore(self):
        main.CONFIG_PATH = self.original_path
        main._config_cache = self.original_cache

    def save(self, body):
        return main.save_config(body)[0]


class SaveConfigTests(ConfigTestCase):
    def test_empty_token_keeps_existing_one(self):
        updated = self.save({"med_pdl": "22222222222222"})
        self.assertEqual(updated["med_token"], "ancien-token")
        self.assertEqual(updated["med_pdl"], "22222222222222")

    def test_token_clear_removes_token(self):
        updated = self.save({"med_token_clear": True})
        self.assertEqual(updated["med_token"], "")

    def test_new_token_wins_over_clear(self):
        updated = self.save({"med_token": "nouveau-token", "med_token_clear": True})
        self.assertEqual(updated["med_token"], "nouveau-token")

    def test_invalid_pdl_is_rejected(self):
        with self.assertRaises(ValueError):
            self.save({"med_pdl": "1234"})

    def test_invalid_url_is_rejected(self):
        with self.assertRaises(ValueError):
            self.save({"med_url": "ftp://med.example"})

    def test_future_start_is_rejected(self):
        future = (TODAY + timedelta(days=1)).isoformat()
        with self.assertRaises(ValueError):
            self.save({"med_start": future})

    def test_start_older_than_enedis_limit_is_rejected(self):
        too_old = (TODAY - timedelta(days=MAX_HISTORY_DAYS)).isoformat()
        with self.assertRaises(ValueError):
            self.save({"med_start": too_old})

    def test_start_at_enedis_limit_is_accepted(self):
        oldest = (TODAY - timedelta(days=MAX_HISTORY_DAYS - 1)).isoformat()
        updated = self.save({"med_start": oldest})
        self.assertEqual(updated["med_start"], oldest)


class PublicConfigTests(unittest.TestCase):
    def test_token_is_never_exposed(self):
        public = main.public_config({
            "med_enabled": True,
            "med_url": "https://med.example",
            "med_token": "secret",
            "med_pdl": "12345678901234",
            "med_start": "2024-01-01",
        })
        self.assertTrue(public["med_token_set"])
        self.assertNotIn("med_token", public)
        self.assertEqual(public["med_pdl"], "12345678901234")


class MergeEnergyConfigTests(ConfigTestCase):
    def test_merge_does_not_write_file(self):
        before = self.path.read_text(encoding="utf-8")
        config = main.merge_energy_config(
            {"med_token": "ancien-token"},
            {
                "med_url": "https://autre.example/",
                "med_pdl": "12345678901234",
                "med_start": "2024-01-01",
                "med_token": "nouveau",
            },
        )
        self.assertEqual(config["med_url"], "https://autre.example")
        self.assertEqual(config["med_token"], "nouveau")
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_merge_rejects_invalid_pdl(self):
        with self.assertRaises(ValueError):
            main.merge_energy_config({}, {"med_pdl": "abc"})

    def test_merge_keeps_token_when_blank(self):
        config = main.merge_energy_config(
            {"med_token": "ancien-token"}, {"med_token": ""}
        )
        self.assertEqual(config["med_token"], "ancien-token")


if __name__ == "__main__":
    unittest.main()
