"""
Structure-aware chunking with recursive fallback.

Implements tasks T032-T036 and requirements FR-016..FR-020.

Two passes, deliberately in this order
-------------------------------------
PASS 1 (structure-aware) : group parsed elements into sections using the heading
                           hierarchy the layout parser already gave us. Section
                           boundaries beat size. A chunk never spans two
                           top-level sections (FR-020).

PASS 2 (recursive)       : ONLY for sections that exceed the token budget, split
                           down a ladder of separators with overlap (FR-017).

Tables are atomic where possible; when oversized they split by ROWS with the
header repeated in every part (FR-018). Every chunk is prefixed with its heading
breadcrumb (FR-019) so it is interpretable on its own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Sequence

from .hashing import compute_chunk_hash

# Separator ladder, strongest structural break first (FR-017).
SEPARATOR_LADDER: tuple[str, ...] = ("\n## ", "\n### ", "\n\n", "\n", ". ", " ")

_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")


@dataclass
class Element:
    """One structural unit produced by the parser."""
    element_type: str          # heading | paragraph | list | table | image | caption
    content: str
    page_number: int
    reading_order: int = 0
    heading_level: int | None = None
    image_path: str | None = None


@dataclass
class Section:
    """A heading and everything under it, until the next heading of same/higher level."""
    breadcrumb: str
    level: int
    elements: list[Element] = field(default_factory=list)


@dataclass
class Chunk:
    """The retrievable unit."""
    content: str
    breadcrumb: str
    page_number: int
    chunk_index: int
    element_types: str
    token_count: int
    chunk_hash: str
    image_path: str | None = None


# --------------------------------------------------------------------------- #
# Token counting
# --------------------------------------------------------------------------- #

def count_tokens(text: str, encoding_name: str = "cl100k_base") -> int:
    """Exact count via tiktoken; ~4 chars/token heuristic if unavailable offline."""
    try:
        import tiktoken
        return len(tiktoken.get_encoding(encoding_name).encode(text))
    except Exception:
        return max(1, len(text) // 4)


# --------------------------------------------------------------------------- #
# PASS 1 - structure-aware sectioning (T032, T033)
# --------------------------------------------------------------------------- #

def build_sections(elements: Sequence[Element], document_title: str) -> list[Section]:
    """
    Group elements under their heading hierarchy and build breadcrumbs
    like ``HR Policy > Leave > Emergency Leave`` (FR-016, FR-019).
    """
    ordered = sorted(elements, key=lambda e: (e.page_number, e.reading_order))
    sections: list[Section] = []
    stack: list[tuple[int, str]] = []          # (heading_level, heading_text)
    current = Section(breadcrumb=document_title, level=0)

    for element in ordered:
        if element.element_type == "heading":
            if current.elements:
                sections.append(current)

            level = element.heading_level or 1
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, element.content.strip()))

            breadcrumb = " > ".join([document_title] + [h for _, h in stack])
            current = Section(breadcrumb=breadcrumb, level=level)
        else:
            current.elements.append(element)

    if current.elements:
        sections.append(current)
    return sections


# --------------------------------------------------------------------------- #
# PASS 2 - recursive splitting (T034)
# --------------------------------------------------------------------------- #

def recursive_split(
    text: str,
    max_tokens: int,
    overlap_tokens: int,
    separators: tuple[str, ...] = SEPARATOR_LADDER,
) -> list[str]:
    """
    Split only when necessary, always at the strongest separator that works.

    Falls back down the ladder: section heading -> paragraph -> line ->
    sentence -> word. Overlap carries context across the seam so a sentence cut
    in half is still retrievable from both sides.
    """
    if count_tokens(text) <= max_tokens:
        return [text]

    for separator in separators:
        if separator not in text:
            continue

        parts = [p for p in text.split(separator) if p.strip()]
        if len(parts) < 2:
            continue

        chunks: list[str] = []
        buffer = ""
        for part in parts:
            candidate = f"{buffer}{separator}{part}" if buffer else part
            if count_tokens(candidate) <= max_tokens:
                buffer = candidate
            else:
                if buffer:
                    chunks.append(buffer.strip())
                    buffer = _carry_overlap(buffer, overlap_tokens) + separator + part
                else:
                    buffer = part
        if buffer.strip():
            chunks.append(buffer.strip())

        # A part may still be oversized; recurse with the next separator down.
        final: list[str] = []
        for chunk in chunks:
            if count_tokens(chunk) > max_tokens:
                idx = separators.index(separator)
                final.extend(
                    recursive_split(chunk, max_tokens, overlap_tokens, separators[idx + 1:])
                )
            else:
                final.append(chunk)
        return final

    # No separator left - hard word-level cut.
    words = text.split()
    step = max(1, max_tokens * 3)
    return [" ".join(words[i:i + step]) for i in range(0, len(words), step)]


def _carry_overlap(text: str, overlap_tokens: int) -> str:
    """Take the trailing ~overlap_tokens worth of words as the next chunk's prefix."""
    if overlap_tokens <= 0:
        return ""
    words = text.split()
    return " ".join(words[-(overlap_tokens * 3 // 4 or 1):])


# --------------------------------------------------------------------------- #
# Table handling (T035 / FR-018)
# --------------------------------------------------------------------------- #

def split_markdown_table(table_md: str, max_tokens: int) -> list[str]:
    """
    Keep a table atomic if it fits. If not, split by rows and REPEAT the header
    row in every part - otherwise the second half is a grid of numbers with no
    column meaning, which is useless to both the retriever and the LLM.
    """
    if count_tokens(table_md) <= max_tokens:
        return [table_md]

    lines = [ln for ln in table_md.splitlines() if ln.strip()]
    header = [ln for ln in lines[:2] if _TABLE_ROW_RE.match(ln)]
    body = lines[len(header):]
    if not header:
        return recursive_split(table_md, max_tokens, 0)

    header_block = "\n".join(header)
    header_cost = count_tokens(header_block)

    parts: list[str] = []
    buffer: list[str] = []
    running = header_cost
    for row in body:
        row_cost = count_tokens(row)
        if running + row_cost > max_tokens and buffer:
            parts.append(header_block + "\n" + "\n".join(buffer))
            buffer, running = [], header_cost
        buffer.append(row)
        running += row_cost
    if buffer:
        parts.append(header_block + "\n" + "\n".join(buffer))
    return parts


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def chunk_document(
    elements: Sequence[Element],
    document_title: str,
    *,
    target_tokens: int = 800,
    max_tokens: int = 1000,
    overlap_tokens: int = 120,
) -> list[Chunk]:
    """
    Full chunking pipeline: sectioning -> table handling -> recursive splitting
    -> breadcrumb prefixing -> Tier-3 hashing.

    Guarantees:
      * no chunk spans two sections (FR-020)
      * every chunk carries its breadcrumb (FR-019)
      * every chunk carries a stable content hash for reuse (FR-007)
    """
    chunks: list[Chunk] = []
    index = 0

    for section in build_sections(elements, document_title):
        # Tables and images are emitted as their own chunks - never merged into
        # prose, because mixing a table with surrounding narrative dilutes the
        # embedding and makes both harder to retrieve.
        special = [e for e in section.elements if e.element_type in ("table", "image")]
        prose = [e for e in section.elements if e.element_type not in ("table", "image")]

        for element in special:
            pieces = (
                split_markdown_table(element.content, max_tokens)
                if element.element_type == "table"
                else [element.content]
            )
            for piece in pieces:
                chunks.append(_make_chunk(
                    piece, section.breadcrumb, element.page_number,
                    index, element.element_type, element.image_path,
                ))
                index += 1

        if not prose:
            continue

        body = "\n\n".join(e.content.strip() for e in prose if e.content.strip())
        if not body:
            continue

        page_number = prose[0].page_number
        types = ",".join(sorted({e.element_type for e in prose}))

        budget = max_tokens - count_tokens(section.breadcrumb) - 4
        for piece in recursive_split(body, min(target_tokens, budget), overlap_tokens):
            chunks.append(_make_chunk(
                piece, section.breadcrumb, page_number, index, types, None,
            ))
            index += 1

    return chunks


def _make_chunk(
    text: str,
    breadcrumb: str,
    page_number: int,
    index: int,
    element_types: str,
    image_path: str | None,
) -> Chunk:
    """Prefix the breadcrumb, then hash. Breadcrumb is part of the embedded text."""
    content = f"{breadcrumb}\n\n{text.strip()}"
    return Chunk(
        content=content,
        breadcrumb=breadcrumb,
        page_number=page_number,
        chunk_index=index,
        element_types=element_types,
        token_count=count_tokens(content),
        chunk_hash=compute_chunk_hash(content),
        image_path=image_path,
    )
