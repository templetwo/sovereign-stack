// Sovereign Stack — Operations Console (vanilla DOM, no framework, no deps)
// ---------------------------------------------------------------------------
// Data contract (UNCHANGED from the server): polls GET /snapshot.json every
// POLL_MS and renders it in place. Shapes consumed:
//   timestamp        : epoch seconds
//   connectivity     : { overall, counts{}, endpoints[{name,status,pid,
//                        http_status,log_age_seconds}] }
//   halts_count, decisions_count, unacked_honks, listener_stale
//   latest           : { insight, open_thread, learning, handoff, decision,
//                        halt, honk }  (each a small dict or null)
//   feed             : [{ time, ts, category, message }]
//
// The git timeline (COMMIT), the launchd/service stream (DEPLOY/SERVICE/
// STARTUP) and chronicle writes (INSIGHT/THREAD/…) are NOT separate routes —
// they are categories within the single `feed` array, sliced client-side.
//
// Version + tool-count are NOT in the snapshot contract. They are filled by a
// best-effort, fully isolated GET to the local bridge heartbeat; any failure
// leaves those two slots at "—". This never touches the snapshot poll loop.
//
// Security note: every value rendered from the snapshot (feed messages,
// previews, domains) is chronicle content and is written with textContent
// only — never innerHTML — so a chronicle entry can never inject markup.

'use strict';

const POLL_MS = 3000;
const HEARTBEAT_MS = 30000;
const BRIDGE_HEARTBEAT_URL =
  `${location.protocol}//${location.hostname}:8100/api/heartbeat`;

const $ = (id) => document.getElementById(id);

// ── Small DOM helper (textContent-safe by construction) ────────────────────
function el(tag, className, text) {
  const n = document.createElement(tag);
  if (className) n.className = className;
  if (text != null) n.textContent = text;
  return n;
}

// ── Formatters ─────────────────────────────────────────────────────────────
function fmtAge(seconds) {
  if (seconds == null) return null;
  if (seconds < 90) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86400)}d`;
}

function fmtClock(epochSeconds) {
  if (!epochSeconds) return '—';
  const d = new Date(epochSeconds * 1000);
  return d.toLocaleTimeString([], {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  });
}

// Accepts either epoch (number) or ISO string; returns "12m ago" style.
function fmtRelTime(isoOrEpoch) {
  if (isoOrEpoch == null) return '';
  let t;
  if (typeof isoOrEpoch === 'number') {
    t = isoOrEpoch * (isoOrEpoch < 1e12 ? 1000 : 1);
  } else {
    t = Date.parse(isoOrEpoch);
    if (isNaN(t)) return '';
  }
  const diff = Math.max(0, (Date.now() - t) / 1000);
  if (diff < 60) return `${Math.round(diff)}s ago`;
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)}h ago`;
  return `${Math.round(diff / 86400)}d ago`;
}

// ── Status vocab ───────────────────────────────────────────────────────────
// Endpoints: ok | degraded | down | stale | unknown.  Overall: ok|degraded|down.
const STATUS_CLASS = {
  ok: 'is-ok', degraded: 'is-degraded', down: 'is-down',
  stale: 'is-stale', unknown: 'is-unknown',
};
const OVERALL_LABEL = {
  ok: 'Operational', degraded: 'Degraded', down: 'Offline', unknown: 'Unknown',
};
const OVERALL_CLASS = {
  ok: 'is-ok', degraded: 'is-degraded', down: 'is-down', unknown: '',
};

// ── Activity-stream filters (slice the one feed array) ─────────────────────
const CHRONICLE_CATS = new Set(
  ['INSIGHT', 'THREAD', 'CHRONICLE', 'DECISION', 'HALT', 'HONK', 'COMMS', 'TOOLS']);
const GIT_CATS = new Set(['COMMIT']);
const SERVICE_CATS = new Set(['DEPLOY', 'SERVICE', 'STARTUP']);
const FILTERS = [
  { key: 'all', label: 'All', match: () => true },
  { key: 'chronicle', label: 'Chronicle', match: (c) => CHRONICLE_CATS.has(c) },
  { key: 'git', label: 'Git', match: (c) => GIT_CATS.has(c) },
  { key: 'services', label: 'Services', match: (c) => SERVICE_CATS.has(c) },
];

const state = { feed: [], filter: 'all', filtersBuilt: false };

// ── Header heartbeat (overall status + services + updated) ─────────────────
function renderHeartbeat(snapshot) {
  const overall = snapshot.connectivity?.overall || 'unknown';
  const pill = $('hb-status');
  pill.className = 'hb-status ' + (OVERALL_CLASS[overall] || '');
  $('overall-dot').className = 'hb-dot';
  $('overall-text').textContent = OVERALL_LABEL[overall] || 'Unknown';

  const eps = snapshot.connectivity?.endpoints || [];
  const upN = eps.filter((e) => e.status === 'ok').length;
  const svc = $('stat-services');
  if (eps.length) {
    svc.textContent = `${upN}/${eps.length}`;
    svc.classList.remove('is-muted');
    const worst = eps.some((e) => e.status === 'down') ? 'var(--down)'
      : (upN < eps.length ? 'var(--degraded)' : 'var(--up)');
    svc.style.color = worst;
  } else {
    svc.textContent = '—';
    svc.classList.add('is-muted');
    svc.style.color = '';
  }

  const up = $('stat-updated');
  up.textContent = fmtClock(snapshot.timestamp);
  up.classList.remove('is-muted');
}

