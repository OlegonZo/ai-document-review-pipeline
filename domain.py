from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Protocol


class DecisionStatus(StrEnum):
    READY = "ready"
    REVIEW = "review"


@dataclass(frozen=True)
class Classification:
    category: str
    confidence: float
    counterparty: str | None
    amount: Decimal | None
    currency: str | None


class Classifier(Protocol):
    def classify(self, text: str) -> Classification: ...


class DemoClassifier:
    """Transparent placeholder; it deliberately makes no external AI calls."""

    def classify(self, text: str) -> Classification:
        tokens = text.replace(",", ".").split()
        amount = next((self._decimal(token) for token in tokens if self._decimal(token) is not None), None)
        currency = next((token.upper() for token in tokens if token.upper() in {"RUB", "USD", "EUR"}), None)
        marker = "ООО "
        counterparty = None
        if marker in text:
            tail = text.split(marker, 1)[1].strip().split()
            counterparty = f"{marker}{tail[0]}" if tail else None
        category = "invoice_payment" if "оплат" in text.lower() else "unknown"
        confidence = 0.92 if category == "invoice_payment" and amount and counterparty else 0.45
        return Classification(category, confidence, counterparty, amount, currency)

    @staticmethod
    def _decimal(token: str) -> Decimal | None:
        try:
            value = Decimal(token)
        except InvalidOperation:
            return None
        return value if value > 0 else None


@dataclass(frozen=True)
class Decision:
    status: DecisionStatus
    reasons: tuple[str, ...]


def decide(classification: Classification, minimum_confidence: float = 0.85) -> Decision:
    reasons: list[str] = []
    if classification.category != "invoice_payment":
        reasons.append("unknown_category")
    if classification.confidence < minimum_confidence:
        reasons.append("low_confidence")
    if not classification.counterparty:
        reasons.append("missing_counterparty")
    if classification.amount is None or classification.amount <= 0:
        reasons.append("invalid_amount")
    if classification.currency not in {"RUB", "USD", "EUR"}:
        reasons.append("unsupported_currency")
    return Decision(DecisionStatus.REVIEW if reasons else DecisionStatus.READY, tuple(reasons))
