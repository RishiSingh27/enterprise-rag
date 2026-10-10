/* ==========================================================================
   app.js — UI behaviour.
   --------------------------------------------------------------------------
   No framework, no build step. Plain ES modules-free script so it can be
   opened from any static host (or python -m http.server) with zero tooling.
   ========================================================================== */

(() => {
'use strict';

/* ---------- element lookup ---------- */
const $  = sel => document.querySelector(sel);
const $$ = sel => [...document.querySelectorAll(sel)];

const el = {
  html: document.documentElement,
  sidebar: $('#sidebar'), scrim: $('#scrim'),
  menuBtn: $('#menuBtn'), sidebarClose: $('#sidebarClose'),
  themeGrid: $('#themeGrid'),
  status: $('#status'), statusDetail: $('#statusDetail'), topBadge: $('#topBadge'),
  dropzone: $('#dropzone'), fileInput: $('#fileInput'),
  queue: $('#queue'), ingestBtn: $('#ingestBtn'), reports: $('#reports'),
  topK: $('#topK'), topKOut: $('#topKOut'),
  docsAccordion: $('#docsAccordion'), docList: $('#docList'),
  chat: $('#chat'), empty: $('#empty'), chips: $('#chips'),
  form: $('#form'), input: $('#input'), send: $('#send'),
  clearBtn: $('#clearBtn'), toasts: $('#toasts'),
  lightbox: $('#lightbox'), lightboxImg: $('#lightboxImg'), lightboxClose: $('#lightboxClose'),
};

const state = { files: [], busy: false, messages: [] };

/* ==========================================================================
   Utilities
   ========================================================================== */

const escapeHtml = s => String(s ?? '')
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;');

const bytes = n => n < 1024 ? `${n} B`
  : n < 1048576 ? `${(n / 1024).toFixed(0)} KB`
  : `${(n / 1048576).toFixed(1)} MB`;

function toast(message, kind = '') {
  const node = document.createElement('div');
  node.className = `toast ${kind ? 'toast--' + kind : ''}`;
  node.textContent = message;
  el.toasts.appendChild(node);
  setTimeout(() => {
    node.classList.add('is-out');
    node.addEventListener('animationend', () => node.remove());
  }, 4200);
}

/**
 * Minimal markdown renderer.
 *
 * Deliberately NOT a full parser — we only support what the answer prompt can
 * actually emit: bold, italic, inline code, headings, lists, GFM tables and
 * [Document, page N] citations. Input is escaped FIRST, so nothing the model
 * returns can inject markup.
 */
function renderMarkdown(src) {
  let t = escapeHtml(src);

  // fenced code
  t = t.replace(/```([\s\S]*?)```/g, (_, c) => `<pre><code>${c.trim()}</code></pre>`);

  // GFM tables
  t = t.replace(/(^\|.+\|\s*$\n^\|[\s:|-]+\|\s*$\n(?:^\|.*\|\s*$\n?)*)/gm, block => {
    const rows = block.trim().split('\n').map(r =>
      r.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim()));
    const [head, , ...body] = rows;
    const th = head.map(c => `<th>${c}</th>`).join('');
    const tb = body.map(r => `<tr>${r.map(c => `<td>${c}</td>`).join('')}</tr>`).join('');
    return `<table><thead><tr>${th}</tr></thead><tbody>${tb}</tbody></table>`;
  });

  t = t.replace(/^### (.+)$/gm, '<h4>$1</h4>')
       .replace(/^## (.+)$/gm,  '<h3>$1</h3>')
       .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
       .replace(/(^|[\s(])\*([^*\n]+)\*/g, '$1<em>$2</em>')
       .replace(/`([^`\n]+)`/g, '<code>$1</code>');

  // citations: [Some_Doc.pdf, page 3]
  t = t.replace(/\[([^\]]*?(?:\.pdf|\.docx|\.pptx|\.xlsx|\.txt|\.md)[^\]]*?)\]/gi,
                '<span class="cite">$1</span>');

  // lists
  t = t.replace(/(?:^[-*] .+$\n?)+/gm, b =>
        `<ul>${b.trim().split('\n').map(l => `<li>${l.replace(/^[-*]\s+/, '')}</li>`).join('')}</ul>`)
       .replace(/(?:^\d+\. .+$\n?)+/gm, b =>
        `<ol>${b.trim().split('\n').map(l => `<li>${l.replace(/^\d+\.\s+/, '')}</li>`).join('')}</ol>`);

  return t.split(/\n{2,}/)
          .map(p => /^<(h\d|ul|ol|pre|table)/.test(p.trim()) ? p : `<p>${p.replace(/\n/g, '<br>')}</p>`)
          .join('');
}

/* ==========================================================================
   Theme
   ========================================================================== */

function setTheme(name) {
  el.html.dataset.theme = name;
  try { localStorage.setItem('ekb-theme', name); } catch {}
  $$('.theme-swatch').forEach(b => {
    const active = b.dataset.theme === name;
    b.classList.toggle('is-active', active);
    b.setAttribute('aria-checked', String(active));
  });
}

el.themeGrid.addEventListener('click', e => {
  const btn = e.target.closest('.theme-swatch');
  if (btn) setTheme(btn.dataset.theme);
});

setTheme((() => {
  try { return localStorage.getItem('ekb-theme') || 'midnight'; } catch { return 'midnight'; }
})());

/* ==========================================================================
   Sidebar (mobile)
   ========================================================================== */

const openSidebar  = () => { el.sidebar.classList.add('is-open'); el.scrim.hidden = false; };
const closeSidebar = () => { el.sidebar.classList.remove('is-open'); el.scrim.hidden = true; };

el.menuBtn.addEventListener('click', openSidebar);
el.sidebarClose.addEventListener('click', closeSidebar);
el.scrim.addEventListener('click', closeSidebar);

/* ==========================================================================
   Status + stats polling
   ========================================================================== */

async function refreshStatus() {
  const { ready, checks } = await API.ready();
  const dot  = el.status.querySelector('.status__dot');
  const text = el.status.querySelector('.status__text');

  dot.className = `status__dot ${ready ? 'is-ok' : 'is-off'}`;
  text.textContent = ready ? 'All services operational' : 'Backend unavailable';
  el.topBadge.hidden = !ready;

  const failing = Object.entries(checks)
    .filter(([k, v]) => v === false && k !== 'status')
    .map(([k]) => k.replace(/_/g, ' '));

  if (!ready && failing.length) {
    el.statusDetail.hidden = false;
    el.statusDetail.textContent = `Not ready: ${failing.join(', ')}`;
  } else {
    el.statusDetail.hidden = true;
  }
}

async function refreshStats() {
  try {
    const s = await API.stats();
    for (const [key, value] of Object.entries(s)) {
      const node = document.querySelector(`[data-stat="${key}"]`);
      if (!node) continue;
      const next = String(value ?? '–');
      if (node.textContent !== next && node.textContent !== '–') {
        node.classList.remove('is-bump');
        void node.offsetWidth;                 // restart the animation
        node.classList.add('is-bump');
      }
      node.textContent = next;
    }
  } catch { /* status panel already surfaces the outage */ }
}

async function refreshDocs() {
  try {
    const { items = [] } = await API.documents();
    el.docList.innerHTML = items.length
      ? items.map(d => `
          <div class="doc-row">
            <div class="doc-row__name">${escapeHtml(d.filename)}</div>
            <div class="doc-row__meta">v${d.version_no || 1} · ${d.page_count || 0} pages · ${d.chunk_count || 0} chunks</div>
          </div>`).join('')
      : '<p class="muted sm">No documents indexed yet.</p>';
  } catch {
    el.docList.innerHTML = '<p class="muted sm">Could not load documents.</p>';
  }
}

function refreshAll() { refreshStatus(); refreshStats(); refreshDocs(); }

/* ==========================================================================
   Upload
   ========================================================================== */

const ALLOWED = ['pdf','docx','pptx','xlsx','txt','md','png','jpg','jpeg'];

function addFiles(list) {
  for (const file of list) {
    const ext = file.name.split('.').pop().toLowerCase();
    if (!ALLOWED.includes(ext)) { toast(`${file.name}: unsupported type`, 'err'); continue; }
    if (state.files.some(f => f.name === file.name && f.size === file.size)) continue;
    state.files.push(file);
  }
  renderQueue();
}

function renderQueue() {
  el.queue.innerHTML = state.files.map((f, i) => `
    <li class="queue__item">
      <span class="queue__name">${escapeHtml(f.name)}</span>
      <span class="queue__size">${bytes(f.size)}</span>
      <button class="queue__x" data-i="${i}" aria-label="Remove ${escapeHtml(f.name)}">
        <svg viewBox="0 0 24 24"><path d="M18 6L6 18M6 6l12 12"/></svg>
      </button>
    </li>`).join('');

  el.ingestBtn.hidden = state.files.length === 0;
  el.ingestBtn.querySelector('.btn__label').textContent =
    `Ingest ${state.files.length} file${state.files.length === 1 ? '' : 's'}`;
}

el.queue.addEventListener('click', e => {
  const btn = e.target.closest('.queue__x');
  if (!btn) return;
  state.files.splice(Number(btn.dataset.i), 1);
  renderQueue();
});

el.dropzone.addEventListener('click', () => el.fileInput.click());
el.dropzone.addEventListener('keydown', e => {
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); el.fileInput.click(); }
});
el.fileInput.addEventListener('change', e => { addFiles(e.target.files); e.target.value = ''; });

['dragenter', 'dragover'].forEach(type =>
  el.dropzone.addEventListener(type, e => {
    e.preventDefault(); el.dropzone.classList.add('is-over');
  }));
['dragleave', 'drop'].forEach(type =>
  el.dropzone.addEventListener(type, e => {
    e.preventDefault(); el.dropzone.classList.remove('is-over');
  }));
el.dropzone.addEventListener('drop', e => addFiles(e.dataTransfer.files));

el.ingestBtn.addEventListener('click', async () => {
  if (!state.files.length) return;
  el.ingestBtn.classList.add('is-busy');
  el.ingestBtn.disabled = true;
  el.reports.innerHTML = '';

  for (const file of state.files) {
    try {
      const report = await API.ingest(file);
      el.reports.insertAdjacentHTML('beforeend', reportCard(file.name, report));
    } catch (err) {
      el.reports.insertAdjacentHTML('beforeend', reportCard(file.name, {
        outcome: 'failed', message: err.message,
      }));
    }
  }

  state.files = [];
  renderQueue();
  el.ingestBtn.classList.remove('is-busy');
  el.ingestBtn.disabled = false;
  refreshAll();
});

function reportCard(name, r) {
  const outcome = r.outcome || 'failed';

  if (outcome === 'duplicate') {
    return `<div class="report report--info">
      <div class="report__title">${escapeHtml(name)}</div>
      <div class="report__meta">Already indexed — 0 embedding calls, no cost.</div>
    </div>`;
  }
  if (outcome === 'rejected' || outcome === 'failed') {
    return `<div class="report report--err">
      <div class="report__title">${escapeHtml(name)}</div>
      <div class="report__meta">${escapeHtml(r.message || 'Unknown error')}</div>
    </div>`;
  }

  const changed = r.changed_page_numbers?.length
    ? `<br>Pages changed: [${r.changed_page_numbers.join(', ')}]` : '';
  return `<div class="report report--ok">
    <div class="report__title">${escapeHtml(name)} — ${escapeHtml(outcome)}</div>
    <div class="report__meta">
      ${r.chunks_created ?? 0} embedded · ${r.chunks_reused ?? 0} reused · ${r.duration_seconds ?? 0}s${changed}
    </div>
  </div>`;
}

/* ==========================================================================
   Chat
   ========================================================================== */

el.topK.addEventListener('input', () => { el.topKOut.textContent = el.topK.value; });

function hideEmpty() { if (el.empty) { el.empty.remove(); el.empty = null; } }

function addMessage(role, html) {
  hideEmpty();
  const node = document.createElement('div');
  node.className = `msg msg--${role}`;
  node.innerHTML = `
    <div class="msg__avatar">${role === 'user' ? 'You' : 'EK'}</div>
    <div class="msg__body"><div class="msg__bubble">${html}</div></div>`;
  el.chat.appendChild(node);
  el.chat.scrollTop = el.chat.scrollHeight;
  el.clearBtn.hidden = false;
  return node;
}

function sourcesBlock(sources) {
  if (!sources?.length) return '';
  const cards = sources.map(s => {
    const pct = Math.round(Math.min(1, Math.max(0, s.score || 0)) * 100);
    const img = API.imageUrl(s.image_path);
    return `<div class="src">
      <div class="src__top">
        <span class="src__doc">${escapeHtml(s.document || '—')}</span>
        <span class="src__page">Page ${s.page_number ?? '—'}</span>
      </div>
      ${s.breadcrumb ? `<div class="src__crumb">${escapeHtml(s.breadcrumb)}</div>` : ''}
      <div class="src__text">${escapeHtml((s.snippet || '').slice(0, 400))}…</div>
      ${img ? `<img class="src__thumb" src="${img}" alt="Figure from page ${s.page_number}" loading="lazy" onerror="this.remove()">` : ''}
      <div class="src__foot">
        <span>${(s.score ?? 0).toFixed(3)}</span>
        <span class="src__bar"><i style="width:${pct}%"></i></span>
        <span>${escapeHtml(s.element_types || 'text')}</span>
      </div>
    </div>`;
  }).join('');

  return `<div class="sources">
    <button class="sources__toggle">
      Sources (${sources.length})
      <svg viewBox="0 0 24 24"><path d="M6 9l6 6 6-6"/></svg>
    </button>
    <div class="sources__list">${cards}</div>
  </div>`;
}

el.chat.addEventListener('click', e => {
  const toggle = e.target.closest('.sources__toggle');
  if (toggle) toggle.closest('.sources').classList.toggle('is-open');

  const thumb = e.target.closest('.src__thumb');
  if (thumb) { el.lightboxImg.src = thumb.src; el.lightbox.hidden = false; }
});

const closeLightbox = () => { el.lightbox.hidden = true; el.lightboxImg.src = ''; };
el.lightboxClose.addEventListener('click', closeLightbox);
el.lightbox.addEventListener('click', e => { if (e.target === el.lightbox) closeLightbox(); });
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  if (!el.lightbox.hidden) closeLightbox();
  else if (el.sidebar.classList.contains('is-open')) closeSidebar();
});

async function ask(question) {
  if (state.busy || !question.trim()) return;
  state.busy = true;
  el.send.disabled = true;

  addMessage('user', `<p>${escapeHtml(question)}</p>`);

  const pending = addMessage('assistant',
    '<div class="typing"><span></span><span></span><span></span></div>');
  const bubble = pending.querySelector('.msg__bubble');
  const body   = pending.querySelector('.msg__body');

  try {
    const res = await API.query(question, Number(el.topK.value));
    bubble.innerHTML = renderMarkdown(res.answer || '');

    const meta = res.abstained
      ? `<div class="meta meta--warn">⚠ No supporting document found — answer not backed by your corpus</div>`
      : `<div class="meta">
           <span>Confidence <b>${(res.top_score ?? 0).toFixed(3)}</b></span>
           <span>Latency <b>${res.latency_ms ?? 0} ms</b></span>
           <span>Sources <b>${res.sources?.length ?? 0}</b></span>
         </div>`;

    body.insertAdjacentHTML('beforeend', meta + sourcesBlock(res.sources));
  } catch (err) {
    bubble.innerHTML = `<p>Something went wrong reaching the backend.</p>
      <p class="muted sm">${escapeHtml(err.message)}</p>`;
    toast(err.message, 'err');
  } finally {
    state.busy = false;
    el.send.disabled = false;
    el.chat.scrollTop = el.chat.scrollHeight;
    el.input.focus();
  }
}

el.form.addEventListener('submit', e => {
  e.preventDefault();
  const q = el.input.value.trim();
  if (!q) return;
  el.input.value = '';
  el.input.style.height = 'auto';
  ask(q);
});

// Enter sends, Shift+Enter newlines. Textarea grows with content.
el.input.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); el.form.requestSubmit(); }
});
el.input.addEventListener('input', () => {
  el.input.style.height = 'auto';
  el.input.style.height = Math.min(el.input.scrollHeight, 180) + 'px';
});

el.chips?.addEventListener('click', e => {
  const chip = e.target.closest('.chip');
  if (chip) ask(chip.textContent.trim());
});

el.clearBtn.addEventListener('click', () => {
  el.chat.innerHTML = '';
  el.clearBtn.hidden = true;
  location.reload();
});

/* ==========================================================================
   Boot
   ========================================================================== */

refreshAll();
setInterval(refreshStatus, 30000);
el.input.focus();

})();
