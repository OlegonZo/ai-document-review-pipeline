"""Сборка сигналов в один отчёт: счёт, состояние, сценарий с инвалидацией."""

from __future__ import annotations

from datetime import UTC, datetime

from . import signals as sig
from .config import RadarConfig
from .indicators import atr
from .levels import nearest_resistance, nearest_support, pick_level, relevant_levels
from .models import (
    Candle,
    Direction,
    MarketSnapshot,
    Plan,
    RadarReport,
    RadarState,
    Signal,
    TimeframeView,
)


def analyse(
    snapshot: MarketSnapshot,
    config: RadarConfig,
    previous_oi: float | None = None,
    previous_price: float | None = None,
    oi_age_hours: float | None = None,
) -> RadarReport:
    primary = list(snapshot.frame(config.primary_timeframe))
    fast = list(snapshot.frame(config.fast_timeframe))
    warnings: list[str] = []

    level = pick_level(primary, snapshot.price, config.level) if primary else config.level
    if level is None:
        warnings.append("Уровень не задан и не найден автоматически — сигналы у уровня отключены.")

    views: list[TimeframeView] = []
    for timeframe in config.timeframes:
        candles = list(snapshot.frame(timeframe))
        if not candles:
            warnings.append(f"Нет свечей {timeframe}.")
            continue
        view = sig.timeframe_view(candles, timeframe)
        if view is not None:
            views.append(view)

    collected: list[Signal | None] = []
    if level is not None and primary:
        collected.append(sig.level_reaction(primary, level, config, config.primary_timeframe))
        collected.append(sig.level_fatigue(primary, level, config, config.primary_timeframe))
    if primary:
        collected.append(sig.volume_confirmation(primary, config, config.primary_timeframe))
        collected.append(sig.rsi_extreme(primary, config, config.primary_timeframe))
    if fast:
        collected.append(sig.volume_spike(fast, config, config.fast_timeframe))
    for timeframe in config.timeframes:
        candles = list(snapshot.frame(timeframe))
        if candles:
            collected.append(sig.rsi_divergence(candles, config, timeframe))
    collected.append(sig.funding_signal(snapshot.funding_hourly, config))
    collected.append(
        sig.open_interest_signal(
            snapshot.open_interest, previous_oi, snapshot.price, previous_price, config, oi_age_hours
        )
    )
    collected.append(sig.book_pressure(snapshot.book, config, level))
    collected.append(sig.mtf_alignment(views, config))

    built = tuple(signal for signal in collected if signal is not None)
    if snapshot.book is None:
        warnings.append("Стакан недоступен — сигнал по плотности заявок не учитывался.")
    if snapshot.funding_hourly is None:
        warnings.append("Funding недоступен — сигнал по перекосу позиций не учитывался.")
    if snapshot.open_interest is not None and previous_oi is None:
        warnings.append("Нет предыдущего снимка OI — динамика открытого интереса появится позже.")

    score = _score(built, config)
    state = _state(score, config)
    report = RadarReport(
        coin=snapshot.coin,
        price=snapshot.price,
        generated_at=snapshot.taken_at or datetime.now(UTC),
        state=state,
        score=score,
        coverage=_coverage(built, config),
        level=level,
        signals=built,
        timeframes=tuple(views),
        plan=build_plan(snapshot, config, level, primary),
        warnings=tuple(dict.fromkeys(warnings)),
    )
    return report


def _score(built: tuple[Signal, ...], config: RadarConfig) -> float:
    total_weight = sum(signal.weight for signal in built)
    if total_weight <= 0:
        return 0.0
    raw = sum(signal.contribution for signal in built) / (total_weight * config.score_gain)
    return max(-100.0, min(100.0, raw * 100))


def _coverage(built: tuple[Signal, ...], config: RadarConfig) -> float:
    possible = sum(config.weight(key) for key in config.weights if key != "rsi_divergence")
    possible += config.weight("rsi_divergence") * sum(
        config.timeframe_weights.get(timeframe, 1.0) for timeframe in config.timeframes
    )
    if possible <= 0:
        return 0.0
    return min(1.0, sum(signal.weight for signal in built) / possible)


def _state(score: float, config: RadarConfig) -> RadarState:
    if score <= -config.confirmed_threshold:
        return RadarState.REVERSAL_DOWN
    if score <= -config.warning_threshold:
        return RadarState.WEAKENING
    if score >= config.confirmed_threshold:
        return RadarState.BREAKOUT_UP
    if score >= config.warning_threshold:
        return RadarState.STRENGTHENING
    return RadarState.UNDECIDED


