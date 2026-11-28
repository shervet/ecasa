"""Tests du module pur de courbe de chauffe (fit, avance, table)."""

import math
import threading
import time
import unittest
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest import mock

from ecasa import heating_curve
from ecasa.engine import Engine, MIN_POLL_SECONDS
from ecasa.main import _enrich_room_heating_curve
from ecasa.heating_curve import (
    _outside_temp_at,
    advance_minutes,
    build_table,
    fit_tau,
    room_transitions,
)
from ecasa.storage import MemoryStore
from ecasa.weather import WeatherError


def transition(t_out, t_room, target, tau_hours, loss_minutes=0.0):
    """Montée synthétique : lag théorique du modèle RC, avec petites pertes."""
    ratio = (target - t_out) / (t_room - t_out)
    lag = 60.0 * tau_hours * math.log(ratio) - loss_minutes
    return {
        "t_out": t_out,
        "t_room": t_room,
        "target": target,
        "lag_minutes": lag,
    }


class FitTauTests(unittest.TestCase):
    def test_recovers_known_linear_tau_on_twelve_transitions(self):
        p0, p1 = 3.0, 0.06
        transitions = [
            transition(
                t_out=-5.0 + i * 1.6,
                t_room=17.0,
                target=21.0,
                tau_hours=p0 + p1 * (-5.0 + i * 1.6),
                loss_minutes=0.2 + (i % 4) * 0.2,  # petites pertes (0,2 à 0,8 min)
            )
            for i in range(12)
        ]

        fit = fit_tau(transitions)
        self.assertIsNotNone(fit)
        self.assertEqual(fit["n"], 12)
        self.assertAlmostEqual(fit["tau0"], p0, delta=0.15)
        self.assertAlmostEqual(fit["tau1"], p1, delta=0.03)
        self.assertGreater(fit["r2"], 0.99)

    def test_returns_none_with_fewer_than_three_valid_samples(self):
        t_out = 0.0
        transitions = [
            transition(t_out + i, 17.0, 21.0, 2.8) for i in range(2)
        ]
        self.assertIsNone(fit_tau(transitions))

        three = [transition(t_out + i, 17.0, 21.0, 2.8) for i in range(3)]
        fit = fit_tau(three)
        self.assertIsNotNone(fit)
        self.assertEqual(fit["n"], 3)

    def test_accepts_tau_of_fourteen_hours(self):
        transitions = [transition(float(i), 17.0, 21.0, 14.0) for i in range(3)]
        counters = {}
        fit = fit_tau(transitions, counters=counters)
        self.assertIsNotNone(fit)
        self.assertEqual(fit["n"], 3)
        self.assertEqual(counters["kept"], 3)
        self.assertEqual(counters["reject_tau_range"], 0)
        self.assertAlmostEqual(fit["tau0"], 14.0, delta=0.01)

    def test_filters_invalid_samples(self):
        valid = [
            transition(float(i), 17.0, 21.0, 2.5) for i in range(5)
        ]
        invalid = [
            # target <= t_out + 0,5
            {"t_out": 20.6, "t_room": 17.0, "target": 21.0, "lag_minutes": 30.0},
            # t_room <= t_out + 0,5
            {"t_out": 16.6, "t_room": 17.0, "target": 21.0, "lag_minutes": 30.0},
            # target <= t_room + 0,5
            {"t_out": 0.0, "t_room": 20.8, "target": 21.0, "lag_minutes": 30.0},
            # tau hors de ]0, 24]
            {"t_out": 0.0, "t_room": 17.0, "target": 21.0, "lag_minutes": 0.0},
            {"t_out": 0.0, "t_room": 17.0, "target": 21.0, "lag_minutes": 400.0},
            # données manquantes / malformées
            {"t_out": 0.0, "t_room": 17.0, "target": 21.0},
            {"t_out": "x", "t_room": 17.0, "target": 21.0, "lag_minutes": 30.0},
        ]

        fit = fit_tau(valid + invalid)
        self.assertIsNotNone(fit)
        self.assertEqual(fit["n"], 5)
        self.assertAlmostEqual(fit["tau0"], 2.5, delta=0.05)
        self.assertAlmostEqual(fit["tau1"], 0.0, delta=0.05)

    def test_counters_report_each_internal_filter(self):
        counters = {}
        transitions = [transition(float(i), 17.0, 21.0, 2.5) for i in range(5)]
        transitions += [
            # t_room trop proche de t_out : ratio non calculable.
            {"t_out": 16.6, "t_room": 17.0, "target": 21.0, "lag_minutes": 30.0},
            # τ hors de ]0, 24 h].
            {"t_out": 0.0, "t_room": 17.0, "target": 21.0, "lag_minutes": 0.0},
            # target <= t_room + 0,5.
            transition(0.0, 20.8, 21.0, 2.5),
        ]

        fit = fit_tau(transitions, counters=counters)
        self.assertIsNotNone(fit)
        self.assertEqual(
            counters,
            {
                "kept": 5,
                "reject_ratio": 1,
                "reject_tau_range": 1,
                "reject_delta": 1,
                "total": 8,
            },
        )
        self.assertEqual(
            fit["debug"],
            {
                "kept": 5,
                "reject_ratio": 1,
                "reject_tau_range": 1,
                "reject_delta": 1,
            },
        )

    def test_counters_are_filled_even_when_fit_fails(self):
        counters = {}
        transitions = [
            {"t_out": 0.0, "t_room": 17.0, "target": 21.0, "lag_minutes": 0.0}
            for _ in range(6)
        ]
        self.assertIsNone(fit_tau(transitions, counters=counters))
        self.assertEqual(
            counters,
            {
                "kept": 0,
                "reject_ratio": 0,
                "reject_tau_range": 6,
                "reject_delta": 0,
                "total": 6,
            },
        )

    def test_empty_or_malformed_input_returns_none(self):
        for value in (
            None,
            [],
            [{}],
            ["pas un dict"],
            [{"t_out": None, "t_room": None, "target": None, "lag_minutes": None}],
        ):
            with self.subTest(value=value):
                self.assertIsNone(fit_tau(value))


