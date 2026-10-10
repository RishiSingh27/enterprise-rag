# Constitution — Enterprise Knowledge Assistant (RAG)

**Version:** 1.0.0
**Ratified:** 2026-09-21
**Status:** Active — binding on all contributors
**Owner:** Rishi Singh (Engineer — Cloud & Infra Management)

> The constitution is the highest authority in this repository. If `spec.md`,
> `plan.md`, or any code conflicts with an article below, **the constitution wins**
> and the conflicting artifact must be corrected. Every pull request is reviewed
> against this checklist.

---

## Article I — Grounded Answers Only

1. The assistant **must never answer from model parametric memory**. Every answer
   is generated strictly from retrieved chunks supplied in the prompt context.
2. Every answer **must carry citations** — at minimum `document name + page number`
   for each claim.
3. If the top retrieval score falls below the configured confidence floor
   (`RETRIEVAL_MIN_SCORE`, default `0.35`), the system returns an explicit
   *"I could not find this in the knowledge base"* response. It does **not** guess.
4. Answer generation temperature is capped at `0.2`.

**Rationale:** In an enterprise, a confident wrong answer is more expensive than
no answer. Abstention is a feature, not a failure.

---

## Article II — Idempotent, Content-Addressed Ingestion

1. Ingestion **must be idempotent**. Re-uploading a byte-identical document must
   produce **zero** new parsing, zero new embedding calls, and zero new vectors.
2. Change detection operates at **two tiers**:
   - **Tier 1 — Document hash** (SHA-256 of raw bytes) → "is this the same file?"
   - **Tier 2 — Page hash** (SHA-256 of normalised page text + structural
     fingerprint) → "which pages actually changed?"
3. When a document is re-uploaded with modifications, only pages whose Tier-2
   hash changed may be re-parsed, re-chunked and re-embedded. Unchanged pages
   **must** retain their existing vectors and IDs.
4. Every chunk additionally stores a **chunk hash**, so that a page edit that
   does not alter a given chunk's text does not force re-embedding of that chunk.
5. No ingestion operation may leave the index in a partially-written state.
   Ingestion is transactional: **stage → verify → commit → prune stale vectors**.

**Rationale:** Embeddings cost money and time. Re-embedding a 400-page manual
because someone fixed one typo is unacceptable at enterprise scale.

---

## Article III — Structure Is Meaning

1. Documents **must** be parsed with a layout-aware parser. Naive
   `text = pdf.extract_text()` is prohibited.
2. Tables **must** be preserved as structured Markdown, never flattened into
   run-on prose.
3. Images, charts and diagrams **must** be routed to a multimodal model to
   produce a text description, which is then embedded alongside body text.
4. Chunking **must** be structure-aware first (respect heading/section
   boundaries), and recursive splitting is applied **only within** a section
   that exceeds the token budget.
5. A chunk **must never** span two different top-level sections.

**Rationale:** A table split across two chunks becomes noise. A chart with no
text description is invisible to the retriever.

---

## Article IV — Contract-First Interfaces

1. The OpenAPI contract (`contracts/openapi.yaml`) is authored **before** the
   endpoint is implemented.
2. The Streamlit frontend communicates with the backend **exclusively** over
   these HTTP contracts. It may not import backend modules directly.
3. Breaking contract changes require a version bump and an entry in the
   changelog.

**Rationale:** The UI team and the pipeline team must be able to work in
parallel without blocking each other.

---

## Article V — Security and Data Residency

1. No secret is ever committed. All credentials come from environment variables
   or Azure Key Vault. `.env` is git-ignored; only `.env.example` is committed.
2. Document content and user queries **must not** be written to application logs.
   Log identifiers (`doc_id`, `chunk_id`, `trace_id`), never payloads.
3. All AI inference is served from **Azure AI Foundry** inside the corporate
   tenant. No document content may leave the tenant boundary.
4. Every API call and every ingestion run emits a structured audit record.

---

## Article VI — Observability and Operability

1. Every request carries a `trace_id` propagated from frontend to LLM call.
2. Structured JSON logging only. Per-stage timings (parse / chunk / embed /
   retrieve / generate) are recorded for every run.
3. `/health` and `/ready` endpoints are mandatory.
4. Runbooks ship **with** the code, not after it.

---

## Article VII — Simplicity and Declared Non-Goals

1. **No containerisation in v1.** No Dockerfile, no compose file. The application
   runs on a Python virtual environment directly on the host/VM. *(Explicit
   product decision — corporate desktop virtualisation constraints.)*
2. Prefer the boring option. New dependencies require a justification entry in
   `research.md`.
3. v1 is **single-tenant, shared-corpus**. Per-user document-level ACLs are
   deferred to v2 and are recorded as a known limitation.

---

## Article VIII — Traceability

Every task in `tasks.md` maps to at least one functional requirement (`FR-xxx`)
in `spec.md`. Every `FR` maps to at least one acceptance scenario. Any code with
no traceable requirement behind it is scope creep and must be removed or
specified.

---

## Amendment Procedure

Amendments require: (a) a written rationale, (b) approval from the project owner
and the reviewing architect, (c) a semantic version bump, (d) a sweep of all
downstream artifacts for conflicts.

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-09-21 | Initial ratification |
