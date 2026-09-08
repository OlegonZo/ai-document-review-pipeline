import unittest
from dataclasses import replace
from decimal import Decimal

from app.domain import Classification, DecisionStatus, decide


class DecisionTests(unittest.TestCase):
    def test_ready_when_all_guardrails_pass(self) -> None:
        classification = Classification("invoice_payment", 0.95, "ООО Ромашка", Decimal("100"), "RUB")
        result = decide(classification)
        self.assertEqual(result.status, DecisionStatus.READY)
        self.assertEqual(result.reasons, ())

    def test_low_confidence_goes_to_manual_review(self) -> None:
        classification = Classification("invoice_payment", 0.51, "ООО Ромашка", Decimal("100"), "RUB")
        result = decide(classification)
        self.assertEqual(result.status, DecisionStatus.REVIEW)
        self.assertIn("low_confidence", result.reasons)

    def test_missing_amount_is_not_auto_approved(self) -> None:
        classification = Classification("invoice_payment", 0.95, "ООО Ромашка", None, "RUB")
        result = decide(classification)
        self.assertEqual(result.status, DecisionStatus.REVIEW)
        self.assertIn("invalid_amount", result.reasons)

    def test_each_validation_rule_can_reject_independently(self) -> None:
        valid = Classification("invoice_payment", 0.95, "ООО Ромашка", Decimal("100"), "RUB")
        cases = [
            ({"category": "unknown"}, "unknown_category"),
            ({"confidence": 0.84}, "low_confidence"),
            ({"counterparty": None}, "missing_counterparty"),
            ({"amount": Decimal("0")}, "invalid_amount"),
            ({"amount": Decimal("-1")}, "invalid_amount"),
            ({"currency": "GBP"}, "unsupported_currency"),
        ]
        for changes, reason in cases:
            with self.subTest(changes=changes):
                result = decide(replace(valid, **changes))
                self.assertEqual(result.status, DecisionStatus.REVIEW)
                self.assertEqual(result.reasons, (reason,))

    def test_confidence_threshold_is_inclusive(self) -> None:
        classification = Classification("invoice_payment", 0.85, "ООО Ромашка", Decimal("100"), "RUB")
        self.assertEqual(decide(classification).status, DecisionStatus.READY)

    def test_multiple_reasons_are_not_silently_discarded(self) -> None:
        classification = Classification("unknown", 0.4, None, None, None)
        result = decide(classification)
        self.assertEqual(result.status, DecisionStatus.REVIEW)
        self.assertEqual(result.reasons, (
            "unknown_category", "low_confidence", "missing_counterparty", "invalid_amount", "unsupported_currency"
        ))
