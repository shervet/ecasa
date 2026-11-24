"""Tests de la synchro quotidienne (électricité + Pronote) et des services.

Aucune connexion réseau : l'énergie est simulée, l'import Pronote est
remplacé par un espion, et les services externes par des fonctions factices.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ecasa import external_services, main  # noqa: E402
from ecasa.energy import cache_ttl, next_sync_time  # noqa: E402
from ecasa.storage import MemoryStore  # noqa: E402


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

    def test_pronote_waits_for_configured_hour(self):
        # Électricité à 3 h, Pronote à 7 h : à 5 h seule l'énergie est due.
        now = datetime(2026, 11, 10, 5, 0)
        self.assertTrue(main.daily_sync_due(now, {}, pronote_minutes=7 * 60))
        self.assertFalse(
            main.daily_sync_due(
                now, {"energy": "2026-11-10"}, pronote_minutes=7 * 60
            )
        )
        self.assertTrue(
            main.daily_sync_due(
                now + timedelta(hours=2),
                {"energy": "2026-11-10"},
                pronote_minutes=7 * 60,
            )
        )

    def test_disabled_pronote_is_never_due(self):
        now = datetime(2026, 11, 10, 23, 0)
        self.assertFalse(
            main.daily_sync_due(
                now, {"energy": "2026-11-10"}, pronote_enabled=False
            )
        )


class PronoteRoomOverridesTests(unittest.TestCase):
    """Réglages confort/réduit par pièce (clim en froid, chambres en chaud)."""

    def test_valid_overrides_are_kept(self):
        cleaned = main._clean_room_overrides(
            {
                "1": {"comfort_mode": "cool", "comfort_temp": 24},
                "2": {"reduced_mode": "heat", "reduced_temp": 17.5},
            }
        )
        self.assertEqual(cleaned["1"], {"comfort_mode": "cool", "comfort_temp": 24.0})
        self.assertEqual(cleaned["2"], {"reduced_mode": "heat", "reduced_temp": 17.5})

    def test_empty_values_inherit_global_settings(self):
        cleaned = main._clean_room_overrides(
            {"3": {"comfort_mode": "", "comfort_temp": None}}
        )
        self.assertEqual(cleaned, {})

    def test_invalid_mode_and_temp_are_rejected(self):
        with self.assertRaises(ValueError):
            main._clean_room_overrides({"1": {"comfort_mode": "bogus"}})
        with self.assertRaises(ValueError):
            main._clean_room_overrides({"1": {"comfort_temp": 55}})
        with self.assertRaises(ValueError):
            main._clean_room_overrides("nope")

    def test_room_options_only_returns_defined_keys(self):
        config = {
            "pronote_room_overrides": {
                "1": {"comfort_mode": "cool", "reduced_mode": "off"},
            }
        }
        self.assertEqual(
            main.pronote_room_options(config, 1),
            {"comfort_mode": "cool", "reduced_mode": "off"},
        )
        self.assertEqual(main.pronote_room_options(config, 2), {})
        self.assertEqual(main.pronote_room_options({}, 1), {})


class MergePreservedSlotsTests(unittest.TestCase):
    """Import partiel : seule la portion gérée par Pronote est écrite."""

    @staticmethod
    def scopes(*values):
        values = list(values) + [("full", 0)] * (7 - len(values))
        return values[:7]

    def test_prefix_keeps_end_of_day(self):
        new = [
            [
                {"time": "00:00", "mode": "off", "temp": 16},
                {"time": "07:00", "mode": "heat", "temp": 20},
                {"time": "07:40", "mode": "off", "temp": 16},
            ],
            [], [], [], [], [], [],
        ]
        existing = [
            {"weekday": 0, "minutes": 7 * 60 + 30, "mode": "off", "temp": 16},
            {"weekday": 0, "minutes": 22 * 60, "mode": "heat", "temp": 19},
        ]
        merged = main._merge_preserved_slots(
            new, existing, self.scopes(("prefix", 3))
        )
        self.assertEqual(
            [slot["time"] for slot in merged[0]],
            ["00:00", "07:00", "07:40", "22:00"],
        )

    def test_suffix_keeps_start_of_day(self):
        new = [
            [
                {"time": "12:20", "mode": "heat", "temp": 20},
                {"time": "13:40", "mode": "off", "temp": 16},
            ],
            [], [], [], [], [], [],
        ]
        existing = [
            {"weekday": 0, "minutes": 0, "mode": "off", "temp": 16},
            {"weekday": 0, "minutes": 7 * 60, "mode": "heat", "temp": 20},
            {"weekday": 0, "minutes": 12 * 60 + 30, "mode": "off", "temp": 16},
        ]
        merged = main._merge_preserved_slots(
            new, existing, self.scopes(("suffix", 0))
        )
        self.assertEqual(
            [slot["time"] for slot in merged[0]],
            ["00:00", "07:00", "12:20", "13:40"],
        )

    def test_full_scope_replaces_the_day(self):
        new = [[{"time": "08:00", "mode": "heat", "temp": 20}], [], [], [], [], [], []]
        existing = [{"weekday": 0, "minutes": 0, "mode": "off", "temp": 16}]
        merged = main._merge_preserved_slots(new, existing, self.scopes())
        self.assertEqual([slot["time"] for slot in merged[0]], ["08:00"])

    def test_day_without_lessons_keeps_existing_planning(self):
        new = [[] for _ in range(7)]
        existing = [{"weekday": 2, "minutes": 0, "mode": "off", "temp": 16}]
        merged = main._merge_preserved_slots(new, existing, self.scopes())
        self.assertEqual([slot["time"] for slot in merged[2]], ["00:00"])

    def test_invalid_scopes_are_ignored(self):
        self.assertIsNone(main._clean_scopes("nope"))
        self.assertIsNone(main._clean_scopes([["full", 0]]))
        self.assertIsNone(main._clean_scopes([["bogus", 0]] * 7))
        self.assertEqual(
            main._clean_scopes([["prefix", 2]] * 7), [("prefix", 2)] * 7
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

    def test_import_replaces_school_day_completely(self):
        # Jour avec cours : le planning existant est remplacé intégralement
        # par le planning Pronote (même règle en import manuel et auto).
        room_id = self.room["id"]
        existing = [[
            {"time": "06:00", "mode": "heat", "temp": 20.0},
            {"time": "08:00", "mode": "heat", "temp": 20.0},
            {"time": "21:00", "mode": "off", "temp": 16.0},
        ]] + [[] for _ in range(6)]
        self.store.set_slots(room_id=room_id, week="A", days=existing)

        plan_days = [
            [
                {"time": "07:30", "mode": "heat", "temp": 20.0},
                {"time": "16:30", "mode": "off", "temp": 17.0},
            ]
        ] + [[] for _ in range(6)]
        main.import_pronote_weeks(
            self.store, dict(CONFIG), room_id, date(2026, 11, 9), ["A"],
            room=True, preview_days=plan_days, preview_type="A",
        )
        slots = self.store.slots(room_id=room_id, week="A")
        monday = sorted(
            slot["minutes"] for slot in slots if slot["weekday"] == 0
        )
        self.assertEqual(monday, [450, 990])

    def test_import_preserves_weekday_without_lessons(self):
        # Journée sans cours (mercredi) : jamais écrasée par l'import,
        # les créneaux manuels sont conservés tels quels.
        room_id = self.room["id"]
        existing = [[] for _ in range(7)]
        existing[2] = [
            {"time": "09:00", "mode": "heat", "temp": 21.0},
            {"time": "12:00", "mode": "off", "temp": 17.0},
        ]
        self.store.set_slots(room_id=room_id, week="A", days=existing)

        plan_days = [[], [], []] + [
            [{"time": "07:00", "mode": "heat", "temp": 20.0}]
        ] + [[], [], []]
        main.import_pronote_weeks(
            self.store, CONFIG, room_id, date(2026, 11, 9), ["A"],
            room=True, preview_days=plan_days, preview_type="A",
        )
        slots = self.store.slots(room_id=room_id, week="A")
        wednesday = sorted(
            (slot["minutes"], slot["mode"], slot["temp"])
            for slot in slots if slot["weekday"] == 2
        )
        self.assertEqual(
            wednesday, [(540, "heat", 21.0), (720, "off", 17.0)]
        )
        # Jeudi (avec cours) : importé normalement.
        thursday = [slot["minutes"] for slot in slots if slot["weekday"] == 3]
        self.assertEqual(thursday, [420])

    def test_import_preview_into_chosen_week(self):
        # L'aperçu d'une semaine A peut être écrit dans la semaine B.
        room_id = self.room["id"]
        plan_days = [
            [{"time": "07:00", "mode": "heat", "temp": 20.0}]
            for _ in range(7)
        ]
        starts = main.import_pronote_weeks(
            self.store, CONFIG, room_id, date(2026, 11, 9), ["B"],
            room=True, preview_days=plan_days, preview_type="A",
        )
        self.assertEqual(starts, {"B": "2026-11-09"})
        slots = self.store.slots(room_id=room_id, week="B")
        self.assertEqual(
            sorted({slot["minutes"] for slot in slots}), [420]
        )
        self.assertFalse(self.store.slots(room_id=room_id, week="A"))

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


class PronoteAutoImportWeeksTests(unittest.TestCase):
    """Import automatique limite aux semaines liees a Pronote."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.store = MemoryStore(
            str(base / "state.json"), str(base / "history")
        )
        self.addCleanup(self.store.close)
        self.room_id = self.store.rooms()[0]["id"]
        self.config_path = base / "config.json"
        self.config = {
            **CONFIG,
            "pronote_weeks": ["A"],
            "pronote_school_days": {
                str(self.room_id): {"A": [True] * 7, "B": [True] * 7}
            },
        }

    def run_auto_import(self):
        app = FakeApp(self.store, FakeEnergy())
        app.engine = mock.Mock()
        imported = mock.Mock(return_value={"A": "2026-11-09"})
        with mock.patch.object(
            main, "load_config", lambda force=False: self.config
        ), mock.patch.object(
            main, "CONFIG_PATH", self.config_path
        ), mock.patch.object(
            main, "import_pronote_weeks", imported
        ):
            result = main.run_pronote_auto_import(
                app, datetime(2026, 11, 9, 4, 0)
            )
        return result, imported, app

    def test_only_linked_week_is_imported(self):
        result, imported, _ = self.run_auto_import()
        self.assertTrue(result["ok"])
        imported.assert_called_once()
        # (store, config, room_id, monday, weeks, room=True)
        call_args = imported.call_args.args
        self.assertEqual(call_args[2], self.room_id)
        self.assertEqual(call_args[3], date(2026, 11, 9))
        self.assertEqual(call_args[4], ["A"])
        self.assertEqual(
            result["rooms"], {self.room_id: {"A": "2026-11-09"}}
        )

    def test_unlinked_week_school_days_are_reset(self):
        # La semaine B n'est plus liee a Pronote : ses jours de cours sont
        # remis a zero pour que l'interface la laisse modifiable.
        result, _, _ = self.run_auto_import()
        self.assertTrue(result["ok"])
        room_days = self.config["pronote_school_days"][str(self.room_id)]
        self.assertEqual(room_days["A"], [True] * 7)
        self.assertEqual(room_days["B"], [False] * 7)

    def test_without_link_setting_both_weeks_are_imported(self):
        self.config["pronote_weeks"] = []
        result, imported, _ = self.run_auto_import()
        self.assertTrue(result["ok"])
        self.assertEqual(imported.call_args.args[4], ["A", "B"])
        room_days = self.config["pronote_school_days"][str(self.room_id)]
        self.assertEqual(room_days["A"], [True] * 7)
        self.assertEqual(room_days["B"], [True] * 7)

    def test_pronote_disabled_skips_and_removes_locks(self):
        self.config["pronote_enabled"] = False
        result, imported, _ = self.run_auto_import()
        self.assertEqual(result, {"ok": False, "skipped": "disabled"})
        imported.assert_not_called()
        self.assertEqual(main._pronote_locked_days(self.config, self.room_id), {})


