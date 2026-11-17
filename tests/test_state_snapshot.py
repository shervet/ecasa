"""Tests du snapshot /api/state (métadonnées consommées par l'accueil)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ecasa import main  # noqa: E402


class StateSnapshotTests(unittest.TestCase):
    def make_app(self, models):
        engine = SimpleNamespace(snapshot=lambda: {"rooms": [], "devices": []})
        store = SimpleNamespace(
            room_model_links=lambda: {"1": {"model_id": "model-1", "pending": False}},
            week_models=lambda: models,
        )
        return SimpleNamespace(engine=engine, store=store)

    def snapshot(self, models):
        with mock.patch.object(main, "update_state_snapshot", return_value={"available": False}), \
                mock.patch.object(main, "cpu_history_snapshot", return_value={}), \
                mock.patch.object(main, "disk_usage_snapshot", return_value={}):
            return main.state_snapshot(self.make_app(models))

    def test_exposes_lightweight_model_names(self):
        snap = self.snapshot([
            {"id": "model-1", "name": "Confort", "days": {"A": [[]] * 7, "B": [[]] * 7}},
            {"id": "model-2", "name": "", "days": {"A": [[]] * 7, "B": [[]] * 7}},
        ])
        self.assertEqual(
            snap["model_names"], {"model-1": "Confort", "model-2": "model-2"}
        )
        # Les plannings complets ne doivent pas transiter par le snapshot.
        self.assertNotIn("models", snap)
        self.assertIn("model_links", snap)

    def test_model_names_never_break_snapshot(self):
        def boom():
            raise RuntimeError("état illisible")

        app = self.make_app([])
        app.store.week_models = boom
        with mock.patch.object(main, "update_state_snapshot", return_value={"available": False}), \
                mock.patch.object(main, "cpu_history_snapshot", return_value={}), \
                mock.patch.object(main, "disk_usage_snapshot", return_value={}):
            snap = main.state_snapshot(app)
        self.assertEqual(snap["model_names"], {})


if __name__ == "__main__":
    unittest.main()
