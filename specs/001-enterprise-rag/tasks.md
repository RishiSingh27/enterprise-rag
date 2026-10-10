# Tasks — Enterprise Knowledge Assistant (RAG)

**Feature:** 001-enterprise-rag | **Date:** 2026-09-21
**Legend:** `[P]` = parallelisable (no shared file, no dependency) · `[ ]` open · `[x]` done

> Every task names an **exact file path** and maps to a **requirement ID**.
> If a task has no FR behind it, it is scope creep (Constitution Art. VIII).

---

## Phase 0 — Foundation (T001–T009)

| ID | P | Task | File | Traces |
|---|---|---|---|---|
| T001 | | Create repo structure per `plan.md` §3 | repo root | — |
| T002 | [P] | Pin dependencies | `requirements.txt` | — |
| T003 | [P] | Settings class (Azure endpoints, deployments, chunk sizes, score floor) | `src/backend/app/core/config.py` | NFR-004 |
| T004 | [P] | `.env.example` with every variable, no real values | `.env.example` | NFR-004 |
| T005 | [P] | Git ignore `.env`, `data/`, `__pycache__`, `*.db`, `*.faiss` | `.gitignore` | NFR-004 |
| T006 | | JSON logger with `trace_id` and stage timers; payload redaction | `src/backend/app/core/logging.py` | Art. VI, NFR-004 |
| T007 | | Azure AI Foundry clients (embeddings, chat, vision, doc-intelligence) | `src/backend/app/core/azure_clients.py` | NFR-005 |
| T008 | | FastAPI app factory + CORS + exception handlers | `src/backend/main.py` | FR-030 |
| T009 | | `/health` and `/ready` endpoints | `src/backend/app/api/routes_admin.py` | FR-030 |

**Exit gate:** `uvicorn` starts, `/health` returns 200, `/ready` confirms Azure reachability.

---

## Phase 1 — Hashing & Registry (T010–T021)

| ID | P | Task | File | Traces |
|---|---|---|---|---|
| T010 | | SQLAlchemy models: Document, Page, Element, Chunk, Embedding, IngestionRun, QuerySession | `src/backend/app/models/db.py` | data-model §2 |
| T011 | | All indexes from `data-model.md` §4 | `src/backend/app/models/db.py` | FR-003, FR-007 |
| T012 | [P] | `compute_document_hash(bytes)` — Tier 1 SHA-256 | `src/backend/app/ingestion/hashing.py` | FR-002 |
| T013 | [P] | `normalize_text()` — strip headers/footers/page numbers, collapse whitespace, lowercase | `src/backend/app/ingestion/hashing.py` | FR-004 |
| T014 | [P] | `build_structure_signature(elements)` | `src/backend/app/ingestion/hashing.py` | FR-004 |
| T015 | | `compute_page_hash()` — Tier 2 (normalised text + signature) | `src/backend/app/ingestion/hashing.py` | FR-004 |
| T016 | [P] | `compute_chunk_hash()` — Tier 3 | `src/backend/app/ingestion/hashing.py` | FR-007 |
| T017 | | `diff_pages(old, new)` → unchanged/modified/added/removed | `src/backend/app/ingestion/hashing.py` | FR-005 |
| T018 | | Registry repository: lookup by doc_hash, upsert pages, chunk reuse query | `src/backend/app/models/repository.py` | FR-003, FR-007 |
| T019 | | Transactional commit + rollback wrapper | `src/backend/app/models/repository.py` | FR-009 |
| T020 | [P] | Unit tests: identical bytes → same hash; whitespace-only change → same page hash; one word change → different page hash | `tests/test_hashing.py` | FR-002/004 |
| T021 | [P] | Unit tests for `diff_pages` covering all four states | `tests/test_diff.py` | FR-005 |

**Exit gate:** Re-uploading the same bytes returns `duplicate` with `embedding_calls = 0`, proven by test.

---

## Phase 2 — Parsing (T022–T031)

