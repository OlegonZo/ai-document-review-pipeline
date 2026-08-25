from __future__ import annotations

from typing import Protocol

from ..models import MarketSnapshot


class SourceError(RuntimeError):
    """Источник данных недоступен или ответил неожиданным форматом."""


class MarketSource(Protocol):
    name: str

    def fetch(self, coin: str, timeframes: tuple[str, ...], limit: int) -> MarketSnapshot: ...
