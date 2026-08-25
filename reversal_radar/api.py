"""HTTP-обёртка: JSON API и веб-виджет. Запуск: python -m reversal_radar serve."""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from .config import RadarConfig
from .engine import analyse
from .models import DISCLAIMER, MarketSnapshot, RadarReport
from .sources.base import MarketSource, SourceError
from .sources.hyperliquid import HyperliquidSource
from .store import RadarStore

app = FastAPI(title="Reversal Radar", version="0.1.0")

_config = RadarConfig.from_env()
_source: MarketSource = HyperliquidSource()
_store: RadarStore | None = None
_cache: dict[tuple[str, float | None], tuple[float, RadarReport]] = {}
CACHE_SECONDS = 30.0
WIDGET = Path(__file__).with_name("widget.html")


def configure(config: RadarConfig, source: MarketSource | None = None) -> None:
    """Позволяет подменить конфиг и источник (например, синтетический) до запуска."""
    global _config, _source, _store
    _config = config
    if source is not None:
        _source = source
    _store = None
    _cache.clear()


def store() -> RadarStore:
    global _store
    if _store is None:
        _store = RadarStore(_config.database)
    return _store


def build_report(coin: str, level: float | None, force: bool = False) -> RadarReport:
    key = (coin.upper(), level)
    cached = _cache.get(key)
    if cached and not force and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]

    config = replace(_config, coin=coin.upper(), level=level)
    snapshot: MarketSnapshot = _source.fetch(config.coin, config.timeframes, config.candle_limit)
    reference = store().reference_snapshot(config.coin, config.oi_window_hours)
    report = analyse(
        snapshot,
        config,
        previous_oi=reference["open_interest"] if reference else None,
        previous_price=reference["price"] if reference else None,
        oi_age_hours=config.oi_window_hours if reference else None,
    )
    store().record(report, snapshot.funding_hourly, snapshot.open_interest)
    _cache[key] = (time.monotonic(), report)
    return report


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "coin": _config.coin, "source": getattr(_source, "name", "unknown")}


@app.get("/api/report")
def report(
    coin: str | None = Query(default=None, max_length=20),
    level: float | None = Query(default=None, gt=0),
) -> dict:
    try:
        result = build_report(coin or _config.coin, level if level is not None else _config.level)
    except SourceError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return result.as_dict()


@app.get("/api/history")
def history(coin: str | None = Query(default=None, max_length=20), limit: int = Query(default=60, ge=1, le=500)) -> dict:
    target = (coin or _config.coin).upper()
    return {"coin": target, "snapshots": store().history(target, limit), "alerts": store().alerts(target)}


@app.get("/", response_class=HTMLResponse)
def widget() -> HTMLResponse:
    if not WIDGET.exists():
        raise HTTPException(status_code=500, detail="widget.html не найден")
    html = WIDGET.read_text(encoding="utf-8").replace("__DISCLAIMER__", DISCLAIMER).replace("__COIN__", _config.coin)
    return HTMLResponse(html)