class AdvanceMinutesTests(unittest.TestCase):
    def test_zero_when_delta_below_epsilon(self):
        self.assertEqual(advance_minutes(3.0, 0.0, None, 20.0, 20.0), 0.0)
        self.assertEqual(advance_minutes(3.0, 0.0, None, 19.8, 20.0), 0.0)
        self.assertEqual(advance_minutes(3.0, 0.0, None, 21.0, 20.0), 0.0)

    def test_grows_with_delta_and_tau(self):
        small_delta = advance_minutes(0.5, 0.0, None, 19.5, 20.0)
        big_delta = advance_minutes(0.5, 0.0, None, 19.0, 20.0)
        self.assertGreater(small_delta, 0.0)
        self.assertGreater(big_delta, small_delta)

        short_tau = advance_minutes(0.5, 0.0, None, 19.5, 20.0)
        long_tau = advance_minutes(1.0, 0.0, None, 19.5, 20.0)
        self.assertGreater(long_tau, short_tau)

    def test_capped_at_cap_minutes(self):
        self.assertEqual(
            advance_minutes(20.0, 0.0, None, 15.0, 22.0, cap_minutes=120),
            120.0,
        )
        # Défaut verrouillé à 120 min (2 h).
        self.assertEqual(advance_minutes(20.0, 0.0, None, 15.0, 22.0), 120.0)

    def test_missing_t_out_uses_tau0_only_branch(self):
        without = advance_minutes(0.6, 0.2, None, 19.5, 20.0)
        tau0_only = advance_minutes(0.6, 0.0, None, 19.5, 20.0)
        self.assertAlmostEqual(without, tau0_only, places=6)
        # Avec t_out connu, la formule calibrée (target - eps - t_out) diffère.
        with_t_out = advance_minutes(0.6, 0.2, 5.0, 19.5, 20.0)
        self.assertNotAlmostEqual(with_t_out, without, places=6)

    def test_agrees_with_fitted_tau_for_known_t_out(self):
        # Montée synthétique calibrée à τ = 2,5 h (t_out=5, t_room=17, cible=21).
        transitions = [
            transition(4.0, 17.0, 21.0, 2.5),
            transition(5.0, 17.0, 21.0, 2.5),
            transition(6.0, 17.0, 21.0, 2.5),
        ]
        fit = fit_tau(transitions)
        self.assertIsNotNone(fit)
        self.assertAlmostEqual(fit["tau0"], 2.5, delta=1e-6)
        self.assertAlmostEqual(fit["tau1"], 0.0, delta=1e-6)

        minutes = advance_minutes(fit["tau0"], fit["tau1"], 5.0, 17.0, 21.0)
        expected = 2.5 * 60.0 * math.log((21.0 - 0.2 - 5.0) / (17.0 - 5.0))
        self.assertAlmostEqual(minutes, expected, places=6)
        self.assertAlmostEqual(minutes, 41.3, delta=0.1)

    def test_non_positive_tau_returns_zero(self):
        self.assertEqual(advance_minutes(0.0, 0.0, None, 16.0, 20.0), 0.0)
        self.assertEqual(advance_minutes(-1.0, 0.0, None, 16.0, 20.0), 0.0)

    def test_invalid_types_return_zero(self):
        for args in (
            ("abc", 0.0, None, 16.0, 20.0),
            (None, 0.0, None, 16.0, 20.0),
            (3.0, 0.0, None, "abc", 20.0),
            (3.0, 0.0, None, 16.0, None),
        ):
            with self.subTest(args=args):
                self.assertEqual(advance_minutes(*args), 0.0)
        self.assertEqual(
            advance_minutes(3.0, 0.0, None, 16.0, 20.0, eps="x"), 0.0
        )
        self.assertEqual(
            advance_minutes(3.0, 0.0, None, 16.0, 20.0, cap_minutes="x"), 0.0
        )


