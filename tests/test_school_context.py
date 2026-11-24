"""Tests du contexte calendrier applique a l'import Pronote.

``school_calendar_context`` vit dans ``ecasa.main`` : on remplace le service
de calendrier par un bouchon pour rester hors ligne.
"""

from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ecasa import main as server  # noqa: E402
from ecasa.schedule import week_type  # noqa: E402

WEEK_START = date(2026, 10, 19)
HOLIDAY_START = date(2026, 10, 17)
HOLIDAY_END = date(2026, 11, 2)


class FakeCalendar:
    def ensure(self, zone, years):
        return []

    def day_info(self, day, zone):
        if HOLIDAY_START <= day < HOLIDAY_END:
            return {"name": "Vacances de la Toussaint", "kind": "school"}
        return None


class StubStore:
    def __init__(self, settings=None):
        self._settings = dict(settings or {"week_mode": "none"})

    def settings(self):
        return dict(self._settings)

    def week_overrides(self):
        return {}


def base_config(**overrides):
    config = {
        "school_zone": "A",
        "school_holidays_enabled": True,
        "school_holiday_plan": "comfort",
        "school_holiday_weeks": "both",
        "pronote_comfort_mode": "heat",
        "pronote_comfort_temp": 20,
        "pronote_reduced_mode": "heat",
        "pronote_reduced_temp": 16,
    }
    config.update(overrides)
    return config


def context(config, store=None, week_start=WEEK_START):
    with mock.patch.object(server, "school_calendar_service", return_value=FakeCalendar()):
        return server.school_calendar_context(
            store or StubStore(), config, week_start
        )


class SchoolContextTests(unittest.TestCase):
    def test_disabled_or_without_zone(self):
        self.assertEqual(context(base_config(school_holidays_enabled=False)), ({}, []))
        self.assertEqual(context(base_config(school_zone="")), ({}, []))

    def test_all_holiday_days_are_replaced(self):
        holiday_days, holidays = context(base_config())
        self.assertEqual(sorted(holiday_days), list(range(7)))
        self.assertEqual(len(holidays), 7)
        self.assertTrue(all(item["present"] for item in holidays))
        self.assertEqual(holiday_days[0], {"mode": "heat", "temp": 20.0})

    def test_plan_variants(self):
        self.assertEqual(
            context(base_config(school_holiday_plan="reduced"))[0][0],
            {"mode": "heat", "temp": 16.0},
        )
        self.assertEqual(
            context(base_config(school_holiday_plan="absent"))[0][0],
            {"mode": "off", "temp": 16.0},
        )
        self.assertEqual(
            context(base_config(school_holiday_plan="skip"))[0][0],
            {"skip": True},
        )

    def test_alternation_keeps_other_weeks_untouched(self):
        # week_mode = none : toutes les semaines sont des semaines A.
        store = StubStore({"week_mode": "none"})
        week = week_type(datetime(2026, 10, 19, 12), store.settings(), {})
        self.assertEqual(week, "A")

        holiday_days, holidays = context(
            base_config(school_holiday_weeks="A"), store
        )
        self.assertTrue(holiday_days)
        self.assertTrue(all(item["present"] for item in holidays))

        holiday_days, holidays = context(
            base_config(school_holiday_weeks="B"), store
        )
        self.assertEqual(holiday_days, {})
        self.assertTrue(holidays)
        self.assertTrue(all(not item["present"] for item in holidays))

    def test_no_week_is_present(self):
        self.assertEqual(
            context(base_config(school_holiday_weeks="none"))[0], {}
        )

    def test_week_without_holiday(self):
        holiday_days, holidays = context(
            base_config(), week_start=WEEK_START - timedelta(days=14)
        )
        self.assertEqual(holiday_days, {})
        self.assertEqual(holidays, [])


if __name__ == "__main__":
    unittest.main()
