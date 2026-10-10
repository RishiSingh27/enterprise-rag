/* ==========================================================================
   api.js — the ONLY module that talks to the backend.
   --------------------------------------------------------------------------
   Constitution Article IV: the frontend consumes the published HTTP contract
   in contracts/openapi.yaml and nothing else. Keeping every fetch in one file
   means a contract change touches exactly one place.
   ========================================================================== */

const API = (() => {

  // Change this if the backend runs elsewhere. Kept as a single constant so
  // there is one place to point at a deployed host.
  const BASE = window.EKB_BACKEND_URL || 'http://localhost:8000';
  const V1   = `${BASE}/api/v1`;

  const TIMEOUT_QUERY  = 120000;   // generation can legitimately take a while
  const TIMEOUT_UPLOAD = 900000;   // a large PDF through DI + vision is slow
  const TIMEOUT_FAST   = 15000;

  /** fetch with an abort-based timeout, since fetch() has none natively. */
  async function request(url, options = {}, timeout = TIMEOUT_FAST) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const res = await fetch(url, { ...options, signal: controller.signal });
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
          const body = await res.json();
          detail = body.detail || body.error || detail;
          // FastAPI validation errors arrive as a list of objects
          if (Array.isArray(detail)) detail = detail.map(d => d.msg).join('; ');
        } catch { /* response had no JSON body */ }
        throw new Error(detail);
      }
      return res.status === 204 ? null : await res.json();
    } catch (err) {
      if (err.name === 'AbortError') throw new Error('Request timed out.');
      throw err;
    } finally {
      clearTimeout(timer);
    }
  }

  return {
    BASE,

    /** GET /ready — returns {ready, checks} and never throws. */
    async ready() {
      try {
        const res = await fetch(`${BASE}/ready`, { signal: AbortSignal.timeout(8000) });
        const checks = await res.json().catch(() => ({}));
        return { ready: res.ok, checks };
      } catch {
        return { ready: false, checks: {} };
      }
    },

    /** GET /api/v1/stats */
    stats() {
      return request(`${V1}/stats`);
    },

    /** GET /api/v1/documents */
    documents(limit = 100) {
      return request(`${V1}/documents?limit=${limit}`);
    },

    /** DELETE /api/v1/documents/{id} */
    deleteDocument(docId) {
      return request(`${V1}/documents/${docId}`, { method: 'DELETE' });
    },

    /**
     * POST /api/v1/ingest
     * Uses XMLHttpRequest rather than fetch because only XHR exposes real
     * upload progress events, and a large PDF needs a progress bar.
     */
    ingest(file, onProgress) {
      return new Promise((resolve, reject) => {
        const form = new FormData();
        form.append('file', file, file.name);

        const xhr = new XMLHttpRequest();
        xhr.open('POST', `${V1}/ingest`);
        xhr.timeout = TIMEOUT_UPLOAD;

        xhr.upload.onprogress = e => {
          if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
        };
        xhr.onload = () => {
          try {
            const body = JSON.parse(xhr.responseText);
            // Ingestion reports are returned with HTTP 200 even for the
            // `rejected` and `duplicate` outcomes — the report IS the contract.
            resolve(body);
          } catch {
            reject(new Error(`Malformed response (HTTP ${xhr.status})`));
          }
        };
        xhr.onerror   = () => reject(new Error('Network error during upload.'));
        xhr.ontimeout = () => reject(new Error('Upload timed out.'));
        xhr.send(form);
      });
    },

    /**
     * POST /api/v1/query
     * `question` is padded to the 3-character schema minimum so that a
     * greeting like "Hi" is answered rather than rejected with a 422. The
     * padding is a transport detail; the UI always shows what the user typed.
     */
    query(question, topK = 5, docIds = null) {
      const payload = {
        question: question.length < 3 ? question.padEnd(3) : question,
        top_k: topK,
        trace_id: crypto.randomUUID(),
      };
      if (docIds && docIds.length) payload.doc_ids = docIds;

      return request(`${V1}/query`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      }, TIMEOUT_QUERY);
    },

    /**
     * POST /api/v1/query/stream — server-sent events.
     * onToken(text) fires per chunk; resolves with the final sources payload.
     */
    async queryStream(question, topK, onToken) {
      const payload = {
        question: question.length < 3 ? question.padEnd(3) : question,
        top_k: topK,
        trace_id: crypto.randomUUID(),
      };

      const res = await fetch(`${V1}/query/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const reader  = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '', meta = { sources: [], abstained: false, top_score: 0 };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const frames = buffer.split('\n\n');
        buffer = frames.pop();                       // keep the partial frame

        for (const frame of frames) {
          const isSources = frame.includes('event: sources');
          const line = frame.split('\n').find(l => l.startsWith('data: '));
          if (!line) continue;
          try {
            const data = JSON.parse(line.slice(6));
            if (isSources) meta = { ...meta, ...data };
            else if (data.token) onToken(data.token);
          } catch { /* ignore malformed frame */ }
        }
      }
      return meta;
    },

    /** Resolve a backend-side image path to a URL the browser can load. */
    imageUrl(path) {
      if (!path) return null;
      if (/^https?:\/\//i.test(path)) return path;
      const name = String(path).replace(/\\/g, '/').split('/').pop();
      return `${BASE}/images/${encodeURIComponent(name)}`;
    },
  };
})();