class BuildTableTests(unittest.TestCase):
    def test_thirteen_rows_increasing_bounded_and_rounded(self):
        table = build_table(3.0, 0.0, None, None, t_min=13.0, t_max=19.0, step=0.5)
        self.assertEqual(len(table), 13)

        temps = [row[0] for row in table]
        self.assertEqual(temps[0], 13.0)
        self.assertEqual(temps[-1], 19.0)
        self.assertEqual(temps, sorted(temps))
        self.assertEqual(len(set(temps)), 13)

        for temp, minutes in table:
            self.assertEqual(temp, round(temp, 1))
            self.assertEqual(minutes, round(minutes, 1))
            self.assertGreaterEqual(minutes, 0.0)
            self.assertLessEqual(minutes, 120.0)

    def test_known_t_out_produces_non_flat_table(self):
        # Scénario du bug : τ = 3 h + 0,06 h/°C à t_out = 5 °C, cible 21 °C.
        table = build_table(3.0, 0.06, 5.0, 21.0)
        self.assertEqual(len(table), 13)
        minutes = [row[1] for row in table]
        self.assertGreater(len(set(minutes)), 1)
        self.assertEqual(minutes, sorted(minutes, reverse=True))
        expected_last = round(
            (3.0 + 0.06 * 5.0)
            * 60.0
            * math.log((21.0 - 0.2 - 5.0) / (19.0 - 5.0)),
            1,
        )
        self.assertEqual(minutes[-1], expected_last)

    def test_missing_target_defaults_to_19(self):
        self.assertEqual(
            build_table(3.0, 0.1, 5.0, None),
            build_table(3.0, 0.1, 5.0, 19.0),
        )
        self.assertNotEqual(
            build_table(3.0, 0.1, 5.0, None),
            build_table(3.0, 0.1, 5.0, 22.0),
        )


