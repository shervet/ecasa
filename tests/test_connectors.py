"""Connecteurs Daikin / Bosch / Netatmo / Mitsubishi : catalogue et réglages."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mecasa import connectors  # noqa: E402


class CatalogTests(unittest.TestCase):
    def test_catalog_lists_the_four_systems(self):
        ids = [item["id"] for item in connectors.catalog()]
        self.assertEqual(ids, ["daikin", "bosch", "netatmo", "mitsubishi"])

    def test_catalog_exposes_fields_without_values(self):
        for item in connectors.catalog():
            self.assertTrue(item["label"])
            self.assertIn(item["status"], ("beta", "planned"))
            for field in item["fields"]:
                self.assertIn("key", field)
                self.assertIn("label", field)
                self.assertNotIn("value", field)

    def test_every_connector_has_at_least_one_field(self):
        for item in connectors.CONNECTORS:
            self.assertGreaterEqual(len(item["fields"]), 1)


class SettingsTests(unittest.TestCase):
    def test_defaults_cover_every_field(self):
        defaults = connectors.default_settings()
        for item in connectors.CONNECTORS:
            self.assertEqual(
                set(defaults[item["id"]]),
                {field["key"] for field in item["fields"]},
            )

    def test_public_settings_masks_secrets(self):
        config = {
            "connectors": {"mitsubishi": {"email": "a@b.c", "password": "s3cret"}}
        }
        public = connectors.public_settings(config)
        self.assertEqual(public["mitsubishi"]["email"], "a@b.c")
        self.assertTrue(public["mitsubishi"]["password_set"])
        self.assertNotIn("password", public["mitsubishi"])
        self.assertFalse(public["bosch"]["token_set"])

    def test_merge_keeps_secret_when_left_empty(self):
        current = {"mitsubishi": {"email": "a@b.c", "password": "s3cret"}}
        merged = connectors.merge_settings(
            {"mitsubishi": {"email": "a@b.c", "password": ""}}, current
        )
        self.assertEqual(merged["mitsubishi"]["password"], "s3cret")
        self.assertEqual(merged["mitsubishi"]["email"], "a@b.c")

    def test_merge_replaces_secret_when_provided(self):
        merged = connectors.merge_settings(
            {"netatmo": {"client_secret": "nouveau"}}, {}
        )
        self.assertEqual(merged["netatmo"]["client_secret"], "nouveau")

    def test_merge_drops_empty_and_unknown_entries(self):
        self.assertEqual(connectors.merge_settings({"nope": {"x": "1"}}, {}), {})
        self.assertEqual(
            connectors.merge_settings({"bosch": {"host": "", "token": ""}}, {}), {}
        )

    def test_merge_rejects_non_dict(self):
        with self.assertRaises(ValueError):
            connectors.merge_settings("nope", {})


class TestConnectorTests(unittest.TestCase):
    def test_unknown_connector(self):
        result = connectors.test_connector("nope", {})
        self.assertFalse(result["ok"])
        self.assertIn("inconnu", result["error"])

    def test_daikin_without_host_reports_a_clear_error(self):
        result = connectors.test_connector("daikin", {})
        self.assertFalse(result["ok"])
        self.assertIn("Adresse", result["error"])

    def test_daikin_unreachable_host_fails_without_raising(self):
        result = connectors.test_connector(
            "daikin", {"daikin": {"host": "127.0.0.1:9"}}, timeout=1
        )
        self.assertFalse(result["ok"])
        self.assertIn("injoignable", result["error"])

    def test_planned_connectors_explain_the_status(self):
        for connector_id in ("bosch", "netatmo", "mitsubishi"):
            result = connectors.test_connector(connector_id, {})
            self.assertFalse(result["ok"])
            self.assertIn("pilote", result["message"])


if __name__ == "__main__":
    unittest.main()
