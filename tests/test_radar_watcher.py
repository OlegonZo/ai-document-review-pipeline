import unittest
from datetime import UTC, datetime, timedelta

from reversal_radar.bot import RadarBot
from reversal_radar.config import RadarConfig
from reversal_radar.models import Plan, RadarReport, RadarState
from reversal_radar.sources.offline import SyntheticSource
from reversal_radar.store import RadarStore
from reversal_radar.watcher import Watcher

LEVEL = 83.40


def make_report(state: RadarState, score: float, coin: str = "HYPE") -> RadarReport:
    return RadarReport(
        coin=coin,
        price=83.0,
        generated_at=datetime.now(UTC),
        state=state,
        score=score,
        coverage=1.0,
        level=LEVEL,
        signals=(),
        timeframes=(),
        plan=Plan(LEVEL, "лонг", 82.0, "шорт", 84.0),
    )


class RecordingNotifier:
    name = "recording"

    def __init__(self) -> None:
        self.messages: list[str] = []

    def send(self, text: str) -> bool:
        self.messages.append(text)
        return True


def make_watcher(**config_kwargs) -> tuple[Watcher, RecordingNotifier]:
    config = RadarConfig(coin="HYPE", level=LEVEL, database=":memory:", **config_kwargs)
    notifier = RecordingNotifier()
    watcher = Watcher(SyntheticSource("exhaustion", LEVEL), config, RadarStore(":memory:"), [notifier])
    return watcher, notifier


class AlertPolicyTests(unittest.TestCase):
    def test_first_meaningful_signal_alerts(self) -> None:
        watcher, _ = make_watcher()
        self.assertTrue(watcher.decide_alert(make_report(RadarState.WEAKENING, -30)).send)

    def test_first_poll_without_a_verdict_stays_quiet(self) -> None:
        watcher, _ = make_watcher()
        self.assertFalse(watcher.decide_alert(make_report(RadarState.UNDECIDED, -5)).send)

    def test_repeat_of_the_same_picture_is_suppressed(self) -> None:
        watcher, notifier = make_watcher()
        report = make_report(RadarState.WEAKENING, -30)
        watcher.notify(report)
        self.assertFalse(watcher.decide_alert(report).send)
        self.assertEqual(len(notifier.messages), 1)

    def test_confirmed_state_breaks_through_the_cooldown(self) -> None:
        watcher, _ = make_watcher()
        watcher.notify(make_report(RadarState.WEAKENING, -30))
        decision = watcher.decide_alert(make_report(RadarState.REVERSAL_DOWN, -70))
        self.assertTrue(decision.send)
        self.assertIn("совпали", decision.reason)

    def test_score_drift_alerts_after_the_cooldown(self) -> None:
        watcher, _ = make_watcher(alert_cooldown_seconds=600)
        watcher.notify(make_report(RadarState.WEAKENING, -30))
        stale = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        watcher.store.connection.execute("UPDATE alerts SET sent_at = ?", (stale,))
        watcher.store.connection.commit()
        self.assertTrue(watcher.decide_alert(make_report(RadarState.WEAKENING, -50)).send)
        self.assertFalse(watcher.decide_alert(make_report(RadarState.WEAKENING, -33)).send)

    def test_notify_writes_the_alert_journal(self) -> None:
        watcher, notifier = make_watcher()
        watcher.notify(make_report(RadarState.REVERSAL_DOWN, -70))
        self.assertEqual(len(notifier.messages), 1)
        self.assertIn("не прогноз", notifier.messages[0])
        self.assertEqual(len(watcher.store.alerts("HYPE")), 1)


class PollingTests(unittest.TestCase):
    def test_poll_records_history_and_fills_open_interest_later(self) -> None:
        watcher, _ = make_watcher()
        first = watcher.poll()
        first_oi = next(signal for signal in first.signals if signal.key == "open_interest")
        self.assertIn("Истории", first_oi.detail)

        second = watcher.poll()
        second_oi = next(signal for signal in second.signals if signal.key == "open_interest")
        self.assertNotIn("Истории", second_oi.detail)
        self.assertEqual(len(watcher.store.history("HYPE")), 2)

    def test_run_forever_stops_after_requested_iterations(self) -> None:
        watcher, _ = make_watcher(poll_seconds=0)
        watcher.run_forever(iterations=2)
        self.assertEqual(len(watcher.store.history("HYPE")), 2)


class FakeTelegram:
    chat_id = "42"

    def __init__(self, updates: list[dict] | None = None) -> None:
        self.updates = updates or []
        self.sent: list[str] = []

    def call(self, method: str, payload: dict):
        if method == "getUpdates":
            batch, self.updates = self.updates, []
            return batch
        if method == "sendMessage":
            self.sent.append(payload["text"])
            return {"message_id": len(self.sent)}
        return None

    def send(self, text: str) -> bool:
        self.sent.append(text)
        return True


class BotTests(unittest.TestCase):
    def make_bot(self, updates: list[dict] | None = None) -> tuple[RadarBot, FakeTelegram]:
        watcher, _ = make_watcher()
        telegram = FakeTelegram(updates)
        return RadarBot(watcher, telegram), telegram

    def test_level_command_updates_config(self) -> None:
        bot, _ = self.make_bot()
        self.assertIn("83.4", bot.handle("/level 83,40"))
        self.assertEqual(bot.watcher.config.level, 83.40)
        bot.handle("/level auto")
        self.assertIsNone(bot.watcher.config.level)

    def test_coin_command_resets_level(self) -> None:
        bot, _ = self.make_bot()
        bot.handle("/level 83.40")
        bot.handle("/coin eth")
        self.assertEqual(bot.watcher.config.coin, "ETH")
        self.assertIsNone(bot.watcher.config.level)

    def test_watch_toggle(self) -> None:
        bot, _ = self.make_bot()
        bot.handle("/watch off")
        self.assertFalse(bot.watching)
        bot.handle("/watch on")
        self.assertTrue(bot.watching)

    def test_unknown_command_is_explained(self) -> None:
        bot, _ = self.make_bot()
        self.assertIn("/help", bot.handle("/цена"))

    def test_status_returns_a_report(self) -> None:
        bot, _ = self.make_bot()
        answer = bot.handle("/status")
        self.assertIn("HYPE", answer)
        self.assertIn("Шорт:", answer)

    def test_updates_from_another_chat_are_ignored(self) -> None:
        updates = [
            {"update_id": 1, "message": {"chat": {"id": 999}, "text": "/help"}},
            {"update_id": 2, "message": {"chat": {"id": 42}, "text": "/watch off"}},
        ]
        bot, telegram = self.make_bot(updates)
        bot.pump_updates()
        self.assertEqual(len(telegram.sent), 1)
        self.assertFalse(bot.watching)
        self.assertEqual(bot.offset, 3)


if __name__ == "__main__":
    unittest.main()
