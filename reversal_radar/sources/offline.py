"""Источники без сети: JSON-снимок и синтетический сценарий для демо и тестов."""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime
from pathlib import Path

from ..models import BookLevel, Candle, MarketSnapshot, OrderBook
from .base import SourceError

INTERVAL_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


class JsonSource:
    """Читает снимок, ранее сохранённый командой `radar snapshot --save`."""

    name = "json"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def fetch(self, coin: str, timeframes: tuple[str, ...], limit: int = 200) -> MarketSnapshot:
        if not self.path.exists():
            raise SourceError(f"файл снимка не найден: {self.path}")
        return snapshot_from_dict(json.loads(self.path.read_text()))


class SyntheticSource:
    """Детерминированный сценарий: рынок упирается в уровень и слабеет (или пробивает его)."""

    name = "synthetic"

    def __init__(self, scenario: str = "exhaustion", level: float = 83.40, seed: int = 7) -> None:
        if scenario not in {"exhaustion", "breakout"}:
            raise SourceError("сценарий должен быть exhaustion или breakout")
        self.scenario = scenario
        self.level = level
        self.seed = seed

    def fetch(self, coin: str, timeframes: tuple[str, ...], limit: int = 200) -> MarketSnapshot:
        candles = {
            timeframe: build_candles(
                self.scenario,
                self.level,
                self.seed + sum(ord(char) for char in timeframe),
                INTERVAL_MS.get(timeframe, 3_600_000),
            )
            for timeframe in timeframes
        }
        primary = candles.get("1h") or next(iter(candles.values()))
        price = primary[-1].close
        bearish = self.scenario == "exhaustion"
        return MarketSnapshot(
            coin=coin,
            price=price,
            taken_at=datetime.now(UTC),
            mark_price=price,
            funding_hourly=0.00005 if bearish else 0.000005,
            open_interest=4_120_000 if bearish else 3_400_000,
            day_volume=91_000_000,
            candles=candles,
            book=_book(price, heavy_side="ask" if bearish else "bid"),
        )


def build_candles(scenario: str, level: float, seed: int, step_ms: int) -> tuple[Candle, ...]:
    """Две попытки взять уровень: вторая выше по цене, но слабее по импульсу и объёму."""
    noise = random.Random(seed)
    closes: list[float] = []
    volumes: list[float] = []

    def leg(start: float, end: float, count: int, volume_from: float, volume_to: float, jitter: float) -> None:
        for step in range(count):
            share = (step + 1) / count
            closes.append(start + (end - start) * share + noise.uniform(-jitter, jitter))
            volumes.append(volume_from + (volume_to - volume_from) * share + noise.uniform(-40, 40))

    leg(70.0, 76.0, 45, 900, 1400, 0.25)          # первый импульс
    leg(76.0, level - 0.55, 25, 1400, 1150, 0.20)  # первый подход к уровню
    leg(level - 0.55, 79.6, 18, 1000, 700, 0.22)   # откат
    if scenario == "exhaustion":
        leg(79.6, level - 0.12, 32, 720, 430, 0.18)  # второй подход: выше, но тише
        leg(level - 0.12, level - 0.9, 6, 430, 980, 0.15)  # отказ и сброс
    else:
        leg(79.6, level - 0.10, 28, 700, 1500, 0.18)
        leg(level - 0.10, level + 1.5, 10, 1600, 2600, 0.15)  # пробой на объёме

    candles: list[Candle] = []
    start_ms = 1_700_000_000_000
    previous = closes[0] - 0.3
    for index, (close, volume) in enumerate(zip(closes, volumes)):
        open_price = previous
        wick = abs(noise.gauss(0, 0.12)) + 0.05
        high = max(open_price, close) + wick
        low = min(open_price, close) - wick * 0.6
        if scenario == "exhaustion" and index >= len(closes) - 6:
            high = max(high, level + 0.05 + noise.uniform(0, 0.12))  # проколы уровня без закрытия выше
        candles.append(
            Candle(
                open_time=start_ms + index * step_ms,
                close_time=start_ms + (index + 1) * step_ms,
                open=round(open_price, 3),
                high=round(high, 3),
                low=round(low, 3),
                close=round(close, 3),
                volume=round(max(volume, 40.0), 2),
                trades=int(max(volume, 40) // 3),
            )
        )
        previous = close
    return tuple(candles)


def _book(price: float, heavy_side: str) -> OrderBook:
    bids = []
    asks = []
    for step in range(1, 21):
        offset = price * 0.0005 * step
        bid_size = 900 + step * 25
        ask_size = 900 + step * 25
        if heavy_side == "ask" and step in {3, 4}:
            ask_size *= 6
        if heavy_side == "bid" and step in {3, 4}:
            bid_size *= 6
        bids.append(BookLevel(round(price - offset, 4), float(bid_size)))
        asks.append(BookLevel(round(price + offset, 4), float(ask_size)))
    return OrderBook(bids=tuple(bids), asks=tuple(asks))


def snapshot_to_dict(snapshot: MarketSnapshot) -> dict:
    return {
        "coin": snapshot.coin,
        "price": snapshot.price,
        "taken_at": snapshot.taken_at.isoformat(),
        "mark_price": snapshot.mark_price,
        "oracle_price": snapshot.oracle_price,
        "funding_hourly": snapshot.funding_hourly,
        "open_interest": snapshot.open_interest,
        "day_volume": snapshot.day_volume,
        "candles": {
            timeframe: [
                [c.open_time, c.close_time, c.open, c.high, c.low, c.close, c.volume, c.trades] for c in candles
            ]
            for timeframe, candles in snapshot.candles.items()
        },
        "book": None
        if snapshot.book is None
        else {
            "bids": [[level.price, level.size] for level in snapshot.book.bids],
            "asks": [[level.price, level.size] for level in snapshot.book.asks],
        },
    }


def snapshot_from_dict(payload: dict) -> MarketSnapshot:
    book = payload.get("book")
    return MarketSnapshot(
        coin=payload["coin"],
        price=float(payload["price"]),
        taken_at=datetime.fromisoformat(payload["taken_at"]),
        mark_price=payload.get("mark_price"),
        oracle_price=payload.get("oracle_price"),
        funding_hourly=payload.get("funding_hourly"),
        open_interest=payload.get("open_interest"),
        day_volume=payload.get("day_volume"),
        candles={
            timeframe: tuple(Candle(int(r[0]), int(r[1]), *(float(v) for v in r[2:7]), int(r[7])) for r in rows)
            for timeframe, rows in payload.get("candles", {}).items()
        },
        book=None
        if not book
        else OrderBook(
            bids=tuple(BookLevel(float(p), float(s)) for p, s in book["bids"]),
            asks=tuple(BookLevel(float(p), float(s)) for p, s in book["asks"]),
        ),
    )
