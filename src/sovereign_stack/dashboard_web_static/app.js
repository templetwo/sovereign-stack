// Sovereign Stack — Operations Console v4 "cockpit" (vanilla DOM, no deps)
// ---------------------------------------------------------------------------
// Data contract consumed (see BUILD_SPEC.md §1):
//   timestamp        : epoch SECONDS (float)
//   connectivity     : { overall, counts{}, endpoints[{name,label,kind,
//                        status,launchctl_state,pid,last_exit_code,
//                        http_status,http_ok,http_error,log_age_seconds,
//                        notes[]}] }
//   halts_count, decisions_count, unacked_honks, listener_stale
//   latest           : { insight, open_thread, learning, handoff, decision,
//                        halt, honk }  (each a small dict or null)
//   feed             : [{ time, ts, category, message }]   — ts is epoch
//                        SECONDS (ActivityEvent.timestamp = time.time()).
//   service_telemetry (NEW, §1b) — top-level key, optional/absent until the
//     backend lane lands. Object keyed by the 5 connectivity endpoint
//     `name`s: { restart_count, current_uptime_seconds, p95_probe_ms,
//     recent_log_lines[] }. Every field individually may be null. The whole
//     key may be absent (older server). Every read below is defensive.
//
// The service denominator is ALWAYS endpoints.length — never hardcoded.
// Topology is laid out radially for N endpoints, hub = "bridge".
//
// Security: every render path uses textContent (via el()) or attribute-only
// SVG construction (via svgEl()) — never innerHTML, anywhere. This is the
// sharp-edge discipline for `recent_log_lines` (server-redacted, §2c) and
// for `feed`/`latest`/chronicle preview text (chronicle-authored content).
//
// CSRF contract (frontend-owned until the backend §2a substrate lands):
// primary source is <meta name="csrf-token" content="…"> (this file's
// index.html ships the tag empty; a future backend serve-time rewrite of
// index.html fills it in). Fallback: GET /session returning a JSON object
// with a csrf_token/token/csrfToken field (BUILD_SPEC §2a option B). If
// neither yields a token, mutating POSTs still fire (best-effort) but a
// real backend will 403 them — that's correct fail-closed behavior, not a
// bug: we never fabricate success locally except on the documented
// 404-degrades-to-local-only paths (ACK/RESTART/TAIL when the action route
// itself doesn't exist yet).

'use strict';

const POLL_MS = 3000;
const HEARTBEAT_MS = 30000;
const BRIDGE_HEARTBEAT_URL =
  `${location.protocol}//${location.hostname}:8100/api/heartbeat`;
const SVG_NS = 'http://www.w3.org/2000/svg';

const $ = (id) => document.getElementById(id);

function prefersReducedMotion() {
  return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

// ── textContent-safe DOM helpers (never innerHTML) ──────────────────────────
function el(tag, className, text) {
  const n = document.createElement(tag);
  if (className) n.className = className;
  if (text != null) n.textContent = text;
  return n;
}

function svgEl(tag, attrs) {
  const n = document.createElementNS(SVG_NS, tag);
  if (attrs) {
    for (const k in attrs) {
      if (Object.prototype.hasOwnProperty.call(attrs, k)) n.setAttribute(k, String(attrs[k]));
    }
  }
  return n;
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// ── Formatters ────────────────────────────────────────────────────────────
function fmtAge(seconds) {
  if (seconds == null) return null;
  if (seconds < 90) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86400)}d`;
}

function fmtUptime(seconds) {
  if (seconds == null) return '—';
  const s = Math.max(0, Math.round(seconds));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d}d${h}h`;
  if (h > 0) return `${h}h${m}m`;
  if (m > 0) return `${m}m`;
  return `${s}s`;
}

function fmtClock(epochSeconds) {
  if (!epochSeconds) return '—';
  const d = new Date(epochSeconds * 1000);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
}

function fmtHM(epochMs) {
  return new Date(epochMs).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
}

function fmtISO(epochMs) {
  return `${new Date(epochMs).toLocaleDateString('en-CA')} ${new Date(epochMs).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })}`;
}

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

// ── Status vocab ─────────────────────────────────────────────────────────
const STATUS_CLASS = { ok: 'is-ok', degraded: 'is-degraded', down: 'is-down', stale: 'is-stale', unknown: 'is-unknown' };
const STATUS_VAR = { ok: '--up', degraded: '--degraded', stale: '--degraded', down: '--down', unknown: '--unknown' };
const OVERALL_LABEL = { ok: 'OPERATIONAL', degraded: 'DEGRADED', down: 'OFFLINE', unknown: 'UNKNOWN' };

