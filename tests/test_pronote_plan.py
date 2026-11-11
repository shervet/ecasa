"""Tests du planificateur Pronote (build_days / build_plan).

Aucune connexion reseau : les cours sont fournis directement, ou fetch_week
est remplace par un bouchon pour build_plan.
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mecasa import pronote  # noqa: E402


def lesson(weekday, start, end):
    return {"weekday": weekday, "start_minutes": start, "end_minutes": end}


def summary(day):
    return [(slot["time"], slot["mode"], slot["temp"]) for slot in day]


class BuildDaysTests(unittest.TestCase):
    def build(self, lessons, **kwargs):
        options = {
            "travel_minutes": 20,
            "comfort_mode": "heat",
            "comfort_temp": 20.0,
            "reduced_mode": "off",
            "reduced_temp": 16.0,
            "wake_minutes": 7 * 60,
            "bedtime_minutes": 22 * 60,
        }
        options.update(kwargs)
        return pronote.build_days(lessons, **options)

    def test_day_without_lessons_is_empty(self):
        days = self.build([])
        self.assertEqual(len(days), 7)
        self.assertTrue(all(day == [] for day in days))

    def test_school_day_frames_night_and_absences(self):
        # Lundi : cours 8 h-12 h et 14 h-16 h, trajet 20 min.
        days = self.build(
            [lesson(0, 8 * 60, 12 * 60), lesson(0, 14 * 60, 16 * 60)]
        )
        self.assertEqual(
            summary(days[0]),
            [
                ("00:00", "off", 16.0),    # nuit (coucher de la veille)
                ("07:00", "heat", 20.0),   # lever
                ("07:40", "off", 16.0),    # depart (cours - trajet)
                ("12:20", "heat", 20.0),   # retour du midi
                ("13:40", "off", 16.0),    # depart de l'apres-midi
                ("16:20", "heat", 20.0),   # retour du soir
                ("22:00", "off", 16.0),    # coucher
            ],
        )
        # Les autres jours restent vides (preserves a l'import).
        self.assertTrue(all(days[index] == [] for index in range(1, 7)))

    def test_wake_inside_absence_keeps_reduced(self):
        # Lever (8 h) apres le depart (7 h 40) : pas de confort au lever,
        # le reduit continue jusqu'au retour.
        days = self.build(
            [lesson(1, 8 * 60, 10 * 60)], wake_minutes=8 * 60
        )
        self.assertEqual(
            summary(days[1]),
            [
                ("00:00", "off", 16.0),
                ("10:20", "heat", 20.0),
                ("22:00", "off", 16.0),
            ],
        )

    def test_demi_pensionnaire_has_no_lunch_comfort(self):
        days = self.build(
            [lesson(2, 8 * 60, 10 * 60), lesson(2, 14 * 60, 16 * 60)],
            student_status="demi_pensionnaire",
        )
        self.assertEqual(
            summary(days[2]),
            [
                ("00:00", "off", 16.0),
                ("07:00", "heat", 20.0),
                ("07:40", "off", 16.0),    # journee continue, cantine le midi
                ("16:20", "heat", 20.0),
                ("22:00", "off", 16.0),
            ],
        )

    def test_custom_wake_and_bedtime(self):
        days = self.build(
            [lesson(3, 10 * 60, 12 * 60)],
            wake_minutes=8 * 60 + 30,
            bedtime_minutes=23 * 60 + 30,
        )
        self.assertEqual(
            summary(days[3]),
            [
                ("00:00", "off", 16.0),
                ("08:30", "heat", 20.0),
                ("09:40", "off", 16.0),
                ("12:20", "heat", 20.0),
                ("23:30", "off", 16.0),
            ],
        )


class BuildPlanTests(unittest.TestCase):
    def test_wake_minutes_comes_from_config(self):
        config = {
            "pronote_wake_minutes": 6 * 60 + 45,
            "pronote_bedtime_minutes": 23 * 60,
            "pronote_travel_minutes": 15,
        }
        lessons = [lesson(0, 9 * 60, 11 * 60)]
        with mock.patch.object(
            pronote, "fetch_week", return_value=(lessons, 0)
        ):
            plan = pronote.build_plan(config, date(2026, 11, 9))
        self.assertEqual(plan["wake_minutes"], 6 * 60 + 45)
        self.assertEqual(plan["bedtime_minutes"], 23 * 60)
        self.assertNotIn("morning_prep_minutes", plan)
        times = [slot["time"] for slot in plan["days"][0]]
        self.assertEqual(times[0], "00:00")
        self.assertIn("06:45", times)  # lever en confort
        self.assertIn("23:00", times)  # coucher en reduit


if __name__ == "__main__":
    unittest.main()
