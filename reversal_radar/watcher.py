"""Опрос рынка по расписанию и алерты только на смену картины, а не на каждый тик."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime

from .config import RadarConfig
from .engine import analyse, summarize
from .models import DISCLAIMER, MarketSnapshot, RadarReport, RadarState
from .notifiers import Notifier
from .sources.base import MarketSource, SourceError
from .store import RadarStore

CONFIRMED = {RadarState.REVERSAL_DOWN, RadarState.BREAKOUT_UP}


@dataclass(frozen=True)
class AlertDecision:
    send: bool
    reason: str


class Watcher:
    def __init__(
        self,
        source: MarketSource,
        config: RadarConfig,
        store: RadarStore,
        notifiers: list[Notifier] | None = None,
    ) -> None:
        self.source = source
        self.config = config
        self.store = store
        self.notifiers = notifiers or []

    def poll(self) -> RadarReport:
        snapshot = self.source.fetch(self.config.coin, self.config.timeframes, self.config.candle_limit)
        return self.analyse(snapshot)

    def analyse(self, snapshot: MarketSnapshot) -> RadarReport:
        reference = self.store.reference_snapshot(self.config.coin, self.config.oi_window_hours)
        age_hours = None
        if reference is not None:
            taken = datetime.fromisoformat(reference["taken_at"])
            age_hours = (datetime.now(UTC) - taken).total_seconds() / 3600
        report = analyse(
            snapshot,
            self.config,
            previous_oi=reference["open_interest"] if reference else None,
            previous_price=reference["price"] if reference else None,
            oi_age_hours=age_hours,
        )
        self.store.record(report, snapshot.funding_hourly, snapshot.open_interest)
        return report

    def decide_alert(self, report: RadarReport, now: datetime | None = None) -> AlertDecision:
        now = now or datetime.now(UTC)
        previous = self.store.last_alert(self.config.coin)
        if previous is None:
            if report.state is RadarState.UNDECIDED:
                return AlertDecision(False, "первый опрос, картина не сложилась")
            return AlertDecision(True, "первый значимый сигнал")

        elapsed = (now - datetime.fromisoformat(previous["sent_at"])).total_seconds()
        state_changed = previous["state"] != report.state.value
        moved = abs(report.score - previous["score"])

        if state_changed and report.state in CONFIRMED:
            return AlertDecision(True, f"сигналы совпали: {report.state.value}")
        if elapsed < self.config.alert_cooldown_seconds:
            return AlertDecision(False, f"пауза после прошлого алерта ({elapsed:.0f}s)")
        if state_changed and report.state is not RadarState.UNDECIDED:
            return AlertDecision(True, f"состояние сменилось: {previous['state']} → {report.state.value}")
        if moved >= self.config.alert_score_step:
            return AlertDecision(True, f"счёт сдвинулся на {moved:.0f} пунктов")
        return AlertDecision(False, f"без изменений (Δ{moved:.0f})")

    def notify(self, report: RadarReport) -> bool:
        text = f"{summarize(report)}\n\n{DISCLAIMER}"
        delivered = any(notifier.send(text) for notifier in self.notifiers)
        if delivered:
            self.store.save_alert(report, text)
        return delivered

    def run_once(self) -> RadarReport | None:
        try:
            report = self.poll()
        except SourceError as error:
            print(f"[radar] источник недоступен: {error}", flush=True)
            return None
        decision = self.decide_alert(report)
        stamp = report.generated_at.strftime("%H:%M:%S")
        print(
            f"[radar {stamp}] {report.coin} {report.price:g} score={report.score:+.0f} "
            f"state={report.state.value} alert={'да' if decision.send else 'нет'} ({decision.reason})",
            flush=True,
        )
        if decision.send:
            self.notify(report)
        return report

    def run_forever(self, iterations: int | None = None) -> None:
        done = 0
        while iterations is None or done < iterations:
            self.run_once()
            done += 1
            if iterations is not None and done >= iterations:
                break
            time.sleep(self.config.poll_seconds)