def build_plan(
    snapshot: MarketSnapshot,
    config: RadarConfig,
    level: float | None,
    primary: list[Candle],
) -> Plan:
    """Сценарий на оба исхода: что подтверждает лонг, что подтверждает шорт, где план сломан."""
    measured = atr(primary) if primary else None
    price = snapshot.price
    levels = relevant_levels(primary) if primary else []
    support = nearest_support(levels, price)
    resistance = nearest_resistance(levels, price)
    notes: list[str] = []

    if level is None:
        return Plan(
            level=None,
            long_trigger="Нет опорного уровня — задайте его вручную (--level) или дождитесь формирования пивотов.",
            long_invalidation=None,
            short_trigger="Нет опорного уровня.",
            short_invalidation=None,
            notes=("Без уровня план не строится: инвалидацию не к чему привязать.",),
        )

    span = measured or price * 0.01
    buffer = span * 0.5
    is_resistance = level >= price

    if is_resistance:
        # Лонг берётся на пробое уровня, поэтому стоп не дальше 1.5 ATR под ним,
        # даже если ближайшая поддержка по пивотам лежит намного ниже.
        floor_stop = level - max(1.5 * span, price * 0.005)
        long_stop = max(support.price - buffer, floor_stop) if support else floor_stop
        short_stop = level + buffer
    else:
        # Уровень под ценой: лонг — отбой от него, шорт — пробой вниз.
        long_stop = level - buffer
        short_stop = (resistance.price + buffer) if resistance else level + max(1.5 * span, price * 0.005)

    if is_resistance:
        long_trigger = (
            f"Закрытие свечи {config.primary_timeframe} выше {level:g} на объёме от 1.5x к среднему "
            f"и удержание при ретесте."
        )
        short_trigger = (
            f"Отказ от {level:g}: длинный верхний фитиль и закрытие ниже уровня, "
            f"желательно с всплеском объёма на красной свече."
        )
    else:
        long_trigger = (
            f"Отбой от {level:g}: длинный нижний фитиль и закрытие выше уровня на растущем объёме."
        )
        short_trigger = (
            f"Закрытие свечи {config.primary_timeframe} ниже {level:g} на повышенном объёме "
            f"и неудачный ретест снизу."
        )

    if measured:
        notes.append(f"ATR({config.primary_timeframe}) = {measured:.4g} — стоп ближе этого значения будет выбит шумом.")
    if support:
        notes.append(f"Ближайшая поддержка по пивотам: {support.price:g} ({support.touches} касания).")
    if resistance:
        notes.append(f"Ближайшее сопротивление по пивотам: {resistance.price:g} ({resistance.touches} касания).")
    if snapshot.funding_hourly is not None and snapshot.funding_hourly * 24 * 365 > 0.3:
        notes.append("Funding сильно положительный: вход в лонг здесь — против переполненной стороны.")
    notes.append("Сработавший сценарий отменяет второй: одновременно оба не берут.")

    return Plan(
        level=level,
        long_trigger=long_trigger,
        long_invalidation=round(long_stop, 6),
        short_trigger=short_trigger,
        short_invalidation=round(short_stop, 6),
        notes=tuple(notes),
    )


def summarize(report: RadarReport) -> str:
    """Короткий текст для алерта/консоли."""
    arrow = {Direction.UP: "▲", Direction.DOWN: "▼", Direction.NEUTRAL: "•"}[Direction.of(report.score, 5)]
    lines = [
        f"{arrow} {report.coin} {report.price:g} — {report.state.title} ({report.score:+.0f}/100)",
    ]
    if report.level is not None:
        lines.append(
            f"Уровень: {report.level:g} | таймфреймов за этот сценарий: "
            f"{report.alignment}/{len(report.timeframes)}"
        )
    for signal in report.top_signals(4):
        mark = {Direction.UP: "+", Direction.DOWN: "-", Direction.NEUTRAL: "="}[signal.direction]
        lines.append(f"  [{mark}] {signal.title}: {signal.detail}")
    lines.append(f"Лонг: {report.plan.long_trigger}")
    if report.plan.long_invalidation is not None:
        lines.append(f"  стоп-инвалидация: {report.plan.long_invalidation:g}")
    lines.append(f"Шорт: {report.plan.short_trigger}")
    if report.plan.short_invalidation is not None:
        lines.append(f"  стоп-инвалидация: {report.plan.short_invalidation:g}")
    return "\n".join(lines)
