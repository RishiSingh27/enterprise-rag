# Backend changes required for the HTML frontend

Two changes to `src/backend/main.py`. Nothing else in the backend moves —
the API contract is unchanged, so `routes_*.py`, the pipeline and the
retrieval layer are all untouched.

---

## 1. CORS — allow the new frontend origin

The Streamlit build ran on port **8501**; the static frontend runs on **5500**.
The browser will block every request until that origin is allowed.

**Find this block in `main.py`:**

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501"],
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**Replace with:**

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5500",   # static frontend (Live Server / http.server)
        "http://127.0.0.1:5500",
        "http://localhost:8501",   # Streamlit, kept during transition
        "http://localhost:3000",   # optional: Node dev server
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Trace-Id", "X-Duration-Ms"],
)
```

> Do **not** use `allow_origins=["*"]`. With `allow_credentials=True` browsers
> reject the wildcard, and an open CORS policy on an internal knowledge base is
> a finding waiting to happen at review (Constitution Article V).

---

## 2. Serve cropped figures

Source cards show the chart or figure a citation came from. Those PNGs live in
`data/images/` on the server, and a browser cannot read a local disk path — it
needs an HTTP URL. `api.js` calls `GET /images/{filename}`.

**Add near the top of `main.py`, with the other imports:**

```python
from fastapi.staticfiles import StaticFiles
```

**Add after the `app.add_middleware(...)` block:**

```python
# Serve cropped figure images so the UI can display the visual a citation
# came from. Read-only, and scoped to the images directory alone.
settings.images_dir.mkdir(parents=True, exist_ok=True)
app.mount("/images", StaticFiles(directory=str(settings.images_dir)), name="images")
```

`StaticFiles` refuses paths that escape the mounted directory, so this cannot
be used to read arbitrary files off the host.

---

## 3. Optional — serve the frontend from FastAPI itself

Useful for a single-process demo: one URL, no second server, no CORS at all.

**Add at the very END of `main.py`, after the routers are included:**

```python
# Serve the static frontend from the same process. MUST be mounted last —
# the "/" mount is a catch-all and would otherwise shadow the API routes.
_frontend = Path(__file__).resolve().parents[1] / "frontend"
if _frontend.exists():
    app.mount("/", StaticFiles(directory=str(_frontend), html=True), name="frontend")
```

with `from pathlib import Path` imported at the top.

The app is then at <http://localhost:8000> and you only run one command.
Mount order matters: put this after `include_router`, or `/api/v1/query` will
resolve to the static handler and 404.

---

## Verify

```powershell
python -m uvicorn src.backend.main:app --reload --port 8000
```

- <http://localhost:8000/docs> — all 8 endpoints still listed
- <http://localhost:8000/images/> — 404 is fine; a 500 means the mount failed
- Open the frontend, press F12 → Console. No CORS errors, sidebar tiles populate.

A CORS failure looks like:
`Access to fetch at 'http://localhost:8000/api/v1/stats' from origin
'http://localhost:5500' has been blocked by CORS policy`
— that means step 1 was missed or the backend was not restarted.