LAT, LON = 45.0, 5.0
DEVICE_ID = 9100
CONFIG = {
    "weather_enabled": True,
    "weather_latitude": LAT,
    "weather_longitude": LON,
}


class _FailingCache(dict):
    """Cache météo qui lève ``WeatherError`` sur certaines dates."""

    def __init__(self, failing_dates=()):
        super().__init__()
        self.failing_dates = set(failing_dates)

    def get(self, key, default=None):
        if key[2] in self.failing_dates:
            raise WeatherError("météo indisponible")
        return super().get(key, default)


class FakeWeather:
    """Weather factice : cache positif + erreur ciblée par date."""

    def __init__(self, hours_by_date=None, failing_dates=()):
        self._lock = threading.Lock()
        self._cache = _FailingCache(failing_dates)
        for day, hours in (hours_by_date or {}).items():
            self._cache[(LAT, LON, day, day)] = (
                time.time() + 600.0,
                SimpleNamespace(hours={day: hours}),
            )

    def hourly(self, latitude, longitude, start, end):
        raise WeatherError("météo indisponible")


class _Client:
    pass


def _moment(days_ago, hour=8):
    return (datetime.now() - timedelta(days=days_ago)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )


def _rise_rows(moment, t_room=17.0, target=21.0, reached=True):
    """Relevés horaires : avant montée, montée, puis cible atteinte (ou non)."""
    ambient_end = target - 0.1 if reached else 18.9
    return [
        {
            "device_id": DEVICE_ID,
            "ts": moment - timedelta(hours=1),
            "ambient": 17.0,
            "setpoint": 17.0,
        },
        {
            "device_id": DEVICE_ID,
            "ts": moment,
            "ambient": t_room,
            "setpoint": target,
        },
        {
            "device_id": DEVICE_ID,
            "ts": moment + timedelta(hours=1),
            "ambient": 18.5,
            "setpoint": target,
        },
        {
            "device_id": DEVICE_ID,
            "ts": moment + timedelta(hours=2),
            "ambient": ambient_end,
            "setpoint": target,
        },
    ]


def _store_with_rises(rows):
    store = MemoryStore(None)
    store.upsert_device(DEVICE_ID, "Radiateur test")
    room = store.add_room("Chambre test")
    store.update_room(
        room["id"],
        {"actuator_device_id": DEVICE_ID, "heating_curve": True},
    )
    store.update_device(DEVICE_ID, {"room_id": room["id"], "device_type": "wiser"})
    store.add_samples(rows)
    return store, room["id"]


class RoomTransitionsCountersTests(unittest.TestCase):
    def test_counters_are_exact(self):
        moments = {n: _moment(n) for n in (5, 4, 3, 2, 1)}
        weather = FakeWeather(
            hours_by_date={
                moments[n].date().isoformat(): {8: 5.0 + n} for n in moments
            },
            failing_dates={moments[2].date().isoformat()},
        )
        rows = []
        rows += _rise_rows(moments[5])  # valide
        rows += _rise_rows(moments[4])  # valide
        rows += _rise_rows(moments[3], t_room=25.0)  # hors plage
        rows += _rise_rows(moments[2])  # sans météo
        rows += _rise_rows(moments[1], reached=False)  # cible non atteinte
        store, room_id = _store_with_rises(rows)

        counters = {}
        transitions = room_transitions(
            store, room_id, weather=weather, config=CONFIG, counters=counters
        )

        self.assertEqual(
            counters,
            {
                "rises": 5,
                "out_of_range": 1,
                "no_t_out": 1,
                "no_lag": 1,
                "valid": 2,
            },
        )
        self.assertEqual(len(transitions), 2)


