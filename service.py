from __future__ import annotations

from .domain import Classifier, decide
from .repository import Repository


class DocumentService:
    def __init__(self, repository: Repository, classifier: Classifier) -> None:
        self.repository = repository
        self.classifier = classifier

    def process(self, source: str, text: str) -> dict:
        classification = self.classifier.classify(text)
        decision = decide(classification)
        return self.repository.save(source, text, classification, decision)
