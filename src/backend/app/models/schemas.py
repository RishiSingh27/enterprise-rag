"""
Pydantic request/response models (T051).

These mirror contracts/openapi.yaml exactly. Constitution Article IV: the
contract was authored first; this file implements it, not the other way round.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class IngestionReportModel(BaseModel):
    run_id: str
    doc_id: str = ""
    filename: str = ""
    doc_hash: str = ""
    outcome: str
    message: str = ""
    pages_added: int = 0
    pages_modified: int = 0
    pages_unchanged: int = 0
    pages_removed: int = 0
    changed_page_numbers: list[int] = Field(default_factory=list)
    chunks_created: int = 0
    chunks_reused: int = 0
    embedding_calls: int = 0
    duration_seconds: float = 0.0


class QueryRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)
    doc_ids: list[str] | None = None
    trace_id: str | None = None


class SourceModel(BaseModel):
    chunk_id: str
    document: str
    page_number: int
    breadcrumb: str = ""
    element_types: str = ""
    score: float
    snippet: str = ""
    image_path: str | None = None


class QueryResponse(BaseModel):
    query_id: str
    trace_id: str
    answer: str
    abstained: bool
    top_score: float
    sources: list[SourceModel] = Field(default_factory=list)
    latency_ms: int = 0
    token_usage: dict = Field(default_factory=dict)


class DocumentSummary(BaseModel):
    doc_id: str
    filename: str
    file_type: str
    page_count: int
    chunk_count: int
    status: str
    version_no: int
    updated_at: str


class PageDetail(BaseModel):
    page_number: int
    page_hash: str
    change_state: str
    element_count: int


class DocumentDetail(DocumentSummary):
    doc_hash: str
    pages: list[PageDetail] = Field(default_factory=list)


class DocumentList(BaseModel):
    total: int
    items: list[DocumentSummary]


class StatsResponse(BaseModel):
    documents: int
    pages: int
    active_chunks: int
    vectors: int
    embedding_calls_total: int
    chunks_reused_total: int
    embedding_model: str
    llm_model: str


class ErrorResponse(BaseModel):
    error: str
    detail: str = ""
    trace_id: str = ""
