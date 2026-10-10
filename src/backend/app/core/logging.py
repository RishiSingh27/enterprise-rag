"""
Structured JSON logging with trace correlation (T006).

Constitution Article V.2 / VI: log IDENTIFIERS, never payloads. Document text
and user questions must never reach the log stream.
"""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar

_trace_id: ContextVar[str] = ContextVar("trace_id", default="")

# Anything matching these keys is dropped before serialisation.
_FORBIDDEN_KEYS = {"content", "text", "question", "answer", "snippet", "body", "api_key"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "trace_id": _trace_id.get() or getattr(record, "trace_id", ""),
        }
        for key, value in record.__dict__.items():
            if key in _FORBIDDEN_KEYS:
                continue
            if key not in logging.LogRecord("", 0, "", 0, "", (), None).__dict__:
                if key not in payload and not key.startswith("_"):
                    payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> logging.Logger:
    root = logging.getLogger()
    root.setLevel(level.upper())
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    logging.getLogger("uvicorn.access").handlers.clear()
    return logging.getLogger("ekb")


def set_trace_id(value: str | None = None) -> str:
    tid = value or str(uuid.uuid4())
    _trace_id.set(tid)
    return tid


def get_trace_id() -> str:
    return _trace_id.get()


@contextmanager
def stage_timer(logger: logging.Logger, stage: str, **fields):
    """Per-stage timing for NFR-001/002 (T060)."""
    started = time.perf_counter()
    try:
        yield
    finally:
        logger.info(
            f"stage.{stage}",
            extra={"stage": stage, "duration_ms": int((time.perf_counter() - started) * 1000), **fields},
        )
