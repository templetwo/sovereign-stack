// Sovereign Stack — Sovereign Console v2 (vanilla DOM, no deps)
// ---------------------------------------------------------------------------
// v2 is an ADDITIVE RESKIN over the v4 "cockpit", not a replacement — the
// name is confusing and the confusion is expensive, so: v2 is NEWER than v4.
// Every v4 affordance still ships and is still wired to the same element id:
// search (initSearch), the incident strip + signature-scoped honk ACK
// (renderIncident/handleAck/ackedSignatures), the ECG, the 24h timeline
// brush (initBrush), the theme toggle, the two-step guarded restart
// (handleRestart) and log tail (handleTail), the per-service status
// sparkline (svcHistoryPush/buildSparkline) and drill-down
// (toggleServiceExpanded). The v2 prototype has none of these; recreating
// its markup literally would have shipped a regression.
//
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
//   watchman (NEW) — top-level key: { sweeps: [...up to 8, newest-first],
//     malformed_skipped, summary: { last_sweep_age_seconds, status
//     ('quiet'|'active'|'unknown'), surfaces_watched, flagged_trend } }.
//     Each sweep: { sweep_id, timestamp (epoch SECONDS), items_seen,
//     grok_scope: {classified, mechanical_only}, grok_reply_state,
//     severity_ceiling ('urgent'|'attend'|'info'|null), reasons[] (already-
//     sanitized, <=3, <=140 chars each) }. Whole key and every field may be
//     absent/null — same defensive-read discipline as service_telemetry.
//   spiral, self_model, guardian, open_threads, arrival_gate,
//     bridge_heartbeat, lineage (NEW, v2) — keys 11..17, each INDIVIDUALLY
//     NULLABLE and each carrying `source` + `age_seconds`. Null means "that
//     source could not be read", and the panel must SAY SO. It must never
//     fall back to a previous value or to a plausible-looking number: the
//     v2 prototype's five demo-seeded panels keep rendering under a green
//     LIVE badge because nothing ever clears them, and a stale number that
//     looks live is worse than a blank that admits it.
//       spiral           : {session_id, current_phase, reflection_depth,
//                           tool_call_count, phase_history_count, started,
//                           session_age_seconds}
//       self_model       : {entries[{category, observation, timestamp,
//                           age_seconds, entry_count}], stale,
//                           stale_after_days}
//       guardian         : {health_score, listeners, ollama_localhost_only,
//                           issues[], issue_count}
//       open_threads     : {unresolved_count, threads[], files_scanned,
//                           malformed_skipped}
//       arrival_gate     : {status('asked'|'quiet'), pending[], pending_count,
//                           expired_by_cutoff, pending_window_seconds,
//                           tokens_available:false, tokens_note}
//       bridge_heartbeat : {version, tools, source_commit, bridge_commit,
//                           service_uptime_seconds, aperture_surfaces,
//                           gate_total_pending_all_substrates}
//       lineage          : {letters[{bucket, title, date, from,
//                           age_seconds}], counts{}, total}
//
// PROVENANCE IS PER PANEL, NOT GLOBAL. The prototype drives one mode badge
// off heartbeat reachability alone, so panels that never received live data
// sit under a green LIVE. Here every panel renders its own age, and a panel
// whose source is missing renders that fact instead of a number.
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
// (The client-side bridge-heartbeat poll and its :8100 URL are gone — the
// server fetches the heartbeat once, cached, and ships it in the snapshot.
// See "Bridge heartbeat: now SERVER-SIDE" below.)
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

// Feed rows print HH:MM only, which reads as out-of-order the moment a lane
// carries anything older than today. The server's own short-lived feed never
// did; the synthesized WATCHMAN lane replays sweeps from the spool and does.
// Same-day rows keep the compact form; anything else is prefixed with its
// date, so "20:28 above 19:59" is either sorted or visibly a different day.
function fmtFeedTime(epochMs) {
  if (epochMs == null || !isFinite(epochMs)) return '';
  const d = new Date(epochMs);
  const now = new Date();
  const sameDay = d.getFullYear() === now.getFullYear()
    && d.getMonth() === now.getMonth()
    && d.getDate() === now.getDate();
  return sameDay ? fmtHM(epochMs) : `${d.toLocaleDateString('en-CA').slice(5)} ${fmtHM(epochMs)}`;
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
  ERROR: '--c-error', WATCHMAN: '--watchman', ARRIVAL: '--c-arrival', METABOLISM: '--c-metabolism',
};
const CAT_CLASS_SLUG = {
  COMMIT: 'commit', SERVICE: 'service', DEPLOY: 'deploy', STARTUP: 'startup',
  INSIGHT: 'insight', THREAD: 'thread', CHRONICLE: 'chronicle', DECISION: 'decision',
  HALT: 'halt', HONK: 'honk', COMMS: 'comms', TOOLS: 'tools', LEARNING: 'learning', ERROR: 'error',
  WATCHMAN: 'watchman', ARRIVAL: 'arrival', METABOLISM: 'metabolism',
};
const CAT_SOURCE = {
  COMMIT: 'git-mirror', SERVICE: 'launchd-watcher', DEPLOY: 'launchd-watcher', STARTUP: 'launchd-watcher',
  HONK: 'honk-listener', HALT: 'honk-listener', COMMS: 'mcp-bridge', TOOLS: 'spiral tool_call_count delta',
  WATCHMAN: 'watchman spool',
};