// ── Services card ──────────────────────────────────────────────────────────
function renderServices(snapshot) {
  const ul = $('services');
  const counts = $('counts');
  ul.replaceChildren();
  counts.replaceChildren();

  const c = snapshot.connectivity?.counts || {};
  const COUNT_CLASS = { ok: 'is-up', degraded: 'is-degraded', stale: 'is-degraded', down: 'is-down' };
  for (const [k, v] of Object.entries(c)) {
    const span = el('span', 'count ' + (COUNT_CLASS[k] || ''));
    span.append(el('b', null, String(v)), document.createTextNode(' ' + k));
    counts.appendChild(span);
  }

  const endpoints = snapshot.connectivity?.endpoints || [];
  if (!endpoints.length) {
    ul.appendChild(el('li', 'placeholder', 'No endpoints reported.'));
    return;
  }

  for (const ep of endpoints) {
    const st = ep.status || 'unknown';
    const li = el('li', 'service ' + (STATUS_CLASS[st] || 'is-unknown'));

    li.appendChild(el('span', 'service-name', ep.name));

    const pill = el('span', 'pill ' + (STATUS_CLASS[st] || 'is-unknown'), st);
    li.appendChild(pill);

    const parts = [];
    if (ep.pid) parts.push(`pid ${ep.pid}`);
    if (ep.http_status != null) parts.push(`http ${ep.http_status}`);
    const age = fmtAge(ep.log_age_seconds);
    if (age) parts.push(`age ${age}`);
    const meta = el('span', 'service-meta');
    if (parts.length) {
      parts.forEach((p, i) => {
        if (i) meta.append(el('span', 'sep', '·'));
        meta.append(document.createTextNode(p));
      });
    } else {
      meta.textContent = '—';
    }
    li.appendChild(meta);
    ul.appendChild(li);
  }
}

// ── Anomaly banner (silent when all-clear) ─────────────────────────────────
function renderAlert(snapshot) {
  const region = $('alert-region');
  const body = $('alert-body');
  body.replaceChildren();

  const eps = snapshot.connectivity?.endpoints || [];
  const down = eps.filter((e) => e.status === 'down').map((e) => e.name);
  const degraded = eps.filter((e) => e.status === 'degraded' || e.status === 'stale')
    .map((e) => e.name);

  const segs = [];   // { count|null, text }
  let critical = false;

  if (down.length) { critical = true; segs.push({ count: null, text: `${down.join(', ')} down` }); }
  if (snapshot.halts_count > 0) {
    critical = true;
    segs.push({ count: snapshot.halts_count, text: snapshot.halts_count === 1 ? 'halt note' : 'halt notes' });
  }
  if (snapshot.unacked_honks > 0) {
    segs.push({ count: snapshot.unacked_honks, text: snapshot.unacked_honks === 1 ? 'unacked honk' : 'unacked honks' });
  }
  if (snapshot.listener_stale) segs.push({ count: null, text: 'listener stale' });
  if (degraded.length) segs.push({ count: null, text: `${degraded.join(', ')} degraded` });

  if (!segs.length) { region.hidden = true; return; }

  segs.forEach((s, i) => {
    if (i) body.append(document.createTextNode('   ·   '));
    if (s.count != null) {
      body.append(el('b', null, String(s.count)), document.createTextNode(' ' + s.text));
    } else {
      body.append(document.createTextNode(s.text));
    }
  });
  region.classList.toggle('is-critical', critical);
  region.hidden = false;
}

// ── Recent chronicle writes ────────────────────────────────────────────────
const LATEST_ORDER = [
  ['insight', 'Insight', (l) => l.domain],
  ['handoff', 'Handoff', (l) => l.thread || l.source_instance],
  ['open_thread', 'Open thread', (l) => l.domain],
  ['learning', 'Learning', (l) => l.applies_to],
  ['decision', 'Decision', (l) => l.filename],
  ['halt', 'Halt', (l) => l.filename],
  ['honk', 'Honk', (l) => `${l.level || ''} ${l.pattern || ''}`.trim()],
];

function renderLatest(snapshot) {
  const container = $('latest');
  container.replaceChildren();
  const latest = snapshot.latest || {};

  for (const [key, label, domainFn] of LATEST_ORDER) {
    const entry = latest[key];
    const typeClass = key === 'open_thread' ? 'thread' : key;
    const card = el('div', `entry t-${typeClass}`);

    const head = el('div', 'entry-head');
    head.append(
      el('span', 'entry-type', label),
      el('span', 'entry-when', entry ? fmtRelTime(entry.timestamp) : ''),
    );
    card.appendChild(head);

    if (!entry) {
      card.appendChild(el('div', 'entry-empty', 'no entries yet'));
    } else {
      const dom = domainFn(entry);
      if (dom) card.appendChild(el('div', 'entry-domain', dom));
      card.appendChild(el('div', 'entry-preview', entry.preview || '(empty)'));
    }
    container.appendChild(card);
  }
}