| ID | P | Task | File | Traces |
|---|---|---|---|---|
| T022 | | File-type validation and rejection with clear errors | `src/backend/app/ingestion/parser.py` | FR-001 |
| T023 | | Document Intelligence `prebuilt-layout` call with retry/backoff | `src/backend/app/ingestion/parser.py` | FR-011 |
| T024 | | Map layout response → `Element` list with page_number, type, reading_order | `src/backend/app/ingestion/parser.py` | FR-015 |
| T025 | | Table → GFM Markdown serialiser, header preserved | `src/backend/app/ingestion/table_utils.py` | FR-012 |
| T026 | | Cross-page table continuation merge | `src/backend/app/ingestion/table_utils.py` | Edge case |
| T027 | | Figure crop from bbox → PNG under `data/images/` | `src/backend/app/ingestion/parser.py` | FR-013 |
| T028 | | GPT-4.1-mini vision captioning + cache by `image_hash` | `src/backend/app/ingestion/multimodal.py` | FR-013 |
| T029 | | OCR path for image-only pages | `src/backend/app/ingestion/parser.py` | FR-014 |
| T030 | [P] | Offline fallback backend (`unstructured` + `pdfplumber` + Tesseract) behind `PARSER_BACKEND` | `src/backend/app/ingestion/parser_local.py` | D-002 |
| T031 | [P] | Tests: table Markdown integrity; image element carries description + path | `tests/test_parser.py` | FR-012/013 |

**Exit gate:** A PDF containing a table and a bar chart yields both a Markdown table element and a descriptive image element.

---

## Phase 3 — Chunking & Embedding (T032–T040)

| ID | P | Task | File | Traces |
|---|---|---|---|---|
| T032 | | Pass 1: group elements into sections by heading hierarchy | `src/backend/app/ingestion/chunker.py` | FR-016 |
| T033 | | Breadcrumb builder `Doc > Section > Subsection` | `src/backend/app/ingestion/chunker.py` | FR-019 |
| T034 | | Pass 2: recursive splitter with separator ladder + overlap | `src/backend/app/ingestion/chunker.py` | FR-017 |
| T035 | | Table chunk rule: atomic, else row-split with repeated header | `src/backend/app/ingestion/chunker.py` | FR-018 |
| T036 | | Enforce no chunk spans two top-level sections | `src/backend/app/ingestion/chunker.py` | FR-020 |
| T037 | | Embedder: `text-embedding-3-large`, batch 64, backoff on 429, L2-normalise | `src/backend/app/ingestion/embedder.py` | FR-021 |
| T038 | | Skip embedding when `chunk_hash` already active; count `chunks_reused` | `src/backend/app/ingestion/embedder.py` | FR-007 |
| T039 | | Pipeline orchestrator: hash → diff → parse → chunk → embed → stage → commit → prune | `src/backend/app/ingestion/pipeline.py` | FR-006/008/009/010 |
| T040 | [P] | Tests: section boundary respected; table not split when under budget; one-line edit re-embeds only affected chunks | `tests/test_chunker.py` | FR-016–020 |

**Exit gate:** Editing one line on page 7 of a 40-page document produces `pages_modified: 1` and a single-digit `embedding_calls`.

---

## Phase 4 — Retrieval & Generation (T041–T050)

| ID | P | Task | File | Traces |
|---|---|---|---|---|
| T041 | | FAISS `IndexFlatIP` store: add, search, delete-by-id, persist/load | `src/backend/app/retrieval/vector_store.py` | FR-022 |
| T042 | [P] | BM25 sparse index build + persist | `src/backend/app/retrieval/bm25_store.py` | FR-022 |
| T043 | | Query embedding using the identical ingestion model | `src/backend/app/retrieval/retriever.py` | FR-021 |
| T044 | | Reciprocal Rank Fusion (k=60) over dense + sparse | `src/backend/app/retrieval/retriever.py` | FR-022 |
| T045 | | LLM re-rank fused top-20 → top-5 | `src/backend/app/retrieval/reranker.py` | FR-023 |
| T046 | | Confidence gate → abstention response | `src/backend/app/retrieval/retriever.py` | FR-025 |
| T047 | | Grounded system prompt: context-only, mandatory citations, temp 0.2 | `src/backend/app/retrieval/prompts.py` | FR-024, Art. I |
| T048 | | Generator with source assembly (doc, page, score, snippet) | `src/backend/app/retrieval/generator.py` | FR-024/026 |
| T049 | | Streaming generation (SSE) | `src/backend/app/retrieval/generator.py` | FR-027 |
| T050 | [P] | Tests: out-of-corpus question triggers abstention; every answer carries ≥1 source | `tests/test_retrieval.py` | FR-025/026 |