class _ConfigHandler(main.Handler):
    """Handler HTTP minimal : pas de socket, réponse capturée."""

    def __init__(self, app, body):
        self.server = mock.Mock()
        self.server.app = app
        self.client_address = ("127.0.0.1", 0)
        self.command = "PUT"
        self.path = "/api/config"
        self._body_value = body
        self.responses = []

    def _body(self):
        return self._body_value

    def _json(self, status, payload):
        self.responses.append((status, payload))


class _InlineThread:
    """Thread synchrone : le traitement d'arrière-plan est exécuté direct."""

    def __init__(self, target=None, args=(), kwargs=None, **_):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        self._target(*self._args, **self._kwargs)


class ConfigSwitchReimportTests(unittest.TestCase):
    """PUT /api/config : changer le jour d'alternance relance l'import."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.store = MemoryStore(
            str(base / "state.json"), str(base / "history")
        )
        self.addCleanup(self.store.close)
        self.room_id = 2
        self.config_path = base / "config.json"
        self.config_path.write_text(
            json.dumps({
                **CONFIG,
                "pronote_room_id": self.room_id,
                "pronote_room_ids": [self.room_id],
                "pronote_weeks": ["A"],
                "pronote_switch_day": "monday",
                "pronote_school_days": {
                    str(self.room_id): {
                        "A": [False] * 7,
                        "B": [False] * 7,
                    },
                },
            }),
            encoding="utf-8",
        )
        self.app = FakeApp(self.store, FakeEnergy())
        self.app.engine = mock.Mock()
        self._original_cache = main._config_cache
        main._config_cache = None
        self.addCleanup(setattr, main, "_config_cache", self._original_cache)

    def put_config(self, body):
        plan = {
            "days": [
                [{"time": "07:00", "mode": "heat", "temp": 20.0}]
                for _ in range(5)
            ] + [[], []],
            # Vendredi partiel : il reste éditable malgré des créneaux.
            "scopes": [
                ["full", 0], ["full", 0], ["full", 0], ["full", 0],
                ["prefix", 3], ["full", 0], ["full", 0],
            ],
        }
        handler = _ConfigHandler(self.app, body)
        with mock.patch.object(
            main, "CONFIG_PATH", self.config_path
        ), mock.patch.object(
            main, "build_pronote_plan", return_value=plan
        ), mock.patch.object(
            main.threading, "Thread", _InlineThread
        ):
            handler._api("PUT", "/api/config")
        return handler.responses

    def test_switch_day_change_reimports_school_days(self):
        responses = self.put_config({"pronote_switch_day": "friday"})

        self.assertEqual(responses[-1][0], 200)
        self.assertTrue(responses[-1][1]["ok"])
        config = main.load_config()
        self.assertEqual(config["pronote_switch_day"], "friday")
        self.assertEqual(
            config["pronote_school_days"][str(self.room_id)]["A"],
            [True, True, True, True, False, False, False],
        )
        self.assertEqual(self.store.settings()["week_switch_day"], "friday")

    def test_same_switch_day_does_not_reimport(self):
        with mock.patch.object(
            main, "run_pronote_auto_import"
        ) as reimport:
            responses = self.put_config({"pronote_switch_day": "monday"})

        self.assertEqual(responses[-1][0], 200)
        reimport.assert_not_called()
        self.assertEqual(
            main.load_config()["pronote_school_days"][str(self.room_id)]["A"],
            [False] * 7,
        )

    def test_weeks_change_reimports(self):
        with mock.patch.object(
            main, "run_pronote_auto_import"
        ) as reimport:
            responses = self.put_config({"pronote_weeks": ["A", "B"]})

        self.assertEqual(responses[-1][0], 200)
        reimport.assert_called_once()


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
        names = ["myelectricaldata", "med", "med-api", "nginx", "ecasa"]
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