// ── Filter chips, re-mapped to the vocabulary the SERVER actually emits ────
//
// The v2 design specifies six chips: ALL / TOOLS / CHRONICLE / COMMS /
// GUARDIAN / WATCHMAN. Measured against `_watcher_loop`, four of those six
// read ZERO FOREVER — the watcher emits exactly nine categories (STARTUP,
// INSIGHT, THREAD, HALT, DECISION, HONK, COMMIT, DEPLOY, ERROR) and none of
// them is TOOLS, COMMS, GUARDIAN or WATCHMAN. Worse in the other direction:
// HONK, HALT, DECISION, COMMIT, DEPLOY and THREAD were unreachable by any
// chip except ALL, and `unacked_honks` is routinely non-zero.
//
// So the chips below are the server's real vocabulary, plus TWO SYNTHESIZED
// LANES that give the design's intent a real source instead of a dead one:
//   TOOLS    — differenced client-side from spiral.tool_call_count.
//   WATCHMAN — one event per watchman sweep, keyed on sweep_id.
// GUARDIAN and COMMS chips are dropped: there is no event source for either,
// and a chip that can only ever show 0 is a lie with a counter on it.
const CHRONICLE_CATS = new Set(['INSIGHT', 'THREAD', 'CHRONICLE', 'DECISION', 'LEARNING', 'METABOLISM', 'ARRIVAL']);
const GIT_CATS = new Set(['COMMIT']);
const SERVICE_CATS = new Set(['DEPLOY', 'SERVICE', 'STARTUP']);
const ALERT_CATS = new Set(['HALT', 'HONK', 'ERROR']);
const FILTERS = [
  { key: 'all', label: 'ALL', match: () => true },
  { key: 'chronicle', label: 'CHRONICLE', match: (c) => CHRONICLE_CATS.has(c) },
  { key: 'tools', label: 'TOOLS', match: (c) => c === 'TOOLS' },
  { key: 'watchman', label: 'WATCHMAN', match: (c) => c === 'WATCHMAN' },
  { key: 'services', label: 'SERVICES', match: (c) => SERVICE_CATS.has(c) },
  { key: 'git', label: 'GIT', match: (c) => GIT_CATS.has(c) },
  { key: 'alerts', label: 'ALERTS', match: (c) => ALERT_CATS.has(c) },
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
  // ── v2 synthesized activity lanes ──
  // The server's watcher emits no TOOLS and no WATCHMAN events. Rather than
  // ship two chips that can only read 0, both lanes are derived here from
  // real snapshot data and merged into the feed. `synthFeed` is capped the
  // same way the server caps its own feed.
  lastToolCallCount: null,  // spiral.tool_call_count from the previous poll
  seenSweepIds: new Set(),  // watchman sweep_id dedupe
  synthFeed: [],            // [{time, ts, category, message}]
};

const SYNTH_FEED_MAX = 12;   // well below _FEED_LIMIT_IN_SNAPSHOT (30) —
// a synthesized lane must never be able to out-number the server's whole feed.
const TOOLS_COALESCE_MS = 60000;

// ── Per-panel provenance ─────────────────────────────────────────────────
//
// Every v2 panel answers two questions before it answers any other: is the
// source there, and how old is what you're looking at. `panelUnavailable`
// is the whole "no demo data" rule in one function — when a section is
// null the panel says which source is missing, and renders nothing else.
function panelUnavailable(container, noteEl, what) {
  container.replaceChildren();
  container.appendChild(el('div', 'panel-missing', `source unavailable — ${what}`));
  if (noteEl) {
    noteEl.textContent = 'no data';
    noteEl.className = 'panel-note is-missing';
  }
}

function setPanelAge(noteEl, section, extra) {
  if (!noteEl) return;
  const parts = [];
  if (extra) parts.push(extra);
  const age = section && section.age_seconds;
  if (age != null) {
    const label = fmtAge(age);
    parts.push(label ? `${label} old` : 'just now');
  }
  noteEl.textContent = parts.length ? parts.join(' · ') : '—';
  noteEl.className = 'panel-note';
}

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

  renderHeaderStats(snapshot);
}