// ── Category vocab (matches dashboard.py's CAT_* + design's LEARNING) ──────
const CAT_COLOR_VAR = {
  COMMIT: '--c-commit', SERVICE: '--c-service', DEPLOY: '--c-service', STARTUP: '--c-service',
  INSIGHT: '--c-insight', THREAD: '--c-thread', CHRONICLE: '--c-thread', DECISION: '--c-decision',
  HALT: '--c-halt', HONK: '--c-honk', COMMS: '--c-comms', TOOLS: '--c-tools', LEARNING: '--c-learning',
  ERROR: '--c-error',
};
const CAT_CLASS_SLUG = {
  COMMIT: 'commit', SERVICE: 'service', DEPLOY: 'deploy', STARTUP: 'startup',
  INSIGHT: 'insight', THREAD: 'thread', CHRONICLE: 'chronicle', DECISION: 'decision',
  HALT: 'halt', HONK: 'honk', COMMS: 'comms', TOOLS: 'tools', LEARNING: 'learning', ERROR: 'error',
};
const CAT_SOURCE = {
  COMMIT: 'git-mirror', SERVICE: 'launchd-watcher', DEPLOY: 'launchd-watcher', STARTUP: 'launchd-watcher',
  HONK: 'honk-listener', HALT: 'honk-listener', COMMS: 'mcp-bridge', TOOLS: 'mcp-bridge',
};
const CHRONICLE_CATS = new Set(['INSIGHT', 'THREAD', 'CHRONICLE', 'DECISION', 'HALT', 'HONK', 'COMMS', 'TOOLS', 'LEARNING']);
const GIT_CATS = new Set(['COMMIT']);
const SERVICE_CATS = new Set(['DEPLOY', 'SERVICE', 'STARTUP']);
const FILTERS = [
  { key: 'all', label: 'ALL', match: () => true },
  { key: 'chronicle', label: 'CHRONICLE', match: (c) => CHRONICLE_CATS.has(c) },
  { key: 'git', label: 'GIT', match: (c) => GIT_CATS.has(c) },
  { key: 'services', label: 'SERVICES', match: (c) => SERVICE_CATS.has(c) },
];

const LATEST_ORDER = [
  ['insight', 'INSIGHT', (l) => l.domain],
  ['handoff', 'HANDOFF', (l) => l.thread || l.source_instance],
  ['open_thread', 'OPEN THREAD', (l) => l.domain],
  ['learning', 'LEARNING', (l) => l.applies_to],
  ['decision', 'DECISION', (l) => l.filename],
  ['halt', 'HALT', (l) => l.filename],
  ['honk', 'HONK', (l) => `${l.level || ''} ${l.pattern || ''}`.trim()],
];

// ── CSRF token (frontend contract — see header note) ────────────────────────
let csrfToken = '';
function initCsrf() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  if (meta && meta.content) { csrfToken = meta.content; return; }
  fetch('/session', { cache: 'no-store' })
    .then((r) => (r.ok ? r.json() : null))
    .then((data) => {
      if (!data) return;
      csrfToken = data.csrf_token || data.token || data.csrfToken || '';
    })
    .catch(() => { /* /session not deployed yet — token stays empty */ });
}

async function postAction(path, body) {
  const headers = { 'Content-Type': 'application/json' };
  if (csrfToken) headers['X-CSRF-Token'] = csrfToken;
  const res = await fetch(path, { method: 'POST', headers, body: JSON.stringify(body), cache: 'no-store' });
  let data = null;
  try { data = await res.json(); } catch (_) { /* no/invalid JSON body */ }
  return { ok: res.ok, status: res.status, data };
}

// ── Application state ────────────────────────────────────────────────────
const state = {
  filter: 'all',
  query: '',
  brush: null,        // {a,b} fractions of the 24h window, or null
  brushing: false,
  brushAnchor: null,
  expandedService: null,
  tailOpenFor: null,
  tailLines: {},          // endpoint name -> {lines, error} — cached fetch
  openEventTs: null,
  // Signature-scoped ACK hides — NEVER a session-global boolean (that fail-
  // opened: one inert click permanently hid every future unacked-honk
  // incident, even a brand-new honk). Keyed on the honk's honk_id when
  // present (real ack or the 404/unreachable local-only fallback), else
  // on `ts:<timestamp>` when honk_id is genuinely absent. A different honk
  // — different id or different timestamp — always gets its own entry and
  // re-surfaces the segment.
  ackedSignatures: new Set(),
  restartNotes: {},      // service name -> transient note text
  feed: [],
  lastSnapshot: null,
  filtersBuilt: false,
  svcHistory: {},         // endpoint name -> [{ts, val}] rolling status history
  latencyBuf: [],         // client-timed /snapshot.json RTT, ms, rolling ~60
  throughputBuf: [],      // new-feed-events-per-tick, rolling ~40
  seenFeedTs: new Set(),  // dedupe for "new" flash + throughput counting
  toolsAnimated: false,
  upAnimated: false,
  lastPollAt: null,
  newRowUntil: new Map(), // event ts -> epoch ms until which it's "new"
};

// ── Header: status pill + ECG + N/M UP ──────────────────────────────────────
function renderHeader(snapshot) {
  const overall = snapshot.connectivity?.overall || 'unknown';
  const pill = $('status-pill');
  pill.className = 'status-pill ' + (STATUS_CLASS[overall] || 'is-unknown');
  $('status-label').textContent = OVERALL_LABEL[overall] || 'UNKNOWN';

  const eps = snapshot.connectivity?.endpoints || [];
  const total = eps.length; // NEVER hardcoded — derived from the live list
  const upN = eps.filter((e) => e.status === 'ok').length;

  const upTextEl = $('status-uptext');
  if (total) {
    if (!state.upAnimated) {
      state.upAnimated = true;
      animateCountUp((v) => { upTextEl.textContent = `${v}/${total} UP`; }, upN);
    } else {
      upTextEl.textContent = `${upN}/${total} UP`;
    }
  } else {
    upTextEl.textContent = '—/— UP';
  }

  const ecg = $('ecg');
  // SVGElement.className is an SVGAnimatedString — direct assignment throws
  // ("has only a getter"). setAttribute is the correct way to set class on
  // an SVG element (caught live via browser console, not by node --check).
  ecg.setAttribute('class', 'ecg ' + (STATUS_CLASS[overall] || 'is-unknown'));
}