class WeatherFallbackTests(unittest.TestCase):
    def setUp(self):
        self.days = (9, 8, 7, 6, 5, 4)
        failing = {_moment(n).date().isoformat() for n in self.days}
        self.weather = FakeWeather(failing_dates=failing)
        rows = []
        for n in self.days:
            rows += _rise_rows(_moment(n))
        self.store, self.room_id = _store_with_rises(rows)

    def test_without_fallback_all_rises_are_rejected(self):
        counters = {}
        transitions = room_transitions(
            self.store,
            self.room_id,
            weather=self.weather,
            config=CONFIG,
            counters=counters,
        )
        self.assertEqual(transitions, [])
        self.assertEqual(counters["rises"], 6)
        self.assertEqual(counters["no_t_out"], 6)
        self.assertIsNone(fit_tau(transitions))

    def test_fallback_forces_real_weather_to_10c(self):
        weather = FakeWeather(
            hours_by_date={_moment(8).date().isoformat(): {8: -2.0}},
            failing_dates={_moment(9).date().isoformat()},
        )
        store, room_id = _store_with_rises(
            _rise_rows(_moment(9)) + _rise_rows(_moment(8))
        )
        transitions = room_transitions(
            store, room_id, weather=weather, config=CONFIG, t_out_fallback=10.0
        )
        self.assertEqual(len(transitions), 2)
        self.assertEqual({item["t_out"] for item in transitions}, {10.0})

    def test_fallback_with_mixed_weather_forces_10c_and_zero_slope(self):
        present_days = (8, 7)
        missing_days = (9, 6, 5)
        weather = FakeWeather(
            hours_by_date={
                _moment(n).date().isoformat(): {8: -2.0 + n} for n in present_days
            },
            failing_dates={_moment(n).date().isoformat() for n in missing_days},
        )
        rows = []
        for n in present_days + missing_days:
            rows += _rise_rows(_moment(n))
        store, room_id = _store_with_rises(rows)
        counters = {}
        transitions = room_transitions(
            store,
            room_id,
            weather=weather,
            config=CONFIG,
            counters=counters,
            t_out_fallback=10.0,
        )
        self.assertEqual(len(transitions), 5)
        self.assertTrue(all(item["t_out"] == 10.0 for item in transitions))
        self.assertEqual(counters["no_t_out"], 0)
        fit = fit_tau(transitions)
        self.assertIsNotNone(fit)
        self.assertEqual(fit["n"], 5)
        self.assertAlmostEqual(fit["tau1"], 0.0, places=6)

    def test_fallback_uses_10c_and_fit_has_zero_slope(self):
        counters = {}
        transitions = room_transitions(
            self.store,
            self.room_id,
            weather=self.weather,
            config=CONFIG,
            counters=counters,
            t_out_fallback=10.0,
        )
        self.assertEqual(len(transitions), 6)
        self.assertTrue(all(item["t_out"] == 10.0 for item in transitions))
        self.assertEqual(
            counters,
            {
                "rises": 6,
                "out_of_range": 0,
                "no_t_out": 0,
                "no_lag": 0,
                "valid": 6,
            },
        )
        fit = fit_tau(transitions)
        self.assertIsNotNone(fit)
        self.assertEqual(fit["n"], 6)
        self.assertAlmostEqual(fit["tau1"], 0.0, places=6)


class OutsideTempRobustnessTests(unittest.TestCase):
    def test_weather_error_returns_none_and_keeps_positive_cache(self):
        good_day = _moment(3).date().isoformat()
        bad_day = _moment(2).date().isoformat()
        weather = FakeWeather(
            hours_by_date={good_day: {8: 7.5}},
            failing_dates={bad_day},
        )
        self.assertIsNone(_outside_temp_at(weather, CONFIG, _moment(2)))
        self.assertEqual(_outside_temp_at(weather, CONFIG, _moment(3)), 7.5)

    def test_bad_hour_keys_return_none_without_raising(self):
        day = _moment(2).date().isoformat()
        weather = FakeWeather(hours_by_date={day: {"08": 7.5}})
        self.assertIsNone(_outside_temp_at(weather, CONFIG, _moment(2)))


