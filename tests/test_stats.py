"""Tests de l'agrégation des statistiques."""

from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ecasa.stats import (  # noqa: E402
    annual_temperature,
    build_stats,
    parse_anchor,
    period_bounds,
)


class FakeResult:
    def __init__(self, hours):
        self.hours = hours
        self.source = "fake-meteo"
        self.latitude = 48.85
        self.longitude = 2.35


class FakeWeather:
    def __init__(self, hours):
        self.hours = hours

    def hourly(self, latitude, longitude, start, end):
        return FakeResult(self.hours)


class FakeAnnualWeather:
    """Cache météo déjà chargé (aucun appel réseau)."""

    def __init__(self, hours):
        self.hours = hours

    def cached_hours(self, latitude, longitude, start, end):
        return {
            day: values
            for day, values in self.hours.items()
            if start.isoformat() <= day <= end.isoformat()
        }


class FakeStore:
    def __init__(self, rows):
        self.rows = rows

    def samples(self, device_id, start, end):
        result = []
        for row in self.rows:
            moment = datetime.fromisoformat(str(row["ts"]))
            if start <= moment <= end:
                result.append(row)
        return result

    def sample_bounds(self, device_id):
        stamps = sorted(str(row["ts"]) for row in self.rows)
        if not stamps:
            return None, None
        return stamps[0], stamps[-1]


class DeviceStore:
    """Store qui distingue les appareils (rows étiquetées par device_id)."""

    def __init__(self, rows):
        self.rows = rows

    def samples(self, device_id, start, end):
        result = []
        for row in self.rows:
            if int(row.get("device_id")) != int(device_id):
                continue
            moment = datetime.fromisoformat(str(row["ts"]))
            if start <= moment <= end:
                result.append(row)
        return result

    def sample_bounds(self, device_id):
        stamps = sorted(
            str(row["ts"])
            for row in self.rows
            if int(row.get("device_id")) == int(device_id)
        )
        if not stamps:
            return None, None
        return stamps[0], stamps[-1]


def make_room():
    return {
        "id": 1,
        "name": "Salon",
        "devices": [
            {"device_id": 7, "name": "Clim salon", "device_type": "atlantic"},
        ],
    }


def make_room_with_actuator(**overrides):
    room = {
        "id": 1,
        "name": "Salon",
        "thermostat_device_id": 9001,
        "actuator_device_id": 28129468,
        "devices": [
            {"device_id": 9001, "name": "Salon (Wiser)", "device_type": "wiser"},
            {"device_id": 28129468, "name": "Clim", "device_type": "atlantic"},
        ],
    }
    room.update(overrides)
    return room


# Thermostat Wiser sans planning : sa « consigne » enregistrée est sa propre
# température mesurée (11.5 °C). L'actuateur (Clim) applique 19.5 °C.
DEVICE_ROWS = [
    {"device_id": 9001, "ts": "2026-10-20T08:05:00", "ambient": 18.0, "setpoint": 11.5},
    {"device_id": 28129468, "ts": "2026-10-20T08:10:00", "ambient": 18.4, "setpoint": 19.5},
    {"device_id": 9001, "ts": "2026-10-20T09:05:00", "ambient": 19.0, "setpoint": 11.5},
    {"device_id": 28129468, "ts": "2026-10-20T09:10:00", "ambient": 19.2, "setpoint": 19.5},
]


CONFIG = {"weather_enabled": True, "weather_latitude": 48.85, "weather_longitude": 2.35}

ROWS = [
    {"ts": "2026-10-20T08:10:00", "ambient": 20.0, "setpoint": 19.0, "mode": "heat"},
    {"ts": "2026-10-20T09:40:00", "ambient": 22.0, "setpoint": 21.0, "mode": "heat"},
    {"ts": "2026-10-21T08:00:00", "ambient": 25.0, "setpoint": 22.0, "mode": "cool"},
]

WEATHER_HOURS = {
    "2026-10-20": {8: 12.0, 9: 12.6},
    "2026-10-21": {8: 15.0},
}


class ParseAnchorTests(unittest.TestCase):
    def test_none_is_today(self):
        self.assertEqual(parse_anchor(None), datetime.now().date())

    def test_iso_date(self):
        self.assertEqual(parse_anchor("2026-11-06"), date(2026, 11, 6))

    def test_datetime_string_truncated(self):
        self.assertEqual(parse_anchor("2026-11-06T21:30:00"), date(2026, 11, 6))

    def test_invalid_falls_back_to_today(self):
        self.assertEqual(parse_anchor("pas une date"), datetime.now().date())


