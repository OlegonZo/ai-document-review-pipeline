"""Отдельные проверяемые условия. Каждая функция — один сигнал, без побочных эффектов."""

from __future__ import annotations

from .config import RadarConfig
from statistics import fmean

from .indicators import atr, clamp, ema, percent, rsi, slope, sma, swing_highs, swing_lows, zscore
from .models import Candle, Direction, OrderBook, Signal, TimeframeView

NEUTRAL_STRENGTH = 0.0


def _neutral(key: str, title: str, weight: float, detail: str, timeframe: str | None = None) -> Signal:
    return Signal(key, title, Direction.NEUTRAL, NEUTRAL_STRENGTH, weight, detail, timeframe)


def level_reaction(candles: list[Candle], level: float, config: RadarConfig, timeframe: str) -> Signal | None:
    """Как последняя свеча повела себя у уровня: отказ, пробой или ничего."""
    weight = config.weight("level_reaction")
    if len(candles) < 25 or level <= 0:
        return None

    last = candles[-1]
    measured = atr(candles) or 0.0
    tolerance = max(0.0015 * level, 0.25 * measured)
    volumes = [candle.volume for candle in candles]
    volume_ma = sma(volumes, 20)[-1] or 0.0
    volume_factor = clamp(last.volume / volume_ma / 1.5) if volume_ma else 0.5
    # Сторону выбираем по предыдущему закрытию: свеча, пересекшая уровень снизу вверх,
    # это пробой сопротивления, а не отбой от поддержки.
    reference_close = candles[-2].close if len(candles) > 1 else last.close
    side = "resistance" if level >= reference_close else "support"

    held = _recent_break(candles, level)

    if side == "resistance":
        if last.high < level - tolerance:
            if held and held[1] == "down":
                return _held_signal(candles, held[0], level, volume_ma, Direction.DOWN, weight, timeframe)
            return _neutral(
                "level_reaction",
                f"Реакция на уровень {level:g}",
                weight,
                f"Цена не доходила до уровня: максимум {last.high:g} ниже на {abs(percent(level, last.high)) * 100:.2f}%.",
                timeframe,
            )
        if last.high > level and last.close < level - tolerance * 0.2:
            strength = clamp(0.35 + 0.65 * last.upper_wick_ratio()) * clamp(0.6 + 0.4 * volume_factor)
            return Signal(
                "level_reaction",
                f"Отказ от уровня {level:g}",
                Direction.DOWN,
                strength,
                weight,
                f"Прокол до {last.high:g}, закрытие {last.close:g} ниже уровня; "
                f"верхний фитиль {last.upper_wick_ratio() * 100:.0f}% свечи, объём {_volume_phrase(last.volume, volume_ma)}.",
                timeframe,
            )
        if last.close > level + tolerance * 0.2:
            strength = clamp(0.3 + 0.7 * volume_factor)
            return Signal(
                "level_reaction",
                f"Пробой уровня {level:g}",
                Direction.UP,
                strength,
                weight,
                f"Закрытие {last.close:g} выше уровня, объём {_volume_phrase(last.volume, volume_ma)}.",
                timeframe,
            )
        return _neutral(
            "level_reaction",
            f"Борьба у уровня {level:g}",
            weight,
            f"Свеча закрылась внутри допуска ±{tolerance:g} от уровня — исход не решён.",
            timeframe,
        )

    if last.low > level + tolerance:
        if held and held[1] == "up":
            return _held_signal(candles, held[0], level, volume_ma, Direction.UP, weight, timeframe)
        return _neutral(
            "level_reaction",
            f"Реакция на уровень {level:g}",
            weight,
            f"Цена не доходила до поддержки: минимум {last.low:g} выше на {percent(last.low, level) * 100:.2f}%.",
            timeframe,
        )
    if last.low < level and last.close > level + tolerance * 0.2:
        strength = clamp(0.35 + 0.65 * last.lower_wick_ratio()) * clamp(0.6 + 0.4 * volume_factor)
        return Signal(
            "level_reaction",
            f"Отбой от поддержки {level:g}",
            Direction.UP,
            strength,
            weight,
            f"Прокол до {last.low:g}, закрытие {last.close:g} выше уровня; "
            f"нижний фитиль {last.lower_wick_ratio() * 100:.0f}% свечи, объём {_volume_phrase(last.volume, volume_ma)}.",
            timeframe,
        )
    if last.close < level - tolerance * 0.2:
        strength = clamp(0.3 + 0.7 * volume_factor)
        return Signal(
            "level_reaction",
            f"Пробой поддержки {level:g}",
            Direction.DOWN,
            strength,
            weight,
            f"Закрытие {last.close:g} ниже уровня, объём {_volume_phrase(last.volume, volume_ma)}.",
            timeframe,
        )
    return _neutral(
        "level_reaction",
        f"Борьба у уровня {level:g}",
        weight,
        f"Свеча закрылась внутри допуска ±{tolerance:g} от уровня — исход не решён.",
        timeframe,
    )


