"""Tests du suivi de consommation électrique MyElectricalData."""

from __future__ import annotations

import re
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mecasa.energy import (  # noqa: E402
    EnergyError,
    EnergyHTTPError,
    EnergyService,
    _parse_daily,
    build_energy,
    parse_energy_month,
    parse_med_start,
    test_energy,
)

DAILY = {
    date(2024, 1, 5): 10000,    # 10 kWh
    date(2024, 1, 20): 5000,    # 5 kWh
    date(2024, 2, 10): 20000,   # 20 kWh
    date(2024, 12, 31): 1000,   # 1 kWh (semaine ISO 1 de 2025)
    date(2025, 1, 2): 3000,     # 3 kWh
    date(2025, 2, 15): 4000,    # 4 kWh
    date(2025, 3, 10): 6000,    # 6 kWh
}

CONFIG = {
    "med_enabled": True,
    "med_url": "https://med.example",
    "med_token": "secret-token",
    "med_pdl": "12345678901234",
    "med_start": "2024-01-01",
}

TODAY = date(2025, 3, 15)


def parse_range(url: str) -> tuple[date, date]:
    match = re.search(
        r"/start/(\d{4}-\d{2}-\d{2})/end/(\d{4}-\d{2}-\d{2})", url
    )
    if match:
        return date.fromisoformat(match.group(1)), date.fromisoformat(match.group(2))
    match = re.search(r"/(\d{4}-\d{2}-\d{2})/(\d{4}-\d{2}-\d{2})$", url)
    if not match:
        raise AssertionError(f"URL inattendue : {url}")
    return date.fromisoformat(match.group(1)), date.fromisoformat(match.group(2))


class FakeFetcher:
    def __init__(self, data=None, payload_format="enedis", fail=None):
        self.data = dict(DAILY if data is None else data)
        self.payload_format = payload_format
        self.fail = fail or {}
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, headers, timeout=20):
        self.calls.append((url, dict(headers)))
        if url in self.fail:
            raise self.fail[url]
        start, end = parse_range(url)
        points = {
            day: value
            for day, value in self.data.items()
            if start <= day <= end
        }
        if self.payload_format == "enedis":
            return {
                "meter_reading": {
                    "interval_reading": [
                        {"date": day.isoformat(), "value": str(value)}
                        for day, value in sorted(points.items())
                    ]
                }
            }
        return {
            "unit": "w",
            "data": {day.isoformat(): value for day, value in sorted(points.items())},
        }


class ParseTests(unittest.TestCase):
    def test_enedis_wh_to_kwh(self):
        values = _parse_daily({
            "meter_reading": {
                "interval_reading": [
                    {"date": "2024-01-01", "value": "12540"},
                    {"date": "2024-01-02", "value": 9800},
                ]
            }
        })
        self.assertEqual(values, {"2024-01-01": 12.54, "2024-01-02": 9.8})

    def test_self_hosted_data_map(self):
        values = _parse_daily({
            "unit": "w",
            "data": {"2024-01-01": 12540, "2024-01-02": 0},
        })
        self.assertEqual(values, {"2024-01-01": 12.54, "2024-01-02": 0.0})

    def test_invalid_values_are_ignored(self):
        values = _parse_daily({
            "meter_reading": {
                "interval_reading": [
                    {"date": "pas-une-date", "value": 1200},
                    {"date": "2024-01-01", "value": "n/a"},
                    {"date": "2024-01-02", "value": -5},
                ]
            }
        })
        self.assertEqual(values, {})

    def test_unexpected_payload(self):
        with self.assertRaises(EnergyError):
            _parse_daily("texte")


