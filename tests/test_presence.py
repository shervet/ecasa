"""Tests de la fenetre de vacances / absence programmee.

La fenetre s'ajoute a ``presence_mode`` sans le modifier : une maison absente
manuellement le reste, et une fenetre expiree redevient transparente.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ecasa.engine import (  # noqa: E402
    absence_active,
    absence_window,
    presence_status,
)


def moment(year, month, day, hour=12):
    return datetime(year, month, day, hour)


class AbsenceWindowTests(unittest.TestCase):
    def test_no_dates(self):
        self.assertIsNone(absence_window({}))

    def test_active_inclusive_range(self):
        settings = {"absence_start": "2026-10-17", "absence_until": "2026-11-01"}
        for day in (17, 25, 1):
            month = 10 if day > 10 else 11
            window = absence_window(settings, moment(2026, month, day))
            self.assertIsNotNone(window, f"{day} devrait etre absent")
        self.assertEqual(window["start"], "2026-10-17")
        self.assertEqual(window["until"], "2026-11-01")

    def test_before_start(self):
        settings = {"absence_start": "2026-10-17", "absence_until": "2026-11-01"}
        self.assertIsNone(absence_window(settings, moment(2026, 10, 16)))

    def test_after_end(self):
        settings = {"absence_start": "2026-10-17", "absence_until": "2026-11-01"}
        self.assertIsNone(absence_window(settings, moment(2026, 11, 2)))

    def test_start_only_stays_open(self):
        settings = {"absence_start": "2026-10-17"}
        self.assertIsNotNone(absence_window(settings, moment(2027, 3, 1)))
        self.assertIsNone(absence_window(settings, moment(2026, 10, 16)))

    def test_end_only(self):
        settings = {"absence_until": "2026-11-01"}
        self.assertIsNotNone(absence_window(settings, moment(2026, 1, 1)))
        self.assertIsNone(absence_window(settings, moment(2026, 11, 2)))

    def test_invalid_dates_are_ignored(self):
        self.assertIsNone(absence_window({"absence_start": "nope"}))


class PresenceStatusTests(unittest.TestCase):
    def test_present_by_default(self):
        status = presence_status({})
        self.assertFalse(status["active"])
        self.assertEqual(status["reason"], "present")

    def test_manual_absence(self):
        status = presence_status({"presence_mode": "absent"})
        self.assertTrue(status["active"])
        self.assertEqual(status["reason"], "manual")
        self.assertEqual(status["mode"], "absent")

    def test_vacation_takes_precedence(self):
        settings = {
            "presence_mode": "present",
            "absence_start": "2026-10-17",
            "absence_until": "2026-11-01",
        }
        status = presence_status(settings, moment(2026, 10, 20))
        self.assertTrue(status["active"])
        self.assertEqual(status["reason"], "vacation")
        self.assertEqual(status["until"], "2026-11-01")

    def test_expired_vacation_returns_to_present(self):
        settings = {
            "presence_mode": "present",
            "absence_start": "2026-10-17",
            "absence_until": "2026-11-01",
        }
        status = presence_status(settings, moment(2026, 11, 2))
        self.assertFalse(status["active"])
        self.assertEqual(status["reason"], "present")

    def test_manual_absence_survives_expired_vacation(self):
        settings = {
            "presence_mode": "absent",
            "absence_start": "2026-10-17",
            "absence_until": "2026-11-01",
        }
        status = presence_status(settings, moment(2026, 11, 2))
        self.assertTrue(status["active"])
        self.assertEqual(status["reason"], "manual")

    def test_absence_active_helper(self):
        self.assertTrue(
            absence_active(
                {"absence_start": "2026-10-17", "absence_until": "2026-11-01"},
                moment(2026, 10, 20),
            )
        )
        self.assertFalse(absence_active({}))


if __name__ == "__main__":
    unittest.main()
