# Research & Decision Log

**Feature:** 001-enterprise-rag | **Date:** 2026-09-21

> Purpose: capture **why** each choice was made, so that six months from now
> nobody has to ask "why did we build it this way?". Each entry states the
> decision, the alternatives considered, and the reason for rejection.

---

## D-001 — Two-tier (in practice three-tier) hashing for incremental ingestion

**Decision:** SHA-256 at document level (raw bytes), page level (normalised text
+ structural signature), and chunk level (chunk text).

**Alternatives considered**

| Option | Why rejected |
|---|---|
| Last-modified timestamp | Changes on every re-save/re-export even with identical content. Unreliable. |
| Document-level hash only | Detects duplicates but forces full re-embedding for a one-word edit. Fails G4. |
| MD5 | Collision-prone; unacceptable for an integrity-critical identity key. |
| Per-paragraph hashing | Too granular — boundary drift makes nearly every paragraph look "changed". |

**Rationale:** Document hash gives free O(1) duplicate rejection. Page hash is
the right granularity for how humans actually edit documents. Chunk hash is the
final safety net so a page edit doesn't re-bill the entire page.

**Why the structural signature is included in the page hash:** text-only hashing
would report "unchanged" when a paragraph is restructured into a table — the
text is the same but the correct chunking is completely different.

**Why normalisation before hashing:** PDF exports frequently carry a generation
timestamp in the footer. Without stripping repeated headers/footers and
collapsing whitespace, every page would hash as modified on every re-export,
nullifying the entire optimisation.

---

## D-002 — Azure AI Document Intelligence (`prebuilt-layout`) as primary parser

**Decision:** Use Document Intelligence layout model; `unstructured` +
`pdfplumber` + Tesseract as a configurable offline fallback.

**Alternatives considered**

| Option | Why rejected as primary |
|---|---|
| PyPDF2 / pdfminer text extraction | No layout, no tables, no reading order. Violates Constitution Art. III. |
| LayoutParser (Detectron2) | Strong research tool but needs GPU + heavy model downloads; corporate network restrictions on model hosts make this fragile. Kept as an option, not the default. |
| Pure LLM parsing (send whole page image to GPT) | Expensive at scale and non-deterministic for tables. Used only for figures. |

**Rationale:** Document Intelligence returns headings, reading order, table cell
structure and figure bounding boxes in one call, stays inside the Azure tenant
(Constitution Art. V), and needs no local model weights. The fallback path exists
specifically because corporate proxies sometimes block external model downloads.

---

## D-003 — Multimodal captioning of images and charts

**Decision:** Crop figure regions using bounding boxes from the layout pass, send
each to GPT-4.1-mini vision, embed the returned description as a normal chunk
tagged `element_type=image`.

**Alternatives considered:** OCR-only on images (captures axis labels but not the
*message* of a chart); skipping images entirely (fails G3 and US-1 scenario 4).

**Rationale:** "Which quarter had the highest revenue?" is unanswerable from OCR
of a bar chart. A one-sentence factual description makes the chart retrievable
with the same machinery as text. Captions are cached by image hash so re-runs
cost nothing.

---

## D-004 — Structure-aware chunking with recursive fallback

**Decision:** Group by heading hierarchy first; apply recursive character
splitting only inside oversized sections. 800-token target, 120-token overlap,
heading breadcrumb prepended to every chunk.

**Alternatives considered**

| Option | Why rejected |
|---|---|
| Fixed-size chunking | Cuts tables and sentences in half; classic cause of nonsense retrieval. |
| Semantic (embedding-similarity) chunking | Costs an embedding pass just to decide boundaries; non-deterministic across runs, which breaks chunk-hash stability. |
| Recursive-only | Good, but ignores real document structure that the layout parser already gave us for free. |

**Rationale:** The layout parser already identified sections — discarding that
and falling back to blind splitting would be wasteful. Determinism matters here:
non-deterministic boundaries would make chunk hashes unstable and break D-001.

**Why the breadcrumb:** a chunk reading "…must be approved within 48 hours"
is meaningless alone. Prefixed with `HR Policy > Leave > Emergency Leave` it is
both retrievable and interpretable.

---