function animateCountUp(setter, target, duration = 1400) {
  if (prefersReducedMotion() || target <= 0) { setter(target); return; }
  const start = performance.now();
  function step(ts) {
    const p = Math.min(1, (ts - start) / duration);
    const eased = 1 - Math.pow(1 - p, 3);
    setter(Math.round(target * eased));
    if (p < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

// ── Clock (local tick, independent of poll) ─────────────────────────────────
function tickClock() {
  const now = new Date();
  $('clock-time').textContent = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
  $('clock-date').textContent = now.toLocaleDateString([], { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' }).toUpperCase();
}

// A honk's identity for locally-scoped ACK hides: honk_id when present,
// else its timestamp. Returns null when neither exists (nothing stable to
// scope a hide to — in that case the affordance simply never hides itself,
// which is correct per the "never a session-global hide" rule).
function honkSignature(honk) {
  if (!honk) return null;
  const id = honk.honk_id || honk.id;
  if (id) return `id:${id}`;
  if (honk.timestamp != null) return `ts:${honk.timestamp}`;
  return null;
}

// ── Incident strip ───────────────────────────────────────────────────────
function renderIncident(snapshot) {
  if (!snapshot) return;
  const strip = $('incident-strip');
  const segsEl = $('incident-segs');
  segsEl.replaceChildren();

  const eps = snapshot.connectivity?.endpoints || [];
  const down = eps.filter((e) => e.status === 'down').map((e) => e.name);
  const degraded = eps.filter((e) => e.status === 'degraded' || e.status === 'stale').map((e) => e.name);

  const segs = [];
  let critical = false;

  if (down.length) { critical = true; segs.push({ strong: down.join(', '), text: down.length === 1 ? 'down' : 'down' }); }
  if (snapshot.halts_count > 0) {
    critical = true;
    segs.push({ strong: String(snapshot.halts_count), text: snapshot.halts_count === 1 ? 'halt note' : 'halt notes' });
  }
  const honk = snapshot.latest?.honk || null;
  const honkId = honk && (honk.honk_id || honk.id) || null;
  const honkSig = honkSignature(honk);
  // Scoped to THIS honk's signature only — a different (or new, climbing)
  // honk always has a different signature and therefore always re-shows.
  const showAck = snapshot.unacked_honks > 0 && !(honkSig && state.ackedSignatures.has(honkSig));
  if (showAck) {
    segs.push({
      strong: String(snapshot.unacked_honks),
      text: snapshot.unacked_honks === 1 ? 'unacked honk' : 'unacked honks',
      ack: true, honkId, honkSig,
    });
  }
  if (snapshot.listener_stale) segs.push({ strong: null, text: 'listener stale' });
  if (degraded.length) segs.push({ strong: null, text: `${degraded.join(', ')} degraded` });

  if (!segs.length) { strip.hidden = true; return; }

  segs.forEach((s, i) => {
    const seg = el('span', 'incident-seg');
    if (s.strong != null) seg.append(el('b', null, s.strong), document.createTextNode(' ' + s.text));
    else seg.append(document.createTextNode(s.text));
    if (s.ack) {
      const btn = el('button', 'btn-ack', 'ACK');
      btn.type = 'button';
      btn.addEventListener('click', () => handleAck(s.honkId, s.honkSig, btn));
      seg.appendChild(btn);
    }
    segsEl.appendChild(seg);
    if (i < segs.length - 1) segsEl.appendChild(el('span', 'incident-sep', '·'));
  });

  strip.classList.toggle('is-critical', critical);
  strip.hidden = false;
  document.getElementById('app').classList.toggle('has-incident', true);
}

async function handleAck(honkId, honkSig, btnEl) {
  btnEl.disabled = true;
  if (!honkId) {
    // honk_id genuinely absent on this record (e.g. an older honk written
    // before the backend carried the field). Fall back to a local-only
    // hide, but SCOPED to this honk's signature (its timestamp) — never a
    // session-global flag. A new/different honk has a different signature
    // and always re-surfaces the segment.
    if (honkSig) state.ackedSignatures.add(honkSig);
    renderIncident(state.lastSnapshot);
    return;
  }
  try {
    const { status } = await postAction('/actions/ack', {
      honk_id: honkId,
      note: 'acknowledged via ops console',
    });
    if (status === 200) {
      // Real ack, confirmed by the server — hide only this honk.
      state.ackedSignatures.add(honkSig || `id:${honkId}`);
    } else if (status === 404) {
      // Action route not deployed yet on this server — degrade gracefully,
      // still scoped to this honk's own signature only.
      if (honkSig) state.ackedSignatures.add(honkSig);
    } else {
      btnEl.disabled = false;
      btnEl.textContent = `ACK (${status})`;
      setTimeout(() => { btnEl.textContent = 'ACK'; }, 3000);
      return;
    }
  } catch (_) {
    if (honkSig) state.ackedSignatures.add(honkSig);
  }
  renderIncident(state.lastSnapshot);
}

// ── Services panel: counts, topology, list, drill-down ───────────────────
function renderServiceCounts(snapshot) {
  const c = snapshot.connectivity?.counts || {};
  const parts = Object.entries(c).map(([k, v]) => `${v} ${k}`);
  $('svc-counts').textContent = parts.length ? parts.join(' · ') : '';
}

function svcHistoryPush(name, statusVal) {
  const buf = state.svcHistory[name] || (state.svcHistory[name] = []);
  buf.push(statusVal);
  if (buf.length > 24) buf.shift();
}

function statusToVal(status) {
  if (status === 'ok') return 3;
  if (status === 'degraded' || status === 'stale') return 2;
  if (status === 'down') return 0;
  return 1;
}

function buildSparkline(name, statusClass) {
  const buf = state.svcHistory[name] || [3];
  const pts = buf.length ? buf : [3];
  const min = Math.min(...pts), max = Math.max(...pts), rng = (max - min) || 1;
  const W = 58, H = 18, PAD = 2;
  const n = pts.length;
  const xy = pts.map((v, i) => {
    const x = n > 1 ? PAD + i * ((W - 2 * PAD) / (n - 1)) : PAD;
    const y = 15 - ((v - min) / rng) * 12;
    return [x, y];
  });
  const d = 'M' + xy.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' L');
  const [lx, ly] = xy[xy.length - 1];

  const svg = svgEl('svg', { width: 62, height: 20, viewBox: '0 0 58 18', class: 'svc-spark' });
  svg.appendChild(svgEl('path', { d, class: 'svc-spark-path ' + statusClass }));
  svg.appendChild(svgEl('circle', { cx: lx.toFixed(1), cy: ly.toFixed(1), r: 1.8, class: 'svc-spark-dot ' + statusClass }));
  return svg;
}

// Radial topology: hub = "bridge" if present, else the first endpoint.
// Satellites are the remaining N-1 endpoints, spaced evenly starting at
// the top, clockwise. Generalizes to any N (never hardcoded to 6).
function computeTopologyLayout(endpoints) {
  const W = 300, H = 196, cx = 150, cy = 98, radius = 60;
  const hubIdx = Math.max(0, endpoints.findIndex((e) => e.name === 'bridge'));
  const hub = endpoints[hubIdx];
  const satellites = endpoints.filter((_, i) => i !== hubIdx);
  const pos = {};
  pos[hub.name] = { x: cx, y: cy, r: 12, isHub: true };
  const n = satellites.length || 1;
  satellites.forEach((ep, i) => {
    const angle = (-90 + i * (360 / n)) * (Math.PI / 180);
    pos[ep.name] = {
      x: cx + radius * Math.cos(angle),
      y: cy + radius * Math.sin(angle),
      r: 9,
      isHub: false,
    };
  });
  return { pos, hubName: hub.name, W, H };
}

function renderTopology(endpoints) {
  const svg = $('topology');
  svg.replaceChildren();
  if (!endpoints.length) return;

  const { pos, hubName } = computeTopologyLayout(endpoints);
  const byName = {};
  endpoints.forEach((e) => { byName[e.name] = e; });

  const edgesGroup = svgEl('g');
  const nodesGroup = svgEl('g');

  for (const ep of endpoints) {
    if (ep.name === hubName) continue;
    const a = pos[hubName], b = pos[ep.name];
    const dx = a.x - b.x, dy = a.y - b.y, len = Math.hypot(dx, dy) || 1;
    const t1 = (b.r + 3) / len, t2 = 1 - (a.r + 3) / len;
    const x1 = b.x + dx * t1, y1 = b.y + dy * t1;
    const x2 = b.x + dx * t2, y2 = b.y + dy * t2;
    const st = STATUS_CLASS[ep.status] || 'is-unknown';
    edgesGroup.appendChild(svgEl('line', {
      x1: x1.toFixed(1), y1: y1.toFixed(1), x2: x2.toFixed(1), y2: y2.toFixed(1),
      class: 'topo-edge ' + (ep.status === 'degraded' || ep.status === 'stale' || ep.status === 'down' ? st : ''),
    }));
  }

  for (const ep of endpoints) {
    const p = pos[ep.name];
    const st = STATUS_CLASS[ep.status] || 'is-unknown';
    const selected = state.expandedService === ep.name;
    const g = svgEl('g', { class: 'topo-node' });
    g.appendChild(svgEl('circle', { cx: p.x, cy: p.y, r: 14, class: 'topo-halo ' + st }));
    g.appendChild(svgEl('circle', {
      cx: p.x, cy: p.y, r: p.r, class: 'topo-ring ' + st,
      'stroke-width': selected ? 2.4 : 1.4,
    }));
    g.appendChild(svgEl('circle', { cx: p.x, cy: p.y, r: 3.2, class: 'topo-dot ' + st }));
    const labelClass = 'topo-label' + (selected ? ' is-selected' : (ep.status === 'degraded' ? ' is-degraded' : ''));
    const text = svgEl('text', {
      x: p.x, y: p.y + p.r + 11, 'text-anchor': 'middle', class: labelClass,
    });
    text.textContent = ep.name;
    g.appendChild(text);
    g.addEventListener('click', () => toggleServiceExpanded(ep.name));
    nodesGroup.appendChild(g);
  }

  svg.appendChild(edgesGroup);
  svg.appendChild(nodesGroup);
}

function toggleServiceExpanded(name) {
  state.expandedService = state.expandedService === name ? null : name;
  state.tailOpenFor = null;
  renderServices(state.lastSnapshot);
}

function renderServiceLogLines(container, lines) {
  container.replaceChildren();
  if (!lines || !lines.length) {
    container.appendChild(el('div', 'svc-log-empty', 'no recent log lines'));
    return;
  }
  for (const line of lines) {
    const isWarn = /\bWARN\b/.test(line);
    // textContent-only — recent_log_lines is the sharp edge (server-side
    // redaction is the first guard, this is the second).
    container.appendChild(el('div', 'svc-log-line' + (isWarn ? ' is-warn' : ''), line));
  }
}

async function handleRestart(name, btnEl) {
  btnEl.disabled = true;
  let text;
  try {
    const { data } = await postAction('/actions/restart', { service: name });
    text = (data && data.note) || 'restart requested — not enacted (stub)';
  } catch (_) {
    text = 'restart requested — not enacted (action route unavailable)';
  }
  state.restartNotes[name] = text;
  renderServices(state.lastSnapshot);
  setTimeout(() => {
    if (state.restartNotes[name] === text) {
      delete state.restartNotes[name];
      renderServices(state.lastSnapshot);
    }
  }, 4000);
}

// The backend's /actions/tail log allowlist keys the listener endpoint as
// "comms-listener" (its launchd log filename stem), not "listener" (the
// connectivity.ENDPOINTS name shown in the UI). RESTART's allowlist is
// keyed directly off connectivity endpoint names and needs no mapping.
const TAIL_SERVICE_KEY = { listener: 'comms-listener' };

async function handleTail(name) {
  const serviceKey = TAIL_SERVICE_KEY[name] || name;
  try {
    const { ok, status, data } = await postAction('/actions/tail', { service: serviceKey, lines: 100 });
    if (ok && data && Array.isArray(data.lines)) {
      state.tailLines[name] = { lines: data.lines, error: null };
    } else if (status === 404) {
      state.tailLines[name] = { lines: null, error: 'tail action not deployed on this server yet' };
    } else {
      state.tailLines[name] = { lines: null, error: `tail failed (${status})` };
    }
  } catch (_) {
    state.tailLines[name] = { lines: null, error: 'tail action unreachable' };
  }
  if (state.tailOpenFor === name) renderServices(state.lastSnapshot);
}

function renderServices(snapshot) {
  if (!snapshot) return;
  const ul = $('svc-list');
  ul.replaceChildren();
  renderServiceCounts(snapshot);

  const endpoints = snapshot.connectivity?.endpoints || [];
  renderTopology(endpoints);

  if (!endpoints.length) {
    ul.appendChild(el('li', 'feed-empty', 'No endpoints reported.'));
    return;
  }

  const telemetry = snapshot.service_telemetry || {};

  for (const ep of endpoints) {
    svcHistoryPush(ep.name, statusToVal(ep.status));
    const st = STATUS_CLASS[ep.status] || 'is-unknown';
    const li = el('li', 'svc-row ' + st);

    const main = el('div', 'svc-main');
    const nameCol = el('div', 'svc-name-col');
    nameCol.appendChild(el('div', 'svc-name', ep.name));

    const parts = [];
    if (ep.pid) parts.push(`pid ${ep.pid}`);
    if (ep.http_status != null) parts.push(`http ${ep.http_status}`);
    const age = fmtAge(ep.log_age_seconds);
    if (age) parts.push(`age ${age}`);
    nameCol.appendChild(el('div', 'svc-meta', parts.length ? parts.join(' · ') : '—'));
    main.appendChild(nameCol);

    main.appendChild(buildSparkline(ep.name, st));

    const pill = el('span', 'svc-pill ' + st, (ep.status || 'unknown').toUpperCase());
    main.appendChild(pill);

    main.addEventListener('click', () => toggleServiceExpanded(ep.name));
    li.appendChild(main);

    if (state.expandedService === ep.name) {
      const drill = el('div', 'svc-drill');
      const tel = telemetry[ep.name] || null;
      const isPeriodic = ep.kind === 'periodic';

      const stats = el('div', 'svc-stats');
      const uptimeSpan = el('span', null, 'uptime ');
      uptimeSpan.appendChild(el('b', null, tel ? fmtUptime(tel.current_uptime_seconds) : '—'));
      stats.appendChild(uptimeSpan);

      if (!isPeriodic) {
        // §1b + constraints: restart-count suppressed entirely for
        // kind==="periodic" endpoints (listener) — not shown as "—".
        const restartsSpan = el('span', null, 'restarts ');
        restartsSpan.appendChild(el('b', null, tel && tel.restart_count != null ? String(tel.restart_count) : '—'));
        stats.appendChild(restartsSpan);
      }

      const p95Span = el('span', null, 'p95 ');
      p95Span.appendChild(el('b', null, tel && tel.p95_probe_ms != null ? `${tel.p95_probe_ms}ms` : '—'));
      stats.appendChild(p95Span);
      drill.appendChild(stats);

      const logs = el('div', 'svc-logs');
      renderServiceLogLines(logs, tel ? tel.recent_log_lines : null);
      drill.appendChild(logs);

      const actions = el('div', 'svc-actions');
      if (!isPeriodic) {
        // RESTART omitted entirely for periodic (listener) endpoints too.
        const restartBtn = el('button', 'btn-restart', 'RESTART');
        restartBtn.type = 'button';
        actions.appendChild(restartBtn);
        restartBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          handleRestart(ep.name, restartBtn);
        });
      }

      const tailBtn = el('button', 'btn-tail', 'TAIL LOGS');
      tailBtn.type = 'button';
      actions.appendChild(tailBtn);

      if (!isPeriodic && state.restartNotes[ep.name]) {
        actions.appendChild(el('span', 'svc-action-note', state.restartNotes[ep.name]));
      }
      drill.appendChild(actions);

      // Tail results are cached in state.tailLines so they survive the next
      // 3s poll's full re-render (a naive per-render fetch would otherwise
      // blank the view every poll tick). TAIL LOGS toggles open/closed and
      // re-fetches fresh on each open (no follow/streaming, per §2c).
      const tailView = el('div', 'svc-tail-view');
      const tailOpen = state.tailOpenFor === ep.name;
      tailView.hidden = !tailOpen;
      const tailHead = el('div', 'svc-tail-head');
      tailHead.appendChild(el('span', 'svc-tail-label', `tail · ${ep.name}`));
      tailView.appendChild(tailHead);

      const linesEl = el('div', 'svc-tail-lines');
      if (tailOpen) {
        const cached = state.tailLines[ep.name];
        if (!cached) {
          linesEl.appendChild(el('div', 'svc-log-empty', 'loading…'));
        } else if (cached.error) {
          linesEl.appendChild(el('div', 'svc-log-empty', cached.error));
        } else {
          renderServiceLogLines(linesEl, cached.lines);
        }
      }
      tailView.appendChild(linesEl);
      drill.appendChild(tailView);

      tailBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (state.tailOpenFor === ep.name) {
          state.tailOpenFor = null;
          renderServices(state.lastSnapshot);
        } else {
          state.tailOpenFor = ep.name;
          delete state.tailLines[ep.name];
          renderServices(state.lastSnapshot);
          handleTail(ep.name);
        }
      });

      li.appendChild(drill);
    }

    ul.appendChild(li);
  }
}

