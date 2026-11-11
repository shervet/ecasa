"""Tests des règles de planning : type de semaine et créneau actif."""

from __future__ import annotations

import sys
import unittest
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mecasa.schedule import (  # noqa: E402
    describe,
    find_slot,
    iso_monday,
    next_slot,
    week_type,
)

# Lundi de la semaine ISO 2 (paire) et de la semaine ISO 3 (impaire) de 2026.
MONDAY_EVEN = datetime(2026, 1, 5, 10, 0)
MONDAY_ODD = datetime(2026, 1, 12, 10, 0)


def make_slot(weekday: int, time_str: str, mode: str = "heat", temp: float = 20.0):
    hours, minutes = time_str.split(":")
    return {
        "weekday": weekday,
        "minutes": int(hours) * 60 + int(minutes),
        "mode": mode,
        "temp": temp,
    }


class WeekTypeTests(unittest.TestCase):
    def test_parity(self):
        self.assertEqual(week_type(MONDAY_EVEN, {"week_mode": "parity"}, {}), "B")
        self.assertEqual(week_type(MONDAY_ODD, {"week_mode": "parity"}, {}), "A")

    def test_parity_flip(self):
        settings = {"week_mode": "parity", "week_flip": "1"}
        self.assertEqual(week_type(MONDAY_EVEN, settings, {}), "A")
        self.assertEqual(week_type(MONDAY_ODD, settings, {}), "B")

    def test_none_mode(self):
        self.assertEqual(week_type(MONDAY_EVEN, {"week_mode": "none"}, {}), "A")
        self.assertEqual(week_type(MONDAY_ODD, {"week_mode": "none"}, {}), "A")

    def test_explicit_mode(self):
        settings = {"week_mode": "explicit", "default_week": "B"}
        self.assertEqual(week_type(MONDAY_EVEN, settings, {}), "B")
        self.assertEqual(week_type(MONDAY_ODD, settings, {}), "B")

    def test_override_priority(self):
        overrides = {"2026-01-05": "A"}
        # Toute la semaine du 5 janvier passe en A, même paire.
        self.assertEqual(
            week_type(datetime(2026, 1, 7, 8, 0), {"week_mode": "parity"}, overrides),
            "A",
        )
        # Les semaines suivantes ne sont pas touchées.
        self.assertEqual(week_type(MONDAY_ODD, {"week_mode": "parity"}, overrides), "A")
        self.assertEqual(
            week_type(datetime(2026, 1, 19, 8, 0), {"week_mode": "parity"}, overrides),
            "B",
        )

    def test_bad_override_value_ignored(self):
        overrides = {"2026-01-05": "Z"}
        self.assertEqual(week_type(MONDAY_EVEN, {"week_mode": "parity"}, overrides), "B")


class WeekTypeFridaySwitchTests(unittest.TestCase):
    """Bascule de semaine le vendredi (garde alternee).

    Semaine ISO 2 (paire -> B) : du 5 au 11 janvier 2026. Semaine ISO 3
    (impaire -> A) : du 12 au 18 janvier 2026.
    """

    def setUp(self):
        self.settings = {
            "week_mode": "parity",
            "week_switch_day": "friday",
            "friday_switch_minutes": "460",  # 07:40
        }

    def test_weekdays_stay_on_current_week(self):
        # Lundi..jeudi : la semaine en cours n'a pas encore bascule.
        self.assertEqual(week_type(datetime(2026, 1, 5, 10, 0), self.settings, {}), "B")
        self.assertEqual(week_type(datetime(2026, 1, 7, 10, 0), self.settings, {}), "B")
        self.assertEqual(week_type(datetime(2026, 1, 8, 18, 0), self.settings, {}), "B")
        self.assertEqual(week_type(datetime(2026, 1, 12, 10, 0), self.settings, {}), "A")
        self.assertEqual(week_type(datetime(2026, 1, 15, 18, 0), self.settings, {}), "A")

    def test_friday_before_switch_time_stays_on_current_week(self):
        self.assertEqual(week_type(datetime(2026, 1, 9, 7, 30), self.settings, {}), "B")

    def test_friday_from_switch_time_moves_to_next_week(self):
        # 07:40 exactement : l'heure de bascule est incluse.
        self.assertEqual(week_type(datetime(2026, 1, 9, 7, 40), self.settings, {}), "A")
        self.assertEqual(week_type(datetime(2026, 1, 9, 18, 0), self.settings, {}), "A")
        # Semaine impaire -> la suivante est paire.
        self.assertEqual(week_type(datetime(2026, 1, 16, 18, 0), self.settings, {}), "B")

    def test_weekend_moves_to_next_week(self):
        self.assertEqual(week_type(datetime(2026, 1, 10, 12, 0), self.settings, {}), "A")
        self.assertEqual(week_type(datetime(2026, 1, 11, 12, 0), self.settings, {}), "A")

    def test_monday_switch_keeps_weekend_on_current_week(self):
        settings = {"week_mode": "parity", "week_switch_day": "monday"}
        self.assertEqual(week_type(datetime(2026, 1, 9, 18, 0), settings, {}), "B")
        self.assertEqual(week_type(datetime(2026, 1, 10, 12, 0), settings, {}), "B")
        self.assertEqual(week_type(datetime(2026, 1, 11, 12, 0), settings, {}), "B")

    def test_bad_switch_minutes_falls_back_to_0740(self):
        settings = {**self.settings, "friday_switch_minutes": "oops"}
        self.assertEqual(week_type(datetime(2026, 1, 9, 7, 40), settings, {}), "A")
        self.assertEqual(week_type(datetime(2026, 1, 9, 7, 30), settings, {}), "B")