def _recent_break(candles: list[Candle], level: float, window: int = 10) -> tuple[int, str] | None:
    """Свеча, закрывшаяся за уровнем, если с тех пор цена его удерживает."""
    for offset in range(1, min(window, len(candles) - 1) + 1):
        index = len(candles) - offset
        current, prior = candles[index], candles[index - 1]
        if prior.close <= level < current.close and all(c.close > level for c in candles[index:]):
            return index, "up"
        if prior.close >= level > current.close and all(c.close < level for c in candles[index:]):
            return index, "down"
    return None


def _held_signal(
    candles: list[Candle],
    index: int,
    level: float,
    volume_ma: float,
    direction: Direction,
    weight: float,
    timeframe: str,
) -> Signal:
    breaker = candles[index]
    factor = clamp(breaker.volume / volume_ma / 1.5) if volume_ma else 0.5
    held = len(candles) - index - 1
    word = "выше" if direction is Direction.UP else "ниже"
    return Signal(
        "level_reaction",
        f"Пробой {level:g} удержан",
        direction,
        clamp(0.3 + 0.5 * factor),
        weight,
        f"Пробойная свеча закрылась {word} уровня на объёме {_volume_phrase(breaker.volume, volume_ma)}; "
        f"с тех пор {held} свеч(и) держатся {word} — ретест пока не сломал пробой.",
        timeframe,
    )


def level_fatigue(
    candles: list[Candle],
    level: float,
    config: RadarConfig,
    timeframe: str,
    lookback: int = 40,
    min_tests: int = 3,
) -> Signal | None:
    """Несколько подходов к одному уровню с падающим объёмом = топливо кончается."""
    weight = config.weight("level_fatigue")
    if len(candles) < min_tests * 3 or level <= 0:
        return None

    window = candles[-lookback:]
    measured = atr(candles) or 0.0
    tolerance = max(0.0025 * level, 0.5 * measured)
    side = "resistance" if level >= candles[-1].close else "support"
    if side == "resistance":
        tests = [candle for candle in window if candle.high >= level - tolerance]
    else:
        tests = [candle for candle in window if candle.low <= level + tolerance]

    if len(tests) < min_tests:
        return _neutral(
            "level_fatigue",
            "Усталость уровня",
            weight,
            f"Подходов к уровню за {len(window)} свечей: {len(tests)} — мало для вывода.",
            timeframe,
        )

    volume_slope = slope([candle.volume for candle in tests])
    if volume_slope >= -0.02:
        return _neutral(
            "level_fatigue",
            "Усталость уровня",
            weight,
            f"{len(tests)} подхода(ов), объём на подходах не затухает (наклон {volume_slope:+.2%}).",
            timeframe,
        )

    strength = clamp(abs(volume_slope) * 4)
    direction = Direction.DOWN if side == "resistance" else Direction.UP
    wording = "покупатели" if side == "resistance" else "продавцы"
    return Signal(
        "level_fatigue",
        "Уровень выдерживает: подходы слабеют",
        direction,
        strength,
        weight,
        f"{len(tests)} подхода(ов) к {level:g}, объём каждой попытки ниже предыдущей "
        f"(наклон {volume_slope:+.2%}) — {wording} выдыхаются.",
        timeframe,
    )


