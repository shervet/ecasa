"""Tests du module pur de courbe de chauffe (fit, avance, table)."""

import math
import unittest

from ecasa.heating_curve import advance_minutes, build_table, fit_tau


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

    def test_returns_none_with_fewer_than_five_valid_samples(self):
        t_out = 0.0
        transitions = [
            transition(t_out + i, 17.0, 21.0, 2.8) for i in range(4)
        ]
        self.assertIsNone(fit_tau(transitions))

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
            # tau hors de ]0, 12]
            {"t_out": 0.0, "t_room": 17.0, "target": 21.0, "lag_minutes": 0.0},
            {"t_out": 0.0, "t_room": 17.0, "target": 21.0, "lag_minutes": 200.0},
            # données manquantes / malformées
            {"t_out": 0.0, "t_room": 17.0, "target": 21.0},
            {"t_out": "x", "t_room": 17.0, "target": 21.0, "lag_minutes": 30.0},
        ]

        fit = fit_tau(valid + invalid)
        self.assertIsNotNone(fit)
        self.assertEqual(fit["n"], 5)
        self.assertAlmostEqual(fit["tau0"], 2.5, delta=0.05)
        self.assertAlmostEqual(fit["tau1"], 0.0, delta=0.05)

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
        self.assertEqual(advance_minutes(20.0, 0.0, None, 15.0, 22.0), 240.0)

    def test_missing_t_out_uses_tau0_only(self):
        without = advance_minutes(0.6, 0.2, None, 19.5, 20.0)
        tau0_only = advance_minutes(0.6, 0.0, 0.0, 19.5, 20.0)
        with_t_out = advance_minutes(0.6, 0.2, 5.0, 19.5, 20.0)
        self.assertAlmostEqual(without, tau0_only, places=6)
        self.assertNotAlmostEqual(with_t_out, without, places=6)

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
            self.assertLessEqual(minutes, 240.0)

    def test_missing_target_defaults_to_19(self):
        self.assertEqual(
            build_table(3.0, 0.1, 5.0, None),
            build_table(3.0, 0.1, 5.0, 19.0),
        )
        self.assertNotEqual(
            build_table(3.0, 0.1, 5.0, None),
            build_table(3.0, 0.1, 5.0, 22.0),
        )


if __name__ == "__main__":
    unittest.main()
