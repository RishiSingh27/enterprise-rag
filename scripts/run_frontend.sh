#!/usr/bin/env bash
# Start the Streamlit frontend. Activate the venv first.
set -e
streamlit run src/frontend/app.py --server.port 8501
