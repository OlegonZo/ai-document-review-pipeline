import unittest

from reversal_radar.config import RadarConfig
from reversal_radar.models import BookLevel, Candle, Direction, OrderBook, TimeframeView
from reversal_radar.signals import (
    book_pressure,
    funding_signal,
    level_reaction,
    mtf_alignment,
    open_interest_signal,
    rsi_divergence,
    timeframe_view,
    volume_confirmation,
    volume_spike,
)

CONFIG = RadarConfig(coin="HYPE", level=83.40)
LEVEL = 83.40


def candle(open_price: float, high: float, low: float, close: float, volume: float, index: int = 0) -> Candle:
    return Candle(index * 3_600_000, (index + 1) * 3_600_000, open_price, high, low, close, volume, 100)


def base_series(count: int = 40, price: float = 82.0, volume: float = 500.0) -> list[Candle]:
    return [candle(price, price + 0.2, price - 0.2, price, volume, index) for index in range(count)]


class LevelReactionTests(unittest.TestCase):
    def test_wick_above_level_with_close_below_is_rejection(self) -> None:
        series = base_series()
        series.append(candle(83.0, 83.9, 82.8, 82.9, 900, len(series)))
        signal = level_reaction(series, LEVEL, CONFIG, "1h")
        self.assertEqual(signal.direction, Direction.DOWN)
        self.assertIn("Отказ", signal.title)
        self.assertGreater(signal.strength, 0.4)

    def test_close_above_level_on_volume_is_breakout(self) -> None:
        series = base_series()
        series.append(candle(83.2, 84.1, 83.1, 84.0, 1500, len(series)))
        signal = level_reaction(series, LEVEL, CONFIG, "1h")
        self.assertEqual(signal.direction, Direction.UP)
        self.assertIn("Пробой", signal.title)

    def test_candle_far_from_level_is_neutral(self) -> None:
        signal = level_reaction(base_series(), LEVEL, CONFIG, "1h")
        self.assertEqual(signal.direction, Direction.NEUTRAL)
        self.assertEqual(signal.contribution, 0.0)

    def test_held_breakout_stays_bullish_after_the_break(self) -> None:
        series = base_series()
        series.append(candle(83.2, 84.2, 83.1, 84.0, 1600, len(series)))
        for step in range(4):
            series.append(candle(84.0, 84.4, 83.8, 84.1, 700, len(series) + step))
        signal = level_reaction(series, LEVEL, CONFIG, "1h")
        self.assertEqual(signal.direction, Direction.UP)
        self.assertIn("удержан", signal.title)


class VolumeTests(unittest.TestCase):
    def test_rally_on_fading_volume_is_bearish(self) -> None:
        series = base_series(30)
        for step in range(10):
            price = 82.0 + step * 0.15
            series.append(candle(price, price + 0.1, price - 0.1, price + 0.1, 900 - step * 70, len(series)))
        signal = volume_confirmation(series, CONFIG, "1h")
        self.assertEqual(signal.direction, Direction.DOWN)
        self.assertIn("затухающем", signal.title)

    def test_rally_on_growing_volume_is_bullish(self) -> None:
        series = base_series(30)
        for step in range(10):
            price = 82.0 + step * 0.15
            series.append(candle(price, price + 0.1, price - 0.1, price + 0.1, 400 + step * 90, len(series)))
        signal = volume_confirmation(series, CONFIG, "1h")
        self.assertEqual(signal.direction, Direction.UP)

    def test_red_candle_on_abnormal_volume_is_bearish(self) -> None:
        series = base_series(30, volume=500)
        series.append(candle(83.0, 83.1, 82.0, 82.1, 4200, len(series)))
        signal = volume_spike(series, CONFIG, "15m")
        self.assertEqual(signal.direction, Direction.DOWN)
        self.assertGreater(signal.strength, 0.2)

    def test_normal_volume_produces_no_contribution(self) -> None:
        series = base_series(30)
        series.append(candle(82.0, 82.2, 81.9, 82.1, 505, len(series)))
        self.assertEqual(volume_spike(series, CONFIG, "15m").contribution, 0.0)