class ServiceTests(unittest.TestCase):
    def test_split_by_year_and_merge(self):
        fetcher = FakeFetcher()
        service = EnergyService(fetcher=fetcher)
        values = service.daily(
            CONFIG["med_url"], CONFIG["med_pdl"], CONFIG["med_token"],
            date(2024, 12, 31), date(2025, 1, 2),
        )
        self.assertEqual(len(fetcher.calls), 2)
        self.assertEqual(values["2024-12-31"], 1.0)
        self.assertEqual(values["2025-01-02"], 3.0)
        headers = fetcher.calls[0][1]
        self.assertEqual(headers["Authorization"], "secret-token")

    def test_cache_avoids_second_call(self):
        fetcher = FakeFetcher()
        service = EnergyService(fetcher=fetcher)
        service.daily("https://med.example", "12345678901234", "token",
                      date(2024, 1, 1), date(2024, 12, 31))
        service.daily("https://med.example", "12345678901234", "token",
                      date(2024, 1, 1), date(2024, 12, 31))
        self.assertEqual(len(fetcher.calls), 1)

    def test_error_is_cached_briefly(self):
        fetcher = FakeFetcher(fail={"https://med.example/daily_consumption/"
                                    "12345678901234/start/2024-01-01/end/2024-12-31":
                                    EnergyError("quota dépassé")})
        service = EnergyService(fetcher=fetcher)
        with self.assertRaises(EnergyError):
            service.daily("https://med.example", "12345678901234", "token",
                          date(2024, 1, 1), date(2024, 12, 31))
        with self.assertRaises(EnergyError):
            service.daily("https://med.example", "12345678901234", "token",
                          date(2024, 1, 1), date(2024, 12, 31))
        self.assertEqual(len(fetcher.calls), 1)

    def test_cache_key_includes_token(self):
        fetcher = FakeFetcher()
        service = EnergyService(fetcher=fetcher)
        service.daily("https://med.example", "12345678901234", "token-a",
                      date(2024, 1, 1), date(2024, 12, 31))
        service.daily("https://med.example", "12345678901234", "token-b",
                      date(2024, 1, 1), date(2024, 12, 31))
        self.assertEqual(len(fetcher.calls), 2)

    def test_404_is_reported(self):
        url = ("https://med.example/daily_consumption/12345678901234/"
               "start/2024-01-01/end/2024-12-31")
        fetcher = FakeFetcher(fail={url: EnergyHTTPError("no measure found", 404)})
        service = EnergyService(fetcher=fetcher)
        with self.assertRaises(EnergyHTTPError) as ctx:
            service.daily("https://med.example", "12345678901234", "token",
                          date(2024, 1, 1), date(2024, 12, 31))
        self.assertEqual(ctx.exception.status, 404)
        self.assertEqual(len(fetcher.calls), 1)

    def test_self_hosted_format(self):
        fetcher = FakeFetcher(payload_format="data")
        service = EnergyService(fetcher=fetcher)
        values = service.daily("https://med.example", "12345678901234", "token",
                               date(2024, 1, 1), date(2024, 12, 31))
        self.assertEqual(values["2024-02-10"], 20.0)