**Exit gate:** Golden question set passes; "Mars colonisation budget" abstains.

---

## Phase 5 — API & UI (T051–T057)

| ID | P | Task | File | Traces |
|---|---|---|---|---|
| T051 | | Pydantic schemas mirroring `openapi.yaml` exactly | `src/backend/app/models/schemas.py` | Art. IV |
| T052 | | `POST /api/v1/ingest` | `src/backend/app/api/routes_ingest.py` | FR-001–010 |
| T053 | | `POST /api/v1/query` and `/query/stream` | `src/backend/app/api/routes_query.py` | FR-021–027 |
| T054 | | `GET /documents`, `GET /documents/{id}`, `DELETE`, `GET /stats` | `src/backend/app/api/routes_admin.py` | FR-028 |
| T055 | | Streamlit: upload panel with ingestion report display | `src/frontend/app.py` | FR-028 |
| T056 | | Streamlit: chat panel + expandable sources with page numbers and figure previews | `src/frontend/app.py` | FR-026/028 |
| T057 | | Streamlit: corpus browser and stats sidebar; HTTP-only access to backend | `src/frontend/app.py` | FR-029 |

**Exit gate:** Full browser demo — upload, re-upload (duplicate), edited re-upload (partial), question with table answer, question with chart answer, out-of-scope question.

---

## Phase 6 — Hardening & Handover (T058–T064)

| ID | P | Task | File | Traces |
|---|---|---|---|---|
| T058 | [P] | Raise coverage to ≥80% on hashing, chunking, retrieval | `tests/` | NFR-006 |
| T059 | [P] | Golden evaluation set (30 Q&A incl. table and chart questions) | `tests/eval/golden_set.json` | G2, G3 |
| T060 | | Latency instrumentation per stage; verify NFR-001/002 | `src/backend/app/core/logging.py` | NFR-001/002 |
| T061 | [P] | Bulk ingestion script for a folder | `scripts/bulk_ingest.py` | FR-001 |
| T062 | [P] | Run scripts for backend and frontend | `scripts/run_*.sh` | — |
| T063 | [P] | `quickstart.md` verified end-to-end on a clean machine | `specs/001-enterprise-rag/quickstart.md` | — |
| T064 | | `README.md` — architecture, rationale, workflow | `README.md` | — |

---

## Parallel Execution Guide

Tasks marked `[P]` touch different files and can run concurrently:

- **Wave A (after T001):** T002, T003, T004, T005
- **Wave B (after T010–T011):** T012, T013, T014, T016 — all in `hashing.py`, so assign to **one** developer; T020, T021 to another
- **Wave C (after Phase 2 starts):** T030 and T031 alongside T023–T029
- **Wave D (Phase 4):** T042 and T050 alongside T041/T043–T049
- **Wave E (Phase 6):** T058, T059, T061, T062 fully parallel

## Requirement Coverage Matrix

| FR | Covered by |
|---|---|
| FR-001 | T022, T052, T061 |
| FR-002 | T012, T020 |
| FR-003 | T018, T039 |
| FR-004 | T013, T014, T015, T020 |
| FR-005 | T017, T021 |
| FR-006 | T039 |
| FR-007 | T016, T018, T038, T040 |
| FR-008 | T039, T041 |
| FR-009 | T019, T039 |
| FR-010 | T039, T052, T055 |
| FR-011–015 | T023, T024, T025, T027, T028, T029 |
| FR-016–020 | T032–T036, T040 |
| FR-021–027 | T037, T043–T049, T050 |
| FR-028–030 | T009, T054, T055, T056, T057 |
| NFR-001/002 | T060 |
| NFR-004 | T003, T004, T005, T006 |
| NFR-005 | T007 |
| NFR-006 | T058 |

**Totals:** 64 tasks · 6 phases · 30 FRs + 6 NFRs · 3 user stories.
