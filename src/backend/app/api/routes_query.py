"""Query endpoints (T053). POST /api/v1/query and /api/v1/query/stream."""

from __future__ import annotations

import json
import logging
import time
import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from ..models.repository import Repository
from ..models.schemas import QueryRequest, QueryResponse, SourceModel
from ..retrieval.prompts import is_conversational
from ..retrieval.retriever import RetrievalResult
from .deps import build_retriever, get_generator, get_repository

router = APIRouter()
log = logging.getLogger("ekb.api.query")


def _to_sources(chunks) -> list[SourceModel]:
    return [
        SourceModel(
            chunk_id=c.chunk_id,
            document=c.document,
            page_number=c.page_number,
            breadcrumb=c.breadcrumb,
            element_types=c.element_types,
            score=c.score,
            snippet=c.content[:700],
            image_path=c.image_path,
        )
        for c in chunks
    ]


def _empty_retrieval() -> RetrievalResult:
    """Used for conversational turns — nothing was searched, nothing to report."""
    return RetrievalResult(chunks=[], top_score=0.0, abstained=False)


@router.post("/query", response_model=QueryResponse, tags=["Query"])
async def query(request: QueryRequest, repo: Repository = Depends(get_repository)):
    started = time.perf_counter()
    trace_id = request.trace_id or str(uuid.uuid4())
    query_id = str(uuid.uuid4())
    generator = get_generator()

    question = request.question.strip()

    # ---- Conversational short-circuit -------------------------------------
    # Greetings and meta-questions are not retrieval questions. Searching for
    # them wastes an embedding call and, worse, surfaces an abstention warning
    # for what is just small talk. Answer directly instead.
    if is_conversational(question):
        answer, usage = generator.chitchat(question)
        latency_ms = int((time.perf_counter() - started) * 1000)
        log.info("query.conversational", extra={"query_id": query_id, "latency_ms": latency_ms})
        return QueryResponse(
            query_id=query_id,
            trace_id=trace_id,
            answer=answer,
            abstained=False,          # nothing was searched, so nothing was missed
            top_score=0.0,
            sources=[],
            latency_ms=latency_ms,
            token_usage=usage,
        )

    # ---- Normal retrieval path --------------------------------------------
    retriever = build_retriever(repo)
    retrieval = retriever.retrieve(question, request.top_k, request.doc_ids)
    answer, usage = generator.generate(question, retrieval)

    latency_ms = int((time.perf_counter() - started) * 1000)
    sources = _to_sources(retrieval.chunks)

    # Article V: identifiers only in logs, never the question or answer text.
    log.info("query.completed", extra={
        "query_id": query_id, "abstained": retrieval.abstained,
        "top_score": round(retrieval.top_score, 4), "latency_ms": latency_ms,
        "sources": len(sources),
    })

    try:
        repo.save_query_session(
            query_id=query_id, trace_id=trace_id, question=question,
            answer=answer, chunk_ids=[s.chunk_id for s in sources],
            top_score=retrieval.top_score, abstained=retrieval.abstained,
            latency_ms=latency_ms,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("query.audit_failed", extra={"error": str(exc)[:200]})

    return QueryResponse(
        query_id=query_id, trace_id=trace_id, answer=answer,
        abstained=retrieval.abstained, top_score=round(retrieval.top_score, 4),
        sources=sources, latency_ms=latency_ms, token_usage=usage,
    )


@router.post("/query/stream", tags=["Query"])
async def query_stream(request: QueryRequest, repo: Repository = Depends(get_repository)):
    """FR-027. Streams answer tokens, then a final `sources` event."""
    generator = get_generator()
    question = request.question.strip()

    if is_conversational(question):
        answer, _ = generator.chitchat(question)

        def chitchat_stream():
            yield f"data: {json.dumps({'token': answer})}\n\n"
            yield f"event: sources\ndata: {json.dumps({'sources': [], 'abstained': False, 'top_score': 0.0})}\n\n"
            yield "event: done\ndata: {}\n\n"

        return StreamingResponse(chitchat_stream(), media_type="text/event-stream")

    retriever = build_retriever(repo)
    retrieval = retriever.retrieve(question, request.top_k, request.doc_ids)

    def event_stream():
        for token in generator.stream(question, retrieval):
            yield f"data: {json.dumps({'token': token})}\n\n"
        payload = {
            "sources": [s.model_dump() for s in _to_sources(retrieval.chunks)],
            "abstained": retrieval.abstained,
            "top_score": round(retrieval.top_score, 4),
        }
        yield f"event: sources\ndata: {json.dumps(payload)}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