// ── Activity panel: filters, timeline/brush, feed ───────────────────────
function buildChips() {
  const bar = $('chip-row');
  bar.replaceChildren();

  const clearChip = el('button', 'chip is-clear');
  clearChip.type = 'button';
  clearChip.id = 'chip-clear-brush';
  clearChip.hidden = true;
  clearChip.addEventListener('click', () => { state.brush = null; renderActivity(state.lastSnapshot); });
  bar.appendChild(clearChip);

  for (const f of FILTERS) {
    const chip = el('button', 'chip' + (f.key === state.filter ? ' is-active' : ''));
    chip.type = 'button';
    chip.dataset.key = f.key;
    chip.append(document.createTextNode(f.label + ' '), el('span', 'chip-n', '0'));
    chip.addEventListener('click', () => {
      state.filter = f.key;
      renderActivity(state.lastSnapshot);
    });
    bar.appendChild(chip);
  }
  state.filtersBuilt = true;
}

function eventsInRange(events, brush, nowMs) {
  if (!brush) return events;
  const DAY = 24 * 3600000;
  const tsMin = nowMs - (1 - brush.a) * DAY;
  const tsMax = nowMs - (1 - brush.b) * DAY;
  return events.filter((ev) => ev.tsMs >= tsMin && ev.tsMs <= tsMax);
}

