"""
Azure AI Foundry client factories (T007).

Constitution Article V.3: all inference stays inside the corporate tenant.
Clients are cached singletons — creating them per request wastes connections.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from .config import get_settings

log = logging.getLogger("ekb.azure")


@lru_cache
def get_openai_client():
    """Azure OpenAI client serving BOTH gpt-4.1-mini and text-embedding-3-large."""
    from openai import AzureOpenAI

    s = get_settings()
    if not s.azure_openai_endpoint or not s.azure_openai_api_key:
        raise RuntimeError(
            "Azure OpenAI not configured. Set AZURE_OPENAI_ENDPOINT and "
            "AZURE_OPENAI_API_KEY in your .env file."
        )
    return AzureOpenAI(
        azure_endpoint=s.azure_openai_endpoint,
        api_key=s.azure_openai_api_key,
        api_version=s.azure_openai_api_version,
        max_retries=3,
    )


@lru_cache
def get_document_intelligence_client():
    """Layout parser client. Returns None when DI is not configured."""
    s = get_settings()
    if not s.azure_doc_intelligence_endpoint or not s.azure_doc_intelligence_key:
        log.warning("doc_intelligence.unconfigured", extra={"fallback": "local parser"})
        return None

    from azure.ai.documentintelligence import DocumentIntelligenceClient
    from azure.core.credentials import AzureKeyCredential

    return DocumentIntelligenceClient(
        endpoint=s.azure_doc_intelligence_endpoint,
        credential=AzureKeyCredential(s.azure_doc_intelligence_key),
    )


def check_connectivity() -> dict[str, bool]:
    """Used by /ready. Never raises — reports status."""
    s = get_settings()
    status = {
        "azure_embeddings": False,
        "azure_llm": False,
        "doc_intelligence": False,
    }
    try:
        client = get_openai_client()
        client.embeddings.create(model=s.azure_embedding_deployment, input="ping")
        status["azure_embeddings"] = True
    except Exception as exc:  # noqa: BLE001
        log.warning("readiness.embeddings_failed", extra={"error": str(exc)[:200]})

    try:
        client = get_openai_client()
        client.chat.completions.create(
            model=s.azure_chat_deployment,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
        )
        status["azure_llm"] = True
    except Exception as exc:  # noqa: BLE001
        log.warning("readiness.llm_failed", extra={"error": str(exc)[:200]})

    status["doc_intelligence"] = get_document_intelligence_client() is not None
    return status