// PHASE / TOOLS / VERSION / BRIDGE UP.
//
// All four now come from the SNAPSHOT — the server fetches the bridge
// heartbeat itself (no auth, cached ~20s) and hands it over as
// `bridge_heartbeat`. The v4 page fetched :8100/api/heartbeat from the
// browser on its own 30s timer; that is now redundant, and the heartbeat is
// not cheap (9KB, ~130ms, and its attribution scan walks 400 chronicle
// shard files per call). One cached server-side fetch replaces every
// viewer's independent poll, and it removes the page's last cross-origin
// request.
function renderHeaderStats(snapshot) {
  const spiral = snapshot.spiral || null;
  const hb = snapshot.bridge_heartbeat || null;

  const phaseEl = $('stat-phase');
  if (spiral && spiral.current_phase) {
    phaseEl.textContent = spiral.current_phase;
    phaseEl.classList.remove('is-muted');
  } else {
    phaseEl.textContent = '—';
    phaseEl.classList.add('is-muted');
  }

  const vEl = $('stat-version');
  const tEl = $('stat-tools');
  const upEl = $('stat-uptime');

  if (hb) {
    // `tools` is the real key. The prototype falls back to `tool_count`,
    // which does not exist in the payload — a fallback to nothing.
    const version = hb.version != null ? String(hb.version) : null;
    if (version) {
      vEl.textContent = version.startsWith('v') ? version : `v${version}`;
      vEl.classList.remove('is-muted');
      // source_commit cannot go stale the way the version string can — the
      // bridge resolves `version` once at import.
      if (hb.source_commit) vEl.title = `source ${hb.source_commit}` + (hb.bridge_commit ? ` · bridge ${hb.bridge_commit}` : '');
    }
    if (hb.tools != null) {
      const n = Number(hb.tools);
      if (!state.toolsAnimated) {
        state.toolsAnimated = true;
        animateCountUp((v) => { tEl.textContent = String(v); }, n);
      } else {
        tEl.textContent = String(n);
      }
      tEl.classList.remove('is-muted');
    }
    // Labelled BRIDGE UP in the markup on purpose: three different uptimes
    // are in play and an unqualified one gets quoted as the wrong one.
    if (hb.service_uptime_seconds != null) {
      upEl.textContent = fmtUptime(hb.service_uptime_seconds);
      upEl.classList.remove('is-muted');
    }
  } else {
    // Bridge unreachable — say nothing rather than keep the last number.
    for (const [node, text] of [[vEl, '—'], [tEl, '—'], [upEl, '—']]) {
      node.textContent = text;
      node.classList.add('is-muted');
    }
  }
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

  // Toggle the page-level wash BEFORE the early return. It used to be set
  // only on the has-incident path, so once an incident cleared the strip
  // hid itself and the pulsing amber `.bg-incident-wash` stayed painted
  // with nothing on screen to explain it — an alarm with no alarm.
  document.getElementById('app').classList.toggle('has-incident', segs.length > 0);
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
        el('span', 'feed-time', ev.tsMs ? fmtFeedTime(ev.tsMs) : (ev.time || '')),
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

  // Every other v2 panel carries "N · age"; this one shipped a hardcoded
  // "by type" and was the only panel with no provenance at all. The age is
  // the newest of the entries actually rendered, so a panel of six dormant
  // rows cannot read as fresh.
  const note = $('chronicle-note');
  if (note) {
    let present = 0;
    let newestMs = null;
    for (const [key] of LATEST_ORDER) {
      const entry = latest[key];
      if (!entry) continue;
      present++;
      const t = Date.parse(entry.timestamp);
      if (!isNaN(t) && (newestMs === null || t > newestMs)) newestMs = t;
    }
    const age = newestMs === null ? null : fmtAge(Math.max(0, (Date.now() - newestMs) / 1000));
    note.textContent = `${present}/${LATEST_ORDER.length} by type` + (age ? ` · ${age} old` : '');
    note.className = 'panel-note' + (present ? '' : ' is-missing');
  }

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

// ── Watchman (client-derived read of the `watchman` snapshot key) ────────
const WM_CEILING_LABEL = { urgent: 'URGENT', attend: 'ATTEND', info: 'QUIET' };

function wmCeilingClass(ceiling) {
  const c = (ceiling || '').toLowerCase();
  return (c === 'urgent' || c === 'attend') ? c : 'info';
}

function renderWatchman(snapshot) {
  const container = $('watchman-list');
  const summaryEl = $('wm-summary');
  const wm = (snapshot && snapshot.watchman) || null;
  const sweeps = (wm && Array.isArray(wm.sweeps)) ? wm.sweeps : [];
  const summary = (wm && wm.summary) || {};

  if (summaryEl) {
    const parts = [];
    if (summary.surfaces_watched != null) parts.push(`${summary.surfaces_watched} watched`);
    if (summary.status === 'quiet' || summary.status === 'active') parts.push(summary.status);
    if (summary.last_sweep_age_seconds != null) {
      const age = fmtAge(summary.last_sweep_age_seconds);
      if (age) parts.push(`${age} ago`);
    }
    if (summary.flagged_trend === 'rising' || summary.flagged_trend === 'falling') {
      parts.push(`flagged ${summary.flagged_trend}`);
    }
    summaryEl.textContent = parts.length ? parts.join(' · ') : '—';
  }

  container.replaceChildren();

  if (!sweeps.length) {
    const n = summary.surfaces_watched;
    const msg = n != null ? `all quiet — ${n} surfaces watched` : 'all quiet — no sweep data yet';
    container.appendChild(el('div', 'wm-empty', msg));
    return;
  }

  for (const sweep of sweeps) {
    const ceilingClass = wmCeilingClass(sweep.severity_ceiling);
    const entry = el('div', `wm-entry is-${ceilingClass}`);

    const head = el('div', 'wm-head');
    head.append(
      el('span', 'wm-when', sweep.timestamp != null ? fmtRelTime(sweep.timestamp) : ''),
      el('span', `wm-pill is-${ceilingClass}`, WM_CEILING_LABEL[ceilingClass]),
    );
    entry.appendChild(head);

    const meta = el('div', 'wm-meta');
    const scope = sweep.grok_scope || {};

    const deltaSpan = el('span');
    deltaSpan.append(document.createTextNode('Δ '));
    deltaSpan.appendChild(el('b', null, sweep.items_seen != null ? String(sweep.items_seen) : '—'));
    meta.appendChild(deltaSpan);

    const classifiedSpan = el('span');
    classifiedSpan.append(document.createTextNode('classified '));
    classifiedSpan.appendChild(el('b', null, scope.classified != null ? String(scope.classified) : '—'));
    meta.appendChild(classifiedSpan);

    if (sweep.grok_reply_state) {
      const isUnparseable = String(sweep.grok_reply_state).includes('unparseable');
      meta.appendChild(el('span', 'wm-reply' + (isUnparseable ? ' is-unparseable' : ''), sweep.grok_reply_state));
    }
    entry.appendChild(meta);

    const reasons = Array.isArray(sweep.reasons) ? sweep.reasons : [];
    if (reasons.length) {
      const reasonsWrap = el('div', 'wm-reasons');
      for (const reason of reasons) {
        reasonsWrap.appendChild(el('div', 'wm-reason', reason));
      }
      entry.appendChild(reasonsWrap);
    }

    container.appendChild(entry);
  }
}

// ── GUARDIAN (score ring + listeners + issues) ───────────────────────────
function guardianScoreClass(score) {
  if (score == null) return 'is-unknown';
  if (score >= 90) return 'is-ok';
  if (score >= 70) return 'is-degraded';
  return 'is-down';
}

function buildGuardianRing(score) {
  const r = 27;
  const circumference = 2 * Math.PI * r;
  const frac = Math.max(0, Math.min(1, (Number(score) || 0) / 100));
  const cls = guardianScoreClass(score);
  const svg = svgEl('svg', { width: 66, height: 66, viewBox: '0 0 66 66', class: 'grd-ring' });
  svg.appendChild(svgEl('circle', { cx: 33, cy: 33, r, class: 'grd-ring-bg' }));
  svg.appendChild(svgEl('circle', {
    cx: 33, cy: 33, r, class: 'grd-ring-fg ' + cls,
    'stroke-dasharray': `${(circumference * frac).toFixed(1)} ${circumference.toFixed(1)}`,
    transform: 'rotate(-90 33 33)',
  }));
  const label = svgEl('text', { x: 33, y: 38, 'text-anchor': 'middle', class: 'grd-ring-text ' + cls });
  label.textContent = score == null ? '—' : String(score);
  svg.appendChild(label);
  return svg;
}

function renderGuardian(snapshot) {
  const body = $('guardian-body');
  const note = $('guardian-note');
  const grd = snapshot.guardian || null;
  if (!grd) {
    panelUnavailable(body, note, 'guardian probe did not run');
    return;
  }
  setPanelAge(note, grd, grd.ollama_localhost_only === false ? 'exposed listener' : null);

  body.replaceChildren();
  const top = el('div', 'grd-top');
  top.appendChild(buildGuardianRing(grd.health_score));

  const stats = el('div', 'grd-stats');
  const listeners = el('div', 'grd-stat');
  listeners.append(el('span', 'grd-stat-label', 'LISTENERS'), el('b', 'grd-stat-val is-gold', grd.listeners != null ? String(grd.listeners) : '—'));
  stats.appendChild(listeners);

  const issues = el('div', 'grd-stat');
  const issueCount = grd.issue_count != null ? grd.issue_count : (grd.issues || []).length;
  issues.append(
    el('span', 'grd-stat-label', 'ISSUES'),
    el('b', 'grd-stat-val ' + (issueCount ? 'is-down' : 'is-ok'), String(issueCount)),
  );
  stats.appendChild(issues);
  top.appendChild(stats);
  body.appendChild(top);

  for (const issue of grd.issues || []) {
    const row = el('div', 'grd-issue');
    row.append(el('span', 'grd-issue-icon', '⚠'), el('span', null, issue));
    body.appendChild(row);
  }
}

// ── SELF-MODEL MIRROR ────────────────────────────────────────────────────
const MIRROR_LABEL = {
  strength: 'STRENGTH', drift: 'DRIFT', blind_spot: 'BLIND SPOT', tendency: 'TENDENCY',
};

function renderMirror(snapshot) {
  const body = $('mirror-body');
  const note = $('mirror-note');
  const model = snapshot.self_model || null;
  if (!model) {
    panelUnavailable(body, note, 'self_model.json not readable');
    return;
  }

  // The live self_model was last written 2026-05-25 — this panel is fed by
  // a DORMANT instrument, and its age is the most load-bearing thing on it.
  // Past the threshold it degrades visually and still shows its content: a
  // three-month-old truth is old, not absent.
  setPanelAge(note, model, model.stale ? `stale >${model.stale_after_days}d` : null);
  if (model.stale) note.classList.add('is-stale');

  body.replaceChildren();
  body.classList.toggle('is-stale', !!model.stale);

  const entries = model.entries || [];
  if (!entries.length) {
    body.appendChild(el('div', 'panel-missing', 'self-model file has no entries'));
    return;
  }
  for (const entry of entries) {
    const row = el('div', 'mir-entry m-' + (entry.category || 'unknown'));
    const head = el('div', 'mir-head');
    head.append(
      el('span', 'mir-cat', MIRROR_LABEL[entry.category] || String(entry.category || '').toUpperCase()),
      el('span', 'mir-when', entry.timestamp ? fmtRelTime(entry.timestamp) : ''),
    );
    row.appendChild(head);
    row.appendChild(el('div', 'mir-text', entry.observation || '(empty)'));
    body.appendChild(row);
  }
}

// ── SPIRAL · METABOLISM ──────────────────────────────────────────────────
//
// The six cells are NOT the prototype's. It reads them from `metabolize`,
// which (a) writes to metabolism_log.jsonl on every call, and (b) does not
// emit "learnings" or "decisions" at all — so two of its six cells kept
// their demo values (23 and 9) forever, under a green LIVE badge. These six
// come from the heartbeat's already-computed `aperture.surfaces` plus the
// snapshot's own counters, and EACH IS LABELLED WITH WHAT IT ACTUALLY IS
// ("threads unresolved", not "threads"), because a bare number invites the
// reader to supply their own definition.
function metabolismCells(snapshot) {
  const surfaces = (snapshot.bridge_heartbeat && snapshot.bridge_heartbeat.aperture_surfaces) || null;
  const pick = (group, field) => {
    if (!surfaces || !surfaces[group] || surfaces[group][field] == null) return null;
    return surfaces[group][field];
  };
  // THREADS UNRESOLVED comes from `open_threads`, NOT from the aperture.
  // Both producers exist in this house and they disagree by exactly one:
  // aperture.py globs `chronicle/open_threads/*.jsonl` FLAT while
  // dashboard_readers uses rglob, and there is one nested shard
  // (tech-debt,compaction,auto-detection/log.jsonl). Two adjacent panels
  // stating 182 and 183 for one fact is worse than either number, so the
  // console reads one source. NOTE, and it is not a fix: this RELOCATES
  // the disagreement — the console now says 183 while every arriving
  // seat's boot door still reads 182 from the aperture. The durable fix is
  // the glob in aperture.py, which is main's tree and another lane's call.
  const threads = snapshot.open_threads || null;
  return [
    { label: 'INSIGHTS ON DISK', value: pick('insights', 'on_disk'), tone: 'insight', src: 'aperture' },
    {
      label: 'THREADS UNRESOLVED',
      value: threads && threads.unresolved_count != null ? threads.unresolved_count : null,
      tone: 'thread',
      src: 'threads',
    },
    { label: 'HANDOFFS ARCHIVED', value: pick('handoffs', 'on_disk'), tone: 'service', src: 'aperture' },
    { label: 'DECISIONS FILED', value: snapshot.decisions_count, tone: 'decision', src: 'local' },
    { label: 'HALT NOTES', value: snapshot.halts_count, tone: 'halt', hotIfPositive: true, src: 'local' },
    { label: 'HONKS UNACKED', value: snapshot.unacked_honks, tone: 'honk', hotIfPositive: true, src: 'local' },
  ];
}

function renderSpiral(snapshot) {
  const body = $('spiral-body');
  const note = $('spiral-note');
  const spiral = snapshot.spiral || null;
  if (!spiral) {
    panelUnavailable(body, note, 'spiral_state.json not readable');
    return;
  }
  setPanelAge(note, spiral, spiral.tool_call_count != null ? `${spiral.tool_call_count} tool calls` : null);

  body.replaceChildren();

  const phase = el('div', 'spi-phase', spiral.current_phase || 'unknown phase');
  body.appendChild(phase);

  const subParts = [];
  subParts.push(spiral.reflection_depth != null ? `depth ${spiral.reflection_depth}` : 'depth —');
  if (spiral.session_age_seconds != null) subParts.push(`session ${fmtUptime(spiral.session_age_seconds)}`);
  if (spiral.phase_history_count != null) subParts.push(`${spiral.phase_history_count} transitions`);
  body.appendChild(el('div', 'spi-sub', 'current cognitive phase · ' + subParts.join(' · ')));

  const grid = el('div', 'spi-grid');
  const surfaces = (snapshot.bridge_heartbeat && snapshot.bridge_heartbeat.aperture_surfaces) || null;
  for (const cell of metabolismCells(snapshot)) {
    const box = el('div', 'spi-cell c-' + cell.tone);
    const hot = cell.hotIfPositive && Number(cell.value) > 0;
    box.appendChild(el('div', 'spi-cell-val' + (hot ? ' is-hot' : ''), cell.value != null ? String(cell.value) : '—'));
    box.appendChild(el('div', 'spi-cell-label', cell.label));
    grid.appendChild(box);
  }
  body.appendChild(grid);

  // The foot names EVERY source in the grid, not just one. It previously
  // read "counts from bridge aperture" under six cells of which three come
  // from dashboard.collect_state and (now) one from the chronicle reader —
  // half a provenance line is a false one.
  const foot = el('div', 'spi-foot');
  if (!surfaces) {
    // Two of the six cells come from the bridge. Say so rather than
    // rendering em-dashes the reader has to interpret.
    foot.textContent = 'insights/handoffs unavailable — bridge heartbeat not reachable · threads from chronicle · decisions/halts/honks local';
    foot.classList.add('is-missing');
  } else {
    const domains = surfaces.insights && surfaces.insights.domains;
    foot.textContent = 'insights/handoffs from bridge aperture'
      + (domains != null ? ` · ${domains} domains` : '')
      + ' · threads from chronicle (recursive) · decisions/halts/honks local';
  }
  body.appendChild(foot);
}

// ── OPEN THREADS ─────────────────────────────────────────────────────────
function renderThreads(snapshot) {
  const list = $('threads-list');
  const note = $('threads-note');
  const section = snapshot.open_threads || null;
  if (!section) {
    panelUnavailable(list, note, 'chronicle/open_threads not readable');
    return;
  }
  const extra = `${section.unresolved_count} unresolved`
    + (section.malformed_skipped ? ` · ${section.malformed_skipped} malformed skipped` : '');
  setPanelAge(note, section, extra);

  list.replaceChildren();
  const threads = section.threads || [];
  if (!threads.length) {
    list.appendChild(el('div', 'panel-empty', 'no unresolved threads'));
    return;
  }
  for (const thread of threads) {
    const row = el('div', 'thr-row');
    const head = el('div', 'thr-head');
    head.append(
      el('span', 'thr-domain', thread.domain || '(no domain)'),
      el('span', 'thr-when', thread.timestamp ? fmtRelTime(thread.timestamp) : ''),
    );
    row.appendChild(head);
    row.appendChild(el('div', 'thr-q', thread.question || '(no question recorded)'));
    list.appendChild(row);
  }
}

// ── ARRIVAL GATE ─────────────────────────────────────────────────────────
function renderArrival(snapshot) {
  const body = $('arrival-body');
  const statusEl = $('arrival-status');
  const gate = snapshot.arrival_gate || null;
  if (!gate) {
    panelUnavailable(body, statusEl, 'session_tokens.db not readable');
    return;
  }

  const asked = gate.status === 'asked';
  statusEl.textContent = asked ? 'DOOR ASKED' : 'QUIET';
  statusEl.className = 'panel-note arrival-status ' + (asked ? 'is-asked' : 'is-quiet');

  body.replaceChildren();

  for (const pending of gate.pending || []) {
    const card = el('div', 'arr-card');
    const head = el('div', 'arr-head');
    head.append(
      el('span', 'arr-code', pending.code || '(no code)'),
      el('span', 'arr-state', 'PENDING'),
    );
    card.appendChild(head);
    const who = [pending.source_instance, pending.seat_description].filter(Boolean).join(' · ');
    if (who) card.appendChild(el('div', 'arr-desc', who));
    const meta = [];
    if (pending.requested_scope) meta.push(`scope ${pending.requested_scope}`);
    if (pending.age_seconds != null) meta.push(`asked ${fmtAge(pending.age_seconds) || '0s'} ago`);
    if (meta.length) card.appendChild(el('div', 'arr-meta', meta.join(' · ')));
    body.appendChild(card);
  }

  if (!asked) {
    const quiet = el('div', 'arr-quiet', 'no one at the door');
    body.appendChild(quiet);
  }

  const foot = el('div', 'arr-foot');
  const bits = [`${gate.total_requests} requests on record`];
  if (gate.expired_by_cutoff) bits.push(`${gate.expired_by_cutoff} past the ${gate.pending_window_seconds}s window`);
  foot.textContent = bits.join(' · ');
  body.appendChild(foot);

  // The design pairs this card with a session-TOKENS list. That list is
  // MASTER-TOKEN-ONLY, and the design's own instruction ("degrade silently
  // on 401/403") renders an empty list — which reads as "no session tokens
  // exist", a false statement manufactured by a permissions failure. The
  // card is omitted and its absence is stated instead.
  if (gate.tokens_available === false) {
    body.appendChild(el('div', 'arr-tokens-note', gate.tokens_note || 'session tokens not available without the master token'));
  }
}

// ── LINEAGE (replaces the design's COMMS panel) ──────────────────────────
const LINEAGE_LABEL = {
  to_arrival: 'TO ARRIVAL', breakthroughs: 'BREAKTHROUGH', to_self: 'TO SELF',
};

function renderLineage(snapshot) {
  const list = $('lineage-list');
  const note = $('lineage-note');
  const section = snapshot.lineage || null;
  if (!section) {
    panelUnavailable(list, note, 'comms/letters not readable');
    return;
  }
  const counts = section.counts || {};
  setPanelAge(note, section, `${section.total} letters · ${counts.to_arrival || 0}/${counts.breakthroughs || 0}/${counts.to_self || 0}`);

  list.replaceChildren();
  const letters = section.letters || [];
  if (!letters.length) {
    list.appendChild(el('div', 'panel-empty', 'no letters on disk'));
    return;
  }
  for (const letter of letters) {
    // Title and date only. A letter's body is correspondence between
    // instances, not ops telemetry, and the reader never loads it.
    const row = el('div', 'lin-row b-' + (letter.bucket || 'unknown'));
    const head = el('div', 'lin-head');
    head.append(
      el('span', 'lin-bucket', LINEAGE_LABEL[letter.bucket] || String(letter.bucket || '').toUpperCase()),
      el('span', 'lin-when', letter.date ? fmtRelTime(letter.date) : ''),
    );
    row.appendChild(head);
    row.appendChild(el('div', 'lin-title', letter.title || '(untitled)'));
    if (letter.from) row.appendChild(el('div', 'lin-from', letter.from));
    list.appendChild(row);
  }
}

// ── Synthesized activity lanes (TOOLS + WATCHMAN) ────────────────────────
//
// Both lanes exist because the design asked for chips the server has no
// event source for. Rather than ship dead chips or demo events, each is
// differenced from something real: the spiral's monotonic tool counter, and
// the watchman's sweep spool. Every synthesized event is tagged with its
// origin in CAT_SOURCE so a reader clicking a row sees where it came from.
function synthesizeEvents(snapshot) {
  const nowTs = snapshot.timestamp != null ? snapshot.timestamp : (Date.now() / 1000);
  const added = [];

  // TOOLS is COALESCED, not appended. Every synth TOOLS event carries
  // ts = snapshot.timestamp (always now), so it sorts above every server
  // event; during an active session the lane emitted one row per poll and
  // filled the entire first screenful with rows differing only by a delta.
  // Within TOOLS_COALESCE_MS the newest row is REPLACED and its delta
  // accumulated, so a busy minute is one honest rolling row.
  const count = snapshot.spiral && snapshot.spiral.tool_call_count;
  if (typeof count === 'number') {
    if (state.lastToolCallCount != null && count > state.lastToolCallCount) {
      const delta = count - state.lastToolCallCount;
      const head = state.synthFeed.length ? state.synthFeed[0] : null;
      const coalescable = head
        && head.category === 'TOOLS'
        && (nowTs - (head.ts || 0)) * 1000 < TOOLS_COALESCE_MS;
      const total = (coalescable ? (head.delta || 0) : 0) + delta;
      const row = {
        ts: nowTs,
        time: fmtFeedTime(nowTs * 1000),
        category: 'TOOLS',
        delta: total,
        message: `+${total} tool call${total === 1 ? '' : 's'} · ${count} this session`,
      };
      if (coalescable) state.synthFeed[0] = row;
      else added.push(row);
    }
    state.lastToolCallCount = count;
  }

  const sweeps = (snapshot.watchman && Array.isArray(snapshot.watchman.sweeps)) ? snapshot.watchman.sweeps : [];
  for (const sweep of sweeps) {
    const id = sweep.sweep_id || (sweep.timestamp != null ? `ts:${sweep.timestamp}` : null);
    if (!id || state.seenSweepIds.has(id)) continue;
    state.seenSweepIds.add(id);
    const scope = sweep.grok_scope || {};
    const bits = [`sweep · Δ ${sweep.items_seen != null ? sweep.items_seen : '—'}`];
    if (scope.classified != null) bits.push(`classified ${scope.classified}`);
    if (sweep.severity_ceiling) bits.push(String(sweep.severity_ceiling));
    added.push({
      ts: sweep.timestamp != null ? sweep.timestamp : nowTs,
      time: fmtFeedTime((sweep.timestamp != null ? sweep.timestamp : nowTs) * 1000),
      category: 'WATCHMAN',
      message: bits.join(' · '),
    });
  }
  if (state.seenSweepIds.size > 200) state.seenSweepIds = new Set(sweeps.map((s) => s.sweep_id).filter(Boolean));

  if (added.length) {
    state.synthFeed = added.concat(state.synthFeed).slice(0, SYNTH_FEED_MAX);
  }
}

// ── Bridge latency + throughput (client-derived — §1b) ───────────────────
function renderLatencyCard(snapshot) {
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
  const clientP95 = sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.95))] || 0;

  // p95 IS THE SAME SERIES AS "now". It used to prefer the server's
  // service_telemetry.bridge.p95_probe_ms — a different probe of a
  // different endpoint on a different cadence — while "now" stayed the
  // client's /snapshot.json RTT. Observed live: 507/222, 609/226, 464/213,
  // 370/226. A 95th percentile at half the current value is impossible
  // within one series and reads as a broken number; worse, the gold p95
  // line was drawn on the client-RTT y-domain, pinning it at the chart
  // floor BELOW every sample, so the chart asserted that 100% of samples
  // exceeded the 95th percentile. The server's figure is still useful and
  // still shown — as its own labelled row, named for what it measures.
  const tel = (snapshot && snapshot.service_telemetry && snapshot.service_telemetry.bridge) || null;
  const serverP95 = tel && tel.p95_probe_ms != null ? tel.p95_probe_ms : null;
  const p95v = clientP95;

  // ── The red threshold is MEASURED, not inherited. ──
  // The design specifies "red above 140ms". Measured on this box, the
  // bridge's own p95_probe_ms is ~182ms while completely healthy — so a
  // hardcoded 140 ships a permanently red panel and trains the reader to
  // ignore the one color that is supposed to mean something. The threshold
  // is therefore relative to what this machine actually does: 2x the
  // rolling median of the sample buffer, floored at 120ms so a very quiet
  // window can't make a trivial blip look like an incident.
  const median = sorted.length ? sorted[Math.floor(sorted.length / 2)] : 0;
  const hotThreshold = Math.max(120, median * 2);

  nowEl.replaceChildren(document.createTextNode(String(Math.round(latNow))), el('span', 'latency-unit', ' ms'));
  nowEl.classList.toggle('is-hot', latNow > hotThreshold);
  nowEl.title = `hot above ${Math.round(hotThreshold)}ms (2x rolling median of ${sorted.length} samples)`;
  p95El.textContent = String(Math.round(p95v));
  p95El.title = `p95 of ${sorted.length} client-timed /snapshot.json round trips`;
  const srvEl = $('latency-server-p95');
  if (srvEl) {
    srvEl.textContent = serverP95 != null ? String(Math.round(serverP95)) : '—';
    srvEl.title = serverP95 != null
      ? "the bridge watcher's own p95 health-probe time — a different probe of a different endpoint, sampled on its ~2s cadence"
      : 'bridge telemetry absent';
  }
  const noteEl = $('latency-note');
  if (noteEl) {
    noteEl.textContent = `client RTT · ${Math.round((POLL_MS * 60) / 1000)}s window`;
  }
  const tpBuf = state.throughputBuf;
  rpsEl.textContent = tpBuf.length ? String(tpBuf[tpBuf.length - 1]) : '—';

  // ── The chart's y-domain is measured too, for the same reason the red
  // threshold is. v4 hardcoded 40-240ms; the bridge's real p95 on this box
  // is ~255ms and a cold first poll can read 500ms+, so every sample
  // saturated the top of the scale and the "line" chart rendered as a solid
  // filled block. The domain now tracks the buffer with 15% headroom and a
  // 60ms minimum span, so a flat-and-healthy trace still reads as flat
  // rather than being amplified into noise.
  const W = 280, H = 66;
  const lo = Math.min(...lat);
  const hi = Math.max(...lat);
  const pad = Math.max(6, (hi - lo) * 0.15);
  const dLo = Math.max(0, lo - pad);
  const dHi = Math.max(dLo + 60, hi + pad);
  const ly = (v) => {
    const t = (v - dLo) / (dHi - dLo);
    return Math.max(3, Math.min(H - 3, H - 4 - t * (H - 10)));
  };
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

    // Synthesized TOOLS/WATCHMAN lanes are derived BEFORE the merge so they
    // land in the same render pass as the server events they were derived
    // from. Merged newest-first; the server feed stays authoritative for
    // everything it does emit.
    synthesizeEvents(snapshot);
    state.feed = incoming
      .concat(state.synthFeed)
      .sort((a, b) => (b.ts || 0) - (a.ts || 0));
    state.lastSnapshot = snapshot;

    renderHeader(snapshot);
    renderIncident(snapshot);
    renderServices(snapshot);
    renderActivity(snapshot);
    renderChronicle(snapshot);
    renderLatencyCard(snapshot);
    renderWatchman(snapshot);
    renderGuardian(snapshot);
    renderMirror(snapshot);
    renderSpiral(snapshot);
    renderThreads(snapshot);
    renderArrival(snapshot);
    renderLineage(snapshot);

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