def volume_confirmation(candles: list[Candle], config: RadarConfig, timeframe: str, window: int = 10) -> Signal | None:
    """Подтверждает ли объём текущее движение цены."""
    weight = config.weight("volume_confirmation")
    if len(candles) < window + 5:
        return None

    recent = candles[-window:]
    price_change = percent(recent[-1].close, recent[0].open)
    volume_slope = slope([candle.volume for candle in recent])
    strength = clamp(0.35 + abs(volume_slope) * 3)

    if abs(price_change) < 0.004:
        return _neutral(
            "volume_confirmation",
            "Объём против цены",
            weight,
            f"Цена почти на месте ({price_change:+.2%} за {window} свечей) — подтверждать нечего.",
            timeframe,
        )
    rising = price_change > 0
    supported = volume_slope > 0.02

    if rising and not supported:
        return Signal(
            "volume_confirmation",
            "Рост на затухающем объёме",
            Direction.DOWN,
            strength,
            weight,
            f"Цена {price_change:+.2%} за {window} свечей, объём {volume_slope:+.2%} — движение без топлива.",
            timeframe,
        )
    if rising and supported:
        return Signal(
            "volume_confirmation",
            "Рост подтверждён объёмом",
            Direction.UP,
            strength,
            weight,
            f"Цена {price_change:+.2%}, объём {volume_slope:+.2%} за {window} свечей.",
            timeframe,
        )
    if not rising and supported:
        return Signal(
            "volume_confirmation",
            "Падение подтверждено объёмом",
            Direction.DOWN,
            strength,
            weight,
            f"Цена {price_change:+.2%}, объём растёт ({volume_slope:+.2%}) — продавцы активны.",
            timeframe,
        )
    return Signal(
        "volume_confirmation",
        "Падение на затухающем объёме",
        Direction.UP,
        strength * 0.7,
        weight,
        f"Цена {price_change:+.2%}, объём {volume_slope:+.2%} — продажи выдыхаются.",
        timeframe,
    )


def volume_spike(candles: list[Candle], config: RadarConfig, timeframe: str, period: int = 20) -> Signal | None:
    """Аномальный объём на одной свече: кто-то крупный работает в рынок."""
    weight = config.weight("volume_spike")
    if len(candles) < period + 2:
        return None

    volumes = [candle.volume for candle in candles]
    score = zscore(volumes, period)
    if score is None:
        average = fmean(volumes[-period - 1 : -1])
        score = 6.0 if average and volumes[-1] / average >= 2 else 0.0
    last = candles[-1]
    if score < 1.2:
        return _neutral(
            "volume_spike",
            "Всплеск объёма",
            weight,
            f"Объём последней свечи в пределах нормы (z={score:.2f}).",
            timeframe,
        )

    body_ratio = clamp(abs(last.body) / last.range, 0.3, 1.0) if last.range else 0.3
    strength = clamp((score - 1.2) / 2.3) * body_ratio
    direction = Direction.UP if last.body > 0 else Direction.DOWN
    colour = "зелёной" if last.body > 0 else "красной"
    return Signal(
        "volume_spike",
        f"Всплеск объёма на {colour} свече",
        direction,
        strength,
        weight,
        f"Объём выше среднего на {score:.1f}σ, тело {body_ratio * 100:.0f}% свечи "
        f"({last.open:g} → {last.close:g}).",
        timeframe,
    )


def rsi_divergence(
    candles: list[Candle],
    config: RadarConfig,
    timeframe: str,
    period: int = 14,
    lookback: int = 90,
) -> Signal | None:
    """Цена делает новый экстремум, RSI — нет: моментум расходится с ценой."""
    weight = config.weight("rsi_divergence") * config.timeframe_weights.get(timeframe, 1.0)
    if len(candles) < period + 20:
        return None

    window = candles[-lookback:]
    closes = [candle.close for candle in window]
    rsi_series = rsi(closes, period)

    # Пивоты берём широкие (±4 свечи): на узких дивергенция ловится на шуме.
    bearish = _divergence(
        [candle.high for candle in window],
        rsi_series,
        swing_highs([candle.high for candle in window], 4, 4),
        bearish=True,
    )
    bullish = _divergence(
        [candle.low for candle in window],
        rsi_series,
        swing_lows([candle.low for candle in window], 4, 4),
        bearish=False,
    )
    best = max(
        (found for found in (bearish, bullish) if found),
        key=lambda found: (found["index"], found["gap"]),
        default=None,
    )
    if best is None:
        return _neutral("rsi_divergence", "RSI-дивергенция", weight, "Расхождения цены и RSI не видно.", timeframe)

    freshness = clamp(1 - (len(window) - 1 - best["index"]) / 25)
    strength = clamp(best["gap"] / 12) * clamp(0.4 + 0.6 * freshness)
    if best["bearish"]:
        return Signal(
            "rsi_divergence",
            "Медвежья RSI-дивергенция",
            Direction.DOWN,
            strength,
            weight,
            f"Цена: {best['price_a']:g} → {best['price_b']:g} (новый хай), "
            f"RSI: {best['rsi_a']:.1f} → {best['rsi_b']:.1f} (ниже) — импульс слабеет.",
            timeframe,
        )
    return Signal(
        "rsi_divergence",
        "Бычья RSI-дивергенция",
        Direction.UP,
        strength,
        weight,
        f"Цена: {best['price_a']:g} → {best['price_b']:g} (новый лой), "
        f"RSI: {best['rsi_a']:.1f} → {best['rsi_b']:.1f} (выше) — давление продавцов слабеет.",
        timeframe,
    )