class CurveFitDebugTests(unittest.TestCase):
    def _refresh(self, weather):
        days = (9, 8, 7, 6, 5, 4)
        rows = []
        for n in days:
            rows += _rise_rows(_moment(n))
        store, room_id = _store_with_rises(rows)
        engine = Engine(
            store, _Client(), weather=weather, config_getter=lambda: CONFIG
        )
        with mock.patch("ecasa.engine.time.sleep"):
            engine.refresh_room_curve_fit(room_id, force=True)
        return store.room(room_id)["curve_fit"]

    def test_debug_reports_meteo_source(self):
        days = (9, 8, 7, 6, 5, 4)
        weather = FakeWeather(
            hours_by_date={
                _moment(n).date().isoformat(): {8: 5.0 + n} for n in days
            }
        )
        fit = self._refresh(weather)
        self.assertEqual(
            fit["debug"],
            {
                "rises": 6,
                "out_of_range": 0,
                "no_t_out": 0,
                "no_lag": 0,
                "valid": 6,
                "source": "meteo",
                "fit": {
                    "kept": 6,
                    "reject_ratio": 0,
                    "reject_tau_range": 0,
                    "reject_delta": 0,
                    "total": 6,
                },
                "transitions": [
                    {
                        "t_out": t_out,
                        "t_room": 17.0,
                        "target": 21.0,
                        "lag_minutes": 120.0,
                    }
                    for t_out in (14.0, 13.0, 12.0, 11.0, 10.0, 9.0)
                ],
            },
        )
        self.assertNotIn("t_out_fallback", fit)

    def test_debug_reports_fallback_source(self):
        days = (9, 8, 7, 6, 5, 4)
        weather = FakeWeather(
            failing_dates={_moment(n).date().isoformat() for n in days}
        )
        fit = self._refresh(weather)
        self.assertEqual(
            fit["debug"],
            {
                "rises": 6,
                "out_of_range": 0,
                "no_t_out": 0,
                "no_lag": 0,
                "valid": 6,
                "source": "fallback_10c",
                "fit": {
                    "kept": 6,
                    "reject_ratio": 0,
                    "reject_tau_range": 0,
                    "reject_delta": 0,
                    "total": 6,
                },
                "transitions": [
                    {
                        "t_out": 10.0,
                        "t_room": 17.0,
                        "target": 21.0,
                        "lag_minutes": 120.0,
                    }
                    for _ in range(6)
                ],
            },
        )
        self.assertIs(fit["t_out_fallback"], True)
        self.assertEqual(fit["n"], 6)

    def test_fit_none_keeps_fit_and_transitions_debug(self):
        days = (9, 8, 7, 6, 5, 4)
        weather = FakeWeather(
            hours_by_date={
                _moment(n).date().isoformat(): {8: 16.6} for n in days
            }
        )
        rows = []
        for n in days:
            rows += _rise_rows(_moment(n))
        store, room_id = _store_with_rises(rows)
        engine = Engine(
            store, _Client(), weather=weather, config_getter=lambda: CONFIG
        )
        engine.refresh_room_curve_fit(room_id, force=True)
        fit = store.room(room_id)["curve_fit"]

        self.assertEqual(fit["n"], 0)
        self.assertEqual(
            fit["debug"]["fit"],
            {
                "kept": 0,
                "reject_ratio": 6,
                "reject_tau_range": 0,
                "reject_delta": 0,
                "total": 6,
            },
        )
        self.assertEqual(
            fit["debug"]["transitions"],
            [
                {
                    "t_out": 16.6,
                    "t_room": 17.0,
                    "target": 21.0,
                    "lag_minutes": 120.0,
                }
                for _ in range(6)
            ],
        )