function renderActivity(snapshot) {
  if (!snapshot) return;
  if (!state.filtersBuilt) buildChips();

  const nowMs = Date.now();
  const q = state.query.trim().toLowerCase();
  const events = state.feed.map((ev) => ({
    ...ev,
    catU: (ev.category || '').toUpperCase(),
    tsMs: (ev.ts != null ? ev.ts : 0) * 1000,
  }));

  const searchMatch = (ev) => !q || (ev.message + ' ' + ev.catU).toLowerCase().includes(q);
  const brush = state.brush;
  const rangeMatch = (ev) => {
    if (!brush) return true;
    const DAY = 24 * 3600000;
    const tsMin = nowMs - (1 - brush.a) * DAY;
    const tsMax = nowMs - (1 - brush.b) * DAY;
    return ev.tsMs >= tsMin && ev.tsMs <= tsMax;
  };
  const composedMatch = (ev) => searchMatch(ev) && rangeMatch(ev);

  // ── Chips (counts reflect search + brush, per active category set) ──
  const clearChip = $('chip-clear-brush');
  if (brush) {
    clearChip.hidden = false;
    const DAY = 24 * 3600000;
    const tsMin = nowMs - (1 - brush.a) * DAY;
    const tsMax = nowMs - (1 - brush.b) * DAY;
    clearChip.textContent = '';
    clearChip.append(document.createTextNode('× ' + fmtHM(tsMin) + '–' + fmtHM(tsMax)));
  } else {
    clearChip.hidden = true;
  }
  for (const chip of $('chip-row').children) {
    if (chip.id === 'chip-clear-brush') continue;
    const f = FILTERS.find((x) => x.key === chip.dataset.key);
    if (!f) continue;
    chip.classList.toggle('is-active', chip.dataset.key === state.filter);
    const n = events.filter((ev) => f.match(ev.catU) && composedMatch(ev)).length;
    chip.querySelector('.chip-n').textContent = String(n);
  }

  // ── Timeline dots (24h window, all categories, dimmed outside brush) ──
  const dotsWrap = $('timeline-dots');
  dotsWrap.replaceChildren();
  const DAY = 24 * 3600000;
  for (const ev of events) {
    const age = nowMs - ev.tsMs;
    if (age < 0 || age > DAY) continue;
    const frac = 1 - age / DAY;
    const lane = CHRONICLE_CATS.has(ev.catU) ? 8 : (ev.catU === 'COMMIT' ? 17 : 26);
    const slug = CAT_CLASS_SLUG[ev.catU] || 'unknown';
    const dimmed = brush ? !(frac >= brush.a && frac <= brush.b) : false;
    const dot = el('span', 'timeline-dot dot-' + slug + (dimmed ? ' is-dimmed' : ''));
    dot.style.left = (frac * 100).toFixed(2) + '%';
    dot.style.top = lane + 'px';
    dot.title = `${ev.catU} · ${ev.message}`;
    dotsWrap.appendChild(dot);
  }

  const brushEl = $('timeline-brush');
  if (brush) {
    brushEl.classList.add('is-on');
    brushEl.style.left = (brush.a * 100).toFixed(1) + '%';
    brushEl.style.width = ((brush.b - brush.a) * 100).toFixed(1) + '%';
  } else {
    brushEl.classList.remove('is-on');
  }

  // ── Feed rows (active filter + search + brush, newest first) ──────────
  const activeFilter = FILTERS.find((f) => f.key === state.filter) || FILTERS[0];
  const filtered = events.filter((ev) => activeFilter.match(ev.catU) && composedMatch(ev)).slice(0, 80);

  const feedEl = $('feed');
  feedEl.replaceChildren();

  if (!filtered.length) {
    const msg = q
      ? `No events match "${state.query}".`
      : (brush ? 'No events in the selected range.' : 'Watching… events appear as the chronicle, git, daemons and services produce them.');
    feedEl.appendChild(el('li', 'feed-empty', msg));
  } else {
    for (const ev of filtered) {
      const slug = CAT_CLASS_SLUG[ev.catU] || 'unknown';
      const isNew = state.newRowUntil.has(ev.ts) && state.newRowUntil.get(ev.ts) > nowMs;
      const row = el('li', 'feed-row bl-' + slug + (isNew ? ' is-new' : ''));
      const main = el('div', 'feed-main');
      main.append(
        el('span', 'feed-time', ev.time || fmtHM(ev.tsMs)),
        el('span', 'feed-cat cat-' + slug, ev.catU),
        el('span', 'feed-msg', ev.message || ''),
      );
      row.appendChild(main);

      if (state.openEventTs === ev.ts) {
        const detail = el('div', 'feed-detail');
        const meta = el('div', 'feed-detail-meta');
        meta.append(document.createTextNode('ts '));
        meta.appendChild(el('b', null, fmtISO(ev.tsMs)));
        meta.append(document.createTextNode(' · source '));
        meta.appendChild(el('b', null, CAT_SOURCE[ev.catU] || 'chronicle-watcher'));
        meta.append(document.createTextNode(' · category '));
        meta.appendChild(el('b', null, ev.catU));
        detail.appendChild(meta);
        detail.appendChild(el('div', 'feed-detail-msg', ev.message || ''));
        row.appendChild(detail);
      }

      row.addEventListener('click', () => {
        state.openEventTs = state.openEventTs === ev.ts ? null : ev.ts;
        renderActivity(state.lastSnapshot);
      });
      feedEl.appendChild(row);
    }
  }
}

