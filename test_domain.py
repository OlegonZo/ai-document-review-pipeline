import unittest
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
