"""Admin endpoints (T054): documents, stats, health, ready."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from ..core.config import get_settings
from ..models.repository import Repository
from ..models.schemas import (
    DocumentDetail, DocumentList, DocumentSummary, PageDetail, StatsResponse,
)
from .deps import get_bm25_store, get_repository, get_vector_store

router = APIRouter()
log = logging.getLogger("ekb.api.admin")


def _summary(doc, chunk_count: int) -> DocumentSummary:
    return DocumentSummary(
        doc_id=doc.doc_id, filename=doc.filename, file_type=doc.file_type,
        page_count=doc.page_count or 0, chunk_count=chunk_count,
        status=doc.status, version_no=doc.version_no or 1,
        updated_at=doc.updated_at.isoformat() if doc.updated_at else "",
    )


@router.get("/documents", response_model=DocumentList, tags=["Admin"])
async def list_documents(
    status: str | None = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
    repo: Repository = Depends(get_repository),
):
    total, docs = repo.list_documents(status=status, limit=limit, offset=offset)
    return DocumentList(
        total=total,
        items=[_summary(d, repo.count_chunks(d.doc_id)) for d in docs],
    )


@router.get("/documents/{doc_id}", response_model=DocumentDetail, tags=["Admin"])
async def get_document(doc_id: str, repo: Repository = Depends(get_repository)):
    doc = repo.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    base = _summary(doc, repo.count_chunks(doc_id))
    return DocumentDetail(
        **base.model_dump(),
        doc_hash=doc.doc_hash,
        pages=[
            PageDetail(
                page_number=p.page_number, page_hash=p.page_hash,
                change_state=p.change_state or "added",
                element_count=p.element_count or 0,
            )
            for p in sorted(doc.pages, key=lambda x: x.page_number)
        ],
    )


@router.delete("/documents/{doc_id}", status_code=204, tags=["Admin"])
async def delete_document(doc_id: str, repo: Repository = Depends(get_repository)):
    if repo.get_document(doc_id) is None:
        raise HTTPException(status_code=404, detail="Document not found")

    vector_store = get_vector_store()
    bm25 = get_bm25_store()
    with repo.transaction():
        vector_ids = repo.delete_document(doc_id)
    vector_store.remove(vector_ids)
    bm25.rebuild(repo.iter_active_chunks())
    vector_store.persist()
    bm25.persist()
    return JSONResponse(status_code=204, content=None)


@router.get("/stats", response_model=StatsResponse, tags=["Admin"])
async def stats(repo: Repository = Depends(get_repository)):
    settings = get_settings()
    data = repo.get_stats()
    return StatsResponse(
        **data,
        vectors=get_vector_store().size,
        embedding_model=settings.azure_embedding_deployment,
        llm_model=settings.azure_chat_deployment,
    )
