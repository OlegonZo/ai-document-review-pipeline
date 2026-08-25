"""Командная строка: разовый отчёт, наблюдение, телеграм-бот, веб-виджет."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import RadarConfig
from .engine import summarize
from .models import DISCLAIMER, Direction, MarketSnapshot, RadarReport
from .notifiers import TelegramNotifier, from_env
from .sources.base import MarketSource, SourceError
from .sources.hyperliquid import HyperliquidSource
from .sources.offline import JsonSource, SyntheticSource, snapshot_to_dict
from .store import RadarStore
from .watcher import Watcher

MARK = {Direction.UP: "▲", Direction.DOWN: "▼", Direction.NEUTRAL: "·"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reversal-radar",
        description="Сигналы разворота/продолжения по перп-контракту (по умолчанию HYPE на Hyperliquid).",
    )
    parser.add_argument("--coin", default=None, help="тикер перпа, например HYPE")
    parser.add_argument("--level", default=None, help="уровень для анализа, например 83.40 (по умолчанию — авто)")
    parser.add_argument("--timeframes", default=None, help="список ТФ через запятую: 15m,1h,4h,1d")
    parser.add_argument("--database", default=None, help="путь к SQLite с историей снимков")
    parser.add_argument("--demo", choices=("exhaustion", "breakout"), help="работать на синтетике, без сети")
    parser.add_argument("--snapshot", help="анализировать снимок из JSON-файла")

    commands = parser.add_subparsers(dest="command")

    report = commands.add_parser("report", help="разовый отчёт (по умолчанию)")
    report.add_argument("--json", action="store_true", help="вывести отчёт как JSON")
    report.add_argument("--save", help="сохранить сырой снимок рынка в JSON")

    watch = commands.add_parser("watch", help="опрашивать рынок и слать алерты на смену картины")
    watch.add_argument("--interval", type=int, default=None, help="период опроса в секундах")
    watch.add_argument("--iterations", type=int, default=None, help="сколько циклов сделать (по умолчанию бесконечно)")

    bot = commands.add_parser("bot", help="телеграм-бот: команды в чате плюс алерты")
    bot.add_argument("--interval", type=int, default=None, help="период опроса в секундах")
    bot.add_argument("--silent-start", action="store_true", help="не писать приветствие в чат при запуске")

    serve = commands.add_parser("serve", help="веб-виджет и JSON API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8100)

    commands.add_parser("levels", help="показать уровни, найденные по пивотам")
    return parser


def make_config(args: argparse.Namespace) -> RadarConfig:
    overrides: dict = {}
    if args.coin:
        overrides["coin"] = args.coin.upper()
    if args.level:
        overrides["level"] = None if args.level.lower() == "auto" else float(args.level.replace(",", "."))
    if args.timeframes:
        overrides["timeframes"] = tuple(part.strip() for part in args.timeframes.split(",") if part.strip())
    if args.database:
        overrides["database"] = args.database
    interval = getattr(args, "interval", None)
    if interval is not None:
        overrides["poll_seconds"] = interval
    return RadarConfig.from_env(**overrides)


def make_source(args: argparse.Namespace, config: RadarConfig) -> MarketSource:
    if args.snapshot:
        return JsonSource(args.snapshot)
    if args.demo:
        return SyntheticSource(args.demo, level=config.level or 83.40)
    return HyperliquidSource()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "report"
    config = make_config(args)
    source = make_source(args, config)

    if command == "serve":
        return _serve(args, config, source)

    store = RadarStore(config.database)
    watcher = Watcher(source, config, store, notifiers=from_env())

    try:
        if command == "report":
            return _report(watcher, args)
        if command == "levels":
            return _levels(watcher)
        if command == "watch":
            watcher.run_forever(iterations=args.iterations)
            return 0
        if command == "bot":
            return _bot(watcher, args)
    except SourceError as error:
        print(f"Источник данных недоступен: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nОстановлено.", file=sys.stderr)
        return 130
    finally:
        store.close()
    parser.print_help()
    return 1


def _report(watcher: Watcher, args: argparse.Namespace) -> int:
    snapshot = watcher.source.fetch(
        watcher.config.coin, watcher.config.timeframes, watcher.config.candle_limit
    )
    report = watcher.analyse(snapshot)
    if getattr(args, "save", None):
        Path(args.save).write_text(json.dumps(snapshot_to_dict(snapshot), ensure_ascii=False, indent=2))
        print(f"Снимок сохранён: {args.save}", file=sys.stderr)
    if getattr(args, "json", False):
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        return 0
    print(render(report, snapshot))
    return 0


def _levels(watcher: Watcher) -> int:
    from .levels import relevant_levels

    snapshot = watcher.source.fetch(
        watcher.config.coin, watcher.config.timeframes, watcher.config.candle_limit
    )
    candles = list(snapshot.frame(watcher.config.primary_timeframe))
    levels = relevant_levels(candles)
    print(f"{watcher.config.coin} {snapshot.price:g} — уровни по пивотам {watcher.config.primary_timeframe}:")
    for level in levels[:12]:
        distance = (level.price - snapshot.price) / snapshot.price * 100
        print(f"  {level.price:>12,.4f}  {level.kind:<10} касаний: {level.touches}  ({distance:+.2f}% от цены)")
    if not levels:
        print("  Пивотов недостаточно — увеличьте --timeframes или лимит свечей.")
    return 0


def _bot(watcher: Watcher, args: argparse.Namespace) -> int:
    from .bot import RadarBot

    telegram = TelegramNotifier.from_env()
    if telegram is None:
        print(
            "Нужны переменные окружения RADAR_TELEGRAM_TOKEN и RADAR_TELEGRAM_CHAT_ID.",
            file=sys.stderr,
        )
        return 2
    bot = RadarBot(watcher, telegram)
    if not args.silent_start:
        bot.announce_start()
    bot.run()
    return 0


def _serve(args: argparse.Namespace, config: RadarConfig, source: MarketSource) -> int:
    try:
        import uvicorn
    except ImportError:
        print("Нужен uvicorn: pip install -r requirements.txt", file=sys.stderr)
        return 2
    from . import api

    api.configure(config, source)
    uvicorn.run(api.app, host=args.host, port=args.port)
    return 0


def render(report: RadarReport, snapshot: MarketSnapshot | None = None) -> str:
    width = 78
    lines = [
        "=" * width,
        f" {report.coin}  цена {report.price:g}  |  {report.state.title}  |  счёт {report.score:+.0f}/100",
        "=" * width,
    ]
    if report.level is not None:
        lines.append(f" Уровень анализа: {report.level:g}")
    lines.append(
        f" Данных по сигналам: {report.coverage:.0%}  |  "
        f"таймфреймов за этот сценарий: {report.alignment}/{len(report.timeframes)}"
    )
    lines.append("")
    lines.append(" Сигналы (сильные сверху):")
    ranked = sorted(report.signals, key=lambda signal: abs(signal.contribution), reverse=True)
    for signal in ranked:
        mark = MARK[signal.direction]
        head = f"  {mark} {signal.title}"
        if signal.timeframe:
            head += f" [{signal.timeframe}]"
        lines.append(f"{head} (вклад {signal.contribution:+.2f})")
        lines.append(f"      {signal.detail}")

    lines.append("")
    lines.append(" Таймфреймы:")
    for view in report.timeframes:
        lines.append(f"  {view.timeframe:>4}: {view.trend.value:<7} {view.detail}")

    lines.append("")
    lines.append(" План на оба исхода:")
    lines.append(f"  Лонг: {report.plan.long_trigger}")
    if report.plan.long_invalidation is not None:
        lines.append(f"        инвалидация ниже {report.plan.long_invalidation:g}")
    lines.append(f"  Шорт: {report.plan.short_trigger}")
    if report.plan.short_invalidation is not None:
        lines.append(f"        инвалидация выше {report.plan.short_invalidation:g}")
    for note in report.plan.notes:
        lines.append(f"  · {note}")

    if snapshot is not None:
        lines.append("")
        funding = f"{snapshot.funding_hourly:+.4%}/ч" if snapshot.funding_hourly is not None else "нет данных"
        oi = f"{snapshot.open_interest:,.0f}" if snapshot.open_interest is not None else "нет данных"
        lines.append(f" Funding: {funding}   Open interest: {oi}")

    if report.warnings:
        lines.append("")
        lines.append(" Ограничения данных:")
        for warning in report.warnings:
            lines.append(f"  ! {warning}")

    lines.append("")
    lines.append(f" {DISCLAIMER}")
    lines.append("=" * width)
    return "\n".join(lines)
