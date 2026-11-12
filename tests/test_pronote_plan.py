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


def build(lessons, **kwargs):
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


class BuildDaysTests(unittest.TestCase):
    def test_day_without_lessons_is_empty(self):
        days = build([])
        self.assertEqual(len(days), 7)
        self.assertTrue(all(day == [] for day in days))

    def test_school_day_frames_night_and_absences(self):
        # Lundi : cours 8 h-12 h et 14 h-16 h, trajet 20 min.
        days = build(
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
        days = build(
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
        days = build(
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
        days = build(
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


class FridaySwitchTests(unittest.TestCase):
    """Bascule de garde le vendredi : la journee s'arrete au depart."""

    def test_friday_ends_on_departure_without_evening_return(self):
        # Vendredi avec bascule : l'enfant ne rentre pas le soir (garde
        # alternee), la journee s'arrete sur le depart en reduit. Ni retour
        # du midi ni confort du soir.
        days = build([lesson(4, 8 * 60, 12 * 60)], switch_day="friday")
        self.assertEqual(
            summary(days[4]),
            [
                ("00:00", "off", 16.0),   # nuit
                ("07:00", "heat", 20.0),  # lever en confort
                ("07:40", "off", 16.0),   # depart (cours - trajet)
            ],
        )

    def test_friday_afternoon_classes_stop_on_departure(self):
        days = build([lesson(4, 14 * 60, 16 * 60)], switch_day="friday")
        self.assertEqual(
            summary(days[4]),
            [
                ("00:00", "off", 16.0),
                ("07:00", "heat", 20.0),
                ("13:40", "off", 16.0),   # depart pour les cours de l'apres-midi
            ],
        )

    def test_other_weekdays_keep_evening_return(self):
        # Lundi..jeudi restent inchanges avec la bascule du vendredi.
        days = build(
            [lesson(3, 8 * 60, 12 * 60), lesson(3, 14 * 60, 16 * 60)],
            switch_day="friday",
        )
        self.assertEqual(
            summary(days[3]),
            [
                ("00:00", "off", 16.0),
                ("07:00", "heat", 20.0),
                ("07:40", "off", 16.0),
                ("12:20", "heat", 20.0),
                ("13:40", "off", 16.0),
                ("16:20", "heat", 20.0),
                ("22:00", "off", 16.0),
            ],
        )

    def test_friday_without_switch_keeps_evening_return(self):
        # Bascule du lundi par defaut : le vendredi se termine comme les
        # autres jours (confort du soir jusqu'au coucher).
        days = build([lesson(4, 8 * 60, 12 * 60)])
        self.assertEqual(
            summary(days[4]),
            [
                ("00:00", "off", 16.0),
                ("07:00", "heat", 20.0),
                ("07:40", "off", 16.0),
                ("12:20", "heat", 20.0),
                ("22:00", "off", 16.0),
            ],
        )


class FridayDepartureMinutesTests(unittest.TestCase):
    def test_returns_friday_departure_slot(self):
        days = build([lesson(4, 8 * 60, 12 * 60)], switch_day="friday")
        self.assertEqual(
            pronote.friday_departure_minutes(days, 7 * 60, 20), 7 * 60 + 40
        )

    def test_returns_afternoon_departure_slot(self):
        days = build([lesson(4, 14 * 60, 16 * 60)], switch_day="friday")
        self.assertEqual(
            pronote.friday_departure_minutes(days, 7 * 60, 20), 13 * 60 + 40
        )

    def test_empty_friday_falls_back_to_wake_plus_travel(self):
        days = build([], switch_day="friday")
        self.assertEqual(
            pronote.friday_departure_minutes(days, 7 * 60, 20), 7 * 60 + 20
        )

    def test_ignores_morning_wake_slot(self):
        # Seul le lever figure sur le vendredi : ce n'est pas un depart.
        friday = [
            {"time": "00:00", "mode": "off", "temp": 16.0},
            {"time": "07:00", "mode": "heat", "temp": 20.0},
        ]
        days = [[] for _ in range(7)]
        days[4] = friday
        self.assertEqual(
            pronote.friday_departure_minutes(days, 7 * 60, 20), 7 * 60 + 20
        )


class DayScopeTests(unittest.TestCase):
    """Portée de l'import : garde alternée et journées qui finissent le matin."""

    def scopes_for(self, lessons, **kwargs):
        scopes = []
        build(lessons, scopes=scopes, **kwargs)
        return scopes

    def test_friday_switch_writes_the_morning(self):
        scopes = self.scopes_for(
            [lesson(4, 8 * 60, 12 * 60)], switch_day="friday"
        )
        kind, index = scopes[4]
        self.assertEqual(kind, "prefix")
        self.assertGreater(index, 0)

    def test_friday_scope_ignored_when_switch_is_monday(self):
        # Vendredi après-midi : la journée ne s'arrête pas le matin, donc
        # l'import remplace toute la journée quand la bascule est le lundi.
        scopes = self.scopes_for([lesson(4, 14 * 60, 16 * 60)])
        self.assertEqual(scopes[4], ("full", 0))

    def test_monday_externe_writes_after_morning_classes(self):
        lessons = [lesson(0, 8 * 60, 12 * 60), lesson(0, 14 * 60, 16 * 60)]
        kind, index = self.scopes_for(lessons)[0]
        self.assertEqual(kind, "suffix")
        # Le premier créneau écrit est le retour à la maison pour le repas.
        self.assertEqual(build(lessons)[0][index]["time"], "12:20")

    def test_monday_demi_pensionnaire_writes_evening_only(self):
        lessons = [lesson(0, 8 * 60, 12 * 60), lesson(0, 14 * 60, 16 * 60)]
        kind, index = self.scopes_for(
            lessons, student_status="demi_pensionnaire"
        )[0]
        self.assertEqual(kind, "suffix")
        self.assertEqual(
            build(lessons, student_status="demi_pensionnaire")[0][index]["time"],
            "16:20",
        )

    def test_morning_only_day_writes_its_start(self):
        lessons = [lesson(2, 8 * 60, 12 * 60)]
        kind, index = self.scopes_for(lessons)[2]
        self.assertEqual(kind, "prefix")
        self.assertEqual(build(lessons)[2][index - 1]["time"], "12:20")

    def test_full_day_is_replaced_entirely(self):
        scopes = self.scopes_for([lesson(2, 8 * 60, 15 * 60)])
        self.assertEqual(scopes[2], ("full", 0))

    def test_day_without_lessons_is_full_and_preserved(self):
        scopes = self.scopes_for([])
        self.assertEqual(len(scopes), 7)
        self.assertTrue(all(scope == ("full", 0) for scope in scopes))

    def test_no_scopes_requested_keeps_legacy_return(self):
        days = build([lesson(0, 8 * 60, 12 * 60)])
        self.assertEqual(len(days), 7)


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
