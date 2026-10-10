"""
Hybrid retrieval: dense + sparse, fused with Reciprocal Rank Fusion.

Implements T043, T044, T046 and requirements FR-021, FR-022, FR-025.

Why hybrid
----------
Dense (embedding) search understands paraphrase - "how many leaves can I carry
forward" finds "Accrual and Encashment of Earned Leave".
Sparse (BM25) search nails exact tokens - ticket IDs, policy codes, error codes,
which embeddings routinely miss.
They fail on different inputs, so we run both and fuse.

Why RRF and not weighted score averaging
----------------------------------------
Cosine similarity (0..1) and BM25 scores (unbounded) live on incomparable
scales. Averaging them requires a normalisation that is itself arbitrary. RRF
ignores scores entirely and fuses RANKS, which is exactly the problem it solves.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RetrievedChunk:
    chunk_id: str
    content: str
    document: str
    page_number: int
    breadcrumb: str
    element_types: str
    score: float
    image_path: str | None = None


@dataclass
class RetrievalResult:
    chunks: list[RetrievedChunk]
    top_score: float
    abstained: bool


def reciprocal_rank_fusion(
    ranked_lists: list[list[str]],
    k: int = 60,
) -> list[tuple[str, float]]:
    """
    FR-022. score(d) = sum over lists of 1 / (k + rank(d))

    `k` (default 60) damps the influence of the very top positions so that a
    document ranked #1 by one retriever and #30 by the other does not
    automatically beat one ranked #3 by both.
    """
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, doc_id in enumerate(ranked, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


def normalise_fused_score(raw: float, n_lists: int, k: int) -> float:
    """Map the RRF score to 0..1 so it can be compared against the confidence floor."""
    best_possible = n_lists * (1.0 / (k + 1))
    return min(1.0, raw / best_possible) if best_possible else 0.0


class HybridRetriever:
    """Orchestrates embed -> dense + sparse -> fuse -> rerank -> confidence gate."""

    def __init__(self, embedder, vector_store, bm25_store, repository, settings, reranker=None):
        self.embedder = embedder
        self.vector_store = vector_store
        self.bm25_store = bm25_store
        self.repository = repository
        self.settings = settings
        self.reranker = reranker

    def retrieve(self, question: str, top_k: int | None = None,
                 doc_ids: list[str] | None = None) -> RetrievalResult:
        cfg = self.settings
        top_k = top_k or cfg.final_top_k

        # FR-021: the query MUST be embedded with the SAME model used at
        # ingestion. Mixing embedding models puts query and documents in
        # different vector spaces and silently destroys retrieval quality.
        query_vector = self.embedder.embed_query(question)

        dense_hits = self.vector_store.search(query_vector, cfg.dense_top_k)
        dense_ids = [chunk_id for chunk_id, _ in dense_hits]
        sparse_ids = self.bm25_store.search(question, cfg.sparse_top_k)

        fused = reciprocal_rank_fusion([dense_ids, sparse_ids], k=cfg.rrf_k)
        if not fused:
            return RetrievalResult(chunks=[], top_score=0.0, abstained=True)

        # FR-025 / Constitution Article I.
        #
        # The gate uses the best raw COSINE similarity, never the RRF score.
        # RRF measures rank agreement between the two retrievers, so the best
        # of a set of irrelevant matches still ranks #1 in both lists and
        # normalises to ~1.0 - which would wave every out-of-corpus question
        # straight through. Cosine similarity is the only signal that actually
        # says "this text is about the same thing as the question".
        top_score = max((score for _, score in dense_hits), default=0.0)

        if top_score < cfg.retrieval_min_score:
            # Do not generate at all. Deterministic, and it saves the tokens.
            return RetrievalResult(chunks=[], top_score=top_score, abstained=True)

        dense_scores = dict(dense_hits)

        candidate_ids = [cid for cid, _ in fused[: cfg.dense_top_k]]
        records = self.repository.get_chunks(candidate_ids)
        fused_scores = dict(fused)

        chunks = [
            RetrievedChunk(
                chunk_id=r.chunk_id,
                content=r.content,
                document=r.document_name,
                page_number=r.page_number,
                breadcrumb=r.breadcrumb,
                element_types=r.element_types,
                # Report cosine where we have it (interpretable for the user in
                # the sources panel); fall back to the fused rank score for
                # BM25-only hits, which have no vector score.
                score=round(
                    dense_scores.get(
                        r.chunk_id,
                        normalise_fused_score(fused_scores.get(r.chunk_id, 0.0), 2, cfg.rrf_k),
                    ),
                    4,
                ),
                image_path=r.image_path,
            )
            for r in records
        ]
        chunks.sort(key=lambda c: c.score, reverse=True)

        # FR-023: rerank fused candidates before spending context on them.
        if self.reranker and cfg.enable_rerank:
            chunks = self.reranker.rerank(question, chunks, top_k)
        else:
            chunks = chunks[:top_k]

        return RetrievalResult(chunks=chunks, top_score=top_score, abstained=False)
