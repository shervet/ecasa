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

from ecasa.energy import (  # noqa: E402
    EnergyError,
    EnergyHTTPError,
    EnergyService,
    _parse_daily,
    build_energy,
    parse_energy_day,
    parse_energy_days,
    parse_energy_month,
    parse_energy_year,
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
        # Vue mois : 7 mois consécutifs terminés par le mois courant, les
        # mois de tête sans aucune donnée (toutes années) sont retirés.
        # Ici : sept.–nov. sont vides → bloc = déc. 2024 → mars 2025.
        payload = self.build("month")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["labels"], ["Déc 24", "Jan 25", "Fév 25", "Mar 25"])
        self.assertEqual(payload["years"], [2024, 2025])
        window = payload["window"]
        self.assertEqual(window["size"], 7)
        self.assertEqual(window["ref"], "2025-03")
        self.assertEqual(window["first"], "2024-01")
        self.assertEqual(window["limit"], "2025-03")
        self.assertTrue(window["can_prev"])
        self.assertFalse(window["can_next"])
        self.assertEqual(window["label"], "Déc 24 → Mar 25")
        self.assertEqual(window["range_label"], "Décembre → Mars")
        first, second = payload["series"]
        self.assertEqual(first["label"], "2024")
        self.assertEqual(first["values"], [1.0, 15.0, 20.0, None])
        self.assertEqual(first["color"], "var(--series-2)")
        self.assertEqual(second["label"], "2025")
        self.assertEqual(second["values"], [None, 3.0, 4.0, 6.0])
        self.assertEqual(second["color"], "var(--series-1)")
        # Tooltip : une ligne par année pour chaque mois affiché.
        tooltip = payload["tooltip"]
        self.assertEqual(tooltip["labels"], payload["labels"])
        self.assertEqual(len(tooltip["rows"]), 4)
        self.assertEqual(tooltip["rows"][0][0]["value"], 1.0)   # déc. 2024
        self.assertEqual(tooltip["rows"][0][0]["days"], 1)
        self.assertIsNone(tooltip["rows"][0][1]["value"])       # déc. 2025 : futur
        self.assertEqual(tooltip["rows"][3][1]["value"], 6.0)   # mars 2025

    def test_month_view_without_crossing_year(self):
        # Bloc entièrement dans l'année courante : pas d'année dans les
        # libellés de mois.
        data = {
            date(2025, 1, 10): 3000,
            date(2025, 2, 10): 4000,
            date(2025, 3, 10): 6000,
        }
        service = EnergyService(fetcher=FakeFetcher(data=data))
        payload = build_energy(service, CONFIG, view="month", today=TODAY)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["labels"], ["Jan", "Fév", "Mar"])
        self.assertEqual(payload["years"], [2025])
        self.assertEqual(payload["series"][0]["values"], [3.0, 4.0, 6.0])

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
            "14/01|Mar", "15/01|Mer", "16/01|Jeu", "17/01|Ven", "18/01|Sam", "19/01|Dim", "20/01|Lun",
        ])
        first = payload["series"][0]   # 2024
        self.assertEqual(first["values"][-1], 5.0)   # 20 janvier 2024
        self.assertIsNone(first["values"][0])        # 14 janvier : pas de donnée

    def test_day_view_explicit_window(self):
        payload = self.build("day", month=1, day="2025-01-05", days=3)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["window"]["end"], 5)
        self.assertEqual(payload["window"]["start"], 3)
        self.assertEqual(payload["labels"], ["03/01|Ven", "04/01|Sam", "05/01|Dim"])
        series = {item["label"]: item for item in payload["series"]}
        self.assertEqual(series["2024"]["values"], [None, None, 10.0])

    def test_day_view_window_starts_on_first_of_month(self):
        # Moins de 7 jours écoulés dans le mois : la fenêtre démarre au 1er.
        payload = self.build("day", month=1, day="2025-01-02", days=7)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["labels"], [
            "27/12|Ven", "28/12|Sam", "29/12|Dim", "30/12|Lun", "31/12|Mar", "01/01|Mer", "02/01|Jeu",
        ])
        series = {item["label"]: item for item in payload["series"]}
        self.assertEqual(series["2025"]["values"], [None, None, None, None, None, None, 3.0])

    def test_day_view_current_month_clipped_to_today_and_data(self):
        # TODAY = 15 mars 2025 ; dernière donnée de mars : le 10.
        payload = self.build("day", month=3)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["window"]["end"], 10)
        self.assertEqual(payload["labels"][-1], "10/03|Lun")
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

    def test_week_view_block_of_seven_weeks(self):
        # Vue semaine : 7 semaines ISO consécutives (1 barre = 1 semaine),
        # terminées par la semaine de référence (bornée à la dernière
        # donnée, 10 mars → semaine 11). Les semaines de tête sans aucune
        # donnée (semaines 5 et 6) sont retirées : bloc = S7 → S11.
        payload = self.build("week")
        self.assertTrue(payload["ok"])
        window = payload["window"]
        self.assertEqual(window["size"], 7)
        self.assertEqual(window["iso_week"], 11)
        self.assertEqual(window["start"], "10/02")
        self.assertEqual(window["end"], "16/03")
        self.assertEqual(window["start_iso"], "2025-02-10")
        self.assertEqual(window["end_iso"], "2025-03-16")
        self.assertEqual(window["ref_iso"], "2025-03-10")
        self.assertEqual(window["limit_iso"], "2025-03-10")
        # Lundi de la semaine contenant le 1er janvier 2025 (un mercredi).
        self.assertEqual(window["first_iso"], "2024-12-30")
        self.assertEqual(window["label"], "Semaines 7 → 11 · 10/02 → 16/03")
        self.assertTrue(window["can_prev"])
        self.assertFalse(window["can_next"])
        # Une étiquette par semaine affichée.
        self.assertEqual(payload["labels"], ["S7", "S8", "S9", "S10", "S11"])
        # Une série par année avec donnée dans le bloc : 2024 et 2025.
        self.assertEqual(payload["years"], [2024, 2025])
        first, second = payload["series"]
        self.assertEqual(first["label"], "2024")
        self.assertEqual(first["values"], [20.0, None, None, None, None])
        self.assertEqual(first["color"], "var(--series-2)")
        self.assertEqual(second["label"], "2025")
        self.assertEqual(second["values"], [4.0, None, None, None, 6.0])
        self.assertEqual(second["color"], "var(--series-1)")

    def test_week_view_explicit_day_selects_week(self):
        # Le paramètre ``day`` désigne un jour au sein de la semaine de
        # référence : le 5 janvier 2025 (dimanche) → semaine du 30/12 au
        # 05/01, à cheval sur deux mois ET deux ans. Les semaines
        # précédentes sont vides : le bloc se réduit à cette semaine.
        payload = self.build("week", month=1, day="2025-01-05")
        self.assertTrue(payload["ok"])
        window = payload["window"]
        self.assertEqual(window["start"], "30/12")
        self.assertEqual(window["end"], "05/01")
        self.assertEqual(window["start_iso"], "2024-12-30")
        self.assertEqual(window["end_iso"], "2025-01-05")
        self.assertEqual(window["ref_iso"], "2024-12-30")
        self.assertEqual(window["iso_week"], 1)
        self.assertEqual(window["label"], "Semaine 1 · 30/12–05/01")
        self.assertEqual(payload["labels"], ["S1"])
        self.assertEqual(payload["years"], [2024, 2025])
        series = {item["label"]: item for item in payload["series"]}
        # Semaine complète pour chaque année : 2024 = 31/12 + 05/01.
        self.assertEqual(series["2024"]["values"], [11.0])
        self.assertEqual(series["2025"]["values"], [3.0])

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
        self.assertEqual(payload["labels"], ["S49"])
        series = {item["label"]: item for item in payload["series"]}
        self.assertEqual(series["2025"]["values"], [5.0])
        self.assertEqual(series["2026"]["values"], [8.0])
        self.assertEqual(series["2026"]["color"], "var(--series-1)")
        self.assertEqual(series["2025"]["color"], "var(--series-2)")

    def test_week_view_clipped_to_last_data(self):
        # Dernière donnée le dimanche 9 mars : sans jour explicite, la
        # référence (15 mars) est bornée au 9 → semaine 10, pas semaine 11.
        # Le bloc remonte jusqu'à la semaine 8 (donnée du 20 février).
        data = {date(2025, 2, 20): 3000, date(2025, 3, 9): 5000}
        service = EnergyService(fetcher=FakeFetcher(data=data))
        payload = build_energy(
            service, CONFIG, view="week", today=date(2025, 3, 15)
        )
        window = payload["window"]
        self.assertEqual(window["start"], "17/02")
        self.assertEqual(window["end"], "09/03")
        self.assertEqual(payload["labels"], ["S8", "S9", "S10"])
        series = {item["label"]: item for item in payload["series"]}
        self.assertEqual(series["2025"]["values"], [3.0, None, 5.0])
        # Un jour demandé au-delà de la dernière donnée est borné pareil.
        payload = build_energy(
            service, CONFIG, view="week", today=date(2025, 3, 15),
            month=3, day="2025-03-14",
        )
        self.assertEqual(payload["window"]["start"], "17/02")
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
        self.assertEqual(payload["labels"], ["S53"])
        series = {item["label"]: item for item in payload["series"]}
        self.assertEqual(series["2025"]["values"], [7.0])
        self.assertEqual(series["2024"]["values"], [3.0])

    def test_week_view_leap_day(self):
        # Le 29 février n'existe pas les années communes : la semaine de
        # l'année commune ne totalise que le 28.
        data = {date(2023, 2, 28): 4000, date(2024, 2, 29): 9000}
        service = EnergyService(fetcher=FakeFetcher(data=data))
        payload = build_energy(
            service, {**CONFIG, "med_start": "2023-01-01"}, view="week",
            today=date(2024, 3, 1), month=2, day="2024-02-29",
        )
        self.assertEqual(payload["labels"], ["S9"])
        series = {item["label"]: item for item in payload["series"]}
        self.assertEqual(series["2023"]["values"], [4.0])
        self.assertEqual(series["2024"]["values"], [9.0])

    def test_week_view_excludes_year_without_data_in_window(self):
        # 2023 n'a des données qu'en juin : hors du bloc de semaines
        # affiché, elle n'apparaît ni comme barre ni dans la légende.
        data = {
            date(2023, 6, 15): 9000,
            date(2025, 3, 10): 6000,
        }
        service = EnergyService(fetcher=FakeFetcher(data=data))
        payload = build_energy(
            service, {**CONFIG, "med_start": "2023-01-01"}, view="week",
            today=TODAY,
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["years"], [2025])
        self.assertEqual(
            [series["label"] for series in payload["series"]], ["2025"]
        )
        self.assertEqual(payload["series"][0]["values"], [6.0])

    def test_year_view(self):
        # Vue année : une barre par année avec données sur la période
        # comparée (totaux à date, bornés au 15 mars, date de référence).
        # 2024 : 10 (05/01) + 5 (20/01) + 20 (10/02) = 35 kWh ; le 1 kWh du
        # 31/12/2024 est après la date de référence et n'est pas compté.
        # 2025 : 3 (02/01) + 4 (15/02) + 6 (10/03) = 13 kWh.
        payload = self.build("year")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["labels"], ["2024", "2025"])
        self.assertEqual(payload["years"], [2024, 2025])
        first, second = payload["series"]
        self.assertEqual(first["label"], "2024")
        self.assertEqual(first["values"], [35.0, None])
        self.assertEqual(first["color"], "var(--series-2)")
        self.assertEqual(second["label"], "2025")
        self.assertEqual(second["values"], [None, 13.0])
        self.assertEqual(second["color"], "var(--series-1)")
        # Tooltip : une ligne (l'année cliquée) par période.
        tooltip = payload["tooltip"]
        self.assertEqual(len(tooltip["rows"]), 2)
        self.assertEqual(tooltip["rows"][0][0]["value"], 35.0)
        self.assertEqual(tooltip["rows"][0][0]["days"], 3)
        self.assertEqual(tooltip["rows"][1][0]["value"], 13.0)

    def test_year_view_excludes_year_without_data_in_period(self):
        # 2023 n'a des données qu'en décembre : sur la période comparée
        # (année à date au 15 mars), elle est vide → absente des barres,
        # de la légende (séries) et de ``summary.by_year``.
        data = {
            date(2023, 12, 15): 9000,
            date(2024, 1, 10): 5000,
            date(2025, 1, 10): 3000,
        }
        service = EnergyService(fetcher=FakeFetcher(data=data))
        payload = build_energy(
            service, {**CONFIG, "med_start": "2023-01-01"}, view="year",
            today=TODAY,
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["years"], [2024, 2025])
        self.assertEqual(payload["labels"], ["2024", "2025"])
        self.assertEqual(
            [series["label"] for series in payload["series"]],
            ["2024", "2025"],
        )
        self.assertEqual(
            [row["year"] for row in payload["summary"]["by_year"]],
            [2025, 2024],
        )

    def test_color_is_stable_per_year(self):
        # La couleur dépend du rang de l'année par rapport à l'année
        # courante (N → series-1, N-1 → series-2), pas de la période
        # choisie ni des autres années présentes.
        def colors(payload):
            return {
                series["label"]: series["color"]
                for series in payload["series"]
            }

        # Deux années dans la fenêtre : 2024 garde series-2.
        full = self.build("week")
        self.assertEqual(colors(full), {
            "2024": "var(--series-2)",
            "2025": "var(--series-1)",
        })
        # 2025 seule dans la fenêtre : elle garde series-1 (pas de
        # renumérotation par position).
        data = {date(2024, 12, 31): 1000, date(2025, 3, 10): 6000}
        service = EnergyService(fetcher=FakeFetcher(data=data))
        single = build_energy(service, CONFIG, view="week", today=TODAY)
        self.assertEqual(single["years"], [2025])
        self.assertEqual(colors(single), {"2025": "var(--series-1)"})
        # Même convention dans les autres vues.
        self.assertEqual(
            colors(self.build("month")),
            {"2024": "var(--series-2)", "2025": "var(--series-1)"},
        )
        self.assertEqual(
            colors(self.build("year")),
            {"2024": "var(--series-2)", "2025": "var(--series-1)"},
        )
        self.assertEqual(
            colors(self.build("day", month=1)),
            {"2024": "var(--series-2)"},
        )

    def test_tooltip_has_one_row_per_year(self):
        # Le tableau flottant de la période cliquée : une ligne par année
        # de la comparaison, avec valeur, nombre de jours et couleur.
        payload = self.build("week")
        tooltip = payload["tooltip"]
        self.assertEqual(tooltip["labels"], payload["labels"])
        self.assertEqual(len(tooltip["rows"]), len(payload["labels"]))
        for row in tooltip["rows"]:
            self.assertEqual(len(row), len(payload["years"]))
            for entry in row:
                self.assertIn("label", entry)
                self.assertIn("value", entry)
                self.assertIn("days", entry)
                self.assertIn("color", entry)
        # Première semaine affichée (S7, 10/02) : 2024 = 20 kWh (1 jour),
        # 2025 = 4 kWh (1 jour). Dernière (S11) : 2024 sans donnée.
        first = tooltip["rows"][0]
        self.assertEqual(first[0]["label"], "2024")
        self.assertEqual(first[0]["value"], 20.0)
        self.assertEqual(first[0]["days"], 1)
        self.assertEqual(first[0]["color"], "var(--series-2)")
        self.assertEqual(first[1]["label"], "2025")
        self.assertEqual(first[1]["value"], 4.0)
        last = tooltip["rows"][-1]
        self.assertIsNone(last[0]["value"])
        self.assertEqual(last[0]["days"], 0)
        self.assertEqual(last[1]["value"], 6.0)

    def test_summary_compares_same_period(self):
        # Vue mois : comparaison du bloc affiché (déc. → mars) d'année en
        # année. 2025 = 3 + 4 + 6 = 13 kWh ; 2024 = 1 (déc.) + 10 + 5 + 20
        # = 36 kWh → (13 − 36) / 36 ≈ −63,9 %.
        payload = self.build("month")
        summary = payload["summary"]
        self.assertEqual(summary["year"], 2025)
        self.assertEqual(summary["label"], "Décembre → Mars 2025")
        self.assertEqual(summary["previous_label"], "Décembre → Mars 2024")
        self.assertEqual(summary["total_kwh"], 13.0)
        self.assertEqual(summary["previous_kwh"], 36.0)
        self.assertEqual(summary["delta_pct"], -63.9)

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
        # Vue mois : chaque année sur le bloc affiché (déc. → mars),
        # écart vs N-1 sur les mêmes mois calendaires.
        payload = self.build("month")
        by_year = payload["summary"]["by_year"]
        self.assertEqual([row["year"] for row in by_year], [2025, 2024])
        current, previous = by_year
        self.assertEqual(current["kwh"], 13.0)
        self.assertEqual(current["days"], 3)
        # 2024 sur le bloc : 1 + 10 + 5 + 20 = 36 → (13 − 36) / 36 ≈ −63,9 %.
        self.assertEqual(current["delta_pct"], -63.9)
        self.assertEqual(previous["kwh"], 36.0)
        self.assertEqual(previous["days"], 4)
        self.assertIsNone(previous["delta_pct"])  # plus ancienne année
        # Cohérence avec les totaux du résumé et du graphique.
        self.assertEqual(current["kwh"], payload["summary"]["total_kwh"])
        self.assertEqual(current["delta_pct"], payload["summary"]["delta_pct"])
        series = {item["label"]: item for item in payload["series"]}
        self.assertEqual(current["kwh"], sum(
            value for value in series["2025"]["values"] if value is not None
        ))

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

    def test_parse_energy_year(self):
        self.assertEqual(parse_energy_year("2024-12-01", TODAY), 2024)
        self.assertEqual(parse_energy_year(None, TODAY), 2025)
        self.assertEqual(parse_energy_year("pas-une-date", TODAY), 2025)
        # Jamais dans le futur : ramené à l'année courante.
        self.assertEqual(parse_energy_year("2030-06-15", TODAY), 2025)

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
