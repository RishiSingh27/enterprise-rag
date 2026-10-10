"""
FastAPI application factory (T008, T009).

Run:  python -m uvicorn src.backend.main:app --reload --port 8000
Docs: http://localhost:8000/docs   (generated from the same contract)
App:  http://localhost:8000/app    (static HTML/CSS/JS frontend)

No Dockerfile — Constitution Article VII.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .app.core.config import get_settings

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Enterprise RAG API — grounded, cited answers from internal documents.",
    docs_url="/docs",
    openapi_url="/openapi.json",
)

# --------------------------------------------------------------------------- #
# CORS
# --------------------------------------------------------------------------- #
# Named origins only, never "*" (Constitution Article V). A wildcard is also
# rejected by browsers when allow_credentials is True, so it would not work
# even if it were acceptable.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5500",    # static frontend — python -m http.server
        "http://127.0.0.1:5500",
        "http://localhost:5501",    # VS Code Live Server default
        "http://127.0.0.1:5501",
        "http://localhost:3000",    # optional Node dev server
        "http://localhost:8000",    # frontend served by this app
        "http://localhost:8501",    # Streamlit, kept during transition
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Trace-Id", "X-Duration-Ms"],
)


@app.middleware("http")
async def trace_and_time(request: Request, call_next):
    """Constitution Article VI: every request carries a trace_id and a timing."""
    trace_id = request.headers.get("X-Trace-Id") or str(uuid.uuid4())
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Trace-Id"] = trace_id
    response.headers["X-Duration-Ms"] = str(int((time.perf_counter() - started) * 1000))
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Never leak document content or stack internals to the client.
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "detail": "An unexpected error occurred.",
            "trace_id": request.headers.get("X-Trace-Id", ""),
        },
    )


@app.get("/health", tags=["Admin"])
async def health():
    """Liveness probe (FR-030)."""
    return {"status": "ok", "version": settings.app_version}


@app.get("/ready", tags=["Admin"])
async def ready():
    """
    Readiness probe (FR-030). Verifies the dependencies we cannot serve without:
    vector index, registry, and both Azure AI Foundry deployments.
    """
    checks = {
        "vector_index": settings.index_dir.exists(),
        "registry": bool(settings.registry_url),
        "azure_embeddings": bool(
            settings.azure_openai_endpoint and settings.azure_embedding_deployment
        ),
        "azure_llm": bool(
            settings.azure_openai_endpoint and settings.azure_chat_deployment
        ),
    }
    ok = all(checks.values())
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"status": "ready" if ok else "not_ready", **checks},
    )


# --------------------------------------------------------------------------- #
# Routers (T052-T054)
# --------------------------------------------------------------------------- #
from .app.api import routes_admin, routes_ingest, routes_query  # noqa: E402

app.include_router(routes_ingest.router, prefix="/api/v1")
app.include_router(routes_query.router, prefix="/api/v1")
app.include_router(routes_admin.router, prefix="/api/v1")


# --------------------------------------------------------------------------- #
# Static mounts — MUST come after the routers
# --------------------------------------------------------------------------- #
# Mount order matters: a mount is a catch-all for its prefix, so mounting
# before include_router would shadow the API routes and return 404s.

# Cropped figure images, so the UI can show the visual a citation came from.
# StaticFiles refuses paths that escape the mounted directory, so this cannot
# be used to read arbitrary files from the host.
settings.images_dir.mkdir(parents=True, exist_ok=True)
app.mount("/images", StaticFiles(directory=str(settings.images_dir)), name="images")

# The HTML/CSS/JS frontend, served from this same process at /app.
# Serving it here means one command, one origin, and no CORS at all —
# which is the simplest thing to demo.
_frontend_dir = Path(__file__).resolve().parents[1] / "frontend"
if _frontend_dir.exists():
    app.mount("/app", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")


@app.on_event("startup")
async def _startup() -> None:
    """Create tables and data directories on first run."""
    from .app.core.logging import configure_logging
    from .app.models.db import init_db

    configure_logging(settings.log_level)
    init_db()