def _divergence(prices: list[float], rsi_series, pivots: list[int], *, bearish: bool, min_gap: int = 6) -> dict | None:
    """Сравнивает два последних разнесённых по времени экстремума цены и RSI на них."""
    usable = [index for index in pivots if rsi_series[index] is not None]
    if len(usable) < 2:
        return None
    second = usable[-1]
    earlier = [index for index in usable[:-1] if second - index >= min_gap]
    if not earlier:
        return None
    first = earlier[-1]
    price_a, price_b = prices[first], prices[second]
    rsi_a, rsi_b = rsi_series[first], rsi_series[second]
    if bearish and price_b > price_a * 1.0005 and rsi_b < rsi_a - 1:
        gap = rsi_a - rsi_b
    elif not bearish and price_b < price_a * 0.9995 and rsi_b > rsi_a + 1:
        gap = rsi_b - rsi_a
    else:
        return None
    return {
        "index": second,
        "gap": gap,
        "bearish": bearish,
        "price_a": price_a,
        "price_b": price_b,
        "rsi_a": rsi_a,
        "rsi_b": rsi_b,
    }


def rsi_extreme(candles: list[Candle], config: RadarConfig, timeframe: str, period: int = 14) -> Signal | None:
    weight = config.weight("rsi_extreme")
    if len(candles) < period + 2:
        return None
    value = rsi([candle.close for candle in candles], period)[-1]
    if value is None:
        return None
    if value > 72:
        return Signal(
            "rsi_extreme",
            "RSI перегрет",
            Direction.DOWN,
            clamp((value - 72) / 18),
            weight,
            f"RSI({period}) = {value:.1f} на {timeframe}.",
            timeframe,
        )
    if value < 28:
        return Signal(
            "rsi_extreme",
            "RSI перепродан",
            Direction.UP,
            clamp((28 - value) / 18),
            weight,
            f"RSI({period}) = {value:.1f} на {timeframe}.",
            timeframe,
        )
    return _neutral("rsi_extreme", "RSI в норме", weight, f"RSI({period}) = {value:.1f} на {timeframe}.", timeframe)


def funding_signal(funding_hourly: float | None, config: RadarConfig) -> Signal | None:
    """Перекос по funding = перегруженная сторона, топливо для сквиза против неё."""
    weight = config.weight("funding")
    if funding_hourly is None:
        return None
    annual = funding_hourly * 24 * 365
    if abs(annual) < 0.05:
        return _neutral("funding", "Funding rate", weight, f"Funding нейтральный: {annual:+.1%} годовых.", None)
    strength = clamp(abs(annual) / config.funding_annual_full)
    if annual > 0:
        return Signal(
            "funding",
            "Лонги переплачивают",
            Direction.DOWN,
            strength,
            weight,
            f"Funding {funding_hourly:+.4%} в час ({annual:+.1%} годовых): рынок перегружен лонгами, "
            "при слабости это топливо для сквиза вниз.",
        )
    return Signal(
        "funding",
        "Шорты переплачивают",
        Direction.UP,
        strength,
        weight,
        f"Funding {funding_hourly:+.4%} в час ({annual:+.1%} годовых): перегружена короткая сторона.",
    )


def open_interest_signal(
    open_interest: float | None,
    previous_oi: float | None,
    price: float,
    previous_price: float | None,
    config: RadarConfig,
    hours: float | None = None,
) -> Signal | None:
    """OI + цена: новые деньги в тренде или ловушка перед ликвидациями."""
    weight = config.weight("open_interest")
    if open_interest is None:
        return None
    if previous_oi is None or previous_price is None:
        return _neutral(
            "open_interest",
            "Open interest",
            weight,
            f"OI = {open_interest:,.0f}. Истории для сравнения ещё нет — сигнал появится после второго опроса.",
        )

    oi_change = percent(open_interest, previous_oi)
    price_change = percent(price, previous_price)
    span = f"за {hours:.1f} ч" if hours else "с прошлого снимка"
    base = f"OI {oi_change:+.2%}, цена {price_change:+.2%} {span}"
    strength = clamp(abs(oi_change) * 12 + 0.25)

    if abs(oi_change) < 0.005:
        return _neutral("open_interest", "Open interest", weight, f"{base} — OI почти не меняется.")
    if oi_change > 0 and price_change > 0.003:
        return Signal("open_interest", "OI растёт вместе с ценой", Direction.UP, strength, weight,
                      f"{base}: в тренд заходят новые деньги.")
    if oi_change > 0 and abs(price_change) <= 0.003:
        return Signal("open_interest", "OI растёт, цена стоит", Direction.DOWN, clamp(strength + 0.15), weight,
                      f"{base}: позиции копятся без движения — типичная ловушка перед каскадом ликвидаций.")
    if oi_change > 0 and price_change < -0.003:
        return Signal("open_interest", "OI растёт на падении", Direction.DOWN, strength, weight,
                      f"{base}: открываются новые шорты, продолжение вниз.")
    if oi_change < 0 and price_change > 0.003:
        return Signal("open_interest", "Рост на закрытии шортов", Direction.DOWN, clamp(strength * 0.8), weight,
                      f"{base}: рост питается закрытием шортов, а не новыми покупками — топливо кончается.")
    return Signal("open_interest", "Позиции закрываются на падении", Direction.UP, clamp(strength * 0.6), weight,
                  f"{base}: лонги вышли, давление вынужденных продаж падает.")


