"""
Central configuration (T003).

All settings come from environment variables / .env. No secret is ever hardcoded
or committed (Constitution Article V, NFR-004).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ---- App ----
    app_name: str = "Enterprise Knowledge Assistant"
    app_version: str = "1.0.0"
    log_level: str = "INFO"

    # ---- Azure AI Foundry ----
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_api_version: str = "2024-10-21"
    azure_chat_deployment: str = "gpt-4.1-mini"
    azure_embedding_deployment: str = "text-embedding-3-large"
    embedding_dimension: int = 3072

    # ---- Document Intelligence ----
    azure_doc_intelligence_endpoint: str = ""
    azure_doc_intelligence_key: str = ""
    parser_backend: str = "azure"          # azure | local
    enable_multimodal: bool = True
    enable_ocr: bool = True

    # ---- Chunking (plan.md section 7) ----
    target_chunk_tokens: int = 800
    max_chunk_tokens: int = 1000
    chunk_overlap_tokens: int = 120

    # ---- Retrieval (plan.md section 8) ----
    dense_top_k: int = 20
    sparse_top_k: int = 20
    final_top_k: int = 5
    rrf_k: int = 60
    retrieval_min_score: float = 0.35       # FR-025 abstention gate
    enable_rerank: bool = True

    # ---- Generation ----
    llm_temperature: float = 0.2            # Constitution Article I
    llm_max_tokens: int = 1200
    answer_mode: str = "hybrid"

    # ---- Storage ----
    data_dir: Path = Field(default=Path("./data"))
    registry_url: str = "sqlite:///./data/registry.db"

    # ---- Ingestion limits ----
    max_upload_mb: int = 100
    embedding_batch_size: int = 64
    supported_extensions: tuple[str, ...] = (
        ".pdf", ".docx", ".pptx", ".xlsx", ".txt", ".md", ".png", ".jpg", ".jpeg",
    )

    # ---- Frontend ----
    backend_url: str = "http://localhost:8000"

    # ---- Derived paths ----
    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def index_dir(self) -> Path:
        return self.data_dir / "index"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.uploads_dir, self.images_dir, self.index_dir):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
