from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from .domain import DemoClassifier
from .repository import Repository
from .service import DocumentService

database = os.getenv("PIPELINE_DATABASE", str(Path("data") / "pipeline.sqlite3"))
service = DocumentService(Repository(database), DemoClassifier())

app = FastAPI(title="AI Document Review Pipeline", version="0.1.0")


class DocumentInput(BaseModel):
    source: str = Field(min_length=1, max_length=80, examples=["demo"])
    text: str = Field(min_length=1, max_length=10_000, examples=["Оплата счёта ООО Ромашка 12500 RUB"])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "classifier": "demo"}


@app.post("/webhooks/documents", status_code=201)
def receive_document(payload: DocumentInput) -> dict:
    return service.process(payload.source, payload.text)


@app.get("/records")
def records(status: str | None = Query(default=None, pattern="^(ready|review)?$")) -> list[dict]:
    return service.repository.list(status)


@app.get("/records/{document_id}")
def record(document_id: int) -> dict:
    try:
        return service.repository.get(document_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="record not found") from error
