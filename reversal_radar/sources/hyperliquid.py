"""Клиент публичного info-эндпоинта Hyperliquid. Ключи и подписи не нужны."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime

from ..models import BookLevel, Candle, MarketSnapshot, OrderBook
from .base import SourceError

API_URL = "https://api.hyperliquid.xyz/info"

INTERVAL_MS: dict[str, int] = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "3d": 259_200_000,
    "1w": 604_800_000,
}


class HyperliquidSource:
    name = "hyperliquid"

    def __init__(self, api_url: str = API_URL, timeout: float = 10.0, pause: float = 0.15) -> None:
        self.api_url = api_url
        self.timeout = timeout
        self.pause = pause

    def fetch(self, coin: str, timeframes: tuple[str, ...], limit: int = 200) -> MarketSnapshot:
        context = self._asset_context(coin)
        candles = {}
        for timeframe in timeframes:
            candles[timeframe] = self.candles(coin, timeframe, limit)
            time.sleep(self.pause)
        book = self.book(coin)

        price = _float(context.get("midPx")) or _float(context.get("markPx")) or 0.0
        if not price and candles:
            first = next(iter(candles.values()))
            price = first[-1].close if first else 0.0
        return MarketSnapshot(
            coin=coin,
            price=price,
            taken_at=datetime.now(UTC),
            mark_price=_float(context.get("markPx")),
            oracle_price=_float(context.get("oraclePx")),
            funding_hourly=_float(context.get("funding")),
            open_interest=_float(context.get("openInterest")),
            day_volume=_float(context.get("dayNtlVlm")),
            candles=candles,
            book=book,
        )

    def _asset_context(self, coin: str) -> dict:
        payload = self._post({"type": "metaAndAssetCtxs"})
        try:
            universe = payload[0]["universe"]
            contexts = payload[1]
        except (KeyError, IndexError, TypeError) as error:
            raise SourceError("неожиданный ответ metaAndAssetCtxs") from error

        for index, asset in enumerate(universe):
            if asset.get("name", "").upper() == coin.upper():
                if index >= len(contexts):
                    raise SourceError(f"нет контекста для {coin}")
                return contexts[index] or {}
        known = ", ".join(asset.get("name", "?") for asset in universe[:15])
        raise SourceError(f"монета {coin} не найдена среди перпов Hyperliquid (например: {known}…)")

    def candles(self, coin: str, timeframe: str, limit: int = 200) -> tuple[Candle, ...]:
        step = INTERVAL_MS.get(timeframe)
        if step is None:
            raise SourceError(f"неизвестный таймфрейм {timeframe}")
        now_ms = int(time.time() * 1000)
        payload = self._post(
            {
                "type": "candleSnapshot",
                "req": {
                    "coin": coin,
                    "interval": timeframe,
                    "startTime": now_ms - step * (limit + 1),
                    "endTime": now_ms,
                },
            }
        )
        if not isinstance(payload, list):
            raise SourceError("неожиданный ответ candleSnapshot")
        candles = [
            Candle(
                open_time=int(row["t"]),
                close_time=int(row["T"]),
                open=float(row["o"]),
                high=float(row["h"]),
                low=float(row["l"]),
                close=float(row["c"]),
                volume=float(row["v"]),
                trades=int(row.get("n", 0)),
            )
            for row in payload
        ]
        return tuple(drop_forming(candles, now_ms))

    def book(self, coin: str) -> OrderBook | None:
        try:
            payload = self._post({"type": "l2Book", "coin": coin})
        except SourceError:
            return None
        levels = payload.get("levels") if isinstance(payload, dict) else None
        if not levels or len(levels) < 2:
            return None
        return OrderBook(
            bids=tuple(BookLevel(float(item["px"]), float(item["sz"])) for item in levels[0]),
            asks=tuple(BookLevel(float(item["px"]), float(item["sz"])) for item in levels[1]),
        )

    def funding_history(self, coin: str, hours: int = 24) -> list[dict]:
        start = int(time.time() * 1000) - hours * 3_600_000
        payload = self._post({"type": "fundingHistory", "coin": coin, "startTime": start})
        return payload if isinstance(payload, list) else []

    def _post(self, body: dict) -> dict | list:
        request = urllib.request.Request(
            self.api_url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "User-Agent": "reversal-radar/0.1"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            raise SourceError(f"HTTP {error.code} от {self.api_url}: {error.reason}") from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise SourceError(f"сеть недоступна: {error}") from error
        except json.JSONDecodeError as error:
            raise SourceError("ответ не является JSON") from error


def drop_forming(candles: list[Candle], now_ms: int) -> list[Candle]:
    """Незакрытую свечу в анализ не берём: её объём и закрытие ещё изменятся."""
    return [candle for candle in candles if candle.close_time <= now_ms]


def _float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
