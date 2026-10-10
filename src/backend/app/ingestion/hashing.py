"""
Three-tier content hashing for incremental, idempotent ingestion.

    Tier 1  document  -> SHA-256 over raw file bytes          (FR-002)
    Tier 2  page      -> SHA-256 over normalised text + structure signature (FR-004)
    Tier 3  chunk     -> SHA-256 over normalised chunk text   (FR-007)

Implements tasks T012-T017. See specs/001-enterprise-rag/plan.md section 5.

CRITICAL INVARIANT
------------------
Hashing must be DETERMINISTIC. The same logical content must always produce the
same hash, otherwise every re-upload looks "modified" and the entire cost saving
of this design disappears. That is why normalisation runs before hashing.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Sequence

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

_WHITESPACE_RE = re.compile(r"\s+")
_PAGE_NUMBER_RE = re.compile(r"^\s*(page\s+)?\d+\s*(of\s+\d+)?\s*$", re.IGNORECASE)
_DATE_STAMP_RE = re.compile(
    r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?\b"
)
_HEX = re.compile(r"^[0-9a-f]{64}$")


class ChangeState(str, Enum):
    UNCHANGED = "unchanged"
    MODIFIED = "modified"
    ADDED = "added"
    REMOVED = "removed"


@dataclass(frozen=True)
class PageFingerprint:
    """What we store per page in the registry to detect change later."""
    page_number: int
    page_hash: str
    structure_signature: str


@dataclass
class DiffResult:
    """Outcome of comparing a stored document version against an incoming one."""
    unchanged: list[int] = field(default_factory=list)
    modified: list[int] = field(default_factory=list)
    added: list[int] = field(default_factory=list)
    removed: list[int] = field(default_factory=list)

    @property
    def pages_to_process(self) -> list[int]:
        """Only these pages are re-parsed, re-chunked and re-embedded (FR-006)."""
        return sorted(self.modified + self.added)

    @property
    def is_noop(self) -> bool:
        return not (self.modified or self.added or self.removed)

    def as_report(self) -> dict:
        return {
            "pages_unchanged": len(self.unchanged),
            "pages_modified": len(self.modified),
            "pages_added": len(self.added),
            "pages_removed": len(self.removed),
            "changed_page_numbers": self.pages_to_process,
        }


# --------------------------------------------------------------------------- #
# Tier 1 - Document hash
# --------------------------------------------------------------------------- #

def compute_document_hash(file_bytes: bytes) -> str:
    """
    T012 / FR-002. Identity of a document is its CONTENT, not its filename.

    Two uploads named differently but byte-identical are the same document.
    Two uploads named identically but byte-different are two versions.
    """
    if not file_bytes:
        raise ValueError("Cannot hash an empty file")
    return hashlib.sha256(file_bytes).hexdigest()


def compute_stream_hash(chunks: Iterable[bytes]) -> str:
    """Streaming variant for large files - avoids loading everything into memory."""
    digest = hashlib.sha256()
    empty = True
    for block in chunks:
        if block:
            empty = False
            digest.update(block)
    if empty:
        raise ValueError("Cannot hash an empty stream")
    return digest.hexdigest()


# --------------------------------------------------------------------------- #
# Normalisation - the reason this design works in production
# --------------------------------------------------------------------------- #

def normalize_text(
    text: str,
    *,
    strip_lines: Sequence[str] = (),
    strip_datestamps: bool = True,
) -> str:
    """
    T013 / FR-004. Remove cosmetic noise so that only MEANINGFUL edits change
    the hash.

    Without this step a PDF re-exported on a different day would show every page
    as modified because the footer timestamp changed - and we would re-embed the
    whole document for nothing.

    Steps
    -----
    1. Drop lines matching detected repeating headers/footers.
    2. Drop standalone page numbers ("12", "Page 3 of 40").
    3. Optionally blank out date/time stamps.
    4. Collapse all whitespace runs to one space, trim, lowercase.
    """
    if not text:
        return ""

    strip_set = {s.strip().lower() for s in strip_lines if s.strip()}
    kept: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.lower() in strip_set:
            continue
        if _PAGE_NUMBER_RE.match(line):
            continue
        kept.append(line)

    joined = " ".join(kept)
    if strip_datestamps:
        joined = _DATE_STAMP_RE.sub(" ", joined)
    return _WHITESPACE_RE.sub(" ", joined).strip().lower()


def detect_repeating_lines(
    page_texts: Sequence[str],
    *,
    threshold: float = 0.6,
    scan_lines: int = 3,
) -> list[str]:
    """
    Auto-detect running headers and footers.

    A line appearing in the first or last `scan_lines` of at least `threshold`
    of all pages is chrome, not content. Feed the result into `normalize_text`
    via `strip_lines`.
    """
    if len(page_texts) < 3:
        return []

    counter: Counter[str] = Counter()
    for text in page_texts:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            continue
        candidates = set(lines[:scan_lines]) | set(lines[-scan_lines:])
        counter.update(candidates)

    cutoff = max(2, int(len(page_texts) * threshold))
    return [line for line, count in counter.items() if count >= cutoff]


# --------------------------------------------------------------------------- #
# Structure signature
# --------------------------------------------------------------------------- #

def build_structure_signature(elements: Sequence[dict]) -> str:
    """
    T014 / FR-004. A compact, deterministic fingerprint of page LAYOUT.

    Why this is part of the page hash: identical text can have entirely
    different structure. If a paragraph is restructured into a table, the text
    is unchanged but the correct chunking is completely different. Text-only
    hashing would mark the page "unchanged" and silently keep wrong chunks.

    Example output:
        "h:2|p:7|t:1|i:1|l:0|order:h,p,p,t,p,p,i,h,p"
    """
    codes = {
        "heading": "h",
        "paragraph": "p",
        "table": "t",
        "image": "i",
        "list": "l",
        "caption": "c",
        "footnote": "f",
    }

    ordered = sorted(elements, key=lambda e: e.get("reading_order", 0))
    sequence = [codes.get(e.get("element_type", "paragraph"), "p") for e in ordered]
    counts = Counter(sequence)

    parts = [f"{code}:{counts.get(code, 0)}" for code in ("h", "p", "t", "i", "l")]
    parts.append("order:" + ",".join(sequence))
    return "|".join(parts)


# --------------------------------------------------------------------------- #
# Tier 2 - Page hash
# --------------------------------------------------------------------------- #

def compute_page_hash(
    page_text: str,
    elements: Sequence[dict],
    *,
    strip_lines: Sequence[str] = (),
) -> tuple[str, str]:
    """
    T015 / FR-004. Returns ``(page_hash, structure_signature)``.

    page_hash = sha256( normalised_text + "||" + structure_signature )
    """
    normalised = normalize_text(page_text, strip_lines=strip_lines)
    signature = build_structure_signature(elements)
    payload = f"{normalised}||{signature}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest(), signature


# --------------------------------------------------------------------------- #
# Tier 3 - Chunk hash
# --------------------------------------------------------------------------- #

def compute_chunk_hash(chunk_text: str) -> str:
    """
    T016 / FR-007. Final safety net.

    Even when a page is "modified", most chunks on it are usually untouched.
    Matching this hash against the registry lets us reuse the existing vector
    and skip the embedding call entirely.
    """
    normalised = normalize_text(chunk_text, strip_datestamps=False)
    if not normalised:
        raise ValueError("Cannot hash an empty chunk")
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Diff engine
# --------------------------------------------------------------------------- #

def diff_pages(
    stored: Sequence[PageFingerprint],
    incoming: Sequence[PageFingerprint],
) -> DiffResult:
    """
    T017 / FR-005. Classify every page as unchanged / modified / added / removed.

    Comparison is by page NUMBER. Page insertion therefore shifts subsequent
    pages and marks them modified - conservative, and correct: it never leaves
    stale vectors in the index.
    """
    stored_map = {p.page_number: p.page_hash for p in stored}
    incoming_map = {p.page_number: p.page_hash for p in incoming}
    result = DiffResult()

    for number, new_hash in incoming_map.items():
        old_hash = stored_map.get(number)
        if old_hash is None:
            result.added.append(number)
        elif old_hash == new_hash:
            result.unchanged.append(number)
        else:
            result.modified.append(number)

    for number in stored_map:
        if number not in incoming_map:
            result.removed.append(number)

    for bucket in (result.unchanged, result.modified, result.added, result.removed):
        bucket.sort()
    return result


def partition_chunks_for_embedding(
    chunk_hashes: Sequence[str],
    existing_active_hashes: set[str],
) -> tuple[list[str], list[str]]:
    """
    FR-007. Split incoming chunk hashes into ``(to_embed, to_reuse)``.

    ``len(to_reuse)`` is the headline cost-saving metric reported to the user
    and recorded in INGESTION_RUN.chunks_reused.
    """
    to_embed, to_reuse = [], []
    seen: set[str] = set()
    for h in chunk_hashes:
        if h in seen:
            continue
        seen.add(h)
        (to_reuse if h in existing_active_hashes else to_embed).append(h)
    return to_embed, to_reuse


def is_valid_hash(value: str) -> bool:
    """Registry-level validation: exactly 64 lowercase hex characters."""
    return bool(value) and bool(_HEX.match(value))