def book_pressure(book: OrderBook | None, config: RadarConfig, level: float | None = None) -> Signal | None:
    """Плотность заявок рядом с ценой: где стенка толще."""
    weight = config.weight("book_pressure")
    if book is None or book.mid is None:
        return None

    band = config.book_band_pct
    ask_depth = book.depth("ask", band)
    bid_depth = book.depth("bid", band)
    if ask_depth <= 0 or bid_depth <= 0:
        return _neutral("book_pressure", "Плотность стакана", weight, "Стакан внутри полосы пуст с одной стороны.")

    ratio = ask_depth / bid_depth
    wall = book.largest("ask" if ratio >= 1 else "bid", band)
    wall_text = f" Крупнейшая заявка: {wall.size:,.0f} по {wall.price:g}." if wall else ""
    base = f"В полосе ±{band:.2%} от {book.mid:g}: продажи {ask_depth:,.0f} против покупок {bid_depth:,.0f} (x{ratio:.2f})."

    if ratio >= 1.25:
        return Signal("book_pressure", "Стена на продажу выше цены", Direction.DOWN,
                      clamp((ratio - 1.25) / 1.75), weight, base + wall_text)
    if ratio <= 0.8:
        return Signal("book_pressure", "Плотные покупки под ценой", Direction.UP,
                      clamp((1 / ratio - 1.25) / 1.75), weight, base + wall_text)
    return _neutral("book_pressure", "Стакан сбалансирован", weight, base + wall_text)


def timeframe_view(candles: list[Candle], timeframe: str) -> TimeframeView | None:
    """Направление одного таймфрейма по EMA20/EMA50 и позиции цены."""
    if len(candles) < 55:
        return None
    closes = [candle.close for candle in candles]
    fast = ema(closes, 20)[-1]
    slow = ema(closes, 50)[-1]
    value = rsi(closes, 14)[-1]
    if fast is None or slow is None:
        return None

    points = (1 if closes[-1] > fast else -1) + (1 if fast > slow else -1)
    trend = Direction.of(points)
    relation = "выше" if closes[-1] > fast else "ниже"
    cross = "выше" if fast > slow else "ниже"
    return TimeframeView(
        timeframe=timeframe,
        trend=trend,
        rsi=value,
        close=closes[-1],
        detail=f"Цена {relation} EMA20, EMA20 {cross} EMA50, RSI {value:.0f}." if value is not None
        else f"Цена {relation} EMA20, EMA20 {cross} EMA50.",
    )


def mtf_alignment(views: list[TimeframeView], config: RadarConfig) -> Signal | None:
    """Совпадение направления на нескольких ТФ весомее одиночного сигнала."""
    weight = config.weight("mtf_alignment")
    decided = [view for view in views if view.trend is not Direction.NEUTRAL]
    if len(decided) < 2:
        return None

    up = sum(1 for view in decided if view.trend is Direction.UP)
    down = len(decided) - up
    names = ", ".join(f"{view.timeframe}:{view.trend.value}" for view in views)
    if up == down:
        return _neutral("mtf_alignment", "Таймфреймы", weight, f"Таймфреймы спорят ({names}).")
    dominant = Direction.UP if up > down else Direction.DOWN
    share = max(up, down) / len(decided)
    return Signal(
        "mtf_alignment",
        f"Согласие таймфреймов: {dominant.value}",
        dominant,
        clamp((share - 0.5) * 2),
        weight,
        f"{max(up, down)} из {len(decided)} таймфреймов смотрят в одну сторону ({names}).",
    )


def _volume_phrase(volume: float, volume_ma: float) -> str:
    if not volume_ma:
        return f"{volume:,.0f}"
    return f"{volume / volume_ma:.2f}x к среднему за 20 свечей"
