"""Tests de l'import Pronote pendant les vacances scolaires.

``build_days`` recoit ``holiday_days`` (index du jour -> etat) et remplace le
planning de l'ecole par une journee a la maison, uniquement pour les jours
transmis par l'appelant.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ecasa import pronote  # noqa: E402


def lesson(weekday, start, end):
    return {"weekday": weekday, "start_minutes": start, "end_minutes": end}


def summary(day):
    return [(slot["time"], slot["mode"], slot["temp"]) for slot in day]


def build(lessons, scopes=None, **kwargs):
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
    return pronote.build_days(lessons, scopes=scopes, **options)


class HolidayDayTests(unittest.TestCase):
    def test_comfort_plan_replaces_school_day(self):
        scopes = []
        days = build(
            [lesson(0, 8 * 60, 12 * 60)],
            scopes=scopes,
            holiday_days={0: {"mode": "heat", "temp": 20.0}},
        )
        self.assertEqual(
            summary(days[0]),
            [
                ("00:00", "off", 16.0),
                ("07:00", "heat", 20.0),
                ("22:00", "off", 16.0),
            ],
        )
        self.assertEqual(scopes[0], ("full", 0))

    def test_reduced_plan(self):
        days = build(
            [],
            holiday_days={1: {"mode": "heat", "temp": 16.0}},
            reduced_mode="heat",
            reduced_temp=16.0,
        )
        # Reduit = confort reduit : un seul creneau, sans montee inutile.
        self.assertEqual(summary(days[1]), [("00:00", "heat", 16.0)])

    def test_absent_plan_leaves_the_day_off(self):
        days = build(
            [lesson(2, 8 * 60, 10 * 60)],
            holiday_days={2: {"mode": "off", "temp": 16.0}},
        )
        self.assertEqual(summary(days[2]), [("00:00", "off", 16.0)])

    def test_skip_keeps_existing_day(self):
        days = build(
            [lesson(3, 8 * 60, 10 * 60)],
            holiday_days={3: {"skip": True}},
        )
        self.assertEqual(days[3], [])

    def test_other_days_are_untouched(self):
        days = build(
            [lesson(0, 8 * 60, 12 * 60), lesson(1, 9 * 60, 10 * 60)],
            holiday_days={0: {"mode": "heat", "temp": 20.0}},
        )
        # Lundi : jour a la maison.
        self.assertEqual(days[0][1], {"time": "07:00", "mode": "heat", "temp": 20.0})
        # Mardi : planning scolaire classique (depart en reduit).
        self.assertIn(("08:40", "off", 16.0), summary(days[1]))

    def test_scopes_full_for_every_holiday_day(self):
        scopes = []
        build(
            [],
            scopes=scopes,
            holiday_days={
                0: {"mode": "heat", "temp": 20.0},
                1: {"mode": "off", "temp": 16.0},
                2: {"skip": True},
            },
        )
        self.assertEqual(scopes[0], ("full", 0))
        self.assertEqual(scopes[1], ("full", 0))
        self.assertEqual(scopes[2], ("full", 0))

    def test_invalid_weekday_is_ignored(self):
        days = build([], holiday_days={"x": {"mode": "heat", "temp": 20.0}, 9: {}})
        self.assertTrue(all(day == [] for day in days))


if __name__ == "__main__":
    unittest.main()