class BuildTests(unittest.TestCase):
    def build(self, view="month", month=None, config=None):
        service = EnergyService(fetcher=FakeFetcher())
        return build_energy(
            service, config or CONFIG, view=view, month=month, today=TODAY
        )

    def test_month_view(self):
        payload = self.build("month")
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["labels"]), 12)
        self.assertEqual(payload["years"], [2024, 2025])
        first, second = payload["series"]
        self.assertEqual(first["label"], "2024")
        self.assertEqual(first["values"][0], 15.0)
        self.assertEqual(first["values"][1], 20.0)
        self.assertEqual(first["values"][11], 1.0)
        self.assertEqual(second["values"][0], 3.0)
        self.assertEqual(second["values"][2], 6.0)

    def test_day_view(self):
        payload = self.build("day", month=1)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["month"], 1)
        self.assertEqual(len(payload["labels"]), 31)
        first = payload["series"][0]
        self.assertEqual(first["values"][4], 10.0)   # 5 janvier
        self.assertEqual(first["values"][19], 5.0)   # 20 janvier
        second = payload["series"][1]
        self.assertEqual(second["values"][1], 3.0)   # 2 janvier

    def test_week_view_uses_iso_year(self):
        payload = self.build("week")
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["labels"]), 53)
        series = {item["label"]: item for item in payload["series"]}
        self.assertEqual(series["2024"]["values"][0], 10.0)
        # 31 décembre 2024 appartient à la semaine ISO 1 de 2025.
        self.assertEqual(series["2025"]["values"][0], 4.0)

    def test_year_view(self):
        payload = self.build("year")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["labels"], ["2024", "2025"])
        self.assertEqual(payload["series"][0]["values"], [36.0, 13.0])

    def test_summary_compares_same_period(self):
        payload = self.build("month")
        summary = payload["summary"]
        self.assertEqual(summary["year"], 2025)
        self.assertEqual(summary["label"], "Année 2025")
        self.assertEqual(summary["total_kwh"], 13.0)
        self.assertEqual(summary["previous_kwh"], 35.0)
        self.assertEqual(summary["delta_pct"], -62.9)

    def test_summary_day_view_uses_selected_month(self):
        payload = self.build("day", month=1)
        summary = payload["summary"]
        self.assertEqual(summary["label"], "Janvier 2025")
        self.assertEqual(summary["previous_label"], "Janvier 2024")
        self.assertEqual(summary["total_kwh"], 3.0)
        self.assertEqual(summary["previous_kwh"], 15.0)
        self.assertEqual(summary["delta_pct"], -80.0)

    def test_summary_week_view_uses_current_iso_week(self):
        data = {date(2024, 3, 12): 8000, date(2025, 3, 10): 6000}
        service = EnergyService(fetcher=FakeFetcher(data=data))
        payload = build_energy(service, CONFIG, view="week", today=TODAY)
        summary = payload["summary"]
        self.assertEqual(summary["label"], "Semaine 11 · 2025")
        self.assertEqual(summary["total_kwh"], 6.0)
        self.assertEqual(summary["previous_kwh"], 8.0)
        self.assertEqual(summary["delta_pct"], -25.0)

    def test_summary_year_view_uses_last_year(self):
        payload = self.build("year")
        summary = payload["summary"]
        self.assertEqual(summary["label"], "Année 2025")
        self.assertEqual(summary["total_kwh"], 13.0)
        self.assertEqual(summary["previous_kwh"], 36.0)
        self.assertEqual(summary["delta_pct"], -63.9)

    def test_summary_zero_previous_is_full_increase(self):
        data = {date(2024, 1, 2): 0, date(2025, 1, 2): 3000}
        service = EnergyService(fetcher=FakeFetcher(data=data))
        payload = build_energy(service, CONFIG, view="month", today=TODAY)
        self.assertEqual(payload["summary"]["previous_kwh"], 0.0)
        self.assertEqual(payload["summary"]["delta_pct"], 100.0)

    def test_disabled(self):
        payload = self.build(config={**CONFIG, "med_enabled": False})
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["enabled"])
        self.assertIn("désactivé", payload["error"])

    def test_missing_credentials(self):
        payload = self.build(config={**CONFIG, "med_token": "", "med_pdl": ""})
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["configured"])

    def test_no_data(self):
        service = EnergyService(fetcher=FakeFetcher(data={}))
        payload = build_energy(service, CONFIG, view="month", today=TODAY)
        self.assertFalse(payload["ok"])
        self.assertIn("Aucune donnée", payload["error"])

    def test_service_error(self):
        primary = ("https://med.example/daily_consumption/12345678901234/"
                   "start/2024-01-01/end/2024-12-31")
        service = EnergyService(fetcher=FakeFetcher(
            fail={primary: EnergyError("token refusé")}
        ))
        payload = build_energy(service, CONFIG, view="month", today=TODAY)
        self.assertFalse(payload["ok"])
        self.assertIn("token refusé", payload["error"])


class HelpersTests(unittest.TestCase):
    def test_parse_energy_month(self):
        self.assertEqual(parse_energy_month("3", TODAY), 3)
        self.assertEqual(parse_energy_month("13", TODAY), 3)
        self.assertEqual(parse_energy_month(None, TODAY), 3)
        self.assertEqual(parse_energy_month("abc", date(2025, 11, 9)), 11)

    def test_parse_med_start(self):
        self.assertEqual(parse_med_start("2023-06-01", TODAY), date(2023, 6, 1))
        # Vide = 1095 jours (limite Enedis), soit le 17 mars 2022.
        self.assertEqual(parse_med_start("", TODAY), date(2022, 3, 17))
        # Une date plus ancienne est ramenée à la limite basse.
        self.assertEqual(parse_med_start("2019-01-01", TODAY), date(2022, 3, 17))
        # Une date future est ramenée à aujourd'hui.
        self.assertEqual(parse_med_start("2030-01-01", TODAY), TODAY)
        self.assertEqual(
            parse_med_start("", date(2028, 2, 29)), date(2025, 3, 2)
        )


class TestConnectionTests(unittest.TestCase):
    def test_success(self):
        service = EnergyService(fetcher=FakeFetcher())
        result = test_energy(CONFIG, service=service, today=TODAY)
        self.assertTrue(result["ok"])
        self.assertEqual(result["pdl"], CONFIG["med_pdl"])
        self.assertIn("jour", result["message"])

    def test_missing_credentials(self):
        result = test_energy(
            {**CONFIG, "med_token": "", "med_pdl": ""}, today=TODAY
        )
        self.assertFalse(result["ok"])

    def test_failure(self):
        primary = ("https://med.example/daily_consumption/12345678901234/"
                   "start/2025-03-08/end/2025-03-15")
        service = EnergyService(fetcher=FakeFetcher(
            fail={primary: EnergyError("injoignable")}
        ))
        result = test_energy(CONFIG, service=service, today=TODAY)
        self.assertFalse(result["ok"])
        self.assertIn("injoignable", result["message"])


if __name__ == "__main__":
    unittest.main()
