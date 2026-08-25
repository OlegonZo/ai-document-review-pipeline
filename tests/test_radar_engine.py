import unittest
from datetime import UTC, datetime

from reversal_radar.config import RadarConfig
from reversal_radar.engine import analyse, summarize
from reversal_radar.models import Direction, MarketSnapshot, RadarState
from reversal_radar.sources.offline import SyntheticSource, snapshot_from_dict, snapshot_to_dict

LEVEL = 83.40


def report_for(scenario: str, **kwargs):
    config = RadarConfig(coin="HYPE", level=LEVEL)
    snapshot = SyntheticSource(scenario, LEVEL).fetch("HYPE", config.timeframes, 200)
    return analyse(snapshot, config, **kwargs), snapshot


class EngineTests(unittest.TestCase):
    def test_exhaustion_scenario_scores_negative(self) -> None:
        report, _ = report_for("exhaustion")
        self.assertLess(report.score, 0)
        self.assertIn(report.state, {RadarState.WEAKENING, RadarState.REVERSAL_DOWN})
        reaction = next(signal for signal in report.signals if signal.key == "level_reaction")
        self.assertEqual(reaction.direction, Direction.DOWN)

    def test_breakout_scenario_scores_positive(self) -> None:
        report, _ = report_for("breakout")
        self.assertGreater(report.score, 0)
        self.assertIn(report.state, {RadarState.STRENGTHENING, RadarState.BREAKOUT_UP})

    def test_score_is_bounded(self) -> None:
        for scenario in ("exhaustion", "breakout"):
            report, _ = report_for(scenario)
            self.assertGreaterEqual(report.score, -100)
            self.assertLessEqual(report.score, 100)

    def test_plan_puts_invalidation_on_both_sides_of_the_level(self) -> None:
        report, _ = report_for("exhaustion")
        self.assertEqual(report.plan.level, LEVEL)
        self.assertGreater(report.plan.short_invalidation, LEVEL)
        self.assertLess(report.plan.long_invalidation, LEVEL)
        self.assertTrue(report.plan.notes)

    def test_open_interest_history_changes_the_verdict(self) -> None:
        without, _ = report_for("exhaustion")
        with_history, _ = report_for("exhaustion", previous_oi=4_000_000, previous_price=82.60, oi_age_hours=4)
        oi_signal = next(signal for signal in with_history.signals if signal.key == "open_interest")
        self.assertNotEqual(oi_signal.direction, Direction.NEUTRAL)
        self.assertNotEqual(round(without.score, 3), round(with_history.score, 3))

    def test_missing_data_is_reported_not_hidden(self) -> None:
        empty = MarketSnapshot(coin="HYPE", price=83.0, taken_at=datetime.now(UTC))
        report = analyse(empty, RadarConfig(coin="HYPE"))
        self.assertEqual(report.state, RadarState.UNDECIDED)
        self.assertEqual(report.score, 0.0)
        self.assertLess(report.coverage, 0.5)
        self.assertTrue(report.warnings)

    def test_report_serializes_with_disclaimer(self) -> None:
        report, _ = report_for("exhaustion")
        payload = report.as_dict()
        self.assertEqual(payload["coin"], "HYPE")
        self.assertIn("disclaimer", payload)
        self.assertEqual(len(payload["signals"]), len(report.signals))

    def test_summary_mentions_both_scenarios(self) -> None:
        report, _ = report_for("exhaustion")
        text = summarize(report)
        self.assertIn("Лонг:", text)
        self.assertIn("Шорт:", text)

    def test_snapshot_survives_json_round_trip(self) -> None:
        _, snapshot = report_for("exhaustion")
        restored = snapshot_from_dict(snapshot_to_dict(snapshot))
        self.assertEqual(restored.coin, snapshot.coin)
        self.assertAlmostEqual(restored.price, snapshot.price)
        self.assertEqual(len(restored.frame("1h")), len(snapshot.frame("1h")))
        self.assertEqual(restored.book.asks[0].price, snapshot.book.asks[0].price)


if __name__ == "__main__":
    unittest.main()