// ── Timeline brush interaction (pointer capture, drag to select) ──────────
function brushFracFromEvent(e) {
  const r = $('timeline').getBoundingClientRect();
  return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
}

function initBrush() {
  const timeline = $('timeline');
  timeline.addEventListener('pointerdown', (e) => {
    timeline.setPointerCapture(e.pointerId);
    const f = brushFracFromEvent(e);
    state.brushAnchor = f;
    state.brushing = true;
    state.brush = { a: f, b: f };
    state.openEventTs = null;
    renderActivity(state.lastSnapshot);
  });
  timeline.addEventListener('pointermove', (e) => {
    if (!state.brushing) return;
    const f = brushFracFromEvent(e);
    state.brush = { a: Math.min(state.brushAnchor, f), b: Math.max(state.brushAnchor, f) };
    renderActivity(state.lastSnapshot);
  });
  timeline.addEventListener('pointerup', () => {
    state.brushing = false;
    const b = state.brush;
    state.brush = b && (b.b - b.a) > 0.004 ? b : null;
    renderActivity(state.lastSnapshot);
  });
}

// ── Recent chronicle writes (right column) ──────────────────────────────
function renderChronicle(snapshot) {
  const container = $('chronicle-list');
  container.replaceChildren();
  const latest = snapshot.latest || {};

  for (const [key, label, domainFn] of LATEST_ORDER) {
    const entry = latest[key];
    const typeClass = key === 'open_thread' ? 'thread' : key;
    const card = el('div', `chr-entry t-${typeClass}` + (key === 'halt' && entry ? ' is-halt' : ''));

    const head = el('div', 'chr-head');
    head.append(
      el('span', 'chr-type', label),
      el('span', 'chr-when', entry ? fmtRelTime(entry.timestamp) : ''),
    );
    card.appendChild(head);

    if (!entry) {
      card.appendChild(el('div', 'chr-empty', 'no entries yet'));
    } else {
      const dom = domainFn(entry);
      if (dom) card.appendChild(el('div', 'chr-domain', dom));
      card.appendChild(el('div', 'chr-preview', entry.preview || '(empty)'));
    }
    container.appendChild(card);
  }
}

