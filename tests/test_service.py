import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.domain import DemoClassifier
from app.repository import Repository
from app.service import DocumentService


class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = Repository(":memory:")
        self.addCleanup(self.repository.connection.close)
        self.service = DocumentService(self.repository, DemoClassifier())

    def test_processed_document_is_audited(self) -> None:
        record = self.service.process("test", "Оплата счёта ООО Ромашка 1200 RUB")
        self.assertEqual(record["status"], "ready")
        self.assertEqual(record["counterparty"], "ООО Ромашка")
        self.assertEqual(len(self.repository.list("ready")), 1)
        events = self.repository.connection.execute("SELECT * FROM audit_events").fetchall()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["document_id"], record["id"])
        self.assertEqual(events[0]["event_type"], "document_decided")
        self.assertEqual(events[0]["created_at"], record["created_at"])
        payload = json.loads(events[0]["payload_json"])
        self.assertEqual(payload["classification"]["amount"], "1200")
        self.assertEqual(payload["decision"], {"status": "ready", "reasons": []})

    def test_review_reasons_are_preserved_in_record_and_audit(self) -> None:
        record = self.service.process("test", "Непонятный документ")
        self.assertEqual(record["status"], "review")
        self.assertIn("missing_counterparty", record["reasons"])
        self.assertIn("invalid_amount", record["reasons"])
        event = self.repository.connection.execute("SELECT payload_json FROM audit_events").fetchone()
        decision = json.loads(event["payload_json"])["decision"]
        self.assertEqual(decision["status"], "review")
        self.assertEqual(decision["reasons"], record["reasons"])

    def test_status_filter_keeps_ready_and_review_separate(self) -> None:
        ready = self.service.process("test", "Оплата ООО Ромашка 12 RUB")
        review = self.service.process("test", "Непонятный документ")
        self.assertEqual([r["id"] for r in self.repository.list("ready")], [ready["id"]])
        self.assertEqual([r["id"] for r in self.repository.list("review")], [review["id"]])
        self.assertEqual([r["id"] for r in self.repository.list()], [review["id"], ready["id"]])

    def test_audit_insert_failure_rolls_back_document(self) -> None:
        # Inject a database failure only in the disposable test database.
        self.repository.connection.executescript(
            "CREATE TRIGGER fail_audit BEFORE INSERT ON audit_events "
            "BEGIN SELECT RAISE(ABORT, 'injected audit failure'); END;"
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "injected audit failure"):
            self.service.process("test", "Оплата ООО Ромашка 12 RUB")
        self.assertEqual(self.repository.list(), [])
        self.assertEqual(self.repository.connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0], 0)

    def test_missing_document_raises_key_error(self) -> None:
        with self.assertRaises(KeyError):
            self.repository.get(999)

    def test_records_and_audit_survive_reopening_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "nested" / "records.sqlite3")
            repository = Repository(database)
            try:
                record = DocumentService(repository, DemoClassifier()).process("test", "Оплата ООО Ромашка 12 RUB")
            finally:
                repository.connection.close()
            reopened = Repository(database)
            try:
                self.assertEqual(reopened.get(record["id"]), record)
                event = reopened.connection.execute("SELECT document_id FROM audit_events").fetchone()
                self.assertEqual(event["document_id"], record["id"])
            finally:
                reopened.connection.close()
