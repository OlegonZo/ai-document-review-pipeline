"""История снимков и журнал алертов в SQLite: без неё нет динамики OI и есть спам алертов."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .models import RadarReport


class RadarStore:
    def __init__(self, database_path: str) -> None:
        if database_path != ":memory:":
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(database_path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._create_schema()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                coin TEXT NOT NULL,
                taken_at TEXT NOT NULL,
                price REAL NOT NULL,
                open_interest REAL,
                funding_hourly REAL,
                score REAL,
                state TEXT
            );
            CREATE INDEX IF NOT EXISTS snapshots_coin_time ON snapshots(coin, taken_at);
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                coin TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                state TEXT NOT NULL,
                score REAL NOT NULL,
                text TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS alerts_coin_time ON alerts(coin, sent_at);
            """
        )
        self.connection.commit()

    def record(self, report: RadarReport, funding_hourly: float | None, open_interest: float | None) -> None:
        with self.connection:
            self.connection.execute(
                """INSERT INTO snapshots (coin, taken_at, price, open_interest, funding_hourly, score, state)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    report.coin,
                    report.generated_at.isoformat(),
                    report.price,
                    open_interest,
                    funding_hourly,
                    report.score,
                    report.state.value,
                ),
            )

    def reference_snapshot(self, coin: str, hours: float) -> sqlite3.Row | None:
        """Ближайший снимок не новее, чем hours назад — база для сравнения OI и цены."""
        cutoff = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
        row = self.connection.execute(
            """SELECT * FROM snapshots
               WHERE coin = ? AND open_interest IS NOT NULL AND taken_at <= ?
               ORDER BY taken_at DESC LIMIT 1""",
            (coin, cutoff),
        ).fetchone()
        if row is not None:
            return row
        return self.connection.execute(
            """SELECT * FROM snapshots
               WHERE coin = ? AND open_interest IS NOT NULL
               ORDER BY taken_at ASC LIMIT 1""",
            (coin,),
        ).fetchone()

    def last_alert(self, coin: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM alerts WHERE coin = ? ORDER BY sent_at DESC LIMIT 1", (coin,)
        ).fetchone()

    def save_alert(self, report: RadarReport, text: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO alerts (coin, sent_at, state, score, text) VALUES (?, ?, ?, ?, ?)",
                (
                    report.coin,
                    datetime.now(UTC).isoformat(),
                    report.state.value,
                    report.score,
                    text,
                ),
            )

    def history(self, coin: str, limit: int = 50) -> list[dict]:
        rows = self.connection.execute(
            "SELECT taken_at, price, score, state, open_interest, funding_hourly FROM snapshots "
            "WHERE coin = ? ORDER BY taken_at DESC LIMIT ?",
            (coin, limit),
        ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def alerts(self, coin: str, limit: int = 20) -> list[dict]:
        rows = self.connection.execute(
            "SELECT sent_at, state, score, text FROM alerts WHERE coin = ? ORDER BY sent_at DESC LIMIT ?",
            (coin, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        self.connection.close()
