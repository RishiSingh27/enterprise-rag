# Feature Specification — Enterprise Knowledge Assistant (RAG)

**Feature Branch:** `001-enterprise-rag`
**Created:** 2026-09-21
**Status:** Draft — ready for review
**Input:** Employees need to ask natural-language questions and get cited answers
from internal documents that contain text, tables, images and charts.

> **Audience note:** This document describes **WHAT** the system does and **WHY**.
> It deliberately contains no technology names in the requirements section — those
> live in `plan.md`. A non-technical reviewer should be able to sign off on this
> document alone.

---

## 1. Problem Statement

Employees at the organisation lose significant time hunting for information
buried in PDFs, Word documents, policy manuals and slide decks spread across
shared drives. Keyword search fails because people ask questions in everyday
language ("how many leaves do I carry forward?") while the document says
"Accrual and Encashment of Earned Leave". Worse, much of the critical
information sits inside **tables and charts**, which conventional search engines
cannot read at all.

A general-purpose chatbot cannot solve this: it has never seen the company's
internal documents, and when it does not know an answer it invents one.

## 2. Solution Summary

A web application where an employee uploads (or an admin bulk-loads) internal
documents, and any employee can then ask plain-English questions. The system
answers **only** from those documents and shows exactly which document and page
each part of the answer came from. If the answer is not in the corpus, it says so.

## 3. Goals

| # | Goal | Success measure |
|---|---|---|
| G1 | Reduce information lookup time | Median time-to-answer < 30 seconds |
| G2 | Eliminate fabricated answers | 0 uncited claims in the evaluation set |
| G3 | Make tables and charts searchable | ≥ 90% correct answers on table-only questions |
| G4 | Avoid wasted re-processing cost | Re-upload of unchanged file consumes 0 embedding tokens |
| G5 | Auditability | Every answer traceable to source document + page |

## 4. Non-Goals (v1)

- Real-time chat across multiple simultaneous users with per-user permissions.
- Editing or writing back into source documents.
- Support for audio/video source files.
- Containerised or Kubernetes deployment *(explicitly out of scope)*.
- Fine-tuning any model.

---

## 5. User Scenarios

### US-1 — Knowledge Seeker (Primary)
**As an** employee,
**I want to** ask a question in plain English about company documents,
**so that** I get a precise, citable answer without reading a 200-page manual.

**Acceptance scenarios**

1. **Given** the HR policy PDF is indexed, **when** the user asks
   *"How many earned leaves can I carry forward?"*, **then** the system returns
   the correct number **and** displays `HR_Policy_2026.pdf — page 14` as the source.
2. **Given** a question with no supporting content in the corpus, **when** the
   user asks *"What is our Mars colonisation budget?"*, **then** the system
   responds that the information is not available in the knowledge base and
   returns **no** invented answer.
3. **Given** a document containing a comparison table, **when** the user asks
   *"What is the SLA for a Severity-2 incident?"*, **then** the answer is
   extracted correctly from the table row, not from surrounding prose.
4. **Given** a document containing a bar chart of quarterly revenue, **when** the
   user asks *"Which quarter had the highest revenue?"*, **then** the system
   answers from the chart's generated description and cites the page.

### US-2 — Knowledge Administrator
**As a** knowledge administrator,
**I want to** upload and re-upload documents safely,
**so that** the corpus stays current without duplicate or stale content.

**Acceptance scenarios**

1. **Given** `Policy_v1.pdf` is already indexed, **when** the identical file is
   uploaded again, **then** the system reports `status: duplicate`, performs no
   parsing, makes **zero** embedding calls, and the vector count is unchanged.
2. **Given** `Policy_v1.pdf` (40 pages) is indexed, **when** a revised copy with
   edits confined to pages 7 and 8 is uploaded, **then** the system reports
   `pages_changed: [7, 8]`, re-embeds **only** the chunks from those two pages,
   removes the superseded vectors for those pages, and leaves the other 38 pages'
   vectors untouched.
3. **Given** a page where only one line changed, **when** re-ingested, **then**
   only the chunk(s) containing that line are re-embedded; sibling chunks on the
   same page whose chunk-hash is unchanged are reused.
4. **Given** an upload of an unsupported file type, **when** submitted, **then**
   the system rejects it with a clear error and the corpus is unchanged.

### US-3 — Reviewer / Auditor
**As a** compliance reviewer,
**I want to** see which source text produced an answer,
**so that** I can verify the system's output.

**Acceptance scenario**
1. **Given** any generated answer, **when** the reviewer expands the "Sources"
   panel, **then** the exact retrieved chunk text, document name, page number and
   similarity score are visible.

---

## 6. Functional Requirements

### 6.1 Ingestion & Deduplication

