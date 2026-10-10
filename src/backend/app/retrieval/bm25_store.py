"""
BM25 sparse index (T042).

WHY SPARSE SEARCH IS STILL NEEDED
---------------------------------
Embeddings are excellent at paraphrase and terrible at exact identifiers.
Ask for ticket "INC0042318" or error code "SEV-2" and dense search returns
semantically similar but wrong content. BM25 matches the literal token.

Dense and sparse fail on different inputs — which is exactly why we fuse them.

Known v1 limitation (README section 14): rebuilt in full on commit rather than
updated incrementally.
"""

from __future__ import annotations

import logging
import pickle
import re

log = logging.getLogger("ekb.bm25")

_TOKEN_RE = re.compile(r"\w+")


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


class BM25Store:
    def __init__(self, settings):
        self.settings = settings
        self.path = settings.index_dir / "bm25.pkl"
        self._bm25 = None
        self._chunk_ids: list[str] = []
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            with open(self.path, "rb") as fh:
                payload = pickle.load(fh)
            self._bm25 = payload["bm25"]
            self._chunk_ids = payload["chunk_ids"]
            log.info("bm25.loaded", extra={"documents": len(self._chunk_ids)})
        except Exception as exc:  # noqa: BLE001
            log.warning("bm25.load_failed", extra={"error": str(exc)[:200]})

    def persist(self) -> None:
        if self._bm25 is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "wb") as fh:
            pickle.dump({"bm25": self._bm25, "chunk_ids": self._chunk_ids}, fh)

    def rebuild(self, chunk_records) -> None:
        from rank_bm25 import BM25Okapi

        records = list(chunk_records)
        if not records:
            self._bm25, self._chunk_ids = None, []
            return
        self._chunk_ids = [r.chunk_id for r in records]
        self._bm25 = BM25Okapi([tokenize(r.content) for r in records])
        log.info("bm25.rebuilt", extra={"documents": len(self._chunk_ids)})

    def search(self, query: str, top_k: int) -> list[str]:
        if self._bm25 is None or not self._chunk_ids:
            return []
        scores = self._bm25.get_scores(tokenize(query))
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return [self._chunk_ids[i] for i in ranked[:top_k] if scores[i] > 0]

    @property
    def size(self) -> int:
        return len(self._chunk_ids)
