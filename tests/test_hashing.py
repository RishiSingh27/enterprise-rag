"""
Tests for the three-tier hashing design (T020, T021).

These tests are the PROOF of goal G4 — that re-uploading unchanged content
costs nothing. If they pass, the cost model holds.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "backend" / "app"))

from ingestion.hashing import (  # noqa: E402
    PageFingerprint,
    build_structure_signature,
    compute_chunk_hash,
    compute_document_hash,
    compute_page_hash,
    detect_repeating_lines,
    diff_pages,
    is_valid_hash,
    normalize_text,
    partition_chunks_for_embedding,
)


# ----------------------------- Tier 1 -------------------------------------- #

def test_identical_bytes_produce_identical_hash():
    data = b"Leave policy 2026"
    assert compute_document_hash(data) == compute_document_hash(data)


def test_one_byte_difference_changes_hash():
    assert compute_document_hash(b"policy v1") != compute_document_hash(b"policy v2")


def test_empty_file_rejected():
    with pytest.raises(ValueError):
        compute_document_hash(b"")


def test_hash_format_is_64_hex():
    assert is_valid_hash(compute_document_hash(b"x"))


# --------------------------- Normalisation --------------------------------- #

def test_whitespace_changes_are_ignored():
    assert normalize_text("Annual   leave\n\n is  21 days") == normalize_text("Annual leave is 21 days")


def test_case_changes_are_ignored():
    assert normalize_text("ANNUAL LEAVE") == normalize_text("annual leave")


def test_page_numbers_are_stripped():
    assert normalize_text("Content here\nPage 3 of 40") == normalize_text("Content here")


def test_footer_timestamp_does_not_change_hash():
    """The single most important test: a re-export must not invalidate the corpus."""
    a = "Leave Policy\nEmployees get 21 days.\nGenerated 01/02/2026"
    b = "Leave Policy\nEmployees get 21 days.\nGenerated 05/09/2026"
    assert normalize_text(a) == normalize_text(b)


def test_repeating_header_detection():
    pages = ["ACME Confidential\nBody one", "ACME Confidential\nBody two",
             "ACME Confidential\nBody three"]
    assert "ACME Confidential" in detect_repeating_lines(pages)


# ----------------------------- Tier 2 -------------------------------------- #

ELEMENTS = [
    {"element_type": "heading", "reading_order": 0},
    {"element_type": "paragraph", "reading_order": 1},
    {"element_type": "table", "reading_order": 2},
]


def test_structure_signature_is_deterministic():
    assert build_structure_signature(ELEMENTS) == build_structure_signature(ELEMENTS)


def test_same_text_different_structure_yields_different_page_hash():
    """Paragraph restructured into a table: text identical, chunking must change."""
    text = "Severity 2 response 4 hours"
    as_prose = [{"element_type": "paragraph", "reading_order": 0}]
    as_table = [{"element_type": "table", "reading_order": 0}]
    assert compute_page_hash(text, as_prose)[0] != compute_page_hash(text, as_table)[0]


def test_meaningful_edit_changes_page_hash():
    a, _ = compute_page_hash("Employees get 21 days of leave.", ELEMENTS)
    b, _ = compute_page_hash("Employees get 24 days of leave.", ELEMENTS)
    assert a != b


def test_cosmetic_edit_does_not_change_page_hash():
    a, _ = compute_page_hash("Employees get 21 days.", ELEMENTS)
    b, _ = compute_page_hash("Employees   get\n21 days.", ELEMENTS)
    assert a == b


# ----------------------------- Tier 3 -------------------------------------- #

def test_chunk_hash_stable_across_runs():
    text = "HR Policy > Leave\n\nEmployees may carry forward 10 days."
    assert compute_chunk_hash(text) == compute_chunk_hash(text)


def test_partition_reuses_known_chunks():
    incoming = ["a" * 64, "b" * 64, "c" * 64]
    existing = {"a" * 64, "c" * 64}
    to_embed, to_reuse = partition_chunks_for_embedding(incoming, existing)
    assert to_embed == ["b" * 64]
    assert len(to_reuse) == 2


def test_partition_deduplicates_within_one_document():
    incoming = ["a" * 64, "a" * 64]
    to_embed, _ = partition_chunks_for_embedding(incoming, set())
    assert len(to_embed) == 1


# ------------------------------ Diff --------------------------------------- #

def _fp(n, h):
    return PageFingerprint(page_number=n, page_hash=h, structure_signature="s")


def test_diff_detects_all_four_states():
    stored = [_fp(1, "h1"), _fp(2, "h2"), _fp(3, "h3")]
    incoming = [_fp(1, "h1"), _fp(2, "CHANGED"), _fp(4, "h4")]
    d = diff_pages(stored, incoming)
    assert d.unchanged == [1]
    assert d.modified == [2]
    assert d.added == [4]
    assert d.removed == [3]
    assert d.pages_to_process == [2, 4]


def test_identical_document_is_a_noop():
    pages = [_fp(1, "h1"), _fp(2, "h2")]
    d = diff_pages(pages, pages)
    assert d.is_noop
    assert d.pages_to_process == []


def test_forty_page_document_with_one_edit_processes_one_page():
    """Acceptance scenario US-2.2 — the headline cost-saving claim."""
    stored = [_fp(i, f"h{i}") for i in range(1, 41)]
    incoming = [_fp(i, "EDITED" if i == 7 else f"h{i}") for i in range(1, 41)]
    d = diff_pages(stored, incoming)
    assert d.pages_to_process == [7]
    assert len(d.unchanged) == 39