// ── Bridge heartbeat: now SERVER-SIDE ────────────────────────────────────
//
// v4 fetched http://127.0.0.1:8100/api/heartbeat from the browser on a 30s
// timer. v2 reads it from `snapshot.bridge_heartbeat` instead — the server
// makes one no-auth, ~20s-cached fetch and every viewer shares it. That
// matters: the heartbeat is 9KB and its attribution scan walks 400
// chronicle shard files per call, so N open tabs used to mean N independent
// 400-file walks. It also removes the page's last cross-origin request.
// renderHeaderStats() is the consumer.

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

// ── Aurora toggle (design asks for it toggleable + reduced-motion aware) ──
// The CSS already suppresses the drift animation under
// prefers-reduced-motion; this is the explicit user control on top of that.
// Default follows the motion preference rather than overriding it.
function initAurora() {
  const root = document.documentElement;
  let saved = null;
  try { saved = localStorage.getItem('ss-aurora'); } catch (_) { /* ignore */ }
  const on = saved === null ? !prefersReducedMotion() : saved === 'on';
  root.setAttribute('data-aurora', on ? 'on' : 'off');
  const btn = $('aurora-toggle');
  if (!btn) return;
  btn.setAttribute('aria-pressed', on ? 'true' : 'false');
  btn.addEventListener('click', () => {
    const next = root.getAttribute('data-aurora') === 'on' ? 'off' : 'on';
    root.setAttribute('data-aurora', next);
    btn.setAttribute('aria-pressed', next === 'on' ? 'true' : 'false');
    try { localStorage.setItem('ss-aurora', next); } catch (_) { /* ignore */ }
  });
}

// ── Boot ──────────────────────────────────────────────────────────────────
initCsrf();
initTheme();
initAurora();
initViewportLock();
initSearch();
initBrush();
tickClock();
setInterval(tickClock, 1000);
poll();
