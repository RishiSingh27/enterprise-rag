"""Ingestion endpoint (T052). Implements POST /api/v1/ingest per the contract."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, UploadFile

from ..core.config import get_settings
from ..models.repository import Repository
from ..models.schemas import IngestionReportModel
from .deps import build_pipeline, get_repository

router = APIRouter()
log = logging.getLogger("ekb.api.ingest")


@router.post("/ingest", response_model=IngestionReportModel, tags=["Ingestion"])
async def ingest_document(
    file: UploadFile = File(...),
    force_reindex: bool = Form(False),
    repo: Repository = Depends(get_repository),
):
    """
    Upload and ingest a document.

    A byte-identical re-upload returns ``outcome: duplicate`` with
    ``embedding_calls: 0`` and performs no work at all (FR-003).

    Note: rejected and failed uploads still return 200 with the outcome in the
    body, because the ingestion REPORT is the contract — the client needs the
    hash and diff details either way.
    """
    settings = get_settings()
    contents = await file.read()

    destination = settings.uploads_dir / file.filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(contents)

    pipeline = build_pipeline(repo)
    report = pipeline.ingest(destination, contents, force=force_reindex)

    log.info("ingest.completed", extra={
        "outcome": report.outcome,
        "embedding_calls": report.embedding_calls,
        "chunks_reused": report.chunks_reused,
    })
    return IngestionReportModel(**report.to_dict())
