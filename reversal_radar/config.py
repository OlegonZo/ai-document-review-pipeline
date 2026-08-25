from __future__ import annotations

import os
from dataclasses import dataclass, field, replace

DEFAULT_TIMEFRAMES: tuple[str, ...] = ("15m", "1h", "4h", "1d")

# Вес = во сколько раз сигнал важнее остальных при сложении в итоговый счёт.
DEFAULT_WEIGHTS: dict[str, float] = {
    "level_reaction": 2.0,
    "level_fatigue": 0.8,
    "volume_confirmation": 1.2,
    "volume_spike": 1.0,
    "rsi_divergence": 1.4,
    "rsi_extreme": 0.6,
    "funding": 1.0,
    "open_interest": 1.2,
    "book_pressure": 0.8,
    "mtf_alignment": 1.5,
}

# Дивергенция на 4Ч весит больше, чем на 15м: старший ТФ переживает шум.
DEFAULT_TIMEFRAME_WEIGHTS: dict[str, float] = {"15m": 0.5, "1h": 1.0, "4h": 1.4, "1d": 1.6}


@dataclass(frozen=True)
class RadarConfig:
    coin: str = "HYPE"
    level: float | None = None
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES
    candle_limit: int = 200
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    timeframe_weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_TIMEFRAME_WEIGHTS))
    primary_timeframe: str = "1h"
    fast_timeframe: str = "15m"

    # Средняя сила сигналов, при которой счёт достигает 100 по модулю.
    score_gain: float = 0.55

    # Пороги состояний по итоговому счёту (-100..100).
    confirmed_threshold: float = 55.0
    warning_threshold: float = 25.0

    # Funding: годовая ставка, при которой сигнал считается максимально выраженным.
    funding_annual_full: float = 0.60
    # Полоса стакана вокруг середины, в которой считаем плотность заявок.
    book_band_pct: float = 0.005
    # Окно (в часах) для сравнения open interest с прошлым снимком.
    oi_window_hours: float = 4.0

    # Наблюдение и алерты.
    poll_seconds: int = 180
    alert_cooldown_seconds: int = 1800
    alert_score_step: float = 15.0
    database: str = "data/radar.sqlite3"

    def weight(self, key: str) -> float:
        return self.weights.get(key, 0.0)

    def with_level(self, level: float | None) -> "RadarConfig":
        return replace(self, level=level)

    @classmethod
    def from_env(cls, **overrides) -> "RadarConfig":
        env: dict = {}
        if value := os.getenv("RADAR_COIN"):
            env["coin"] = value.upper()
        if value := os.getenv("RADAR_LEVEL"):
            env["level"] = float(value)
        if value := os.getenv("RADAR_TIMEFRAMES"):
            env["timeframes"] = tuple(part.strip() for part in value.split(",") if part.strip())
        if value := os.getenv("RADAR_POLL_SECONDS"):
            env["poll_seconds"] = int(value)
        if value := os.getenv("RADAR_ALERT_COOLDOWN"):
            env["alert_cooldown_seconds"] = int(value)
        if value := os.getenv("RADAR_DATABASE"):
            env["database"] = value
        return cls(**(env | overrides))
