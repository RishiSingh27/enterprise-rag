# Quickstart — Run it in 10 minutes

**No Docker required.** Everything runs in a Python virtual environment.

---

## 1. Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.11+ | `python --version` |
| Azure AI Foundry project | With the two deployments below |
| `gpt-4.1-mini` deployment | Generation + vision |
| `text-embedding-3-large` deployment | 3072-dim embeddings |
| Azure AI Document Intelligence resource | Optional — local fallback available |
| ~2 GB free disk | Index + uploads |

---

## 2. Set up the environment

**Windows (PowerShell)**
```powershell
cd enterprise-rag
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

**macOS / Linux**
```bash
cd enterprise-rag
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

To leave the environment later: `deactivate`.

> **Behind a corporate proxy?** If pip fails with an SSL error, point it at your
> corporate CA bundle:
> `pip install -r requirements.txt --cert /path/to/corp-ca.pem`
> and set `REQUESTS_CA_BUNDLE` / `SSL_CERT_FILE` to the same path in `.env`.

---

## 3. Configure credentials

```bash
cp .env.example .env        # Windows: copy .env.example .env
```

Fill in `.env`:

```ini
AZURE_OPENAI_ENDPOINT=https://<your-foundry>.openai.azure.com/
AZURE_OPENAI_API_KEY=<key>
AZURE_OPENAI_API_VERSION=2024-10-21
AZURE_CHAT_DEPLOYMENT=gpt-4.1-mini
AZURE_EMBEDDING_DEPLOYMENT=text-embedding-3-large

AZURE_DOC_INTELLIGENCE_ENDPOINT=https://<your-di>.cognitiveservices.azure.com/
AZURE_DOC_INTELLIGENCE_KEY=<key>

PARSER_BACKEND=azure          # azure | local
RETRIEVAL_MIN_SCORE=0.35
TARGET_CHUNK_TOKENS=800
CHUNK_OVERLAP_TOKENS=120
```

Where to find these: Azure AI Foundry → your project → **Deployments** for the
model names, and **Keys and Endpoint** for the endpoint/key.

> `.env` is git-ignored. Never commit it.

---

## 4. Initialise storage

```bash
python -m src.backend.app.models.db --init
```

Creates `data/registry.db`, `data/index/`, `data/uploads/`, `data/images/`.

---

## 5. Start the backend

```bash
uvicorn src.backend.main:app --reload --port 8000
```

Verify:
```bash
curl http://localhost:8000/health   # {"status":"ok"}
curl http://localhost:8000/ready    # all four flags true
```

Interactive API docs: <http://localhost:8000/docs>

---

## 6. Start the frontend

In a **second terminal** (activate the venv again):

```bash
streamlit run src/frontend/app.py --server.port 8501
```

Open <http://localhost:8501>.

---

## 7. Verify the system in 6 checks

| # | Action | Expected result |
|---|---|---|
| 1 | Upload a PDF with text, a table and a chart | `outcome: new`, pages and chunks reported |
| 2 | Upload the **exact same file** again | `outcome: duplicate`, **`embedding_calls: 0`** |
| 3 | Edit one line on page 7, re-upload | `pages_modified: 1`, `changed_page_numbers: [7]`, single-digit `embedding_calls` |
| 4 | Ask a question answered by a **table** | Correct value + `page N` citation |
| 5 | Ask a question answered by a **chart** | Correct answer from the generated description + citation |
| 6 | Ask something not in any document | Explicit "not found in the knowledge base", no invented answer |

Checks 2 and 3 are the proof of the hashing design. Check 6 is the proof of the
grounding guarantee.

---

## 8. Bulk load a folder

```bash
python scripts/bulk_ingest.py --path ./my_documents --recursive
```

---

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `SSLError` / `CERTIFICATE_VERIFY_FAILED` | Corporate TLS inspection (Zscaler/Netskope) | Set `REQUESTS_CA_BUNDLE` and `SSL_CERT_FILE` to the corporate CA bundle in `.env` |
| `401 Unauthorized` from Azure | Wrong key or endpoint region | Re-copy from Foundry → Keys and Endpoint |
| `DeploymentNotFound` | Deployment **name** ≠ model name | Use your deployment name in `AZURE_*_DEPLOYMENT` |
| `429 Too Many Requests` | Quota exceeded | Built-in backoff will retry; raise TPM quota in Foundry |
| Every page reports `modified` on re-upload | Footer timestamp defeating normalisation | Add the pattern to the header/footer strip list in `hashing.py` |
| Document Intelligence unreachable | Network policy | Set `PARSER_BACKEND=local` |
| Streamlit cannot reach backend | Backend not running / wrong port | Confirm `BACKEND_URL` in `.env` matches uvicorn's port |
| `ModuleNotFoundError` | venv not activated | Re-activate; confirm prompt shows `(.venv)` |

---

## 10. Daily use

```bash
# Terminal 1
source .venv/bin/activate && uvicorn src.backend.main:app --port 8000

# Terminal 2
source .venv/bin/activate && streamlit run src/frontend/app.py
```

Or use `scripts/run_backend.sh` and `scripts/run_frontend.sh`.
