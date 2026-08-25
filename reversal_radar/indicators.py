from __future__ import annotations

from statistics import fmean, pstdev

from .models import Candle

Series = list[float | None]


def sma(values: list[float], period: int) -> Series:
    if period <= 0:
        raise ValueError("period must be positive")
    result: Series = []
    running = 0.0
    for index, value in enumerate(values):
        running += value
        if index >= period:
            running -= values[index - period]
        result.append(running / period if index >= period - 1 else None)
    return result


def ema(values: list[float], period: int) -> Series:
    if period <= 0:
        raise ValueError("period must be positive")
    if len(values) < period:
        return [None] * len(values)
    multiplier = 2 / (period + 1)
    result: Series = [None] * (period - 1)
    current = fmean(values[:period])
    result.append(current)
    for value in values[period:]:
        current = (value - current) * multiplier + current
        result.append(current)
    return result


def rsi(values: list[float], period: int = 14) -> Series:
    """RSI по Уайлдеру: первое значение — простое среднее, дальше сглаживание."""
    if len(values) <= period:
        return [None] * len(values)
    gains: list[float] = []
    losses: list[float] = []
    for previous, current in zip(values, values[1:]):
        change = current - previous
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    result: Series = [None] * period
    avg_gain = fmean(gains[:period])
    avg_loss = fmean(losses[:period])
    result.append(_rsi_value(avg_gain, avg_loss))
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        result.append(_rsi_value(avg_gain, avg_loss))
    return result


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def true_ranges(candles: list[Candle]) -> list[float]:
    ranges = [candles[0].range] if candles else []
    for previous, current in zip(candles, candles[1:]):
        ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    return ranges


def atr(candles: list[Candle], period: int = 14) -> float | None:
    if len(candles) < period + 1:
        return None
    return fmean(true_ranges(candles)[-period:])


def zscore(values: list[float], period: int) -> float | None:
    """Z-оценка последнего значения относительно предыдущих period штук."""
    if len(values) < period + 1:
        return None
    window = values[-period - 1 : -1]
    spread = pstdev(window)
    mean = fmean(window)
    if spread == 0:
        return None  # без разброса z-оценка не определена, сравнивать нужно по отношению
    return (values[-1] - mean) / spread


def slope(values: list[float]) -> float:
    """Наклон линии наименьших квадратов, нормированный на средний уровень."""
    count = len(values)
    if count < 2:
        return 0.0
    mean_x = (count - 1) / 2
    mean_y = fmean(values)
    covariance = sum((index - mean_x) * (value - mean_y) for index, value in enumerate(values))
    variance = sum((index - mean_x) ** 2 for index in range(count))
    if variance == 0:
        return 0.0
    raw = covariance / variance
    return raw / abs(mean_y) if mean_y else raw


def swing_highs(values: list[float], left: int = 2, right: int = 2) -> list[int]:
    """Индексы подтверждённых локальных максимумов (пивотов)."""
    return _pivots(values, left, right, high=True)


def swing_lows(values: list[float], left: int = 2, right: int = 2) -> list[int]:
    return _pivots(values, left, right, high=False)


def _pivots(values: list[float], left: int, right: int, *, high: bool) -> list[int]:
    """Пивот подтверждается справа строго: на плато берётся последняя свеча плато."""
    found: list[int] = []
    for index in range(left, len(values) - right):
        pivot = values[index]
        before = values[index - left : index]
        after = values[index + 1 : index + right + 1]
        if high and pivot >= max(before) and pivot > max(after):
            found.append(index)
        elif not high and pivot <= min(before) and pivot < min(after):
            found.append(index)
    return found


def percent(new: float, old: float) -> float:
    """Изменение в долях; 0.0 если база нулевая."""
    return (new - old) / old if old else 0.0


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))
