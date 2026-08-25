import unittest

from reversal_radar.indicators import atr, clamp, ema, percent, rsi, slope, sma, swing_highs, swing_lows, zscore
from reversal_radar.levels import detect_levels, nearest_resistance, nearest_support, pick_level
from reversal_radar.models import Candle


def candles(prices: list[float], volume: float = 100.0) -> list[Candle]:
    return [
        Candle(index * 60_000, (index + 1) * 60_000, price, price + 0.5, price - 0.5, price, volume, 10)
        for index, price in enumerate(prices)
    ]


class IndicatorTests(unittest.TestCase):
    def test_sma_starts_after_full_window(self) -> None:
        values = sma([1, 2, 3, 4, 5], 3)
        self.assertEqual(values[:2], [None, None])
        self.assertAlmostEqual(values[2], 2.0)
        self.assertAlmostEqual(values[4], 4.0)

    def test_ema_reacts_faster_than_sma(self) -> None:
        prices = [10] * 20 + [20] * 5
        self.assertGreater(ema(prices, 10)[-1], sma(prices, 10)[-1])

    def test_rsi_is_bounded_and_high_on_pure_uptrend(self) -> None:
        rising = rsi([float(step) for step in range(1, 40)])[-1]
        falling = rsi([float(step) for step in range(40, 1, -1)])[-1]
        self.assertGreater(rising, 95)
        self.assertLess(falling, 5)

    def test_rsi_matches_wilder_reference(self) -> None:
        reference = [
            44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08,
            45.89, 46.03, 45.61, 46.28, 46.28,
        ]
        self.assertAlmostEqual(rsi(reference)[-1], 70.46, delta=0.2)

    def test_atr_needs_enough_candles(self) -> None:
        self.assertIsNone(atr(candles([1, 2, 3])))
        self.assertGreater(atr(candles([float(step) for step in range(30)])), 0)

    def test_zscore_is_none_without_spread(self) -> None:
        self.assertIsNone(zscore([5.0] * 30, 20))
        self.assertIsNone(zscore([1.0] * 25 + [50.0], 20))

    def test_zscore_measures_deviation_of_last_value(self) -> None:
        values = [100.0 + (step % 5) for step in range(25)] + [180.0]
        self.assertGreater(zscore(values, 20), 3)

    def test_slope_sign_follows_direction(self) -> None:
        self.assertGreater(slope([1, 2, 3, 4, 5]), 0)
        self.assertLess(slope([5, 4, 3, 2, 1]), 0)
        self.assertEqual(slope([3, 3, 3]), 0.0)

    def test_pivots_are_confirmed_on_both_sides(self) -> None:
        self.assertEqual(swing_highs([1, 2, 9, 2, 1, 3, 1]), [2])
        self.assertEqual(swing_lows([9, 8, 1, 8, 9]), [2])

    def test_percent_and_clamp_are_safe(self) -> None:
        self.assertAlmostEqual(percent(110, 100), 0.1)
        self.assertEqual(percent(110, 0), 0.0)
        self.assertEqual(clamp(2.5), 1.0)
        self.assertEqual(clamp(-2.5), 0.0)


class LevelTests(unittest.TestCase):
    def make_double_top(self) -> list[Candle]:
        path = (
            [70 + step * 0.5 for step in range(20)]  # рост к 79.5
            + [79.5 - step * 0.4 for step in range(10)]  # откат
            + [75.5 + step * 0.5 for step in range(9)]  # второй подход к тому же уровню
            + [79.5 - step * 0.5 for step in range(12)]
        )
        return candles(path)

    def test_repeated_extremes_collapse_into_one_level(self) -> None:
        levels = detect_levels(self.make_double_top())
        self.assertTrue(levels, "уровни не найдены")
        top = max(levels, key=lambda level: level.touches)
        self.assertGreaterEqual(top.touches, 2)

    def test_nearest_level_helpers_respect_price(self) -> None:
        levels = detect_levels(self.make_double_top())
        price = 76.0
        above = nearest_resistance(levels, price)
        below = nearest_support(levels, price)
        if above:
            self.assertGreater(above.price, price)
        if below:
            self.assertLess(below.price, price)

    def test_manual_level_wins_over_autodetection(self) -> None:
        self.assertEqual(pick_level(self.make_double_top(), 76.0, manual=83.4), 83.4)


if __name__ == "__main__":
    unittest.main()