| ID | Requirement |
|---|---|
| **FR-001** | The system MUST accept `.pdf`, `.docx`, `.pptx`, `.xlsx`, `.txt`, `.md`, `.png`, `.jpg` uploads and reject all other types with a descriptive error. |
| **FR-002** | The system MUST compute a **document-level hash** over the raw file bytes and use it as the document's unique identity. |
| **FR-003** | If an incoming document-level hash already exists in the registry, the system MUST short-circuit the pipeline and report `duplicate` without parsing or embedding. |
| **FR-004** | The system MUST compute a **page-level hash** for every page, derived from normalised page text plus a structural fingerprint (count/order of headings, tables, images). |
| **FR-005** | On re-ingestion of a changed document, the system MUST classify each page as `unchanged`, `modified`, `added`, or `removed` by comparing page hashes. |
| **FR-006** | The system MUST re-parse, re-chunk and re-embed **only** pages classified `modified` or `added`. |
| **FR-007** | The system MUST compute a **chunk-level hash** and skip re-embedding any chunk whose hash is unchanged, even if its parent page was modified. |
| **FR-008** | The system MUST delete vectors belonging to `removed` pages and to superseded chunks of `modified` pages. |
| **FR-009** | Ingestion MUST be transactional — a failure mid-run MUST NOT leave orphaned vectors or a corrupted registry. |
| **FR-010** | The system MUST return an ingestion report containing: status, document id, pages added/modified/unchanged/removed, chunks created, chunks reused, and embedding calls made. |

### 6.2 Parsing & Understanding

| ID | Requirement |
|---|---|
| **FR-011** | Parsing MUST be layout-aware: the system MUST identify headings, paragraphs, lists, tables, images and reading order. |
| **FR-012** | Tables MUST be extracted as structured data and serialised to Markdown with headers preserved. |
| **FR-013** | Images, charts and diagrams MUST be sent to a multimodal vision model to generate a textual description, which is indexed as a searchable element attributed to its page. |
| **FR-014** | Scanned/image-only pages MUST be processed through OCR. |
| **FR-015** | Every extracted element MUST retain `page_number`, `element_type` and `reading_order`. |

### 6.3 Chunking

| ID | Requirement |
|---|---|
| **FR-016** | Chunking MUST be **structure-aware**: section and heading boundaries take precedence over size. |
| **FR-017** | When a section exceeds the token budget, the system MUST apply **recursive splitting** in descending order of separator strength (section → paragraph → sentence → token) with a configurable overlap. |
| **FR-018** | A table MUST be kept as a single atomic chunk when it fits the budget; if it does not, it MUST be split by rows with the header row repeated in each part. |
| **FR-019** | Every chunk MUST carry the heading breadcrumb of its parent section as prepended context. |
| **FR-020** | A chunk MUST NOT span two different top-level sections. |

### 6.4 Retrieval & Generation

| ID | Requirement |
|---|---|
| **FR-021** | The system MUST embed the user query with the **same** embedding model used at ingestion time. |
| **FR-022** | The system MUST perform hybrid retrieval (dense semantic + sparse keyword) and fuse the rankings. |
| **FR-023** | The system MUST re-rank fused candidates and pass the top-`k` (default 5) to the generator. |
| **FR-024** | The generator MUST be instructed to answer **only** from the provided context and to cite document name and page for each claim. |
| **FR-025** | If the best fused score is below the confidence floor, the system MUST return an explicit "not found in knowledge base" response instead of generating. |
| **FR-026** | Each answer response MUST include a list of sources with document name, page number, score, and the retrieved snippet. |
| **FR-027** | The system MUST support answer streaming to the UI. |

### 6.5 Interface

| ID | Requirement |
|---|---|
| **FR-028** | The web UI MUST provide: document upload with progress, a corpus browser showing indexed documents and their status, a chat panel, and an expandable sources panel. |
| **FR-029** | The UI MUST communicate with the backend only through the published HTTP API. |
| **FR-030** | The backend MUST expose `/health` and `/ready` endpoints. |

### 6.6 Non-Functional

| ID | Requirement |
|---|---|
| **NFR-001** | Retrieval latency (excluding generation) MUST be under 500 ms at p95 for a 10,000-chunk corpus. |
| **NFR-002** | End-to-end answer latency MUST be under 10 s at p95. |
| **NFR-003** | Ingestion MUST sustain at least 50 pages/minute. |
| **NFR-004** | No secrets in source control; no document content in logs. |
| **NFR-005** | All AI inference MUST remain within the corporate cloud tenant. |
| **NFR-006** | Unit test coverage MUST be ≥ 80% on the hashing, chunking and retrieval modules. |

---

## 7. Key Entities (conceptual)

- **Document** — an uploaded file; identified by its content hash; owns pages.
- **Page** — one page of a document; identified by its own content hash.
- **Element** — a structural unit on a page (heading, paragraph, table, image).
- **Chunk** — the retrievable unit; text + metadata + its own hash.
- **Embedding** — the vector representation of a chunk.
- **QuerySession** — a question, its retrieved chunks, and the generated answer.

*(Full field-level definitions are in `data-model.md`.)*

---

## 8. Edge Cases

| Situation | Expected behaviour |
|---|---|
| Password-protected PDF | Reject with clear error; corpus unchanged |
| 0-byte or corrupt file | Reject with clear error |
| Document with only images (scanned) | OCR path engages; content still indexed |
| Same content, different filename | Treated as duplicate (hash is over bytes, not name) |
| Same filename, different content | Treated as a new version; page-level diff applied |
| Table spanning two pages | Merged during parsing where continuation is detected |
| Embedding service throttles (429) | Exponential backoff with retry; run resumes, no partial commit |
| Query in a language other than the corpus language | Answer attempted; low confidence triggers abstention |

---

## 9. Review Checklist

- [x] No implementation detail leaks into requirements
- [x] Every requirement is testable and unambiguous
- [x] Success criteria are measurable
- [x] Scope is bounded; non-goals are explicit
- [x] Dependencies and assumptions identified
- [ ] Signed off by Manager
- [ ] Signed off by Architecture review