class DerivativeTests(unittest.TestCase):
    def test_positive_funding_warns_about_crowded_longs(self) -> None:
        signal = funding_signal(0.00005, CONFIG)  # ~44% годовых
        self.assertEqual(signal.direction, Direction.DOWN)
        self.assertGreater(signal.strength, 0.5)

    def test_negative_funding_flips_direction(self) -> None:
        self.assertEqual(funding_signal(-0.00005, CONFIG).direction, Direction.UP)

    def test_missing_funding_is_not_a_signal(self) -> None:
        self.assertIsNone(funding_signal(None, CONFIG))

    def test_open_interest_growth_without_price_is_a_trap(self) -> None:
        signal = open_interest_signal(1_050_000, 1_000_000, 83.0, 82.99, CONFIG, 4)
        self.assertEqual(signal.direction, Direction.DOWN)
        self.assertIn("цена стоит", signal.title)

    def test_open_interest_growth_with_price_is_continuation(self) -> None:
        signal = open_interest_signal(1_050_000, 1_000_000, 84.0, 82.0, CONFIG, 4)
        self.assertEqual(signal.direction, Direction.UP)

    def test_open_interest_without_history_reports_missing_data(self) -> None:
        signal = open_interest_signal(1_000_000, None, 83.0, None, CONFIG)
        self.assertEqual(signal.direction, Direction.NEUTRAL)
        self.assertIn("Истории", signal.detail)

    def test_thick_asks_are_bearish(self) -> None:
        book = OrderBook(
            bids=tuple(BookLevel(82.9 - step * 0.05, 100) for step in range(10)),
            asks=tuple(BookLevel(83.0 + step * 0.05, 600) for step in range(10)),
        )
        signal = book_pressure(book, CONFIG)
        self.assertEqual(signal.direction, Direction.DOWN)
        self.assertIn("Стена", signal.title)

    def test_missing_book_is_not_a_signal(self) -> None:
        self.assertIsNone(book_pressure(None, CONFIG))


class ConfluenceTests(unittest.TestCase):
    def test_higher_price_with_lower_rsi_is_a_bearish_divergence(self) -> None:
        # Быстрый импульс, откат, затем медленный подъём к новому хаю: цена выше, RSI ниже.
        path = (
            [60 + step * 2.0 for step in range(21)]
            + [100 - step * 1.0 for step in range(1, 9)]
            + [92 + step * 0.36 for step in range(1, 26)]
            + [101 - step * 0.8 for step in range(1, 5)]
        )
        series = [candle(price, price + 0.2, price - 0.2, price, 500, index) for index, price in enumerate(path)]
        signal = rsi_divergence(series, CONFIG, "1h")
        self.assertEqual(signal.direction, Direction.DOWN)
        self.assertIn("дивергенция", signal.title.lower())

    def test_divergence_weight_grows_with_timeframe(self) -> None:
        series = base_series(80)
        self.assertLess(
            rsi_divergence(series, CONFIG, "15m").weight,
            rsi_divergence(series, CONFIG, "4h").weight,
        )

    def test_flat_market_has_no_divergence(self) -> None:
        signal = rsi_divergence(base_series(80), CONFIG, "1h")
        self.assertEqual(signal.direction, Direction.NEUTRAL)

    def test_majority_of_timeframes_sets_direction(self) -> None:
        views = [
            TimeframeView("15m", Direction.UP, 60, 83.0, ""),
            TimeframeView("1h", Direction.UP, 61, 83.0, ""),
            TimeframeView("4h", Direction.UP, 58, 83.0, ""),
            TimeframeView("1d", Direction.DOWN, 45, 83.0, ""),
        ]
        signal = mtf_alignment(views, CONFIG)
        self.assertEqual(signal.direction, Direction.UP)
        self.assertGreater(signal.strength, 0.4)

    def test_split_timeframes_produce_no_contribution(self) -> None:
        views = [
            TimeframeView("1h", Direction.UP, 60, 83.0, ""),
            TimeframeView("4h", Direction.DOWN, 40, 83.0, ""),
        ]
        self.assertEqual(mtf_alignment(views, CONFIG).contribution, 0.0)

    def test_timeframe_view_needs_enough_history(self) -> None:
        self.assertIsNone(timeframe_view(base_series(20), "1h"))
        self.assertIsNotNone(timeframe_view(base_series(60), "1h"))


if __name__ == "__main__":
    unittest.main()
