import unittest

from app.domain import DemoClassifier
from app.repository import Repository
from app.service import DocumentService


class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = Repository(":memory:")
        self.service = DocumentService(self.repository, DemoClassifier())

    def test_processed_document_is_audited(self) -> None:
        record = self.service.process("test", "Оплата счёта ООО Ромашка 1200 RUB")
        self.assertEqual(record["status"], "ready")
        self.assertEqual(record["counterparty"], "ООО Ромашка")
        self.assertEqual(len(self.repository.list("ready")), 1)
