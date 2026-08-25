from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class Direction(StrEnum):
    """Куда указывает сигнал."""

    UP = "up"
    DOWN = "down"
    NEUTRAL = "neutral"

    @property
    def sign(self) -> int:
        return {Direction.UP: 1, Direction.DOWN: -1, Direction.NEUTRAL: 0}[self]

    @classmethod
    def of(cls, value: float, deadzone: float = 0.0) -> "Direction":
        if value > deadzone:
            return cls.UP
        if value < -deadzone:
            return cls.DOWN
        return cls.NEUTRAL


class RadarState(StrEnum):
    """Итоговое состояние: от разворота вниз до пробоя вверх."""

    REVERSAL_DOWN = "reversal_down"
    WEAKENING = "weakening"
    UNDECIDED = "undecided"
    STRENGTHENING = "strengthening"
    BREAKOUT_UP = "breakout_up"

    @property
    def title(self) -> str:
        return {
            RadarState.REVERSAL_DOWN: "Разворот вниз: сигналы совпали",
            RadarState.WEAKENING: "Импульс слабеет",
            RadarState.UNDECIDED: "Нет перевеса",
            RadarState.STRENGTHENING: "Импульс усиливается",
            RadarState.BREAKOUT_UP: "Пробой вверх: сигналы совпали",
        }[self]


@dataclass(frozen=True)
class Candle:
    open_time: int  # ms
    close_time: int  # ms
    open: float
    high: float
    low: float
    close: float
    volume: float
    trades: int = 0

    @property
    def body(self) -> float:
        return self.close - self.open

    @property
    def range(self) -> float:
        return max(self.high - self.low, 0.0)

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    def upper_wick_ratio(self) -> float:
        return self.upper_wick / self.range if self.range > 0 else 0.0

    def lower_wick_ratio(self) -> float:
        return self.lower_wick / self.range if self.range > 0 else 0.0


@dataclass(frozen=True)
class BookLevel:
    price: float
    size: float


@dataclass(frozen=True)
class OrderBook:
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]

    @property
    def mid(self) -> float | None:
        if not self.bids or not self.asks:
            return None
        return (self.bids[0].price + self.asks[0].price) / 2

    def depth(self, side: str, band_pct: float) -> float:
        """Суммарный размер заявок в пределах band_pct от середины стакана."""
        mid = self.mid
        if mid is None:
            return 0.0
        if side == "ask":
            limit = mid * (1 + band_pct)
            return sum(level.size for level in self.asks if level.price <= limit)
        limit = mid * (1 - band_pct)
        return sum(level.size for level in self.bids if level.price >= limit)

    def largest(self, side: str, band_pct: float) -> BookLevel | None:
        mid = self.mid
        if mid is None:
            return None
        levels = self.asks if side == "ask" else self.bids
        if side == "ask":
            inside = [lvl for lvl in levels if lvl.price <= mid * (1 + band_pct)]
        else:
            inside = [lvl for lvl in levels if lvl.price >= mid * (1 - band_pct)]
        return max(inside, key=lambda lvl: lvl.size, default=None)


@dataclass(frozen=True)
class MarketSnapshot:
    """Всё, что известно о монете на момент опроса."""

    coin: str
    price: float
    taken_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    mark_price: float | None = None
    oracle_price: float | None = None
    funding_hourly: float | None = None
    open_interest: float | None = None
    day_volume: float | None = None
    candles: dict[str, tuple[Candle, ...]] = field(default_factory=dict)
    book: OrderBook | None = None

    def frame(self, timeframe: str) -> tuple[Candle, ...]:
        return self.candles.get(timeframe, ())


@dataclass(frozen=True)
class Signal:
    key: str
    title: str
    direction: Direction
    strength: float  # 0..1, насколько выражен сигнал
    weight: float  # вклад в итоговый счёт
    detail: str
    timeframe: str | None = None

    @property
    def contribution(self) -> float:
        return self.direction.sign * self.strength * self.weight

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "title": self.title,
            "direction": self.direction.value,
            "strength": round(self.strength, 3),
            "weight": self.weight,
            "contribution": round(self.contribution, 3),
            "detail": self.detail,
            "timeframe": self.timeframe,
        }


@dataclass(frozen=True)
class Plan:
    """Сценарий на оба исхода вместо прогноза одного."""

    level: float | None
    long_trigger: str
    long_invalidation: float | None
    short_trigger: str
    short_invalidation: float | None
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "level": self.level,
            "long_trigger": self.long_trigger,
            "long_invalidation": self.long_invalidation,
            "short_trigger": self.short_trigger,
            "short_invalidation": self.short_invalidation,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class TimeframeView:
    timeframe: str
    trend: Direction
    rsi: float | None
    close: float | None
    detail: str

    def as_dict(self) -> dict:
        return {
            "timeframe": self.timeframe,
            "trend": self.trend.value,
            "rsi": round(self.rsi, 1) if self.rsi is not None else None,
            "close": self.close,
            "detail": self.detail,
        }


DISCLAIMER = (
    "Это не прогноз и не рекомендация. Радар считает наблюдаемые условия "
    "и показывает, какой сценарий сейчас подтверждён сигналами, а какой — нет."
)


@dataclass(frozen=True)
class RadarReport:
    coin: str
    price: float
    generated_at: datetime
    state: RadarState
    score: float  # -100 (разворот вниз) .. +100 (продолжение вверх)
    coverage: float  # доля весов, по которым реально были данные
    level: float | None
    signals: tuple[Signal, ...]
    timeframes: tuple[TimeframeView, ...]
    plan: Plan
    warnings: tuple[str, ...] = ()

    @property
    def alignment(self) -> int:
        """Сколько таймфреймов согласны со знаком итогового счёта."""
        want = Direction.of(self.score, deadzone=0.0)
        if want is Direction.NEUTRAL:
            return 0
        return sum(1 for view in self.timeframes if view.trend is want)

    def top_signals(self, limit: int = 5) -> tuple[Signal, ...]:
        ranked = sorted(self.signals, key=lambda s: abs(s.contribution), reverse=True)
        return tuple(s for s in ranked if s.contribution != 0)[:limit]

    def as_dict(self) -> dict:
        return {
            "coin": self.coin,
            "price": self.price,
            "generated_at": self.generated_at.isoformat(),
            "state": self.state.value,
            "state_title": self.state.title,
            "score": round(self.score, 1),
            "coverage": round(self.coverage, 3),
            "alignment": self.alignment,
            "level": self.level,
            "signals": [signal.as_dict() for signal in self.signals],
            "timeframes": [view.as_dict() for view in self.timeframes],
            "plan": self.plan.as_dict(),
            "warnings": list(self.warnings),
            "disclaimer": DISCLAIMER,
        }