## D-005 — `text-embedding-3-large` for embeddings

**Decision:** Azure AI Foundry `text-embedding-3-large`, 3072 dimensions.

**Alternatives considered:** `text-embedding-3-small` (cheaper, weaker on long
technical passages and table text); open-source `bge-large` / `all-MiniLM` via
Hugging Face (no tenant-boundary guarantee, and corporate network restrictions
on model downloads make deployment unreliable).

**Rationale:** Highest retrieval quality available in-tenant, and quality at the
retrieval stage determines the ceiling for everything downstream. Dimension
reduction to 1024 via the `dimensions` parameter is available later if index
memory becomes a constraint.

---

## D-006 — `gpt-4.1-mini` for generation and vision

**Decision:** Single model for both answer generation and figure captioning.

**Alternatives considered:** GPT-4.1 full (materially higher cost; the generation
task here is extractive/summarising over supplied context, not open-ended
reasoning); a separate dedicated vision model (two deployments to operate for no
quality gain).

**Rationale:** In a well-built RAG system the retriever does the hard work. One
deployment, one quota, one cost line, and native vision support covers D-003.

---

## D-007 — FAISS + BM25 hybrid retrieval with RRF

**Decision:** FAISS `IndexFlatIP` over L2-normalised vectors (= cosine), plus
BM25, fused with Reciprocal Rank Fusion (`k=60`).

**Alternatives considered**

| Option | Why rejected for v1 |
|---|---|
| Azure AI Search | Excellent and the natural v2 target, but adds a paid service and provisioning lead time to v1. |
| Dense-only retrieval | Fails on exact identifiers — ticket numbers, policy codes, error codes. |
| Sparse-only (BM25) | Fails on paraphrase, which is the primary user behaviour we are solving for. |
| Pinecone / Weaviate | External data egress; violates Constitution Art. V. |

**Rationale:** Hybrid is the recognised production default because the two
retrievers fail on different inputs. RRF needs no score normalisation between
two incomparable scoring scales, which is exactly the problem it solves.
The vector store sits behind an interface, so the v2 swap to Azure AI Search
touches one module.

---

## D-008 — Confidence floor and explicit abstention

**Decision:** If the best fused score is below `RETRIEVAL_MIN_SCORE` (0.35),
skip generation entirely and return a "not found in knowledge base" response.

**Alternative considered:** Always generate and rely on the prompt to say
"I don't know". Rejected — prompt-level instructions are probabilistic, a
numeric gate is deterministic, and it also saves the generation token cost.

---

## D-009 — SQLite + SQLAlchemy for the registry

**Decision:** SQLite file for document/page/chunk hash registry.

**Alternatives considered:** JSON file (no transactions, no concurrent access,
no indexes — fails FR-009); Azure SQL / PostgreSQL (correct for multi-node v2,
unnecessary operational overhead for a single-node v1).

**Rationale:** ACID transactions are a hard requirement for FR-009, and SQLite
provides them with zero infrastructure. SQLAlchemy makes the migration to Azure
SQL a connection-string change.

---

## D-010 — Streamlit frontend, FastAPI backend, no Docker

**Decision:** Streamlit UI calling FastAPI over HTTP. Deployed into a Python
virtual environment. **No Dockerfile in v1.**

**Rationale:** Streamlit delivers an acceptable internal-tool UI in a fraction of
the effort of React, and FastAPI gives async I/O plus auto-generated OpenAPI docs
that satisfy Constitution Art. IV. The two are kept in separate processes so the
UI can be replaced later without touching the pipeline.

The no-Docker decision is a deliberate product constraint (Constitution Art. VII)
driven by corporate desktop virtualisation restrictions. Consequence to accept
openly: environment reproducibility depends on pinned `requirements.txt` and the
documented venv procedure in `quickstart.md`. Containerisation is a v2 candidate,
not a defect.

---

## Open Questions for v2

1. Per-user / per-group document access control.
2. Migration from FAISS to Azure AI Search for horizontal scale.
3. Conversational memory across turns (query rewriting with history).
4. Automated nightly evaluation against a golden question set with regression alerts.
5. Incremental BM25 index updates (currently rebuilt on commit).
