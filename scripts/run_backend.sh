#!/usr/bin/env bash
# Start the FastAPI backend. Activate the venv first.
set -e
uvicorn src.backend.main:app --host 0.0.0.0 --port 8000 --reload