class PeriodBoundsTests(unittest.TestCase):
    def test_day(self):
        self.assertEqual(period_bounds("day", date(2026, 11, 6)), (date(2026, 11, 6), date(2026, 11, 6)))

    def test_week_starts_monday(self):
        # Vendredi 6 novembre 2026 -> semaine du lundi 2 au dimanche 8.
        self.assertEqual(
            period_bounds("week", date(2026, 11, 6)),
            (date(2026, 11, 2), date(2026, 11, 8)),
        )

    def test_month(self):
        self.assertEqual(
            period_bounds("month", date(2026, 11, 6)),
            (date(2026, 11, 1), date(2026, 11, 30)),
        )

    def test_december_wraps_to_next_year(self):
        self.assertEqual(
            period_bounds("month", date(2026, 12, 10)),
            (date(2026, 12, 1), date(2026, 12, 31)),
        )

    def test_invalid_range(self):
        with self.assertRaises(ValueError):
            period_bounds("year", date(2026, 11, 6))


class BuildStatsTests(unittest.TestCase):
    def test_invalid_range_rejected(self):
        with self.assertRaises(ValueError):
            build_stats(FakeStore([]), FakeWeather({}), make_room(), "year",
                        date(2026, 10, 20), CONFIG)

    def test_day_range(self):
        stats = build_stats(
            FakeStore(ROWS),
            FakeWeather(WEATHER_HOURS),
            make_room(),
            "day",
            date(2026, 10, 20),
            CONFIG,
        )

        self.assertEqual(stats["range"], "day")
        self.assertEqual(stats["room_name"], "Salon")
        self.assertEqual(stats["start"], "2026-10-20")
        self.assertEqual(stats["end"], "2026-10-20")
        self.assertEqual(len(stats["buckets"]), 24)

        buckets = stats["buckets"]
        self.assertEqual(buckets[8]["ambient"], 20.0)
        self.assertEqual(buckets[8]["setpoint"], 19.0)
        self.assertEqual(buckets[8]["outside"], 12.0)
        self.assertEqual(buckets[8]["count"], 1)
        self.assertEqual(buckets[9]["ambient"], 22.0)
        self.assertEqual(buckets[9]["outside"], 12.6)
        self.assertIsNone(buckets[10]["ambient"])
        self.assertIsNone(buckets[10]["outside"])
        self.assertEqual(buckets[10]["count"], 0)

        summary = stats["summary"]
        self.assertEqual(summary["ambient_avg"], 21.0)
        self.assertEqual(summary["ambient_min"], 20.0)
        self.assertEqual(summary["ambient_max"], 22.0)
        self.assertEqual(summary["setpoint_avg"], 20.0)
        self.assertEqual(summary["gap_avg"], 1.0)
        self.assertEqual(summary["samples"], 2)
        self.assertEqual(summary["buckets_with_data"], 2)
        self.assertEqual(summary["first_sample"], "2026-10-20T08:10:00")

        self.assertTrue(stats["weather"]["ok"])
        self.assertEqual(stats["weather"]["source"], "fake-meteo")

    def test_week_range_buckets_and_outside(self):
        stats = build_stats(
            FakeStore(ROWS),
            FakeWeather(WEATHER_HOURS),
            make_room(),
            "week",
            date(2026, 10, 21),  # mercredi -> semaine du lundi 19
            CONFIG,
        )
        self.assertEqual(stats["start"], "2026-10-19")
        self.assertEqual(stats["end"], "2026-10-25")
        self.assertEqual(len(stats["buckets"]), 7)

        by_date = {bucket["date"]: bucket for bucket in stats["buckets"]}
        # Lundi 20 : deux mesures intérieures, moyenne des deux heures de météo.
        self.assertEqual(by_date["2026-10-20"]["outside"], 12.3)
        self.assertEqual(by_date["2026-10-20"]["ambient"], 21.0)
        self.assertEqual(by_date["2026-10-20"]["count"], 2)
        # Mercredi 21 : la mesure du jour est là.
        self.assertEqual(by_date["2026-10-21"]["ambient"], 25.0)
        self.assertEqual(by_date["2026-10-21"]["outside"], 15.0)
        self.assertEqual(by_date["2026-10-21"]["count"], 1)

    def test_month_range_bucket_count(self):
        stats = build_stats(
            FakeStore(ROWS),
            FakeWeather(WEATHER_HOURS),
            make_room(),
            "month",
            date(2026, 10, 15),
            CONFIG,
        )
        self.assertEqual(stats["start"], "2026-10-01")
        self.assertEqual(stats["end"], "2026-10-31")
        self.assertEqual(len(stats["buckets"]), 31)
        self.assertEqual(stats["summary"]["samples"], 3)

    def test_weather_disabled(self):
        config = dict(CONFIG, weather_enabled=False)
        stats = build_stats(
            FakeStore(ROWS[:1]),
            FakeWeather(WEATHER_HOURS),
            make_room(),
            "day",
            date(2026, 10, 20),
            config,
        )
        self.assertFalse(stats["weather"]["ok"])
        self.assertIn("désactivée", stats["weather"]["error"])
        self.assertTrue(
            all(bucket["outside"] is None for bucket in stats["buckets"])
        )

    def test_bad_weather_coordinates(self):
        config = {"weather_enabled": True, "weather_latitude": None, "weather_longitude": None}
        stats = build_stats(
            FakeStore(ROWS[:1]),
            FakeWeather(WEATHER_HOURS),
            make_room(),
            "day",
            date(2026, 10, 20),
            config,
        )
        self.assertFalse(stats["weather"]["ok"])
        self.assertIn("coordonnées", stats["weather"]["error"].lower())

    def test_no_samples_yields_empty_buckets(self):
        stats = build_stats(
            FakeStore([]),
            FakeWeather({}),
            make_room(),
            "day",
            date(2026, 10, 20),
            CONFIG,
        )
        self.assertEqual(stats["summary"]["samples"], 0)
        self.assertIsNone(stats["summary"]["ambient_avg"])
        self.assertEqual(stats["summary"]["buckets_with_data"], 0)

    def test_devices_without_id_ignored(self):
        room = {
            "id": 2,
            "name": "Chambre",
            "devices": [
                {"device_id": None, "name": "fantôme"},
                {"device_id": 9, "name": "Clim chambre"},
            ],
        }
        stats = build_stats(
            FakeStore(ROWS[:1]),
            FakeWeather(WEATHER_HOURS),
            room,
            "day",
            date(2026, 10, 20),
            CONFIG,
        )
        self.assertEqual(len(stats["devices"]), 1)
        self.assertEqual(stats["devices"][0]["device_id"], 9)


