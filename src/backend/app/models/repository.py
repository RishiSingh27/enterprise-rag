"""
Registry repository (T018, T019).

All database access goes through here. The pipeline never touches SQLAlchemy
directly — that keeps the transactional boundary (FR-009) in exactly one place.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import func, select

from ..ingestion.hashing import PageFingerprint
from .db import Chunk, Document, IngestionRun, Page, QuerySession, get_session_factory


@dataclass
class ChunkRecord:
    """Flat view of a chunk joined to its document, for the retriever."""
    chunk_id: str
    content: str
    document_name: str
    page_number: int
    breadcrumb: str
    element_types: str
    image_path: str | None
    vector_id: int | None


class Repository:
    def __init__(self):
        self._factory = get_session_factory()
        self._session = None

    # ---------------- transaction management (FR-009) ---------------- #

    @property
    def session(self):
        if self._session is None:
            self._session = self._factory()
        return self._session

    @contextmanager
    def transaction(self):
        """
        Stage everything, commit once. Any exception rolls back the whole run,
        so a failure can never leave orphaned vectors or half-written pages.
        """
        session = self.session
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise

    def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None

    # ---------------------- documents ---------------------- #

    def find_document_by_hash(self, doc_hash: str) -> Document | None:
        """TIER 1 duplicate check (FR-003). O(1) via unique index."""
        return self.session.execute(
            select(Document).where(Document.doc_hash == doc_hash)
        ).scalar_one_or_none()

    def find_latest_version_by_filename(self, filename: str) -> Document | None:
        return self.session.execute(
            select(Document)
            .where(Document.filename == filename, Document.status == "indexed")
            .order_by(Document.version_no.desc())
        ).scalars().first()

    def create_document(self, *, doc_hash, filename, file_type, size_bytes,
                        storage_path, version_of=None) -> Document:
        version_no = 1
        if version_of:
            previous = self.session.get(Document, version_of)
            version_no = (previous.version_no + 1) if previous else 1

        doc = Document(
            doc_hash=doc_hash, filename=filename, file_type=file_type,
            file_size_bytes=size_bytes, storage_path=storage_path,
            version_of=version_of, version_no=version_no, status="hashed",
        )
        self.session.add(doc)
        self.session.flush()
        return doc

    def mark_document_indexed(self, doc_id: str, page_count: int = 0) -> None:
        doc = self.session.get(Document, doc_id)
        if doc:
            doc.status = "indexed"
            if page_count:
                doc.page_count = page_count

    def mark_document_failed(self, doc_id: str) -> None:
        doc = self.session.get(Document, doc_id)
        if doc:
            doc.status = "failed"

    def list_documents(self, status=None, limit=50, offset=0):
        stmt = select(Document).order_by(Document.updated_at.desc())
        if status:
            stmt = stmt.where(Document.status == status)
        total = self.session.execute(
            select(func.count()).select_from(Document)
        ).scalar_one()
        docs = self.session.execute(stmt.limit(limit).offset(offset)).scalars().all()
        return total, docs

    def get_document(self, doc_id: str) -> Document | None:
        return self.session.get(Document, doc_id)

    def count_chunks(self, doc_id: str) -> int:
        return self.session.execute(
            select(func.count()).select_from(Chunk)
            .where(Chunk.doc_id == doc_id, Chunk.is_active.is_(True))
        ).scalar_one()

    def delete_document(self, doc_id: str) -> list[int]:
        """Returns vector_ids to purge from FAISS."""
        vector_ids = [
            v for (v,) in self.session.execute(
                select(Chunk.vector_id).where(Chunk.doc_id == doc_id, Chunk.vector_id.isnot(None))
            ).all()
        ]
        doc = self.session.get(Document, doc_id)
        if doc:
            self.session.delete(doc)
        return vector_ids

    # ------------------------ pages ------------------------ #

    def get_page_fingerprints(self, doc_id: str) -> list[PageFingerprint]:
        rows = self.session.execute(
            select(Page).where(Page.doc_id == doc_id).order_by(Page.page_number)
        ).scalars().all()
        return [
            PageFingerprint(p.page_number, p.page_hash, p.structure_signature or "")
            for p in rows
        ]

    def save_pages(self, doc_id: str, fingerprints, diff) -> None:
        state_by_page = {}
        for state, numbers in (
            ("unchanged", diff.unchanged), ("modified", diff.modified),
            ("added", diff.added),
        ):
            for n in numbers:
                state_by_page[n] = state

        for fp in fingerprints:
            self.session.add(Page(
                doc_id=doc_id,
                page_number=fp.page_number,
                page_hash=fp.page_hash,
                structure_signature=fp.structure_signature,
                change_state=state_by_page.get(fp.page_number, "added"),
            ))

        doc = self.session.get(Document, doc_id)
        if doc:
            doc.page_count = len(fingerprints)

    # ------------------------ chunks ----------------------- #

    def get_active_chunk_hashes(self) -> set[str]:
        """TIER 3 reuse lookup (FR-007)."""
        return {
            h for (h,) in self.session.execute(
                select(Chunk.chunk_hash).where(Chunk.is_active.is_(True))
            ).all()
        }

    def save_chunks(self, doc_id: str, chunks, vector_ids: list[int]) -> None:
        for chunk, vector_id in zip(chunks, vector_ids):
            self.session.add(Chunk(
                doc_id=doc_id,
                page_number=chunk.page_number,
                chunk_hash=chunk.chunk_hash,
                content=chunk.content,
                breadcrumb=chunk.breadcrumb,
                element_types=chunk.element_types,
                token_count=chunk.token_count,
                chunk_index=chunk.chunk_index,
                vector_id=vector_id,
                image_path=chunk.image_path,
                is_active=True,
            ))

    def link_reused_chunks(self, doc_id: str, reused_hashes: list[str]) -> None:
        """
        Point the new document version at the EXISTING vectors for chunks whose
        content is unchanged. No embedding call was made for these (FR-007).
        """
        for chunk_hash in reused_hashes:
            source = self.session.execute(
                select(Chunk).where(Chunk.chunk_hash == chunk_hash, Chunk.is_active.is_(True))
            ).scalars().first()
            if source is None:
                continue
            self.session.add(Chunk(
                doc_id=doc_id, page_number=source.page_number,
                chunk_hash=source.chunk_hash, content=source.content,
                breadcrumb=source.breadcrumb, element_types=source.element_types,
                token_count=source.token_count, chunk_index=source.chunk_index,
                vector_id=source.vector_id, image_path=source.image_path,
                is_active=True,
            ))

    def deactivate_chunks_for_document(self, doc_id: str) -> list[int]:
        """Supersede the previous version. Returns vector_ids to prune (FR-008)."""
        rows = self.session.execute(
            select(Chunk).where(Chunk.doc_id == doc_id, Chunk.is_active.is_(True))
        ).scalars().all()
        vector_ids = []
        for chunk in rows:
            chunk.is_active = False
            if chunk.vector_id is not None:
                vector_ids.append(chunk.vector_id)
        return vector_ids

    def get_chunks(self, chunk_ids: list[str]) -> list[ChunkRecord]:
        if not chunk_ids:
            return []
        rows = self.session.execute(
            select(Chunk, Document.filename)
            .join(Document, Chunk.doc_id == Document.doc_id)
            .where(Chunk.chunk_id.in_(chunk_ids))
        ).all()
        return [
            ChunkRecord(
                chunk_id=c.chunk_id, content=c.content, document_name=filename,
                page_number=c.page_number, breadcrumb=c.breadcrumb or "",
                element_types=c.element_types or "", image_path=c.image_path,
                vector_id=c.vector_id,
            )
            for c, filename in rows
        ]

    def iter_active_chunks(self) -> list[ChunkRecord]:
        rows = self.session.execute(
            select(Chunk, Document.filename)
            .join(Document, Chunk.doc_id == Document.doc_id)
            .where(Chunk.is_active.is_(True))
        ).all()
        return [
            ChunkRecord(
                chunk_id=c.chunk_id, content=c.content, document_name=filename,
                page_number=c.page_number, breadcrumb=c.breadcrumb or "",
                element_types=c.element_types or "", image_path=c.image_path,
                vector_id=c.vector_id,
            )
            for c, filename in rows
        ]

    # ------------------------ audit ------------------------ #

    def save_ingestion_run(self, report) -> None:
        self.session.add(IngestionRun(
            run_id=report.run_id, doc_id=report.doc_id or None,
            filename=report.filename, doc_hash=report.doc_hash,
            outcome=report.outcome, message=report.message[:2000],
            pages_added=report.pages_added, pages_modified=report.pages_modified,
            pages_unchanged=report.pages_unchanged, pages_removed=report.pages_removed,
            chunks_created=report.chunks_created, chunks_reused=report.chunks_reused,
            embedding_calls=report.embedding_calls,
            duration_seconds=report.duration_seconds,
        ))
        self.session.commit()

    def save_query_session(self, *, query_id, trace_id, question, answer,
                           chunk_ids, top_score, abstained, latency_ms) -> None:
        self.session.add(QuerySession(
            query_id=query_id, trace_id=trace_id, question=question, answer=answer,
            retrieved_chunk_ids=json.dumps(chunk_ids), top_score=top_score,
            abstained=abstained, latency_ms=latency_ms,
        ))
        self.session.commit()

    # ------------------------ stats ------------------------ #

    def get_stats(self) -> dict:
        s = self.session
        return {
            "documents": s.execute(
                select(func.count()).select_from(Document).where(Document.status == "indexed")
            ).scalar_one(),
            "pages": s.execute(select(func.count()).select_from(Page)).scalar_one(),
            "active_chunks": s.execute(
                select(func.count()).select_from(Chunk).where(Chunk.is_active.is_(True))
            ).scalar_one(),
            "embedding_calls_total": s.execute(
                select(func.coalesce(func.sum(IngestionRun.embedding_calls), 0))
            ).scalar_one(),
            "chunks_reused_total": s.execute(
                select(func.coalesce(func.sum(IngestionRun.chunks_reused), 0))
            ).scalar_one(),
        }
