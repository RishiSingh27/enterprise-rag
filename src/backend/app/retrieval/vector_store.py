"""
FAISS vector store (T041).

IndexFlatIP over L2-normalised vectors == exact cosine similarity search.
Wrapped in IndexIDMap so we control the ids and can delete precisely when a
page is removed or superseded (FR-008).

Deliberately behind a narrow interface — D-007 anticipates swapping this for
Azure AI Search in v2, and that swap should touch only this file.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

log = logging.getLogger("ekb.vectorstore")


class VectorStore:
    def __init__(self, settings):
        self.settings = settings
        self.dimension = settings.embedding_dimension
        self.index_path = settings.index_dir / "faiss.index"
        self.meta_path = settings.index_dir / "faiss_meta.json"
        self._index = None
        self._next_id = 0
        self._id_to_chunk: dict[int, str] = {}
        self.load()

    # ------------------------------------------------------------------ #

    def _ensure_index(self):
        if self._index is None:
            import faiss
            self._index = faiss.IndexIDMap(faiss.IndexFlatIP(self.dimension))
        return self._index

    def load(self) -> None:
        if not self.index_path.exists():
            self._ensure_index()
            return
        try:
            import faiss
            self._index = faiss.read_index(str(self.index_path))
            if self.meta_path.exists():
                meta = json.loads(self.meta_path.read_text())
                self._next_id = meta.get("next_id", 0)
                self._id_to_chunk = {int(k): v for k, v in meta.get("id_to_chunk", {}).items()}
            log.info("vectorstore.loaded", extra={"vectors": self._index.ntotal})
        except Exception as exc:  # noqa: BLE001
            log.warning("vectorstore.load_failed", extra={"error": str(exc)[:200]})
            self._index = None
            self._ensure_index()

    def persist(self) -> None:
        import faiss

        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self._ensure_index(), str(self.index_path))
        self.meta_path.write_text(json.dumps({
            "next_id": self._next_id,
            "id_to_chunk": {str(k): v for k, v in self._id_to_chunk.items()},
            "dimension": self.dimension,
            "model": self.settings.azure_embedding_deployment,
        }))

    # ------------------------------------------------------------------ #

    def add(self, vectors: list[list[float]]) -> list[int]:
        """Returns the assigned vector ids, in the same order."""
        if not vectors:
            return []
        index = self._ensure_index()
        ids = list(range(self._next_id, self._next_id + len(vectors)))
        self._next_id += len(vectors)
        index.add_with_ids(
            np.array(vectors, dtype="float32"),
            np.array(ids, dtype="int64"),
        )
        return ids

    def map_chunks(self, vector_ids: list[int], chunk_ids: list[str]) -> None:
        for vid, cid in zip(vector_ids, chunk_ids):
            self._id_to_chunk[vid] = cid

    def remove(self, vector_ids: list[int]) -> None:
        """FR-008. Precise deletion so superseded content cannot be retrieved."""
        if not vector_ids:
            return
        import faiss

        self._ensure_index().remove_ids(np.array(vector_ids, dtype="int64"))
        for vid in vector_ids:
            self._id_to_chunk.pop(vid, None)
        log.info("vectorstore.removed", extra={"count": len(vector_ids)})

    def search(self, query_vector: list[float], top_k: int) -> list[tuple[str, float]]:
        """
        Returns ``[(chunk_id, cosine_similarity), ...]`` ranked best first.

        The raw cosine score is returned deliberately: it is the only honest
        relevance signal we have. RRF scores cannot be used for the confidence
        gate because RRF rewards RANK agreement, not relevance - the single
        best of a set of bad matches still ranks #1 and scores near 1.0.
        """
        index = self._ensure_index()
        if index.ntotal == 0:
            return []
        scores, ids = index.search(
            np.array([query_vector], dtype="float32"), min(top_k, index.ntotal)
        )
        return [
            (self._id_to_chunk[int(vid)], float(score))
            for vid, score in zip(ids[0], scores[0])
            if vid != -1 and int(vid) in self._id_to_chunk
        ]

    @property
    def size(self) -> int:
        return self._ensure_index().ntotal
