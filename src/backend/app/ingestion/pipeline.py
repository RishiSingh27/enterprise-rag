"""
Ingestion orchestrator (T039).

Sequence: validate -> Tier-1 hash -> Tier-2 diff -> parse changed pages ->
chunk -> Tier-3 hash -> embed new only -> stage -> commit -> prune

Requirements: FR-001, FR-003, FR-006, FR-008, FR-009, FR-010.

TRANSACTIONAL GUARANTEE (FR-009 / Constitution Article II.5)
-----------------------------------------------------------
Nothing reaches the live index until every stage succeeds. A half-ingested
document is worse than no document, because it silently returns partial answers
with no signal that anything is missing.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .chunker import chunk_document
from .hashing import (
    PageFingerprint, compute_document_hash, compute_page_hash,
    detect_repeating_lines, diff_pages, partition_chunks_for_embedding,
)
from .parser import ParseError, UnsupportedFileType


@dataclass
class IngestionReport:
    """Returned to the caller and persisted to INGESTION_RUN (FR-010)."""
    run_id: str
    doc_id: str = ""
    filename: str = ""
    doc_hash: str = ""
    outcome: str = "new"
    message: str = ""
    pages_added: int = 0
    pages_modified: int = 0
    pages_unchanged: int = 0
    pages_removed: int = 0
    changed_page_numbers: list[int] = field(default_factory=list)
    chunks_created: int = 0
    chunks_reused: int = 0
    embedding_calls: int = 0        # the G4 cost metric
    duration_seconds: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


class IngestionPipeline:
    def __init__(self, parser, embedder, vector_store, bm25_store, repository, settings, logger):
        self.parser = parser
        self.embedder = embedder
        self.vector_store = vector_store
        self.bm25_store = bm25_store
        self.repo = repository
        self.settings = settings
        self.log = logger

    # ------------------------------------------------------------------ #

    def ingest(self, file_path: Path, file_bytes: bytes, *, force: bool = False) -> IngestionReport:
        started = time.perf_counter()
        report = IngestionReport(run_id=str(uuid.uuid4()), filename=file_path.name)

        try:
            # ---- FR-001: validate before doing anything expensive ----
            self.parser.validate(file_path, file_bytes)

            # ---- TIER 1: document hash (FR-002, FR-003) ----
            report.doc_hash = compute_document_hash(file_bytes)
            existing = self.repo.find_document_by_hash(report.doc_hash)

            if existing and not force:
                # Byte-identical. Stop here: zero parsing, zero embedding, zero
                # cost. This is the single largest saving in the whole system.
                report.outcome = "duplicate"
                report.doc_id = existing.doc_id
                report.message = "Identical content already indexed. No processing performed."
                self.log.info("ingest.duplicate", extra={"doc_hash": report.doc_hash[:12]})
                return self._finish(report, started)

            previous = self.repo.find_latest_version_by_filename(file_path.name)

            # ---- TIER 2: page hashes and diff (FR-004, FR-005) ----
            raw_pages = self.parser.extract_page_text(file_path)
            # Strip running headers/footers so cosmetic chrome (e.g. an export
            # timestamp) does not make every page look modified.
            chrome = detect_repeating_lines([text for _, text in raw_pages])
            structure = self.parser.quick_structure(file_path)

            incoming = []
            for page_number, text in raw_pages:
                page_hash, signature = compute_page_hash(
                    text, structure.get(page_number, []), strip_lines=chrome
                )
                incoming.append(PageFingerprint(page_number, page_hash, signature))

            stored = self.repo.get_page_fingerprints(previous.doc_id) if previous else []
            diff = diff_pages(stored, incoming)

            report.pages_unchanged = len(diff.unchanged)
            report.pages_modified = len(diff.modified)
            report.pages_added = len(diff.added)
            report.pages_removed = len(diff.removed)
            report.changed_page_numbers = diff.pages_to_process

            if previous and diff.is_noop:
                report.outcome = "duplicate"
                report.doc_id = previous.doc_id
                report.message = "No page-level changes detected. Existing vectors retained."
                return self._finish(report, started)

            document = self.repo.create_document(
                doc_hash=report.doc_hash,
                filename=file_path.name,
                file_type=file_path.suffix.lower().lstrip("."),
                size_bytes=len(file_bytes),
                storage_path=str(file_path),
                version_of=previous.doc_id if previous else None,
            )
            report.doc_id = document.doc_id

            # ---- PARSE ONLY CHANGED PAGES (FR-006) ----
            pages_to_parse = (
                diff.pages_to_process if previous else [p.page_number for p in incoming]
            )
            elements = self.parser.parse_pages(file_path, pages_to_parse)
            if not elements:
                raise ParseError("No readable content extracted from the document.")

            # ---- CHUNK (FR-016..FR-020) ----
            chunks = chunk_document(
                elements,
                document_title=file_path.stem,
                target_tokens=self.settings.target_chunk_tokens,
                max_tokens=self.settings.max_chunk_tokens,
                overlap_tokens=self.settings.chunk_overlap_tokens,
            )

            # ---- TIER 3: reuse unchanged chunks (FR-007) ----
            active_hashes = self.repo.get_active_chunk_hashes()
            to_embed_hashes, to_reuse_hashes = partition_chunks_for_embedding(
                [c.chunk_hash for c in chunks], active_hashes
            )

            embed_set = set(to_embed_hashes)
            seen: set[str] = set()
            new_chunks = []
            for chunk in chunks:
                if chunk.chunk_hash in embed_set and chunk.chunk_hash not in seen:
                    seen.add(chunk.chunk_hash)
                    new_chunks.append(chunk)

            report.chunks_created = len(new_chunks)
            report.chunks_reused = len(to_reuse_hashes)

            # ---- EMBED (FR-021) ----
            vectors = self.embedder.embed_batch([c.content for c in new_chunks]) if new_chunks else []
            report.embedding_calls = len(vectors)

            # ---- STAGE -> COMMIT -> PRUNE (FR-008, FR-009) ----
            stale_vector_ids: list[int] = []
            vector_ids = self.vector_store.add(vectors) if vectors else []

            try:
                with self.repo.transaction():
                    self.repo.save_pages(document.doc_id, incoming, diff)
                    self.repo.save_chunks(document.doc_id, new_chunks, vector_ids)
                    self.repo.link_reused_chunks(document.doc_id, to_reuse_hashes)
                    if previous:
                        stale_vector_ids = self.repo.deactivate_chunks_for_document(previous.doc_id)
                    self.repo.mark_document_indexed(document.doc_id, len(incoming))

                # Only after the registry commits do we mutate the indexes.
                self._map_vectors(document.doc_id, vector_ids)
                if stale_vector_ids:
                    self.vector_store.remove(stale_vector_ids)
                self.bm25_store.rebuild(self.repo.iter_active_chunks())
                self.vector_store.persist()
                self.bm25_store.persist()
            except Exception:
                # Registry rolled back; drop the vectors we speculatively added.
                if vector_ids:
                    self.vector_store.remove(vector_ids)
                raise

            report.outcome = "updated" if previous else "new"
            report.message = (
                f"Indexed. {report.chunks_created} chunk(s) embedded, "
                f"{report.chunks_reused} reused via content hash."
            )
            return self._finish(report, started)

        except (UnsupportedFileType,) as exc:
            report.outcome = "rejected"
            report.message = str(exc)
            return self._finish(report, started)
        except ParseError as exc:
            report.outcome = "rejected"
            report.message = str(exc)
            return self._finish(report, started)
        except Exception as exc:  # noqa: BLE001
            self.repo.rollback()
            report.outcome = "failed"
            report.message = str(exc)[:500]
            self.log.error("ingest.failed", extra={"run_id": report.run_id, "error": str(exc)[:300]})
            return self._finish(report, started)

    # ------------------------------------------------------------------ #

    def _map_vectors(self, doc_id: str, vector_ids: list[int]) -> None:
        """Link FAISS ids to chunk ids so search results resolve back to content."""
        if not vector_ids:
            return
        records = [c for c in self.repo.iter_active_chunks() if c.vector_id in set(vector_ids)]
        by_vector = {r.vector_id: r.chunk_id for r in records}
        ordered_ids = [vid for vid in vector_ids if vid in by_vector]
        self.vector_store.map_chunks(ordered_ids, [by_vector[v] for v in ordered_ids])

    def _finish(self, report: IngestionReport, started: float) -> IngestionReport:
        report.duration_seconds = round(time.perf_counter() - started, 3)
        try:
            self.repo.save_ingestion_run(report)
        except Exception as exc:  # noqa: BLE001
            self.log.warning("audit.save_failed", extra={"error": str(exc)[:200]})
        return report
