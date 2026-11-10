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
    parse_energy_day,
    parse_energy_days,
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
    def build(self, view="month", month=None, config=None, day=None, days=None):
        service = EnergyService(fetcher=FakeFetcher())
        return build_energy(
            service, config or CONFIG, view=view, month=month, today=TODAY,
            day=day, days=days,
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
        # Fenêtre glissante de 7 jours terminant au dernier jour avec
        # données du mois (20 janvier 2024), pas les 31 jours du mois.
        self.assertEqual(payload["window"]["size"], 7)
        self.assertEqual(payload["window"]["end"], 20)
        self.assertEqual(payload["window"]["start"], 14)
        self.assertEqual(payload["labels"], [
            "14/01", "15/01", "16/01", "17/01", "18/01", "19/01", "20/01",
        ])
        first = payload["series"][0]   # 2024
        self.assertEqual(first["values"][-1], 5.0)   # 20 janvier 2024
        self.assertIsNone(first["values"][0])        # 14 janvier : pas de donnée
        second = payload["series"][1]  # 2025 : rien dans la fenêtre
        self.assertTrue(all(value is None for value in second["values"]))

    def test_day_view_explicit_window(self):
        payload = self.build("day", month=1, day="2025-01-05", days=3)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["window"]["end"], 5)
        self.assertEqual(payload["window"]["start"], 3)
        self.assertEqual(payload["labels"], ["03/01", "04/01", "05/01"])
        series = {item["label"]: item for item in payload["series"]}
        self.assertEqual(series["2024"]["values"], [None, None, 10.0])
        self.assertEqual(series["2025"]["values"], [None, None, None])

    def test_day_view_window_starts_on_first_of_month(self):
        # Moins de 7 jours écoulés dans le mois : la fenêtre démarre au 1er.
        payload = self.build("day", month=1, day="2025-01-02", days=7)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["window"]["start"], 1)
        self.assertEqual(payload["labels"], ["01/01", "02/01"])
        series = {item["label"]: item for item in payload["series"]}
        self.assertEqual(series["2025"]["values"], [None, 3.0])

    def test_day_view_current_month_clipped_to_today_and_data(self):
        # TODAY = 15 mars 2025 ; dernière donnée de mars : le 10.
        payload = self.build("day", month=3)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["window"]["end"], 10)
        self.assertEqual(payload["labels"][-1], "10/03")
        series = {item["label"]: item for item in payload["series"]}
        self.assertEqual(series["2025"]["values"][-1], 6.0)

    def test_week_view_without_any_data(self):
        # Aucune donnée sur la période : la vue semaine ne doit pas lever
        # d'exception (fenêtre calculée sur la date du jour).
        service = EnergyService(fetcher=FakeFetcher(data={}))
        payload = build_energy(
            service, CONFIG, view="week", today=TODAY,
        )
        self.assertFalse(payload["ok"])
        self.assertIn("Aucune donnée", payload["error"])

    def test_week_view_window_of_seven_days(self):
        payload = self.build("week")
        self.assertTrue(payload["ok"])
        # Fenêtre : semaine ISO 11 (lundi 10 → dimanche 16 mars), car la
        # référence (15 mars) est bornée à la dernière donnée (10 mars).
        window = payload["window"]
        self.assertEqual(window["size"], 7)
        self.assertEqual(window["iso_week"], 11)
        self.assertEqual(window["start"], "10/03")
        self.assertEqual(window["end"], "16/03")
        self.assertEqual(window["start_iso"], "2025-03-10")
        self.assertEqual(window["end_iso"], "2025-03-16")
        self.assertEqual(window["limit_iso"], "2025-03-10")
        # Lundi de la semaine contenant le 1er janvier 2025 (un mercredi).
        self.assertEqual(window["first_iso"], "2024-12-30")
        self.assertEqual(window["label"], "Semaine 11 · 10–16 Mars")
        # Une étiquette par jour calendaire de la fenêtre (JJ/MM).
        self.assertEqual(payload["labels"], [
            "10/03", "11/03", "12/03", "13/03", "14/03", "15/03", "16/03",
        ])
        # Une série par année avec donnée dans la fenêtre : seule 2025.
        self.assertEqual(payload["years"], [2025])
        series = payload["series"][0]
        self.assertEqual(series["label"], "2025")
        self.assertEqual(
            series["values"], [6.0, None, None, None, None, None, None]
        )

    def test_week_view_explicit_day_selects_week(self):
        # Le paramètre ``day`` désigne un jour au sein de la semaine : le
        # 5 janvier 2025 (dimanche) → semaine du 30/12 au 05/01, à cheval
        # sur deux mois ET deux ans.
        payload = self.build("week", month=1, day="2025-01-05")
        self.assertTrue(payload["ok"])
        window = payload["window"]
        self.assertEqual(window["start"], "30/12")
        self.assertEqual(window["end"], "05/01")
        self.assertEqual(window["start_iso"], "2024-12-30")
        self.assertEqual(window["iso_week"], 1)
        self.assertEqual(window["label"], "Semaine 1 · 30/12–05/01")
        self.assertEqual(payload["labels"], [
            "30/12", "31/12", "01/01", "02/01", "03/01", "04/01", "05/01",
        ])
        series = {item["label"]: item for item in payload["series"]}
        self.assertEqual(series["2024"]["values"], [
            None, 1.0, None, None, None, None, 10.0,
        ])
        self.assertEqual(series["2025"]["values"], [
            None, None, None, 3.0, None, None, None,
        ])

    def test_week_view_spans_two_months(self):
        # Semaine ISO 49 de 2026 : lundi 30/11 → dimanche 06/12.
        data = {
            date(2025, 11, 30): 3000,
            date(2025, 12, 1): 2000,
            date(2026, 11, 30): 7000,
            date(2026, 12, 2): 1000,
        }
        service = EnergyService(fetcher=FakeFetcher(data=data))
        payload = build_energy(
            service, CONFIG, view="week", today=date(2026, 12, 6),
            month=12, day="2026-12-03",
        )
        self.assertTrue(payload["ok"])
        window = payload["window"]
        self.assertEqual(window["iso_week"], 49)
        self.assertEqual(window["start"], "30/11")
        self.assertEqual(window["end"], "06/12")
        self.assertEqual(window["label"], "Semaine 49 · 30/11–06/12")
        self.assertEqual(payload["labels"], [
            "30/11", "01/12", "02/12", "03/12", "04/12", "05/12", "06/12",
        ])
        series = {item["label"]: item for item in payload["series"]}
        self.assertEqual(series["2025"]["values"], [
            3.0, 2.0, None, None, None, None, None,
        ])
        self.assertEqual(series["2026"]["values"], [
            7.0, None, 1.0, None, None, None, None,
        ])

    def test_week_view_clipped_to_last_data(self):
        # Dernière donnée le dimanche 9 mars : sans jour explicite, la
        # référence (15 mars) est bornée au 9 → semaine 10, pas semaine 11.
        data = {date(2025, 2, 20): 3000, date(2025, 3, 9): 5000}
        service = EnergyService(fetcher=FakeFetcher(data=data))
        payload = build_energy(
            service, CONFIG, view="week", today=date(2025, 3, 15)
        )
        window = payload["window"]
        self.assertEqual(window["start"], "03/03")
        self.assertEqual(window["end"], "09/03")
        series = {item["label"]: item for item in payload["series"]}
        self.assertEqual(
            series["2025"]["values"],
            [None, None, None, None, None, None, 5.0],
        )
        # Un jour demandé au-delà de la dernière donnée est borné pareil.
        payload = build_energy(
            service, CONFIG, view="week", today=date(2025, 3, 15),
            month=3, day="2025-03-14",
        )
        self.assertEqual(payload["window"]["start"], "03/03")
        self.assertEqual(payload["window"]["end"], "09/03")

    def test_week_view_future_month_compares_previous_years(self):
        # Décembre sélectionné alors qu'on est en novembre : la semaine
        # reste calculée dans le calendrier de l'année en cours, même si
        # elle est future ; les séries viennent des années précédentes.
        data = {
            date(2025, 12, 29): 7000,
            date(2024, 12, 30): 3000,
        }
        service = EnergyService(fetcher=FakeFetcher(data=data))
        payload = build_energy(
            service, CONFIG, view="week", today=date(2026, 11, 10),
            month=12, day="2026-12-29",
        )
        self.assertTrue(payload["ok"])
        window = payload["window"]
        self.assertEqual(window["start"], "28/12")
        self.assertEqual(window["end"], "03/01")
        self.assertEqual(window["start_iso"], "2026-12-28")
        self.assertEqual(window["label"], "Semaine 53 · 28/12–03/01")
        series = {item["label"]: item for item in payload["series"]}
        self.assertEqual(series["2025"]["values"], [
            None, 7.0, None, None, None, None, None,
        ])
        self.assertEqual(series["2024"]["values"], [
            None, None, 3.0, None, None, None, None,
        ])

    def test_week_view_leap_day(self):
        # Le 29 février n'existe pas les années communes : valeur None.
        data = {date(2023, 2, 28): 4000, date(2024, 2, 29): 9000}
        service = EnergyService(fetcher=FakeFetcher(data=data))
        payload = build_energy(
            service, {**CONFIG, "med_start": "2023-01-01"}, view="week",
            today=date(2024, 3, 1), month=2, day="2024-02-29",
        )
        self.assertEqual(payload["labels"], [
            "26/02", "27/02", "28/02", "29/02", "01/03", "02/03", "03/03",
        ])
        series = {item["label"]: item for item in payload["series"]}
        self.assertEqual(series["2023"]["values"], [
            None, None, 4.0, None, None, None, None,
        ])
        self.assertEqual(series["2024"]["values"], [
            None, None, None, 9.0, None, None, None,
        ])

    def test_year_view(self):
        # Vue année : totaux à date (bornés au 15 mars, date de référence).
        # 2024 : 10 (05/01) + 5 (20/01) + 20 (10/02) = 35 kWh ; le 1 kWh du
        # 31/12/2024 est après la date de référence et n'est pas compté.
        # 2025 : 3 (02/01) + 4 (15/02) + 6 (10/03) = 13 kWh.
        payload = self.build("year")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["labels"], ["2024", "2025"])
        self.assertEqual(payload["series"][0]["values"], [35.0, 13.0])

    def test_summary_compares_same_period(self):
        payload = self.build("month")
        summary = payload["summary"]
        self.assertEqual(summary["year"], 2025)
        self.assertEqual(summary["label"], "Année 2025")
        self.assertEqual(summary["total_kwh"], 13.0)
        self.assertEqual(summary["previous_kwh"], 35.0)
        self.assertEqual(summary["delta_pct"], -62.9)

    def test_summary_day_view_uses_displayed_window(self):
        # Fenêtre par défaut : 14–20 janvier (dernière donnée du mois).
        # Seule l'année 2024 a une donnée dans la fenêtre (20/01 : 5 kWh).
        payload = self.build("day", month=1)
        summary = payload["summary"]
        self.assertEqual(summary["year"], 2024)
        self.assertEqual(summary["label"], "14–20 Janvier 2024")
        self.assertEqual(summary["previous_label"], "14–20 Janvier 2023")
        self.assertEqual(summary["total_kwh"], 5.0)
        self.assertIsNone(summary["previous_kwh"])
        self.assertIsNone(summary["delta_pct"])

    def test_summary_day_view_compares_same_days_across_years(self):
        data = {
            date(2024, 11, 9): 10000,   # 10 kWh
            date(2025, 11, 3): 2000,    # 2 kWh
            date(2025, 11, 9): 8000,    # 8 kWh
        }
        service = EnergyService(fetcher=FakeFetcher(data=data))
        payload = build_energy(
            service, CONFIG, view="day", month=11, today=date(2025, 11, 9),
            day="2025-11-09", days=7,
        )
        summary = payload["summary"]
        # Fenêtre 03–09 novembre : 2025 = 2 + 8, 2024 = 10.
        self.assertEqual(summary["label"], "03–09 Novembre 2025")
        self.assertEqual(summary["total_kwh"], 10.0)
        self.assertEqual(summary["previous_kwh"], 10.0)
        self.assertEqual(summary["delta_pct"], 0.0)

    def test_summary_week_view_uses_current_iso_week(self):
        data = {date(2024, 3, 12): 8000, date(2025, 3, 10): 6000}
        service = EnergyService(fetcher=FakeFetcher(data=data))
        payload = build_energy(service, CONFIG, view="week", today=TODAY)
        summary = payload["summary"]
        self.assertEqual(summary["label"], "Semaine 11 · 10–16 Mars 2025")
        self.assertEqual(summary["total_kwh"], 6.0)
        self.assertEqual(summary["previous_kwh"], 8.0)
        self.assertEqual(summary["delta_pct"], -25.0)

    def test_summary_week_view_without_data_this_year(self):
        # La semaine ISO courante n'a pas encore de donnée : on compare alors
        # la dernière année qui en a, au lieu d'afficher 0 kWh et -100 %.
        data = {date(2024, 3, 12): 8000, date(2024, 3, 13): 4000}
        service = EnergyService(fetcher=FakeFetcher(data=data))
        payload = build_energy(service, CONFIG, view="week", today=TODAY)
        summary = payload["summary"]
        self.assertEqual(summary["year"], 2024)
        self.assertEqual(summary["label"], "Semaine 11 · 10–16 Mars 2024")
        self.assertEqual(summary["total_kwh"], 12.0)
        self.assertIsNone(summary["previous_kwh"])
        self.assertIsNone(summary["delta_pct"])

    def test_summary_year_view_uses_last_year(self):
        # Vue année : comparaison à date (15 mars), comme la vue mois.
        # 2024 à date = 35 kWh (le 31/12 est hors période) -> (13 - 35)/35.
        payload = self.build("year")
        summary = payload["summary"]
        self.assertEqual(summary["label"], "Année 2025")
        self.assertEqual(summary["total_kwh"], 13.0)
        self.assertEqual(summary["previous_kwh"], 35.0)
        self.assertEqual(summary["delta_pct"], -62.9)
        self.assertTrue(summary["partial"])

    def test_summary_zero_previous_is_full_increase(self):
        data = {date(2024, 1, 2): 0, date(2025, 1, 2): 3000}
        service = EnergyService(fetcher=FakeFetcher(data=data))
        payload = build_energy(service, CONFIG, view="month", today=TODAY)
        self.assertEqual(payload["summary"]["previous_kwh"], 0.0)
        self.assertEqual(payload["summary"]["delta_pct"], 100.0)

    def test_summary_by_year_month_view(self):
        # Vue mois : chaque année à date (15 mars), écart vs N-1 à date.
        payload = self.build("month")
        by_year = payload["summary"]["by_year"]
        self.assertEqual([row["year"] for row in by_year], [2025, 2024])
        current, previous = by_year
        self.assertEqual(current["kwh"], 13.0)
        self.assertEqual(current["days"], 3)
        # 2024 au 15 mars : 10 + 5 + 20 = 35 → (13 − 35) / 35 = −62,9 %.
        self.assertEqual(current["delta_pct"], -62.9)
        self.assertEqual(previous["kwh"], 35.0)
        self.assertIsNone(previous["delta_pct"])  # plus ancienne année
        # Cohérence avec les totaux historiques du résumé.
        self.assertEqual(current["kwh"], payload["summary"]["total_kwh"])
        self.assertEqual(current["delta_pct"], payload["summary"]["delta_pct"])

    def test_summary_by_year_year_view(self):
        # Vue année : totaux à date (bornés au 15 mars), l'année en cours
        # reste partielle. 2024 à date = 35 kWh -> (13 - 35)/35 = -62,9 %.
        payload = self.build("year")
        by_year = payload["summary"]["by_year"]
        self.assertEqual([row["year"] for row in by_year], [2025, 2024])
        self.assertEqual(by_year[0]["kwh"], 13.0)
        self.assertEqual(by_year[0]["delta_pct"], -62.9)
        self.assertEqual(by_year[1]["kwh"], 35.0)
        self.assertIsNone(by_year[1]["delta_pct"])
        # Cohérence avec le résumé et le graphique.
        self.assertEqual(by_year[0]["kwh"], payload["summary"]["total_kwh"])
        self.assertEqual(
            by_year[1]["kwh"], payload["series"][0]["values"][0]
        )

    def test_summary_by_year_day_view_uses_window(self):
        data = {
            date(2024, 11, 9): 10000,
            date(2025, 11, 3): 2000,
            date(2025, 11, 9): 8000,
        }
        service = EnergyService(fetcher=FakeFetcher(data=data))
        payload = build_energy(
            service, CONFIG, view="day", month=11, today=date(2025, 11, 9),
            day="2025-11-09", days=7,
        )
        by_year = payload["summary"]["by_year"]
        self.assertEqual([row["year"] for row in by_year], [2025, 2024])
        self.assertEqual(by_year[0]["kwh"], 10.0)
        self.assertEqual(by_year[0]["delta_pct"], 0.0)
        self.assertEqual(by_year[1]["kwh"], 10.0)
        self.assertIsNone(by_year[1]["delta_pct"])

    def test_summary_by_year_empty_without_points(self):
        service = EnergyService(fetcher=FakeFetcher(data={}))
        payload = build_energy(service, CONFIG, view="month", today=TODAY)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["summary"], {})

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

    def test_parse_energy_day(self):
        self.assertEqual(parse_energy_day("2026-11-09", 11), 9)
        # Un jour d'un autre mois est ignoré (fenêtre par défaut).
        self.assertIsNone(parse_energy_day("2026-11-09", 3))
        self.assertIsNone(parse_energy_day("pas-une-date", 11))
        self.assertIsNone(parse_energy_day(None, 11))

    def test_parse_energy_days(self):
        self.assertEqual(parse_energy_days("7"), 7)
        self.assertEqual(parse_energy_days(None), 7)
        self.assertEqual(parse_energy_days("abc"), 7)
        self.assertEqual(parse_energy_days("0"), 1)
        self.assertEqual(parse_energy_days("99"), 31)

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