class SetpointSourceTests(unittest.TestCase):
    """La consigne d'une pièce vient de son actuateur, pas d'une moyenne."""

    def build(self, rows, room):
        return build_stats(
            DeviceStore(rows),
            FakeWeather({}),
            room,
            "day",
            date(2026, 10, 20),
            CONFIG,
        )

    def test_actuator_alone_defines_setpoint(self):
        stats = self.build(DEVICE_ROWS, make_room_with_actuator())
        buckets = stats["buckets"]
        # Sans le correctif, la moyenne 11.5/19.5 donnerait 15.5.
        self.assertEqual(buckets[8]["setpoint"], 19.5)
        self.assertEqual(buckets[9]["setpoint"], 19.5)
        self.assertEqual(stats["summary"]["setpoint_avg"], 19.5)
        self.assertEqual(stats["setpoint_device_id"], 28129468)

    def test_ambient_still_aggregates_all_devices(self):
        stats = self.build(DEVICE_ROWS, make_room_with_actuator())
        bucket = stats["buckets"][8]
        # (18.0 + 18.4) / 2 : la sonde Wiser et la clim remontent la pièce.
        self.assertEqual(bucket["ambient"], 18.2)
        self.assertEqual(bucket["count"], 2)
        self.assertEqual(stats["summary"]["samples"], 4)

    def test_room_without_actuator_keeps_legacy_average(self):
        room = make_room_with_actuator()
        room["actuator_device_id"] = None
        stats = self.build(DEVICE_ROWS, room)
        buckets = stats["buckets"]
        self.assertEqual(buckets[8]["setpoint"], 15.5)
        self.assertEqual(stats["summary"]["setpoint_avg"], 15.5)
        self.assertIsNone(stats["setpoint_device_id"])

    def test_actuator_without_setpoint_rows_yields_none(self):
        rows = [
            row
            for row in DEVICE_ROWS
            if row["device_id"] != 28129468
        ] + [
            {"device_id": 28129468, "ts": "2026-10-20T08:10:00",
             "ambient": 18.4, "setpoint": None},
        ]
        stats = self.build(rows, make_room_with_actuator())
        buckets = stats["buckets"]
        # Pas de repli sur le thermostat : la consigne est inconnue.
        self.assertIsNone(buckets[8]["setpoint"])
        self.assertIsNone(stats["summary"]["setpoint_avg"])
        self.assertIsNone(stats["summary"]["gap_avg"])
        # L'ambiance continue d'agréger tous les appareils.
        self.assertEqual(buckets[8]["ambient"], 18.2)
        self.assertEqual(buckets[8]["count"], 2)
        self.assertEqual(stats["setpoint_device_id"], 28129468)


