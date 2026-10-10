"""
Embedding generation via text-embedding-3-large (T037, T038).

FR-021: the SAME model must be used at ingestion and at query time. Mixing
embedding models puts queries and documents in different vector spaces and
silently destroys retrieval quality — with no error to warn you.

Vectors are L2-normalised so that FAISS inner product == cosine similarity.
"""

from __future__ import annotations

import logging

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

log = logging.getLogger("ekb.embedder")


class Embedder:
    def __init__(self, settings):
        self.settings = settings
        self._call_count = 0

    @property
    def call_count(self) -> int:
        return self._call_count

    # ------------------------------------------------------------------ #

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """
        Batch to reduce round trips. Azure accepts many inputs per call, and
        batching is the difference between minutes and hours on a large corpus.
        """
        if not texts:
            return []

        vectors: list[list[float]] = []
        size = self.settings.embedding_batch_size
        for start in range(0, len(texts), size):
            batch = texts[start:start + size]
            vectors.extend(self._embed_with_retry(batch))
            log.info("embed.batch", extra={
                "batch_size": len(batch),
                "progress": f"{min(start + size, len(texts))}/{len(texts)}",
            })
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._embed_with_retry([text])[0]

    # ------------------------------------------------------------------ #

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=2, max=60),
        reraise=True,
    )
    def _embed_with_retry(self, texts: list[str]) -> list[list[float]]:
        """
        Exponential backoff on 429. Azure throttles by tokens-per-minute; on a
        large ingest this WILL trigger, and retrying is normal rather than an
        error condition.
        """
        from ..core.azure_clients import get_openai_client

        response = get_openai_client().embeddings.create(
            model=self.settings.azure_embedding_deployment,
            input=texts,
        )
        self._call_count += len(texts)
        return [_normalise(item.embedding) for item in response.data]


def _normalise(vector: list[float]) -> list[float]:
    """L2-normalise so inner product equals cosine similarity."""
    import math

    magnitude = math.sqrt(sum(v * v for v in vector))
    if magnitude == 0:
        return vector
    return [v / magnitude for v in vector]