// ── Activity stream ────────────────────────────────────────────────────────
function buildFilters() {
  const bar = $('filters');
  bar.replaceChildren();
  for (const f of FILTERS) {
    const chip = el('button', 'chip' + (f.key === state.filter ? ' is-active' : ''));
    chip.type = 'button';
    chip.dataset.key = f.key;
    chip.append(el('span', null, f.label), el('span', 'chip-n', '0'));
    chip.addEventListener('click', () => {
      state.filter = f.key;
      renderFilters();
      renderFeed();
    });
    bar.appendChild(chip);
  }
  state.filtersBuilt = true;
}

function renderFilters() {
  const bar = $('filters');
  for (const chip of bar.children) {
    const f = FILTERS.find((x) => x.key === chip.dataset.key);
    if (!f) continue;
    chip.classList.toggle('is-active', chip.dataset.key === state.filter);
    const n = state.feed.filter((ev) => f.match((ev.category || '').toUpperCase())).length;
    chip.querySelector('.chip-n').textContent = String(n);
  }
}

function renderFeed() {
  const ol = $('feed');
  ol.replaceChildren();
  const active = FILTERS.find((f) => f.key === state.filter) || FILTERS[0];
  const events = state.feed.filter((ev) => active.match((ev.category || '').toUpperCase()));

  if (!events.length) {
    const msg = state.feed.length
      ? `No ${active.label.toLowerCase()} events yet.`
      : 'Watching… events appear as the chronicle, git, daemons and services produce them.';
    ol.appendChild(el('li', 'placeholder', msg));
    return;
  }

  for (const ev of events) {
    const cat = (ev.category || '').toUpperCase();
    const li = el('li', `feed-item c-${cat.toLowerCase()}`);
    li.append(
      el('span', 'feed-time', ev.time || ''),
      el('span', 'feed-cat', cat),
      el('span', 'feed-msg', ev.message || ''),
    );
    ol.appendChild(li);
  }
}

// ── Poll loop (resilient: try → catch → finally-reschedule) ────────────────
async function poll() {
  const status = $('poll-status');
  try {
    const r = await fetch('/snapshot.json', { cache: 'no-store' });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const snapshot = await r.json();

    if (!state.filtersBuilt) buildFilters();
    state.feed = Array.isArray(snapshot.feed) ? snapshot.feed : [];

    renderHeartbeat(snapshot);
    renderAlert(snapshot);
    renderServices(snapshot);
    renderLatest(snapshot);
    renderFilters();
    renderFeed();

    status.className = 'foot-item is-ok';
    status.textContent = `live · last poll ${fmtClock(Date.now() / 1000)}`;
  } catch (err) {
    status.className = 'foot-item is-err';
    status.textContent = `poll error · ${err.message} · retrying`;
    const pill = $('hb-status');
    pill.className = 'hb-status is-down';
    $('overall-text').textContent = 'Unreachable';
  } finally {
    setTimeout(poll, POLL_MS);
  }
}

// ── Best-effort bridge heartbeat for version + tool count (isolated) ───────
// Cross-origin (dashboard :3456 → bridge :8100). If the bridge sends no
// Access-Control-Allow-Origin, the browser blocks the read and the slots
// stay "—". Wrapped so it can never disturb the snapshot poll.
async function fetchBridgeHeartbeat() {
  const vEl = $('stat-version');
  const tEl = $('stat-tools');
  try {
    const r = await fetch(BRIDGE_HEARTBEAT_URL, { cache: 'no-store' });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const hb = await r.json();
    const version = hb.version != null ? String(hb.version) : null;
    const tools = (hb.tools != null ? hb.tools : hb.tool_count);
    if (version) { vEl.textContent = version.startsWith('v') ? version : `v${version}`; vEl.classList.remove('is-muted'); }
    if (tools != null) { tEl.textContent = String(tools); tEl.classList.remove('is-muted'); }
  } catch (_) {
    // Bridge down / not CORS-readable — leave "—". Not an error condition.
  } finally {
    setTimeout(fetchBridgeHeartbeat, HEARTBEAT_MS);
  }
}

// ── Theme toggle (self-contained, persisted) ───────────────────────────────
function initTheme() {
  const root = document.documentElement;
  const saved = (() => { try { return localStorage.getItem('ss-theme'); } catch (_) { return null; } })();
  if (saved === 'light' || saved === 'dark') root.setAttribute('data-theme', saved);
  $('theme-toggle').addEventListener('click', () => {
    const next = root.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
    root.setAttribute('data-theme', next);
    try { localStorage.setItem('ss-theme', next); } catch (_) { /* ignore */ }
  });
}

initTheme();
poll();
fetchBridgeHeartbeat();
