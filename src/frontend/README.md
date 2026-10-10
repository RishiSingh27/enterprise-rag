# Frontend — Enterprise Knowledge Assistant

Production UI built with plain **HTML, CSS and JavaScript**. No framework,
no build step, no `node_modules`. Three files do the work.

```
src/frontend/
├── index.html          markup + app shell
├── css/styles.css      themes, layout, animation, responsive rules
├── js/api.js           every backend call lives here — and only here
├── js/app.js           UI behaviour
├── BACKEND_CHANGES.md  the two edits main.py needs
└── README.md
```

---

## Run it

The frontend must be served over **HTTP**, not opened as a `file://` path —
browsers block `fetch()` from `file://` origins.

**Option A — Python (already installed):**
```powershell
cd src\frontend
python -m http.server 5500
```
Open <http://localhost:5500>

**Option B — VS Code Live Server:** right-click `index.html` → *Open with Live
Server*. Confirm it uses port **5500**; if not, add your port to the CORS list
in `main.py`.

**Option C — one process:** follow step 3 of `BACKEND_CHANGES.md` and the whole
app is served from <http://localhost:8000>.

Backend must be running:
```powershell
python -m uvicorn src.backend.main:app --reload --port 8000
```

---

## What it does

**Four themes** — Midnight, Daylight, Slate, Azure. Implemented as CSS custom
properties on `[data-theme]`, so switching re-skins everything with no layout
reflow. Choice persists in `localStorage`.

**Chat** — markdown rendering (bold, lists, tables, code), citations
highlighted as pills, typing indicator while the answer is generated,
auto-growing textarea, Enter to send and Shift+Enter for a newline.

**Source cards** — document, page badge, section breadcrumb, snippet, and a
relevance bar. When a citation came from a figure, the cropped image is shown
and opens in a lightbox.

**Upload** — drag-and-drop or click, a removable queue, and styled ingestion
reports. A duplicate reads *"Already indexed — 0 embedding calls, no cost"*,
which is the hashing design's value stated in plain language.

**Live metrics** — documents, pages, chunks and *Embeds saved*, refreshed after
each ingest with a brief count-up animation. Status pill polls `/ready` every
30 seconds.

---

## Animation

Motion is functional, never decorative. Messages slide in so a new one is
noticed; the orb ripples only on the empty state; metric tiles bump when a
number changes; source cards nudge on hover to signal they are interactive.

Everything is wrapped in:

```css
@media (prefers-reduced-motion: reduce) { /* animations reduced to 0.01ms */ }
```

so users who disable motion at the OS level get a static interface. This is an
accessibility requirement, not a nicety.

---

## Responsive

| Breakpoint | Behaviour |
|---|---|
| > 1024px | Full two-column layout, 330px sidebar |
| ≤ 1024px | Horizontal padding tightens |
| ≤ 860px | Sidebar becomes an off-canvas drawer with a scrim; hamburger appears |
| ≤ 560px | Single-column theme picker, smaller avatars, toasts span the width |

Also handled: `print` styles that hide the sidebar and composer, `100dvh` so
mobile browser chrome doesn't clip the composer, and visible focus rings
throughout for keyboard navigation.

---

## Configuration

The backend URL is one constant in `js/api.js`:

```js
const BASE = window.EKB_BACKEND_URL || 'http://localhost:8000';
```

To point at a deployed backend without editing the file, set it in
`index.html` before the scripts load:

```html
<script>window.EKB_BACKEND_URL = 'https://rag-api.internal.corp';</script>
```

---

## Security notes

- All model output is HTML-escaped **before** markdown rendering, so nothing
  returned by the LLM can inject markup.
- The markdown renderer is intentionally minimal — it supports only what the
  answer prompt can emit. A general-purpose parser would be a larger attack
  surface for no benefit.
- No secrets, keys or endpoints are held in frontend code.
- CORS is pinned to named origins rather than `*` (Constitution Article V).
