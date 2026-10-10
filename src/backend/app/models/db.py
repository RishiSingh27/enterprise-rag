"""
SQLAlchemy registry schema (T010, T011).

Implements data-model.md sections 2 and 4. This database is the source of truth
for the three-tier hashing design — without ACID transactions here, FR-009
cannot be satisfied.

Initialise:  python -m src.backend.app.models.db --init
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint, create_engine,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Document(Base):
    __tablename__ = "documents"

    doc_id = Column(String(36), primary_key=True, default=_uuid)
    # TIER 1 - identity is content, not filename (FR-002)
    doc_hash = Column(String(64), nullable=False, unique=True, index=True)
    filename = Column(String(512), nullable=False, index=True)
    file_type = Column(String(10), nullable=False)
    file_size_bytes = Column(Integer, nullable=False)
    page_count = Column(Integer, default=0)
    status = Column(String(20), default="uploaded", index=True)
    version_of = Column(String(36), ForeignKey("documents.doc_id"), nullable=True)
    version_no = Column(Integer, default=1)
    storage_path = Column(String(1024))
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    pages = relationship("Page", back_populates="document", cascade="all, delete-orphan")
    chunks = relationship("Chunk", back_populates="document", cascade="all, delete-orphan")


class Page(Base):
    __tablename__ = "pages"

    page_id = Column(String(36), primary_key=True, default=_uuid)
    doc_id = Column(String(36), ForeignKey("documents.doc_id"), nullable=False, index=True)
    page_number = Column(Integer, nullable=False)
    # TIER 2 - normalised text + structure signature (FR-004)
    page_hash = Column(String(64), nullable=False, index=True)
    structure_signature = Column(String(512))
    change_state = Column(String(12), default="added")
    element_count = Column(Integer, default=0)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    document = relationship("Document", back_populates="pages")

    __table_args__ = (UniqueConstraint("doc_id", "page_number", name="uq_doc_page"),)


class Chunk(Base):
    __tablename__ = "chunks"

    chunk_id = Column(String(36), primary_key=True, default=_uuid)
    doc_id = Column(String(36), ForeignKey("documents.doc_id"), nullable=False, index=True)
    page_number = Column(Integer, nullable=False)
    # TIER 3 - lets us skip embedding unchanged chunks (FR-007)
    chunk_hash = Column(String(64), nullable=False, index=True)
    content = Column(Text, nullable=False)
    breadcrumb = Column(String(512))
    element_types = Column(String(128))
    token_count = Column(Integer, default=0)
    chunk_index = Column(Integer, default=0)
    vector_id = Column(Integer, nullable=True, index=True)
    image_path = Column(String(1024), nullable=True)
    is_active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=_now)

    document = relationship("Document", back_populates="chunks")

    __table_args__ = (Index("ix_chunk_doc_active", "doc_id", "is_active"),)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    run_id = Column(String(36), primary_key=True, default=_uuid)
    doc_id = Column(String(36), nullable=True, index=True)
    trace_id = Column(String(36))
    filename = Column(String(512))
    doc_hash = Column(String(64))
    trigger = Column(String(12), default="upload")
    outcome = Column(String(12), default="new")
    message = Column(Text)
    pages_added = Column(Integer, default=0)
    pages_modified = Column(Integer, default=0)
    pages_unchanged = Column(Integer, default=0)
    pages_removed = Column(Integer, default=0)
    chunks_created = Column(Integer, default=0)
    chunks_reused = Column(Integer, default=0)   # the G4 cost-saving metric
    embedding_calls = Column(Integer, default=0)
    duration_seconds = Column(Float, default=0.0)
    started_at = Column(DateTime, default=_now, index=True)


class QuerySession(Base):
    __tablename__ = "query_sessions"

    query_id = Column(String(36), primary_key=True, default=_uuid)
    trace_id = Column(String(36))
    question = Column(Text)
    answer = Column(Text)
    retrieved_chunk_ids = Column(Text)   # JSON list
    top_score = Column(Float, default=0.0)
    abstained = Column(Boolean, default=False)
    latency_ms = Column(Integer, default=0)
    created_at = Column(DateTime, default=_now, index=True)


# --------------------------------------------------------------------------- #

_engine = None
_SessionFactory = None


def get_engine():
    global _engine
    if _engine is None:
        from ..core.config import get_settings
        _engine = create_engine(
            get_settings().registry_url,
            connect_args={"check_same_thread": False},
            future=True,
        )
    return _engine


def get_session_factory():
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _SessionFactory


def init_db() -> None:
    from ..core.config import get_settings
    get_settings().ensure_dirs()
    Base.metadata.create_all(get_engine())


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Registry management")
    parser.add_argument("--init", action="store_true", help="Create tables and data directories")
    args = parser.parse_args()
    if args.init:
        init_db()
        print("Registry initialised.")
    else:
        parser.print_help()