class AnnualTemperatureTests(unittest.TestCase):
    """Température moyenne annuelle : couverture, source et messages."""

    TODAY = date(2026, 11, 20)
    CONFIG = {
        "weather_enabled": True,
        "weather_latitude": 48.85,
        "weather_longitude": 2.35,
    }

    @staticmethod
    def make_weather(days, temperature=10.0):
        hours = {}
        for offset in range(days):
            day = AnnualTemperatureTests.TODAY - timedelta(days=offset)
            hours[day.isoformat()] = {12: temperature}
        return hours

    def annual(self, weather_hours=None, rows=None, config=None):
        weather = FakeAnnualWeather(weather_hours or {})
        store = DeviceStore(rows or [])
        return annual_temperature(
            store,
            weather,
            [make_room()],
            config or self.CONFIG,
            today=self.TODAY,
        )

    def test_full_year_uses_weather(self):
        payload = self.annual(self.make_weather(365, 11.5))
        self.assertEqual(payload["range"], "year")
        self.assertEqual(payload["window_days"], 365)
        self.assertEqual(payload["start"], "2025-11-21")
        self.assertEqual(payload["end"], "2026-11-20")
        self.assertEqual(payload["avg_c"], 11.5)
        self.assertEqual(payload["coverage_days"], 365)
        self.assertEqual(payload["coverage_pct"], 100.0)
        self.assertEqual(payload["source"], "weather")
        self.assertEqual(payload["source_label"], "météo")
        self.assertIsNone(payload["message"])

    def test_partial_coverage_reports_percentage(self):
        payload = self.annual(self.make_weather(180, 12.0))
        self.assertEqual(payload["avg_c"], 12.0)
        self.assertEqual(payload["coverage_days"], 180)
        self.assertEqual(payload["coverage_pct"], 49.3)
        self.assertEqual(payload["source"], "weather")

    def test_no_data_yields_explicit_message(self):
        payload = self.annual()
        self.assertIsNone(payload["avg_c"])
        self.assertEqual(payload["coverage_days"], 0)
        self.assertEqual(payload["coverage_pct"], 0.0)
        self.assertIsNone(payload["source"])
        self.assertEqual(payload["message"], "pas de relevés sur l'année")

    def test_best_coverage_wins(self):
        # 100 jours de météo contre 200 jours de pièces : les pièces l'emportent.
        rows = []
        for offset in range(200):
            day = self.TODAY - timedelta(days=offset)
            rows.append({
                "device_id": 7,
                "ts": f"{day.isoformat()}T09:00:00",
                "ambient": 20.0,
            })
        payload = self.annual(self.make_weather(100, 10.0), rows)
        self.assertEqual(payload["avg_c"], 20.0)
        self.assertEqual(payload["coverage_days"], 200)
        self.assertEqual(payload["source"], "rooms")
        self.assertEqual(payload["source_label"], "pièces")

    def test_weather_wins_on_equal_coverage(self):
        rows = []
        for offset in range(180):
            day = self.TODAY - timedelta(days=offset)
            rows.append({
                "device_id": 7,
                "ts": f"{day.isoformat()}T09:00:00",
                "ambient": 20.0,
            })
        payload = self.annual(self.make_weather(180, 10.0), rows)
        self.assertEqual(payload["avg_c"], 10.0)
        self.assertEqual(payload["source"], "weather")

    def test_weather_disabled_falls_back_to_rooms(self):
        rows = [
            {"device_id": 7, "ts": "2026-11-20T09:00:00", "ambient": 19.0},
            {"device_id": 7, "ts": "2026-11-19T09:00:00", "ambient": 21.0},
        ]
        payload = self.annual(
            self.make_weather(365, 10.0),
            rows,
            dict(self.CONFIG, weather_enabled=False),
        )
        self.assertEqual(payload["avg_c"], 20.0)
        self.assertEqual(payload["coverage_days"], 2)
        self.assertEqual(payload["source"], "rooms")


if __name__ == "__main__":
    unittest.main()