// ── Bridge latency + throughput (client-derived — §1b) ───────────────────
function renderLatencyCard() {
  const lat = state.latencyBuf;
  const nowEl = $('latency-now');
  const p95El = $('latency-p95');
  const rpsEl = $('latency-rps');
  const chart = $('latency-chart');
  const tp = $('throughput-chart');

  if (!lat.length) {
    nowEl.replaceChildren(document.createTextNode('—'), el('span', 'latency-unit', ' ms'));
    p95El.textContent = '—';
    rpsEl.textContent = '—';
    chart.replaceChildren();
    tp.replaceChildren();
    return;
  }

  const latNow = lat[lat.length - 1];
  const sorted = [...lat].sort((a, b) => a - b);
  const p95v = sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.95))] || 0;

  nowEl.replaceChildren(document.createTextNode(String(Math.round(latNow))), el('span', 'latency-unit', ' ms'));
  nowEl.classList.toggle('is-hot', latNow > 140);
  p95El.textContent = String(Math.round(p95v));
  const tpBuf = state.throughputBuf;
  rpsEl.textContent = tpBuf.length ? String(tpBuf[tpBuf.length - 1]) : '—';

  const W = 280, H = 66;
  const ly = (v) => Math.max(3, H - 4 - ((v - 40) / 200) * (H - 10));
  const pts = lat.map((v, i) => [lat.length > 1 ? i * (W / (lat.length - 1)) : 0, ly(v)]);
  const linePath = 'M' + pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' L');
  const areaPath = linePath + ` L${W},${H} L0,${H} Z`;

  chart.replaceChildren();
  chart.appendChild(svgEl('line', { x1: 0, y1: 22, x2: W, y2: 22, class: 'lat-grid' }));
  chart.appendChild(svgEl('line', { x1: 0, y1: 44, x2: W, y2: 44, class: 'lat-grid' }));
  chart.appendChild(svgEl('line', { x1: 0, y1: ly(p95v).toFixed(1), x2: W, y2: ly(p95v).toFixed(1), class: 'lat-p95-line' }));
  chart.appendChild(svgEl('path', { d: areaPath, class: 'lat-area' }));
  chart.appendChild(svgEl('path', { d: linePath, class: 'lat-line' }));

  tp.replaceChildren();
  const bars = tpBuf.length ? tpBuf : [0];
  const maxV = Math.max(1, ...bars);
  bars.forEach((v, i) => {
    const h = Math.max(1.5, (v / maxV) * 20);
    tp.appendChild(svgEl('rect', {
      x: (i * 7).toFixed(1), y: (22 - h).toFixed(1), width: 5, height: h.toFixed(1), rx: 1,
      class: 'tp-bar' + (i === bars.length - 1 ? ' is-newest' : ''),
    }));
  });
}