class FindSlotTests(unittest.TestCase):
    def test_empty(self):
        self.assertIsNone(find_slot([], MONDAY_EVEN))

    def test_last_started_slot(self):
        slots = [make_slot(0, "07:00"), make_slot(0, "20:00")]
        moment = datetime(2026, 1, 5, 12, 0)  # lundi 12:00
        active = find_slot(slots, moment)
        self.assertIsNotNone(active)
        self.assertEqual(active["minutes"], 7 * 60)

    def test_evening(self):
        slots = [make_slot(0, "07:00"), make_slot(0, "20:00")]
        moment = datetime(2026, 1, 5, 21, 30)
        self.assertEqual(find_slot(slots, moment)["minutes"], 20 * 60)

    def test_before_first_slot_wraps_to_last_of_week(self):
        slots = [make_slot(0, "07:00"), make_slot(6, "22:30")]
        moment = datetime(2026, 1, 5, 6, 0)  # lundi 06:00, avant tout créneau
        active = find_slot(slots, moment)
        self.assertEqual(active["weekday"], 6)
        self.assertEqual(active["minutes"], 22 * 60 + 30)

    def test_exact_start(self):
        slots = [make_slot(0, "07:00")]
        moment = datetime(2026, 1, 5, 7, 0)
        self.assertEqual(find_slot(slots, moment)["minutes"], 7 * 60)


class NextSlotTests(unittest.TestCase):
    def test_empty(self):
        self.assertIsNone(next_slot([], MONDAY_EVEN))

    def test_basic(self):
        slots = [make_slot(0, "20:00"), make_slot(1, "07:00")]
        moment = datetime(2026, 1, 5, 12, 0)
        upcoming = next_slot(slots, moment)
        self.assertEqual(upcoming["minutes"], 20 * 60)
        self.assertEqual(upcoming["in_minutes"], 8 * 60)
        self.assertEqual(upcoming["at"], datetime(2026, 1, 5, 20, 0))

    def test_wraps_to_next_week(self):
        slots = [make_slot(0, "07:00")]
        moment = datetime(2026, 1, 5, 8, 0)  # juste après le créneau
        upcoming = next_slot(slots, moment)
        self.assertEqual(upcoming["in_minutes"], 7 * 1440 - 60)
        self.assertEqual(upcoming["at"], datetime(2026, 1, 12, 7, 0))

    def test_exact_now_means_next_week(self):
        slots = [make_slot(0, "07:00")]
        moment = datetime(2026, 1, 5, 7, 0)
        upcoming = next_slot(slots, moment)
        self.assertEqual(upcoming["in_minutes"], 7 * 1440)
        self.assertEqual(upcoming["at"], datetime(2026, 1, 12, 7, 0))


class DescribeTests(unittest.TestCase):
    def test_none(self):
        self.assertEqual(describe(None), "Aucun créneau")

    def test_off(self):
        self.assertEqual(describe(make_slot(0, "07:00", mode="off")), "Arrêt")

    def test_fan(self):
        self.assertEqual(describe(make_slot(0, "07:00", mode="fan")), "Ventilation")

    def test_heat_with_temp(self):
        self.assertEqual(describe(make_slot(0, "07:00", mode="heat", temp=19.5)), "Chaud 19.5 °C")

    def test_unknown_mode_falls_back_to_name_and_temp(self):
        self.assertEqual(describe(make_slot(0, "07:00", mode="weird")), "weird 20 °C")
        self.assertEqual(describe(make_slot(0, "07:00", mode="weird", temp="n/a")), "weird")


class IsoMondayTests(unittest.TestCase):
    def test_friday(self):
        self.assertEqual(iso_monday(date(2026, 11, 6)), "2026-11-02")

    def test_monday(self):
        self.assertEqual(iso_monday(date(2026, 11, 2)), "2026-11-02")

    def test_sunday(self):
        self.assertEqual(iso_monday(date(2026, 11, 8)), "2026-11-02")


if __name__ == "__main__":
    unittest.main()
