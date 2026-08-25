from __future__ import annotations

from dataclasses import dataclass
from statistics import fmean

from .indicators import atr, swing_highs, swing_lows
from .models import Candle


@dataclass(frozen=True)
class Level:
    price: float
    kind: str  # "resistance" | "support"
    touches: int
    last_touch_ms: int

    def as_dict(self) -> dict:
        return {
            "price": round(self.price, 6),
            "kind": self.kind,
            "touches": self.touches,
            "last_touch_ms": self.last_touch_ms,
        }


def detect_levels(candles: list[Candle], *, tolerance: float | None = None, min_touches: int = 2) -> list[Level]:
    """Кластеризует пивоты в уровни: рядом стоящие экстремумы — это один уровень."""
    if len(candles) < 10:
        return []
    if tolerance is None:
        measured = atr(candles)
        reference = candles[-1].close
        tolerance = max(0.0035 * reference, 0.5 * measured if measured else 0.0)

    highs = [candle.high for candle in candles]
    lows = [candle.low for candle in candles]
    levels: list[Level] = []
    levels += _cluster([(index, highs[index]) for index in swing_highs(highs)], candles, tolerance, "resistance")
    levels += _cluster([(index, lows[index]) for index in swing_lows(lows)], candles, tolerance, "support")
    return sorted(
        (level for level in levels if level.touches >= min_touches),
        key=lambda level: (-level.touches, -level.last_touch_ms),
    )


def _cluster(points: list[tuple[int, float]], candles: list[Candle], tolerance: float, kind: str) -> list[Level]:
    clusters: list[list[tuple[int, float]]] = []
    for index, price in sorted(points, key=lambda point: point[1]):
        if clusters and abs(price - fmean(value for _, value in clusters[-1])) <= tolerance:
            clusters[-1].append((index, price))
        else:
            clusters.append([(index, price)])
    return [
        Level(
            price=fmean(price for _, price in cluster),
            kind=kind,
            touches=len(cluster),
            last_touch_ms=candles[max(index for index, _ in cluster)].close_time,
        )
        for cluster in clusters
    ]


def relevant_levels(candles: list[Candle]) -> list[Level]:
    """Уровни с повторными касаниями, а если их нет — одиночные пивоты трендового рынка."""
    return detect_levels(candles) or detect_levels(candles, min_touches=1)


def nearest_resistance(levels: list[Level], price: float) -> Level | None:
    above = [level for level in levels if level.price > price]
    return min(above, key=lambda level: level.price - price, default=None)


def nearest_support(levels: list[Level], price: float) -> Level | None:
    below = [level for level in levels if level.price < price]
    return min(below, key=lambda level: price - level.price, default=None)


def pick_level(candles: list[Candle], price: float, manual: float | None = None) -> float | None:
    """Уровень для анализа: заданный вручную или ближайшее сопротивление сверху."""
    if manual is not None:
        return manual
    levels = relevant_levels(candles)
    resistance = nearest_resistance(levels, price)
    if resistance is not None:
        return resistance.price
    support = nearest_support(levels, price)
    return support.price if support else None