// ── Poll loop ─────────────────────────────────────────────────────────────
async function poll() {
  const status = $('poll-status');
  const t0 = performance.now();
  try {
    const r = await fetch('/snapshot.json', { cache: 'no-store' });
    const rtt = performance.now() - t0;
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const snapshot = await r.json();

    state.latencyBuf.push(rtt);
    if (state.latencyBuf.length > 60) state.latencyBuf.shift();

    const incoming = Array.isArray(snapshot.feed) ? snapshot.feed : [];
    let newCount = 0;
    const nowMark = Date.now();
    for (const ev of incoming) {
      if (ev.ts != null && !state.seenFeedTs.has(ev.ts)) {
        state.seenFeedTs.add(ev.ts);
        state.newRowUntil.set(ev.ts, nowMark + 2500);
        newCount++;
      }
    }
    // Bound the dedupe set so it doesn't grow unboundedly across a long
    // session; the feed array itself is already server-capped.
    if (state.seenFeedTs.size > 500) {
      const keep = new Set(incoming.map((e) => e.ts));
      state.seenFeedTs = keep;
    }
    state.throughputBuf.push(newCount);
    if (state.throughputBuf.length > 40) state.throughputBuf.shift();

    state.feed = incoming;
    state.lastSnapshot = snapshot;

    renderHeader(snapshot);
    renderIncident(snapshot);
    renderServices(snapshot);
    renderActivity(snapshot);
    renderChronicle(snapshot);
    renderLatencyCard();

    status.className = '';
    status.textContent = `live · last poll ${fmtClock(Date.now() / 1000)} · polling /snapshot.json every 3s`;
    state.lastPollAt = Date.now();
  } catch (err) {
    status.className = 'is-err';
    status.textContent = `poll error · ${err.message} · retrying`;
    const pill = $('status-pill');
    pill.className = 'status-pill is-down';
    $('status-label').textContent = 'UNREACHABLE';
  } finally {
    setTimeout(poll, POLL_MS);
  }
}

// ── Best-effort bridge heartbeat for version + tool count (isolated) ──────
async function fetchBridgeHeartbeat() {
  const vEl = $('stat-version');
  const tEl = $('stat-tools');
  try {
    const r = await fetch(BRIDGE_HEARTBEAT_URL, { cache: 'no-store' });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const hb = await r.json();
    const version = hb.version != null ? String(hb.version) : null;
    const tools = (hb.tools != null ? hb.tools : hb.tool_count);
    if (version) {
      vEl.textContent = version.startsWith('v') ? version : `v${version}`;
      vEl.classList.remove('is-muted');
    }
    if (tools != null) {
      const n = Number(tools);
      if (!state.toolsAnimated) {
        state.toolsAnimated = true;
        animateCountUp((v) => { tEl.textContent = String(v); }, n);
      } else {
        tEl.textContent = String(n);
      }
      tEl.classList.remove('is-muted');
    }
  } catch (_) {
    // Bridge down / not CORS-readable — leave "—". Not an error condition.
  } finally {
    setTimeout(fetchBridgeHeartbeat, HEARTBEAT_MS);
  }
}

// ── Search ────────────────────────────────────────────────────────────────
function initSearch() {
  const input = $('search-input');
  input.addEventListener('input', () => {
    state.query = input.value;
    renderActivity(state.lastSnapshot);
  });
}

// ── Theme toggle (self-contained, persisted) ─────────────────────────────
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

// ── Viewport lock (matches design: ≥920w AND ≥720h locks to 100vh) ───────
function initViewportLock() {
  const app = $('app');
  const update = () => {
    const locked = window.innerWidth >= 920 && window.innerHeight >= 720;
    app.classList.toggle('is-locked', locked);
  };
  window.addEventListener('resize', update);
  update();
}

// ── Boot ──────────────────────────────────────────────────────────────────
initCsrf();
initTheme();
initViewportLock();
initSearch();
initBrush();
tickClock();
setInterval(tickClock, 1000);
poll();
fetchBridgeHeartbeat();
