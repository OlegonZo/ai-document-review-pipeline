from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .domain import Classification, Decision


class Repository:
    def __init__(self, database_path: str) -> None:
        if database_path != ":memory:":
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(database_path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._create_schema()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                raw_text TEXT NOT NULL,
                category TEXT NOT NULL,
                confidence REAL NOT NULL,
                counterparty TEXT,
                amount TEXT,
                currency TEXT,
                status TEXT NOT NULL,
                reasons_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(document_id) REFERENCES documents(id)
            );
            """
        )
        self.connection.commit()

    def save(self, source: str, raw_text: str, classification: Classification, decision: Decision) -> dict:
        created_at = datetime.now(UTC).isoformat()
        with self.connection:
            cursor = self.connection.execute(
                """INSERT INTO documents
                (source, raw_text, category, confidence, counterparty, amount, currency, status, reasons_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    source,
                    raw_text,
                    classification.category,
                    classification.confidence,
                    classification.counterparty,
                    str(classification.amount) if classification.amount is not None else None,
                    classification.currency,
                    decision.status.value,
                    json.dumps(decision.reasons),
                    created_at,
                ),
            )
            document_id = cursor.lastrowid
            self.connection.execute(
                "INSERT INTO audit_events (document_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (
                    document_id,
                    "document_decided",
                    json.dumps({"classification": asdict(classification), "decision": asdict(decision)}, default=str),
                    created_at,
                ),
            )
        return self.get(document_id)

    def get(self, document_id: int) -> dict:
        row = self.connection.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        if row is None:
            raise KeyError(document_id)
        return self._serialize(row)

    def list(self, status: str | None = None) -> list[dict]:
        if status:
            rows = self.connection.execute("SELECT * FROM documents WHERE status = ? ORDER BY id DESC", (status,)).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM documents ORDER BY id DESC").fetchall()
        return [self._serialize(row) for row in rows]

    @staticmethod
    def _serialize(row: sqlite3.Row) -> dict:
        result = dict(row)
        result["reasons"] = json.loads(result.pop("reasons_json"))
        return result
