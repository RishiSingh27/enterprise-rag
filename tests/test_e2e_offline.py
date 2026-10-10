"""
End-to-end API test with Azure stubbed out (T050 complement).

Proves the three acceptance scenarios that define the system WITHOUT spending
any Azure credit:

  US-2.1  identical re-upload      -> duplicate, embedding_calls == 0
  US-2.2  one page edited          -> only that page reprocessed
  US-1.2  question outside corpus  -> abstention, never an invented answer

Embeddings are replaced by a deterministic hash-based vector, so retrieval is
exercised for real while the LLM/embedding endpoints are never called.
"""

import hashlib
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp()
os.environ["DATA_DIR"] = TMP
os.environ["REGISTRY_URL"] = f"sqlite:///{TMP}/registry.db"
os.environ["PARSER_BACKEND"] = "local"
os.environ["ENABLE_MULTIMODAL"] = "false"
os.environ["ENABLE_RERANK"] = "false"
os.environ["EMBEDDING_DIMENSION"] = "64"

from fastapi.testclient import TestClient  # noqa: E402

from src.backend.app.api import deps  # noqa: E402
from src.backend.app.core.config import get_settings  # noqa: E402
from src.backend.main import app  # noqa: E402


class FakeEmbedder:
    """Deterministic pseudo-embeddings. Counts calls so we can assert on cost."""

    def __init__(self):
        self.calls = 0

    def _vec(self, text: str):
        digest = hashlib.sha256(text.lower().encode()).digest()
        raw = [(digest[i % len(digest)] - 128) / 128 for i in range(64)]
        mag = sum(v * v for v in raw) ** 0.5 or 1.0
        return [v / mag for v in raw]

    def embed_batch(self, texts):
        self.calls += len(texts)
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


FAKE = FakeEmbedder()
deps.get_embedder.cache_clear()
deps.get_embedder = lambda: FAKE          # type: ignore[assignment]


@pytest.fixture(scope="module")
def client():
    get_settings.cache_clear()
    with TestClient(app) as c:
        yield c


def _write(path: Path, body: str) -> bytes:
    path.write_text(body, encoding="utf-8")
    return path.read_bytes()


DOC_V1 = """# HR Policy 2026

## Earned Leave
Employees accrue 21 days of earned leave per calendar year.
A maximum of 10 days may be carried forward into the next year.

## Emergency Leave
Emergency leave must be approved within 48 hours of being taken.
"""

DOC_V2 = DOC_V1.replace("10 days may be carried", "15 days may be carried")


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_first_upload_indexes_content(client, tmp_path_factory):
    folder = tmp_path_factory.mktemp("docs")
    data = _write(folder / "hr_policy.md", DOC_V1)

    before = FAKE.calls
    r = client.post("/api/v1/ingest", files={"file": ("hr_policy.md", data, "text/markdown")})
    assert r.status_code == 200

    body = r.json()
    assert body["outcome"] == "new"
    assert body["chunks_created"] > 0
    assert FAKE.calls > before          # it really did embed


def test_identical_reupload_costs_nothing(client, tmp_path_factory):
    """US-2.1 — the headline claim of the hashing design."""
    folder = tmp_path_factory.mktemp("docs2")
    data = _write(folder / "hr_policy.md", DOC_V1)

    before = FAKE.calls
    r = client.post("/api/v1/ingest", files={"file": ("hr_policy.md", data, "text/markdown")})
    body = r.json()

    assert body["outcome"] == "duplicate"
    assert body["embedding_calls"] == 0
    assert FAKE.calls == before          # not a single embedding call


def test_edited_document_reprocesses_only_changed_content(client, tmp_path_factory):
    """US-2.2 — a changed line must not re-embed the whole document."""
    folder = tmp_path_factory.mktemp("docs3")
    data = _write(folder / "hr_policy.md", DOC_V2)

    r = client.post("/api/v1/ingest", files={"file": ("hr_policy.md", data, "text/markdown")})
    body = r.json()

    assert body["outcome"] == "updated"
    assert body["chunks_reused"] >= 1     # untouched chunks were reused
    assert body["doc_hash"] != ""


def test_unsupported_file_is_rejected(client, tmp_path_factory):
    folder = tmp_path_factory.mktemp("docs4")
    data = _write(folder / "notes.exe", "binary-ish")

    body = client.post(
        "/api/v1/ingest", files={"file": ("notes.exe", data, "application/octet-stream")}
    ).json()
    assert body["outcome"] == "rejected"
    assert "Unsupported" in body["message"]


def test_out_of_corpus_question_abstains(client):
    """US-1.2 / FR-025 — no invented answers, ever."""
    body = client.post(
        "/api/v1/query",
        json={"question": "What is our Mars colonisation budget for 2027?"},
    ).json()

    assert body["abstained"] is True
    assert "could not find" in body["answer"].lower()
    assert body["sources"] == []


def test_stats_report_reuse(client):
    body = client.get("/api/v1/stats").json()
    assert body["documents"] >= 1
    assert body["active_chunks"] >= 1
    assert body["embedding_model"] == "text-embedding-3-large"
    assert body["llm_model"] == "gpt-4.1-mini"


def test_documents_listing(client):
    body = client.get("/api/v1/documents", params={"limit": 10}).json()
    assert body["total"] >= 1
    assert any(d["filename"] == "hr_policy.md" for d in body["items"])
