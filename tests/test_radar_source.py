"""Разбор ответов Hyperliquid проверяется на канонических payload-ах, без сети."""

import time
import unittest

from reversal_radar.sources.base import SourceError
from reversal_radar.sources.hyperliquid import HyperliquidSource, drop_forming
from reversal_radar.models import Candle

NOW_MS = int(time.time() * 1000)

META = [
    {"universe": [{"name": "BTC", "szDecimals": 5}, {"name": "HYPE", "szDecimals": 2}]},
    [
        {"funding": "0.0000125", "openInterest": "1234.5", "markPx": "83.1", "midPx": "83.05", "oraclePx": "83.0",
         "dayNtlVlm": "91000000.0", "prevDayPx": "80.0"},
        {"funding": "0.00005", "openInterest": "4120000.0", "markPx": "82.6", "midPx": "82.55", "oraclePx": "82.5",
         "dayNtlVlm": "51000000.0", "prevDayPx": "80.4"},
    ],
]

BOOK = {
    "coin": "HYPE",
    "time": NOW_MS,
    "levels": [
        [{"px": "82.50", "sz": "120.0", "n": 3}, {"px": "82.45", "sz": "80.0", "n": 2}],
        [{"px": "82.60", "sz": "500.0", "n": 5}, {"px": "82.65", "sz": "300.0", "n": 4}],
    ],
}


def candle_row(index: int, close_offset_ms: int) -> dict:
    start = NOW_MS - 3_600_000 * (5 - index)
    return {
        "t": start,
        "T": start + close_offset_ms,
        "s": "HYPE",
        "i": "1h",
        "o": "82.0",
        "h": "83.6",
        "l": "81.9",
        "c": "82.4",
        "v": "1500.5",
        "n": 900,
    }


class FakeHyperliquid(HyperliquidSource):
    def __init__(self) -> None:
        super().__init__(pause=0.0)
        self.calls: list[dict] = []

    def _post(self, body: dict):
        self.calls.append(body)
        if body["type"] == "metaAndAssetCtxs":
            return META
        if body["type"] == "candleSnapshot":
            # Последняя свеча ещё формируется: её закрытие в будущем.
            return [candle_row(index, 3_600_000) for index in range(4)] + [candle_row(4, 7_200_000)]
        if body["type"] == "l2Book":
            return BOOK
        raise AssertionError(f"неожиданный запрос {body}")


class HyperliquidParsingTests(unittest.TestCase):
    def test_snapshot_is_parsed_from_public_payloads(self) -> None:
        snapshot = FakeHyperliquid().fetch("HYPE", ("1h",), 100)
        self.assertEqual(snapshot.coin, "HYPE")
        self.assertAlmostEqual(snapshot.price, 82.55)
        self.assertAlmostEqual(snapshot.funding_hourly, 0.00005)
        self.assertAlmostEqual(snapshot.open_interest, 4_120_000.0)
        self.assertAlmostEqual(snapshot.book.asks[0].price, 82.60)
        self.assertAlmostEqual(snapshot.book.bids[0].size, 120.0)

    def test_forming_candle_is_dropped(self) -> None:
        snapshot = FakeHyperliquid().fetch("HYPE", ("1h",), 100)
        self.assertEqual(len(snapshot.frame("1h")), 4)
        self.assertTrue(all(candle.close_time <= NOW_MS for candle in snapshot.frame("1h")))

    def test_context_is_matched_by_coin_not_by_order(self) -> None:
        snapshot = FakeHyperliquid().fetch("hype", ("1h",), 100)
        self.assertAlmostEqual(snapshot.funding_hourly, 0.00005)  # контекст HYPE, а не BTC

    def test_unknown_coin_raises_with_a_hint(self) -> None:
        with self.assertRaises(SourceError) as error:
            FakeHyperliquid().fetch("NOPE", ("1h",), 100)
        self.assertIn("NOPE", str(error.exception))

    def test_unknown_timeframe_is_rejected(self) -> None:
        with self.assertRaises(SourceError):
            FakeHyperliquid().candles("HYPE", "7m", 10)

    def test_candle_request_asks_for_the_requested_window(self) -> None:
        source = FakeHyperliquid()
        source.fetch("HYPE", ("1h",), 50)
        request = next(call for call in source.calls if call["type"] == "candleSnapshot")["req"]
        self.assertEqual(request["coin"], "HYPE")
        self.assertEqual(request["interval"], "1h")
        self.assertGreater(request["endTime"] - request["startTime"], 3_600_000 * 50)

    def test_drop_forming_keeps_closed_candles_only(self) -> None:
        closed = Candle(0, NOW_MS - 1000, 1, 2, 0.5, 1.5, 10)
        forming = Candle(NOW_MS - 1000, NOW_MS + 60_000, 1, 2, 0.5, 1.5, 10)
        self.assertEqual(drop_forming([closed, forming], NOW_MS), [closed])


if __name__ == "__main__":
    unittest.main()
