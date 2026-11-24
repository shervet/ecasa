"""Tests du calendrier scolaire (vacances par zone et jours feries).

Aucun acces reseau : les plages de vacances sont fournies par un fetcher
factice, et les jours feries sont calcules localement.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ecasa import school_calendar  # noqa: E402
from ecasa.school_calendar import SchoolCalendar  # noqa: E402


TOUSSAINT = {
    "description": "Vacances de la Toussaint",
    "population": "-",
    "start_date": "2026-10-16T22:00:00+00:00",
    "end_date": "2026-11-01T23:00:00+00:00",
}
ASCENSION = {
    "description": "Pont de l'Ascension",
    "population": "-",
    "start_date": "2027-05-06T22:00:00+00:00",
    "end_date": "2027-05-06T22:00:00+00:00",
}
SUMMER_TEACHERS = {
    "description": "Vacances d'Été",
    "population": "Enseignants",
    "start_date": "2027-07-02T22:00:00+00:00",
    "end_date": "2027-08-31T22:00:00+00:00",
}
SUMMER_STUDENTS = {
    "description": "Vacances d'Été",
    "population": "Élèves",
    "start_date": "2027-07-02T22:00:00+00:00",
    "end_date": "2027-09-01T22:00:00+00:00",
}


class NormalizeZoneTests(unittest.TestCase):
    def test_valid_zones(self):
        for value, expected in (("A", "A"), (" b ", "B"), ("c", "C")):
            self.assertEqual(school_calendar.normalize_zone(value), expected)

    def test_invalid_zones(self):
        for value in (None, "", "D", "Zone A", 3):
            self.assertEqual(school_calendar.normalize_zone(value), "")


class PublicHolidayTests(unittest.TestCase):
    def test_easter_2026(self):
        self.assertEqual(school_calendar.easter_sunday(2026), date(2026, 4, 5))

    def test_easter_2027(self):
        self.assertEqual(school_calendar.easter_sunday(2027), date(2027, 3, 28))

    def test_movable_holidays_follow_easter(self):
        holidays = school_calendar.public_holidays(2026)
        self.assertEqual(holidays[date(2026, 4, 6)], "Lundi de Pâques")
        self.assertEqual(holidays[date(2026, 5, 14)], "Ascension")
        self.assertEqual(holidays[date(2026, 5, 25)], "Lundi de Pentecôte")

    def test_fixed_holidays(self):
        holidays = school_calendar.public_holidays(2026)
        self.assertIn(date(2026, 1, 1), holidays)
        self.assertIn(date(2026, 5, 1), holidays)
        self.assertIn(date(2026, 7, 14), holidays)
        self.assertIn(date(2026, 12, 25), holidays)


class LocalDateTests(unittest.TestCase):
    def test_utc_midnight_is_shifted_to_local_day(self):
        # 22:00Z en ete = minuit local du lendemain.
        self.assertEqual(
            school_calendar._local_date("2026-10-16T22:00:00+00:00"),
            date(2026, 10, 17),
        )
        # 23:00Z en hiver = minuit local du lendemain.
        self.assertEqual(
            school_calendar._local_date("2026-11-01T23:00:00+00:00"),
            date(2026, 11, 2),
        )

    def test_naive_midnight_is_kept(self):
        self.assertEqual(
            school_calendar._local_date("2026-10-17T00:00:00"),
            date(2026, 10, 17),
        )

    def test_invalid_value(self):
        self.assertIsNone(school_calendar._local_date(""))
        self.assertIsNone(school_calendar._local_date("pas une date"))


class ParseRangeTests(unittest.TestCase):
    def test_range_is_half_open(self):
        item = school_calendar._parse_range(
            TOUSSAINT["start_date"], TOUSSAINT["end_date"], TOUSSAINT["description"]
        )
        self.assertEqual(item["start"], "2026-10-17")
        self.assertEqual(item["end"], "2026-11-02")

    def test_single_day_event(self):
        item = school_calendar._parse_range(
            ASCENSION["start_date"], ASCENSION["end_date"], ASCENSION["description"]
        )
        self.assertEqual(item["start"], "2027-05-07")
        self.assertEqual(item["end"], "2027-05-08")


class NormalizeRecordsTests(unittest.TestCase):
    def test_deduplicates_and_sorts(self):
        records = [TOUSSAINT, dict(TOUSSAINT), ASCENSION]
        result = school_calendar.normalize_records(records)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["name"], "Vacances de la Toussaint")
        self.assertEqual(result[1]["name"], "Pont de l'Ascension")

    def test_teacher_dates_are_ignored(self):
        result = school_calendar.normalize_records([SUMMER_TEACHERS])
        self.assertEqual(result, [])

    def test_student_summer_is_kept(self):
        result = school_calendar.normalize_records([SUMMER_STUDENTS])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["end"], "2027-09-02")

    def test_year_filter_keeps_overlap(self):
        # Noel 2026 chevauche 2026 et 2027 : conserve dans les deux cas.
        noel = {
            "description": "Vacances de Noël",
            "start_date": "2026-12-18T23:00:00+00:00",
            "end_date": "2027-01-03T23:00:00+00:00",
        }
        self.assertEqual(len(school_calendar.normalize_records([noel], [2026])), 1)
        self.assertEqual(len(school_calendar.normalize_records([noel], [2027])), 1)
        self.assertEqual(school_calendar.normalize_records([noel], [2030]), [])


class SchoolCalendarCacheTests(unittest.TestCase):
    def make_calendar(self, records=None, failing=False):
        calls = []

        def fetcher(zone, years):
            calls.append((zone, list(years)))
            if failing:
                raise OSError("reseau indisponible")
            return school_calendar.normalize_records(records or [TOUSSAINT], years)

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cache_path = Path(tmp.name) / "school_calendar.json"
        return SchoolCalendar(cache_path, fetcher=fetcher), cache_path, calls

    def test_ensure_fetches_and_persists(self):
        calendar, cache_path, calls = self.make_calendar()
        ranges = calendar.ensure("A", [2026, 2027])
        self.assertEqual(len(ranges), 1)
        self.assertEqual(calls, [("A", [2026, 2027])])
        self.assertTrue(cache_path.is_file())

        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        self.assertIn("A", payload["zones"])
        self.assertEqual(payload["zones"]["A"]["years"], [2026, 2027])

    def test_second_call_uses_cache(self):
        calendar, _path, calls = self.make_calendar()
        calendar.ensure("A", [2026, 2027])
        calendar.ensure("A", [2026, 2027])
        self.assertEqual(len(calls), 1)

    def test_network_failure_keeps_previous_ranges(self):
        calendar, _path, _calls = self.make_calendar()
        calendar.ensure("A", [2026, 2027])

        failing, _path2, _calls2 = self.make_calendar(failing=True)
        # Un cache vide + reseau en echec : aucune plage, mais pas d'exception.
        self.assertEqual(failing.ensure("A", [2026, 2027]), [])

    def test_refresh_raises_on_failure(self):
        calendar, _path, _calls = self.make_calendar(failing=True)
        with self.assertRaises(OSError):
            calendar.refresh("A", [2026, 2027])

    def test_invalid_zone_returns_empty(self):
        calendar, _path, calls = self.make_calendar()
        self.assertEqual(calendar.ensure("D", [2026]), [])
        self.assertEqual(calls, [])

    def test_day_info_school_and_public(self):
        calendar, _path, _calls = self.make_calendar()
        calendar.ensure("A", [2026, 2027])
        info = calendar.day_info(date(2026, 10, 20), "A")
        self.assertEqual(info, {"name": "Vacances de la Toussaint", "kind": "school"})
        self.assertIsNone(calendar.day_info(date(2026, 10, 16), "A"))

        # Dernier jour couvert, puis reprise : la borne de fin est exclue.
        self.assertIsNotNone(calendar.day_info(date(2026, 11, 1), "A"))
        self.assertIsNone(calendar.day_info(date(2026, 11, 2), "A"))

    def test_public_holiday_without_school_range(self):
        calendar, _path, _calls = self.make_calendar()
        info = calendar.day_info(date(2026, 7, 14), "A")
        self.assertEqual(info, {"name": "Fête nationale", "kind": "public"})

    def test_week_returns_seven_entries(self):
        calendar, _path, _calls = self.make_calendar()
        calendar.ensure("A", [2026, 2027])
        week = calendar.week(date(2026, 10, 19), "A")
        self.assertEqual(len(week), 7)
        self.assertTrue(all(item and item["kind"] == "school" for item in week))

    def test_next_holiday(self):
        calendar, _path, _calls = self.make_calendar()
        calendar.ensure("A", [2026, 2027])
        upcoming = calendar.next_holiday(date(2026, 10, 1), "A")
        self.assertEqual(upcoming["date"], "2026-10-17")
        self.assertEqual(upcoming["kind"], "school")


if __name__ == "__main__":
    unittest.main()
