"""Tests de la synchro quotidienne (électricité + Pronote) et des services.

Aucune connexion réseau : l'énergie est simulée, l'import Pronote est
remplacé par un espion, et les services externes par des fonctions factices.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mecasa import external_services, main  # noqa: E402
from mecasa.energy import cache_ttl, next_sync_time  # noqa: E402
from mecasa.storage import MemoryStore  # noqa: E402


class SyncTimeTests(unittest.TestCase):
    def test_next_sync_before_three(self):
        now = datetime(2026, 11, 10, 2, 30)
        self.assertEqual(next_sync_time(now), datetime(2026, 11, 10, 3, 0))
        self.assertEqual(cache_ttl(now), 30 * 60)

    def test_next_sync_after_three(self):
        now = datetime(2026, 11, 10, 3, 0)
        self.assertEqual(next_sync_time(now), datetime(2026, 11, 11, 3, 0))
        self.assertEqual(cache_ttl(now), 24 * 3600)

    def test_next_sync_at_night(self):
        now = datetime(2026, 11, 10, 23, 0)
        self.assertEqual(next_sync_time(now), datetime(2026, 11, 11, 3, 0))
        self.assertEqual(cache_ttl(now), 4 * 3600)


class DailySyncDueTests(unittest.TestCase):
    def test_before_three_is_not_due(self):
        now = datetime(2026, 11, 10, 2, 59)
        self.assertFalse(main.daily_sync_due(now, {}))

    def test_empty_state_is_due(self):
        now = datetime(2026, 11, 10, 3, 0)
        self.assertTrue(main.daily_sync_due(now, {}))

    def test_done_today_is_not_due(self):
        now = datetime(2026, 11, 10, 10, 0)
        self.assertFalse(
            main.daily_sync_due(
                now, {"energy": "2026-11-10", "pronote": "2026-11-10"}
            )
        )

    def test_only_one_task_done_is_due(self):
        now = datetime(2026, 11, 10, 10, 0)
        self.assertTrue(main.daily_sync_due(now, {"energy": "2026-11-10"}))

    def test_no_retry_same_day_after_failure(self):
        # Une tentative ratée est comptée : la tâche attend le lendemain
        # (ou une synchro manuelle), sans nouvelle tentative automatique.
        now = datetime(2026, 11, 10, 10, 0)
        state = {"energy": "2026-11-10", "pronote": "2026-11-10"}
        self.assertFalse(main.daily_sync_due(now, state))
        self.assertFalse(main.daily_sync_due(now + timedelta(hours=5), state))

    def test_previous_day_is_due(self):
        now = datetime(2026, 11, 10, 3, 5)
        self.assertTrue(
            main.daily_sync_due(
                now, {"energy": "2026-11-09", "pronote": "2026-11-09"}
            )
        )


class FakeEnergy:
    def __init__(self, values=None, error=None):
        self.values = values if values is not None else {}
        self.error = error
        self.calls = []

    def daily(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error is not None:
            raise self.error
        return dict(self.values)


class FakeApp:
    def __init__(self, store, energy):
        self.store = store
        self.energy = energy


CONFIG = {
    "med_enabled": True,
    "med_url": "https://med.example",
    "med_token": "token",
    "med_pdl": "12345678901234",
    "med_start": "",
    "pronote_room_id": 1,
    "pronote_url": "https://pronote.example",
    "pronote_username": "eleve",
    "pronote_password": "secret",
}


class DailySyncRunTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.store = MemoryStore(
            str(base / "state.json"), str(base / "history")
        )
        self.addCleanup(self.store.close)
        self.state_path = base / "sync.json"
        self.config = dict(CONFIG)

    def run_sync(self, now, energy):
        app = FakeApp(self.store, energy)
        with mock.patch.object(
            main, "load_config", lambda force=False: self.config
        ), mock.patch.object(
            main,
            "run_pronote_auto_import",
            mock.Mock(return_value={"ok": True, "room_id": 1}),
        ) as pronote:
            result = main.run_daily_sync(
                app, now=now, state_path=self.state_path
            )
        return result, pronote

    def test_runs_once_per_day(self):
        energy = FakeEnergy(values={"2026-11-09": 8.0})
        now = datetime(2026, 11, 10, 3, 5)
        result, pronote = self.run_sync(now, energy)
        self.assertTrue(result["ok"])
        self.assertEqual(len(energy.calls), 1)
        self.assertEqual(pronote.call_count, 1)
        state = main.load_sync_state(self.state_path)
        self.assertEqual(state["energy"], "2026-11-10")
        self.assertEqual(state["pronote"], "2026-11-10")

        # Deuxième passage le même jour : plus aucun appel.
        result, pronote = self.run_sync(
            datetime(2026, 11, 10, 9, 30), energy
        )
        self.assertTrue(result["ok"])
        self.assertEqual(len(energy.calls), 1)
        self.assertEqual(pronote.call_count, 0)
        self.assertIsNone(result["energy"])
        self.assertIsNone(result["pronote"])

    def test_next_day_runs_again(self):
        energy = FakeEnergy(values={"2026-11-10": 9.0})
        self.run_sync(datetime(2026, 11, 10, 3, 5), energy)
        self.run_sync(datetime(2026, 11, 11, 3, 5), energy)
        self.assertEqual(len(energy.calls), 2)

    def test_failure_is_not_retried_same_day(self):
        energy = FakeEnergy(error=RuntimeError("réseau indisponible"))
        now = datetime(2026, 11, 10, 3, 5)
        result, _ = self.run_sync(now, energy)
        self.assertFalse(result["ok"])
        state = main.load_sync_state(self.state_path)
        self.assertEqual(state["energy"], "2026-11-10")
        self.assertIn("réseau", state["energy_error"])

        result, _ = self.run_sync(datetime(2026, 11, 10, 8, 0), energy)
        self.assertEqual(len(energy.calls), 1)

    def test_not_configured_skips_energy(self):
        self.config = {**self.config, "med_enabled": False}
        energy = FakeEnergy()
        result, _ = self.run_sync(datetime(2026, 11, 10, 3, 5), energy)
        self.assertTrue(result["ok"])
        self.assertEqual(energy.calls, [])
        self.assertEqual(result["energy"]["skipped"], "not_configured")


class ImportRoomTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.store = MemoryStore(
            str(base / "state.json"), str(base / "history")
        )
        self.addCleanup(self.store.close)
        self.room = self.store.rooms()[0]

    def test_import_writes_room_and_keeps_weekend(self):
        room_id = self.room["id"]
        # Week-end personnalisé déjà présent dans la pièce.
        weekend = [
            {"time": "08:00", "mode": "heat", "temp": 19.0},
            {"time": "22:00", "mode": "off", "temp": 16.0},
        ]
        days = [[] for _ in range(7)]
        days[5] = weekend
        self.store.set_slots(room_id=room_id, week="A", days=days)

        plan_days = [
            [{"time": "07:00", "mode": "heat", "temp": 20.0}]
            for _ in range(5)
        ] + [[], []]
        with mock.patch.object(
            main, "build_pronote_plan", return_value={"days": plan_days}
        ):
            starts = main.import_pronote_weeks(
                self.store,
                CONFIG,
                room_id,
                date(2026, 11, 9),
                ["A"],
                room=True,
                preview_days=plan_days,
                preview_type="A",
            )

        self.assertEqual(starts, {"A": "2026-11-09"})
        slots = self.store.slots(room_id=room_id, week="A")
        self.assertTrue(slots)
        self.assertTrue(all(slot["room_id"] == room_id for slot in slots))
        # Samedi (weekday 5) : les créneaux personnalisés sont conservés.
        saturday = [slot for slot in slots if slot["weekday"] == 5]
        self.assertEqual([slot["minutes"] for slot in saturday], [480, 1320])
        # Aucun créneau ne fuit sur un appareil.
        for slot in slots:
            self.assertIn(slot.get("device_id"), (None, ""))

    def test_import_device_target(self):
        self.store.upsert_device(42, "Test Device")
        device = next(d for d in self.store.devices() if d["device_id"] == 42)
        plan_days = [
            [{"time": "07:00", "mode": "heat", "temp": 20.0}]
            for _ in range(7)
        ]
        main.import_pronote_weeks(
            self.store,
            CONFIG,
            int(device["device_id"]),
            date(2026, 11, 9),
            ["B"],
            preview_days=plan_days,
            preview_type="B",
        )
        slots = self.store.slots(int(device["device_id"]), "B")
        self.assertTrue(slots)
        self.assertTrue(
            all(slot.get("room_id") in (None, "") for slot in slots)
        )


class ExternalServicesTests(unittest.TestCase):
    def test_node_exec_detection(self):
        self.assertTrue(external_services.decide_node_exec("node server.js"))
        self.assertTrue(
            external_services.decide_node_exec("/usr/bin/nodejs app.js")
        )
        self.assertTrue(
            external_services.decide_node_exec("env PORT=3000 node-red")
        )
        self.assertFalse(
            external_services.decide_node_exec("/usr/bin/python3 app.py")
        )
        self.assertFalse(external_services.decide_node_exec(""))

    def test_med_container_matching(self):
        names = ["myelectricaldata", "med", "med-api", "nginx", "mecasa"]
        self.assertEqual(
            external_services.matching_med_containers(names),
            ["myelectricaldata", "med", "med-api"],
        )

    def test_med_local_detection(self):
        self.assertTrue(
            external_services.med_is_local(
                {"med_url": "http://127.0.0.1:8080"}
            )
        )
        self.assertTrue(
            external_services.med_is_local(
                {"med_url": "http://192.168.1.20:8080"}
            )
        )
        self.assertFalse(
            external_services.med_is_local(
                {"med_url": "https://www.myelectricaldata.fr"}
            )
        )
        self.assertFalse(external_services.med_is_local({"med_url": ""}))

    def test_orchestrator_collects_results(self):
        with mock.patch.object(
            external_services,
            "restart_pm2",
            return_value={"name": "pm2", "attempted": True, "ok": True,
                          "detail": "ok"},
        ), mock.patch.object(
            external_services,
            "restart_node_systemd_units",
            return_value=[
                {"name": "systemd node.service", "attempted": True,
                 "ok": True, "detail": "ok"}
            ],
        ), mock.patch.object(
            external_services,
            "restart_med_container",
            return_value={"name": "MyElectricalData", "attempted": True,
                          "ok": False, "detail": "absent"},
        ):
            results = external_services.restart_external_services({})
        names = [item["name"] for item in results]
        self.assertEqual(names, ["pm2", "systemd node.service",
                                 "MyElectricalData"])

    def test_orchestrator_tolerates_failures(self):
        with mock.patch.object(
            external_services,
            "restart_pm2",
            return_value={"name": "pm2", "attempted": False, "ok": True,
                          "detail": "absent"},
        ), mock.patch.object(
            external_services,
            "restart_node_systemd_units",
            side_effect=RuntimeError("systemctl absent"),
        ), mock.patch.object(
            external_services, "restart_med_container", return_value=None
        ):
            results = external_services.restart_external_services({})
        self.assertEqual(results[0]["name"], "pm2")
        self.assertEqual(results[1]["name"], "systemd (Node)")
        self.assertFalse(results[1]["ok"])


if __name__ == "__main__":
    unittest.main()