class WeatherWarmTests(unittest.TestCase):
    """Le cache météo est réchauffé par chunks avant l'apprentissage."""

    DAYS = (9, 8, 7, 6, 5, 4)

    def _refresh(self, weather):
        rows = []
        for n in self.DAYS:
            rows += _rise_rows(_moment(n))
        store, room_id = _store_with_rises(rows)
        engine = Engine(
            store, _Client(), weather=weather, config_getter=lambda: CONFIG
        )
        snapshot = {}
        real = heating_curve.room_transitions

        def spy(*args, **kwargs):
            snapshot.setdefault("calls_before", len(weather.calls))
            return real(*args, **kwargs)

        with mock.patch.object(
            heating_curve, "room_transitions", side_effect=spy
        ), mock.patch("ecasa.engine.time.sleep"):
            engine.refresh_room_curve_fit(room_id, force=True)
        return store.room(room_id)["curve_fit"], snapshot

    def test_cache_is_warmed_by_monthly_chunks_before_transitions(self):
        weather = WarmingWeather()
        fit, snapshot = self._refresh(weather)

        self.assertGreaterEqual(len(weather.calls), 1)
        # Au moins un appel couvre une grande partie de la fenêtre de 120 j.
        today = date.today()
        self.assertEqual(weather.calls[0][1], today)
        self.assertLessEqual(
            min(start for start, _end in weather.calls),
            today - timedelta(days=90),
        )
        self.assertTrue(
            any((end - start).days >= 20 for start, end in weather.calls)
        )
        # Réchauffage terminé avant l'extraction des transitions.
        self.assertEqual(snapshot["calls_before"], len(weather.calls))
        self.assertEqual(fit["debug"]["source"], "meteo")
        self.assertNotIn("t_out_fallback", fit)
        self.assertEqual(fit["n"], 6)

    def test_one_failing_chunk_does_not_interrupt_the_fit(self):
        today = date.today()

        def fail_oldest(start, _end):
            return (today - start).days >= 90

        weather = WarmingWeather(fail_chunk=fail_oldest)
        fit, _snapshot = self._refresh(weather)

        self.assertGreaterEqual(len(weather.calls), 2)
        self.assertEqual(fit["n"], 6)
        self.assertEqual(fit["debug"]["source"], "meteo")

    def test_all_chunks_failing_still_fits_with_fallback(self):
        weather = WarmingWeather(fail_chunk=lambda _start, _end: True)
        fit, _snapshot = self._refresh(weather)

        self.assertEqual(fit["n"], 6)
        self.assertEqual(fit["debug"]["source"], "fallback_10c")
        self.assertIs(fit["t_out_fallback"], True)


class WeatherWarmTtlTests(unittest.TestCase):
    """Le rechauffe meteo est memoise : une seule sequence de fetch par TTL,
    quelle que soit le nombre de pieces (pas de burst entre les pieces)."""

    def _engine(self, weather):
        store, room_id = _store_with_rises(_rise_rows(_moment(5)))
        return Engine(store, _Client(), weather=weather, config_getter=lambda: CONFIG)

    def test_second_warm_within_ttl_does_not_refetch(self):
        weather = WarmingWeather()
        engine = self._engine(weather)
        with mock.patch("ecasa.engine.time.sleep"):
            engine._warm_weather_cache()
        calls_after_first = len(weather.calls)
        self.assertGreaterEqual(calls_after_first, 1)
        with mock.patch("ecasa.engine.time.sleep"):
            engine._warm_weather_cache()
        self.assertEqual(len(weather.calls), calls_after_first)

    def test_forced_warm_bypasses_ttl(self):
        weather = WarmingWeather()
        engine = self._engine(weather)
        with mock.patch("ecasa.engine.time.sleep"):
            engine._warm_weather_cache(force=True)
        first = len(weather.calls)
        self.assertGreaterEqual(first, 1)
        with mock.patch("ecasa.engine.time.sleep"):
            engine._warm_weather_cache(force=True)
        self.assertEqual(len(weather.calls), first * 2)


