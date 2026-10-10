"""
Dependency wiring — single composition root.

Stores are expensive to build (FAISS index load, BM25 unpickle), so they are
module-level singletons. The Repository is per-request because it holds a
database session and a transaction boundary.
"""

from __future__ import annotations

from functools import lru_cache

from ..core.config import get_settings
from ..ingestion.embedder import Embedder
from ..ingestion.multimodal import MultimodalDescriber
from ..ingestion.parser import DocumentParser
from ..ingestion.pipeline import IngestionPipeline
from ..models.repository import Repository
from ..retrieval.bm25_store import BM25Store
from ..retrieval.generator import Generator, Reranker
from ..retrieval.retriever import HybridRetriever
from ..retrieval.vector_store import VectorStore


@lru_cache
def get_vector_store() -> VectorStore:
    return VectorStore(get_settings())


@lru_cache
def get_bm25_store() -> BM25Store:
    return BM25Store(get_settings())


@lru_cache
def get_embedder() -> Embedder:
    return Embedder(get_settings())


@lru_cache
def get_parser() -> DocumentParser:
    settings = get_settings()
    describer = MultimodalDescriber(settings) if settings.enable_multimodal else None
    return DocumentParser(settings, multimodal=describer)


@lru_cache
def get_generator() -> Generator:
    return Generator(get_settings())


@lru_cache
def get_reranker() -> Reranker:
    return Reranker(get_settings())


def get_repository() -> Repository:
    """Per-request: owns a session and the transaction boundary."""
    repo = Repository()
    try:
        yield repo
    finally:
        repo.close()


def build_pipeline(repo: Repository) -> IngestionPipeline:
    import logging
    return IngestionPipeline(
        parser=get_parser(),
        embedder=get_embedder(),
        vector_store=get_vector_store(),
        bm25_store=get_bm25_store(),
        repository=repo,
        settings=get_settings(),
        logger=logging.getLogger("ekb.pipeline"),
    )


def build_retriever(repo: Repository) -> HybridRetriever:
    return HybridRetriever(
        embedder=get_embedder(),
        vector_store=get_vector_store(),
        bm25_store=get_bm25_store(),
        repository=repo,
        settings=get_settings(),
        reranker=get_reranker(),
    )
