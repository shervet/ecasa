"""Tests du stockage MeCasa : persistance, rechargement, mesures, journal."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mecasa.storage import MemoryStore  # noqa: E402


def week_days(template: dict | None = None, days=(0, 4)) -> list[list[dict]]:
    """Planning 7 jours : créneaux 07:00 et 20:00 sur les jours donnés."""
    template = template or {"mode": "heat", "temp": 20.0}
    return [
        [
            {"time": "07:00", **template},
            {"time": "20:00", **template},
        ]
        if index in days
        else []
        for index in range(7)
    ]


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_path = os.path.join(self._tmp.name, "state.json")
        self.history_dir = os.path.join(self._tmp.name, "history")

    def make_store(self, **kwargs) -> MemoryStore:
        store = MemoryStore(
            self.state_path,
            self.history_dir,
            structural_delay=kwargs.pop("structural_delay", 0.05),
            **kwargs,
        )
        self.addCleanup(store.close)
        return store


class PersistenceTests(StoreTestCase):
    def test_fresh_store_seeds_rooms(self):
        store = self.make_store()
        rooms = store.rooms()
        self.assertEqual([room["name"] for room in rooms], ["Salon", "Chambre", "Bureau"])

    def test_round_trip(self):
        store = self.make_store()
        store.set_settings({"week_mode": "explicit", "default_week": "B"})
        store.set_slots(room_id=1, week="A", days=week_days({"mode": "heat", "temp": 19}))
        store.set_slots(device_id=42, week="B", days=week_days({"mode": "cool", "temp": 24}))
        store.set_week_override("2026-11-02", "B")
        store.set_override(42, "cool", 23.5, datetime(2026, 11, 6, 18, 0, 0))
        store.log("info", "Démarrage MeCasa")
        store.close()
        self.assertTrue(os.path.isfile(self.state_path))

        reloaded = self.make_store()
        settings = reloaded.settings()
        self.assertEqual(settings["week_mode"], "explicit")
        self.assertEqual(settings["default_week"], "B")

        room_slots = reloaded.slots(room_id=1, week="A")
        self.assertEqual(len(room_slots), 4)
        self.assertEqual(room_slots[0]["mode"], "heat")
        self.assertEqual(room_slots[0]["temp"], 19.0)

        device_slots = reloaded.slots(device_id=42, week="B")
        self.assertEqual(len(device_slots), 4)
        self.assertEqual(device_slots[0]["mode"], "cool")

        self.assertEqual(reloaded.week_overrides(), {"2026-11-02": "B"})

        override = reloaded.override(42)
        self.assertEqual(override["mode"], "cool")
        self.assertEqual(override["temp"], 23.5)

        events = reloaded.events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["message"], "Démarrage MeCasa")

    def test_state_written_only_when_dirty(self):
        store = self.make_store()
        # L'amorçage initial (pièces par défaut) provoque une première écriture.
        store.flush(force=True)
        baseline = store.state_writes
        self.assertGreaterEqual(baseline, 1)
        # Rien n'a changé depuis : pas de nouvelle écriture.
        store.flush(force=True)
        self.assertEqual(store.state_writes, baseline)
        store.set_settings({"week_mode": "none"})
        store.flush(force=True)
        self.assertEqual(store.state_writes, baseline + 1)

    def test_no_persistence_when_paths_none(self):
        store = MemoryStore(None, None)
        self.addCleanup(store.close)
        store.set_settings({"week_mode": "none"})
        store.flush(force=True)  # ne doit pas lever
        self.assertEqual(store.state_writes, 0)

    def test_corrupt_state_reported(self):
        with open(self.state_path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        store = self.make_store()
        self.assertIsNotNone(store.load_error)
        # Le magasin reste utilisable avec les valeurs par défaut.
        self.assertEqual(store.rooms()[0]["name"], "Salon")


class SlotsTests(StoreTestCase):
    def test_requires_seven_days(self):
        store = self.make_store()
        with self.assertRaises(ValueError):
            store.set_slots(device_id=1, week="A", days=[[]] * 6)

    def test_rejects_bad_week(self):
        store = self.make_store()
        with self.assertRaises(ValueError):
            store.set_slots(device_id=1, week="C", days=week_days())

    def test_rejects_missing_target(self):
        store = self.make_store()
        with self.assertRaises(ValueError):
            store.set_slots(week="A", days=week_days())

    def test_rejects_unknown_mode(self):
        store = self.make_store()
        days = [[{"time": "07:00", "mode": "turbo", "temp": 20}]] + [[]] * 6
        with self.assertRaises(ValueError):
            store.set_slots(device_id=1, week="A", days=days)

    def test_temp_clamped(self):
        store = self.make_store()
        days = [[{"time": "07:00", "mode": "heat", "temp": 40}]] + [[]] * 6
        store.set_slots(device_id=1, week="A", days=days)
        self.assertEqual(store.slots(device_id=1, week="A")[0]["temp"], 30.0)

    def test_wiser_temp_min_zero_not_clamped_to_16(self):
        # Régression : temp_min 0.0 (Wiser) est falsy en Python — l'ancien
        # code replaçait à 16 °C même pour une pièce 100 % Wiser.
        store = self.make_store()
        store.upsert_device(501, "Chambre Hugo (Wiser)")
        store.update_device(501, {"device_type": "wiser"})
        self.assertEqual(store.device(501)["temp_min"], 0.0)
        days = [[{"time": "07:00", "mode": "heat", "temp": 15}]] + [[]] * 6
        store.set_slots(device_id=501, week="A", days=days)
        self.assertEqual(store.slots(device_id=501, week="A")[0]["temp"], 15.0)

    def test_atlantic_temp_min_still_clamped_to_16(self):
        store = self.make_store()
        store.upsert_device(502, "Clim salon")
        store.update_device(502, {"device_type": "atlantic"})
        days = [[{"time": "07:00", "mode": "heat", "temp": 15}]] + [[]] * 6
        store.set_slots(device_id=502, week="A", days=days)
        self.assertEqual(store.slots(device_id=502, week="A")[0]["temp"], 16.0)

    def test_replace_removes_previous_slots(self):
        store = self.make_store()
        store.set_slots(device_id=1, week="A", days=week_days())
        store.set_slots(device_id=1, week="A", days=[[] for _ in range(7)])
        self.assertEqual(store.slots(device_id=1, week="A"), [])
        # Les autres semaines restent intactes.
        store.set_slots(device_id=1, week="B", days=week_days())
        store.set_slots(device_id=1, week="A", days=[[] for _ in range(7)])
        self.assertEqual(len(store.slots(device_id=1, week="B")), 4)

    def test_sorted_by_weekday_then_time(self):
        store = self.make_store()
        days = [[] for _ in range(7)]
        days[0] = [{"time": "20:00", "mode": "heat", "temp": 20}]
        days[0].append({"time": "07:00", "mode": "cool", "temp": 24})
        days[3] = [{"time": "08:30", "mode": "auto", "temp": 21}]
        store.set_slots(device_id=1, week="A", days=days)
        slots = store.slots(device_id=1, week="A")
        self.assertEqual(
            [(slot["weekday"], slot["minutes"]) for slot in slots],
            [(0, 420), (0, 1200), (3, 510)],
        )

    def test_room_and_device_slots_independent(self):
        store = self.make_store()
        store.set_slots(room_id=2, week="A", days=week_days())
        store.set_slots(device_id=9, week="A", days=week_days())
        self.assertEqual(len(store.slots(room_id=2, week="A")), 4)
        self.assertEqual(len(store.slots(device_id=9, week="A")), 4)
        self.assertEqual(len(store.slots(room_id=1, week="A")), 0)


class OverrideTests(StoreTestCase):
    def test_set_and_clear(self):
        store = self.make_store()
        until = datetime.now() + timedelta(minutes=30)
        store.set_override(7, "heat", 21.0, until)
        override = store.override(7)
        self.assertEqual(override["mode"], "heat")
        self.assertEqual(override["temp"], 21.0)
        self.assertIn(7, store.overrides())
        store.clear_override(7)
        self.assertIsNone(store.override(7))

    def test_temp_clamped(self):
        store = self.make_store()
        until = datetime.now() + timedelta(minutes=30)
        store.set_override(7, "heat", 45, until)
        self.assertEqual(store.override(7)["temp"], 30.0)
        store.set_override(7, "heat", 2, until)
        self.assertEqual(store.override(7)["temp"], 16.0)

    def test_wiser_override_below_16_kept(self):
        # Régression : même piège falsy pour temp_min = 0.0 (Wiser).
        store = self.make_store()
        store.upsert_device(503, "Chambre Hugo (Wiser)")
        store.update_device(503, {"device_type": "wiser"})
        until = datetime.now() + timedelta(minutes=30)
        store.set_override(503, "heat", 14, until)
        self.assertEqual(store.override(503)["temp"], 14.0)

    def test_unknown_mode_rejected(self):
        store = self.make_store()
        with self.assertRaises(ValueError):
            store.set_override(7, "turbo", 20, datetime.now())


class WeekOverrideTests(StoreTestCase):
    def test_set_clear_and_validation(self):
        store = self.make_store()
        store.set_week_override("2026-11-02", "B")
        self.assertEqual(store.week_overrides(), {"2026-11-02": "B"})
        store.set_week_override("2026-11-02", None)
        self.assertEqual(store.week_overrides(), {})
        with self.assertRaises(ValueError):
            store.set_week_override("2026-11-02", "Z")


class SamplesTests(StoreTestCase):
    def test_hourly_average(self):
        store = self.make_store()
        base = datetime(2026, 11, 6, 8, 0, 0)
        self.assertTrue(store.add_sample(1, 20.0, 19.0, "heat", ts=base))
        self.assertTrue(store.add_sample(1, 22.0, 21.0, "heat", ts=base + timedelta(minutes=20)))
        rows = store.samples(1, base, base + timedelta(hours=1))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ambient"], 21.0)
        self.assertEqual(rows[0]["setpoint"], 20.0)
        self.assertEqual(rows[0]["mode"], "heat")

    def test_min_interval_blocks_close_samples(self):
        store = self.make_store()
        base = datetime(2026, 11, 6, 8, 0, 0)
        self.assertTrue(store.add_sample(1, 20.0, None, ts=base, min_interval=15))
        self.assertFalse(store.add_sample(1, 21.0, None, ts=base + timedelta(seconds=5), min_interval=15))
        self.assertTrue(store.add_sample(1, 22.0, None, ts=base + timedelta(seconds=16), min_interval=15))
        rows = store.samples(1, base, base + timedelta(hours=1))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ambient"], 21.0)  # (20 + 22) / 2

    def test_no_values_no_sample(self):
        store = self.make_store()
        self.assertFalse(store.add_sample(1, None, None, ts=datetime(2026, 11, 6, 8, 0, 0)))

    def test_samples_outside_window_ignored(self):
        store = self.make_store()
        base = datetime(2026, 11, 6, 8, 0, 0)
        store.add_sample(1, 20.0, None, ts=base)
        store.add_sample(1, 21.0, None, ts=base + timedelta(days=2))
        rows = store.samples(1, base, base + timedelta(hours=2))
        self.assertEqual(len(rows), 1)

    def test_add_samples_with_iso_strings(self):
        store = self.make_store()
        store.add_samples(
            [
                {"device_id": 3, "ts": "2026-11-06T09:10:00", "ambient": 19.5, "setpoint": 19.0, "mode": "heat"},
                {"device_id": 3, "ts": "2026-11-06T09:40:00", "ambient": 20.5, "setpoint": 19.0, "mode": "heat"},
                {"device_id": 3, "ts": "pas une date", "ambient": 99, "setpoint": 99},
            ]
        )
        rows = store.samples(3, datetime(2026, 11, 6, 9, 0), datetime(2026, 11, 6, 10, 0))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ambient"], 20.0)

    def test_sample_bounds(self):
        store = self.make_store()
        base = datetime(2026, 11, 6, 8, 0, 0)
        store.add_sample(5, 20.0, None, ts=base)
        store.add_sample(5, 21.0, None, ts=base + timedelta(hours=3))
        first, last = store.sample_bounds(5)
        self.assertEqual(first, "2026-11-06T08:00:00")
        self.assertEqual(last, "2026-11-06T11:00:00")

    def test_prune_samples(self):
        store = self.make_store()
        old = datetime.now() - timedelta(days=400)
        store.add_sample(5, 20.0, None, ts=old)
        store.add_sample(5, 21.0, None, ts=datetime.now())
        store.prune_samples(keep_days=180)
        rows = store.samples(5, old - timedelta(days=1), datetime.now() + timedelta(days=1))
        self.assertEqual(len(rows), 1)


class HistoryTests(StoreTestCase):
    def test_history_file_written_and_reloaded(self):
        store = self.make_store()
        base = datetime(2026, 11, 6, 8, 0, 0)
        store.add_sample(1, 20.0, 19.0, "heat", ts=base)
        store.add_sample(1, 22.0, 21.0, "heat", ts=base + timedelta(minutes=30))
        store.flush(force=True)

        name = f"history-{base:%Y-%m}.jsonl"
        path = os.path.join(self.history_dir, name)
        self.assertTrue(os.path.isfile(path), "le fichier historique doit exister")
        with open(path, encoding="utf-8") as handle:
            lines = [json.loads(line) for line in handle if line.strip()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["d"], 1)
        self.assertEqual(lines[0]["t"], "2026-11-06T08")
        self.assertEqual(lines[0]["a"], [42.0, 2])
        self.assertEqual(lines[0]["s"], [40.0, 2])
        self.assertEqual(lines[0]["m"], "heat")

        # Le rechargement reconstruit les mesures agrégées.
        reloaded = self.make_store()
        rows = reloaded.samples(1, base, base + timedelta(hours=1))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ambient"], 21.0)

    def test_old_history_files_pruned(self):
        os.makedirs(self.history_dir, exist_ok=True)
        old_name = "history-2020-01.jsonl"
        with open(os.path.join(self.history_dir, old_name), "w", encoding="utf-8") as handle:
            handle.write('{"d":1,"t":"2020-01-06T08","a":[20,1],"s":[19,1],"m":"heat"}\n')
        store = self.make_store(history_days=180)
        self.assertFalse(os.path.exists(os.path.join(self.history_dir, old_name)))

    def test_history_flush_grouped_by_month(self):
        store = self.make_store()
        store.add_sample(1, 20.0, None, ts=datetime(2026, 10, 31, 23, 0, 0))
        store.add_sample(1, 20.5, None, ts=datetime(2026, 11, 1, 0, 30, 0))
        store.flush(force=True)
        self.assertTrue(os.path.isfile(os.path.join(self.history_dir, "history-2026-10.jsonl")))
        self.assertTrue(os.path.isfile(os.path.join(self.history_dir, "history-2026-11.jsonl")))


class JournalTests(StoreTestCase):
    def test_events_newest_first(self):
        store = self.make_store()
        store.log("info", "premier")
        store.log("warn", "deuxième")
        store.log("error", "troisième", device_id=7)
        events = store.events()
        self.assertEqual([event["message"] for event in events], ["troisième", "deuxième", "premier"])
        self.assertEqual(events[0]["device_id"], 7)
        self.assertEqual(events[0]["level"], "error")

    def test_events_limit(self):
        store = self.make_store()
        for index in range(20):
            store.log("info", f"evt {index}")
        self.assertEqual(len(store.events(limit=5)), 5)
        self.assertEqual(store.events(limit=5)[0]["message"], "evt 19")

    def test_clear_events_empties_journal(self):
        store = self.make_store()
        store.log("info", "un")
        store.log("error", "deux")
        self.assertEqual(store.clear_events(), 2)
        self.assertEqual(store.events(), [])
        # Un journal déjà vide ne supprime rien.
        self.assertEqual(store.clear_events(), 0)

    def test_clear_events_is_persisted(self):
        store = self.make_store()
        store.log("info", "un")
        store.clear_events()
        store.flush(force=True)
        store.close()

        reloaded = self.make_store()
        self.assertEqual(reloaded.events(), [])

    def test_ids_stay_monotonic_after_clear(self):
        store = self.make_store()
        store.log("info", "un")
        first = store.events()[0]["id"]
        store.clear_events()
        store.log("info", "deux")
        self.assertGreater(store.events()[0]["id"], first)


class RoomsDevicesTests(StoreTestCase):
    def test_add_room_with_duplicate_name_rejected(self):
        store = self.make_store()
        with self.assertRaises(ValueError):
            store.add_room("salon")  # déjà présent (insensible à la casse)

    def test_update_and_delete_room(self):
        store = self.make_store()
        room = store.add_room("Véranda")
        updated = store.update_room(room["id"], {"name": "Véranda nord"})
        self.assertEqual(updated["name"], "Véranda nord")
        store.upsert_device(77, "Clim véranda")
        store.update_device(77, {"room_id": room["id"]})
        self.assertTrue(store.delete_room(room["id"]))
        self.assertIsNone(store.room(room["id"]))
        self.assertIsNone(store.device(77)["room_id"])  # détachée, pas perdue
        self.assertFalse(store.delete_room(room["id"]))

    def test_delete_device_removes_slots_overrides_samples(self):
        store = self.make_store()
        store.upsert_device(12, "Clim chambre")
        store.set_slots(device_id=12, week="A", days=week_days())
        store.set_override(12, "heat", 21, datetime.now() + timedelta(minutes=10))
        store.add_sample(12, 20.0, None, ts=datetime(2026, 11, 6, 8, 0, 0))
        store.delete_device(12)
        self.assertIsNone(store.device(12))
        self.assertEqual(store.slots(device_id=12, week="A"), [])
        self.assertIsNone(store.override(12))
        self.assertEqual(store.sample_bounds(12), (None, None))

    def test_update_device_rejects_unknown_room(self):
        store = self.make_store()
        store.upsert_device(15, "Clim")
        with self.assertRaises(ValueError):
            store.update_device(15, {"room_id": 999})


class ExportImportTests(StoreTestCase):
    def test_round_trip_includes_room_slots(self):
        store = self.make_store()
        store.set_slots(room_id=1, week="A", days=week_days({"mode": "heat", "temp": 19}))
        store.set_slots(room_id=2, week="B", days=week_days({"mode": "cool", "temp": 24}))
        store.set_slots(device_id=42, week="A", days=week_days({"mode": "auto", "temp": 22}))
        store.set_week_override("2026-11-02", "B")
        payload = store.export()

        target = MemoryStore(None, None)
        self.addCleanup(target.close)
        target.import_data(payload)

        self.assertEqual(len(target.slots(room_id=1, week="A")), 4)
        self.assertEqual(target.slots(room_id=1, week="A")[0]["mode"], "heat")
        self.assertEqual(len(target.slots(room_id=2, week="B")), 4)
        self.assertEqual(len(target.slots(device_id=42, week="A")), 4)
        self.assertEqual(target.week_overrides(), {"2026-11-02": "B"})
        # set_slots écrase : un second import ne doit pas dupliquer.
        target.import_data(payload)
        self.assertEqual(len(target.slots(room_id=1, week="A")), 4)

    def test_import_skips_invalid_rows(self):
        target = MemoryStore(None, None)
        self.addCleanup(target.close)
        target.import_data(
            {
                "rooms": [{"id": 1, "name": "Salon", "position": 0}],
                "devices": [{"device_id": 3, "name": "Clim", "room_id": 1}],
                "slots": [
                    {"room_id": 1, "week": "A", "weekday": 0, "minutes": 420, "mode": "heat", "temp": 20},
                    {"room_id": None, "device_id": None, "week": "A", "weekday": 1, "minutes": 60, "mode": "heat", "temp": 20},
                    {"room_id": None, "device_id": 3, "week": "Z", "weekday": 1, "minutes": 60, "mode": "heat", "temp": 20},
                    {"room_id": None, "device_id": 3, "week": "B", "weekday": 8, "minutes": 60, "mode": "heat", "temp": 20},
                    {"room_id": 55, "week": "A", "weekday": 2, "minutes": 60, "mode": "heat", "temp": 20},
                ],
            }
        )
        self.assertEqual(len(target.slots(room_id=1, week="A")), 1)
        self.assertEqual(len(target.slots(device_id=3, week="A")), 0)
        self.assertEqual(len(target.rooms()), 1)

    def test_import_resets_week_overrides(self):
        target = MemoryStore(None, None)
        self.addCleanup(target.close)
        target.set_week_override("2026-11-02", "B")
        target.import_data({"rooms": [], "week_overrides": []})
        self.assertEqual(target.week_overrides(), {})


class StorageInfoTests(StoreTestCase):
    def test_info_counts(self):
        store = self.make_store()
        store.set_settings({"week_mode": "none"})
        store.add_sample(1, 20.0, None, ts=datetime.now())
        store.flush(force=True)
        info = store.storage_info()
        self.assertGreaterEqual(info.get("state_writes", 0), 1)
        self.assertIn("history_writes", info)


if __name__ == "__main__":
    unittest.main()