class EnginePollScheduleTests(unittest.TestCase):
    """La periode de lecture des appareils reste >= MIN_POLL_SECONDS meme si le
    tick suivant (travail lourd : courbe meteo, fit) est long : le prochain
    declenchement de lecture est calcule avant le travail lent."""

    def _run_one_cycle(self, engine, slow_tick_seconds):
        poll_times = []
        poll_event = threading.Event()
        tick_event = threading.Event()
        stop_event = threading.Event()

        def fake_poll():
            poll_times.append(time.time())
            poll_event.set()

        def slow_tick(force=False):
            time.sleep(slow_tick_seconds)
            tick_event.set()

        engine._stop = stop_event
        loop_thread = threading.Thread(
            target=engine._loop, name="engine-poll-test", daemon=True
        )
        with mock.patch.object(engine, "poll", side_effect=fake_poll) as poll_mock, \
             mock.patch.object(engine, "tick", side_effect=slow_tick) as tick_mock:
            loop_thread.start()
            try:
                self.assertTrue(poll_event.wait(5.0))
            finally:
                stop_event.set()
                loop_thread.join(5.0)
        return poll_times, poll_mock, tick_mock

    def test_slow_tick_keeps_next_read_within_min_poll(self):
        engine = Engine(MemoryStore(None), _Client())
        engine._next_poll = time.time() - 1.0  # une lecture est due
        poll_times, poll_mock, tick_mock = self._run_one_cycle(
            engine, slow_tick_seconds=0.05
        )
        self.assertEqual(len(poll_times), 1)
        self.assertTrue(tick_mock.called)
        delay = engine._next_poll - poll_times[0]
        self.assertAlmostEqual(delay, MIN_POLL_SECONDS, delta=2.0)
        self.assertGreaterEqual(engine._next_poll - time.time(), MIN_POLL_SECONDS - 2.0)


class WarmingWeather:
    """Météo factice : chunks multi-jours mémorisés dans le cache du service."""

    def __init__(self, fail_chunk=None):
        self._lock = threading.Lock()
        self._cache = {}
        self.calls = []
        self.fail_chunk = fail_chunk

    def hourly(self, latitude, longitude, start, end):
        self.calls.append((start, end))
        if self.fail_chunk is not None and self.fail_chunk(start, end):
            raise WeatherError("chunk météo indisponible")
        hours = {}
        day = start
        while day <= end:
            hours[day.isoformat()] = {8: 5.0 + day.day % 5}
            day += timedelta(days=1)
        result = SimpleNamespace(hours=hours)
        key = (
            round(float(latitude), 3),
            round(float(longitude), 3),
            start.isoformat(),
            end.isoformat(),
        )
        with self._lock:
            self._cache[key] = (time.time() + 600.0, result)
        return result


class SnapshotHeatingCurveTests(unittest.TestCase):
    def test_snapshot_exposes_t_out_and_fallback(self):
        store = MemoryStore(None)
        engine = Engine(store, _Client())
        app = SimpleNamespace(engine=engine, store=store)

        engine._current_outside_temp = lambda: 7.25
        room = {"id": 1, "heating_curve": True, "curve_fit": {"t_out_fallback": True}}
        hc = _enrich_room_heating_curve(app, room)["heating_curve"]
        self.assertEqual(hc["t_out"], 7.25)
        self.assertIs(hc["t_out_fallback"], True)

        engine._current_outside_temp = lambda: None
        hc = _enrich_room_heating_curve(app, {"id": 1, "heating_curve": True})["heating_curve"]
        self.assertIsNone(hc["t_out"])
        self.assertIs(hc["t_out_fallback"], False)


class CurveParamsTests(unittest.TestCase):
    def test_learning_threshold_is_three_samples(self):
        engine = Engine(MemoryStore(None), _Client())
        expectations = {2: True, 3: False, 10: False}
        for n, expected in expectations.items():
            room = {"curve_fit": {"tau0": 4.0, "tau1": 0.0, "n": n}}
            self.assertEqual(engine._curve_params(room)[2], expected, f"n={n}")


if __name__ == "__main__":
    unittest.main()
