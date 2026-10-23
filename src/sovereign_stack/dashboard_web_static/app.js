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
//                           gate_total_pending_all_substrates,
//                           served_predates_head (true|false|null),
//                           served_predates_head_reason}
//       lineage          : {letters[{bucket, title, date, from,
//                           age_seconds}], counts{}, total}
//   open_threads_index (NEW) — the LAST key, and the ONE that is not simply
//     nullable-on-absence. It carries `status`: 'present' with counts, or
//     'absent' with {reason, last_seen_at, last_seen_age_seconds} and NO
//     counts at all. `null` is still possible and still means the reader
//     raised. Three branches, three renders — see renderOpenThreadsIndex.
//       present : {open_count, never_touched, touched, answered_elsewhere,
//                  duplicate_clusters, duplicate_cluster_members,
//                  cluster_method, by_category[], by_month[], by_shape[]
//                  (each [{name, count}]), generated_at, age_seconds,
//                  stale_after_days, counts_reconcile}
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

// Re-mint the page's CSRF token (review 2026-10-09): the token has a 30-minute
// server TTL and a terminal panel is used for hours. Called on a timer, before
// every stream open, and once on a csrf 403.
function refreshCsrf() {
  return fetch('/session', { cache: 'no-store' })
    .then((r) => (r.ok ? r.json() : null))
    .then((data) => { if (data) csrfToken = data.csrf_token || data.token || data.csrfToken || csrfToken; })
    .catch(() => { /* keep the token we have */ });
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

// SERVED vs HEAD (design A4). The bridge re-reads the stack's HEAD and says
// whether its running process is older than HEAD's commit, or the tree is
// dirty: `served_predates_head` true / false / null, with a reason. null is
// "the bridge could not decide" and is NEVER read as false: it gets a style of
// its own, and so does a bridge too old to send the field (no reason either).
// Strict comparisons only: a truthiness test would fold null into false.
function servedHeadState(hb) {
  const value = hb ? hb.served_predates_head : undefined;
  const reason = hb && typeof hb.served_predates_head_reason === 'string'
    ? hb.served_predates_head_reason
    : null;
  if (value === true) {
    return { cls: 'is-behind-head', note: `served predates HEAD: ${reason || 'no reason given'}` };
  }
  if (value === false) {
    return { cls: null, note: `served does not predate HEAD: ${reason || 'no reason given'}` };
  }
  return {
    cls: 'is-head-unknown',
    note: reason
      ? `served vs HEAD unknown: ${reason}`
      : 'served vs HEAD unknown: this bridge does not report it',
  };
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

  // Both honk counts, each labelled with what it counts (cockpit Phase 2).
  const hc = snapshot.honk_counts || null;
  const honksAll = $('stat-honks-alltime'), honksLedger = $('stat-honks-ledger');
  if (honksAll) {
    // null is "could not read the nape store", never zero (review of bfc5507, F6).
    honksAll.textContent = !hc
      ? '—'
      : (hc.all_time_unacked == null ? 'honks unreadable' : `${hc.all_time_unacked} unacked (all time)`);
    honksAll.title = hc ? hc.all_time_label : '';
    honksAll.classList.toggle('is-muted', !hc);
  }
  if (honksLedger) {
    honksLedger.textContent = hc
      ? (hc.ledger_open == null ? 'ledger: blind' : `${hc.ledger_open} honk signals (ledger since ${hc.ledger_since})`)
      : '—';
    honksLedger.title = hc ? hc.ledger_label : '';
    honksLedger.classList.toggle('is-muted', !hc);
  }

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
    }
    // source_commit cannot go stale the way the version string can — the
    // bridge resolves `version` once at import. The served-vs-HEAD state
    // (design A4) colours the same stat and gives its reason in the title.
    const head = servedHeadState(hb);
    vEl.classList.toggle('is-behind-head', head.cls === 'is-behind-head');
    vEl.classList.toggle('is-head-unknown', head.cls === 'is-head-unknown');
    const commits = hb.source_commit
      ? `source ${hb.source_commit}` + (hb.bridge_commit ? ` · bridge ${hb.bridge_commit}` : '')
      : '';
    vEl.title = [commits, head.note].filter(Boolean).join('\n');
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
    // ...and no served-vs-HEAD colour or reason outlives the data either.
    vEl.classList.remove('is-behind-head', 'is-head-unknown');
    vEl.title = '';
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

// ── OPEN-THREADS INDEX ───────────────────────────────────────────────────
//
// The panel above renders the six newest open threads. This one renders the
// pile, and the reason it exists is a counting problem, not a staleness one:
// `open_threads.limit` is 6 and there were 191 open with 176 never touched,
// so the oldest rows (April, 138 days) can never appear up there. A lid, not
// a lag.
//
// THREE FAILURE SHAPES, THREE DIFFERENT RENDERS, NEVER A ZERO:
//   null                  → the reader itself raised. Generic unavailable.
//   status === 'absent'   → the file is missing/unreadable/malformed. Render
//                           the server's REASON verbatim plus the age of the
//                           last index file that did exist. No counts exist
//                           in that payload, so none can leak into the page.
//   status === 'present'  → real counts, including a real 0 if the catalog
//                           honestly has nothing open.
//
// EVERY TRUNCATED LIST STATES ITS DENOMINATOR. There are 15 categories and
// this column fits about five; a panel showing the top five without saying
// so is the read-side fail-open — it reads as the whole taxonomy.
const OTI_CATEGORY_ROWS = 5;
const OTI_SHAPE_ROWS = 4;
const OTI_MONTH_ROWS = 12;

function otiBreakdown(label, rows, limit) {
  const wrap = el('div', 'oti-group');
  const total = rows.length;
  const shown = rows.slice(0, limit);
  const head = el('div', 'oti-group-head');
  head.append(
    el('span', 'oti-group-label', label),
    el('span', 'oti-group-count', shown.length === total
      ? `${total}`
      : `${shown.length} of ${total}`),
  );
  wrap.appendChild(head);
  if (!total) {
    wrap.appendChild(el('div', 'oti-none', 'none recorded'));
    return wrap;
  }
  // Bar width is relative to the LARGEST BAR IN THIS GROUP, never to the
  // open total — a 54-of-191 bar drawn against 191 is a sliver in a 190px
  // column and the shape of the distribution disappears.
  const peak = Math.max(...rows.map((r) => r.count || 0), 1);
  for (const row of shown) {
    const line = el('div', 'oti-row');
    line.appendChild(el('span', 'oti-name', row.name || '(unnamed)'));
    const track = el('span', 'oti-track');
    const fill = el('span', 'oti-fill');
    fill.style.width = `${Math.max(2, Math.round(((row.count || 0) / peak) * 100))}%`;
    track.appendChild(fill);
    line.appendChild(track);
    line.appendChild(el('span', 'oti-count', String(row.count)));
    wrap.appendChild(line);
  }
  return wrap;
}

function renderOpenThreadsIndex(snapshot) {
  const body = $('otindex-body');
  const note = $('otindex-note');
  const section = snapshot.open_threads_index || null;

  if (!section) {
    panelUnavailable(body, note, 'open-threads index reader failed');
    return;
  }

  body.replaceChildren();

  if (section.status !== 'present') {
    // The absent branch is the whole "no simulated data" rule made visible:
    // the reason the server gave, and how old the last real catalog was.
    note.textContent = 'catalog absent';
    note.className = 'panel-note is-missing';
    body.appendChild(el('div', 'oti-absent-title', 'CATALOG ABSENT'));
    body.appendChild(el('div', 'oti-absent-why',
      section.reason || 'no reason reported'));
    const lastAge = fmtAge(section.last_seen_age_seconds);
    body.appendChild(el('div', 'oti-absent-age', lastAge
      ? `last index file ${lastAge} old · ${section.last_seen_source || ''}`
      : 'no index file has ever landed here'));
    return;
  }

  const staleSeconds = (section.stale_after_days || 0) * 86400;
  const age = section.age_seconds;
  const stale = age != null && staleSeconds > 0 && age > staleSeconds;
  setPanelAge(note, section, `${section.open_count} open`);
  if (stale) note.className = 'panel-note is-stale';

  const stats = el('div', 'oti-stats');
  for (const [value, label] of [
    [section.open_count, 'OPEN'],
    [section.never_touched, 'NEVER TOUCHED'],
  ]) {
    const cell = el('div', 'oti-stat');
    cell.appendChild(el('span', 'oti-stat-num', value == null ? '—' : String(value)));
    cell.appendChild(el('span', 'oti-stat-label', label));
    stats.appendChild(cell);
  }
  body.appendChild(stats);

  const meta = [];
  if (section.answered_elsewhere != null) {
    meta.push(`${section.answered_elsewhere} may be answered elsewhere`);
  }
  if (section.duplicate_clusters != null) {
    meta.push(`${section.duplicate_clusters} near-duplicate clusters`
      + (section.duplicate_cluster_members != null
        ? ` (${section.duplicate_cluster_members} ids)` : ''));
  }
  if (meta.length) body.appendChild(el('div', 'oti-meta', meta.join(' · ')));

  body.appendChild(otiBreakdown('CATEGORY', section.by_category || [], OTI_CATEGORY_ROWS));
  body.appendChild(otiBreakdown('GATE-SHAPE', section.by_shape || [], OTI_SHAPE_ROWS));
  body.appendChild(otiBreakdown('OPENED', section.by_month || [], OTI_MONTH_ROWS));

  const foot = el('div', 'oti-foot');
  const bits = [];
  if (section.generated_at) bits.push(`generated ${fmtRelTime(section.generated_at)}`);
  const fileAge = fmtAge(age);
  if (fileAge) bits.push(`${fileAge} old${stale ? ' · STALE' : ''}`);
  // A cluster number nobody can tie back to the filing beside it is a rumour;
  // the server says which algorithm produced it, so the panel says it too.
  if (section.cluster_method) bits.push(`clusters by ${section.cluster_method}`);
  foot.textContent = bits.join(' · ');
  body.appendChild(foot);

  // The one honest disagreement this panel can have with its own source, and
  // it must be visible rather than averaged away.
  if (section.counts_reconcile === false) {
    body.appendChild(el('div', 'oti-warn',
      `derived ${section.open_count} open, file's coverage line says `
      + `${section.declared_open_including_nested} — counts do not reconcile`));
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
// ── HQ PANEL (cockpit Phase 2) ───────────────────────────────────────────
// The real HQ seat in a pty on the Studio. Output arrives over SSE as base64
// chunks with sequence ids; a reload replays the buffer. Input goes by POST
// on the §2a substrate. The stream carries the page's CSRF token as a query
// parameter because EventSource cannot set headers.
const hq = {
  term: null, fit: null, es: null, lastSeq: 0, running: false, focused: false,
  pending: '', inflight: false, resumedResize: false,
  // The size this page last told the pty (null = unknown). An UNFORCED post of
  // the same size is skipped: every /hq/resize is a SIGWINCH to HQ, and a
  // repeat of an unchanged size is a redraw storm (review 1, item 5). A FORCED
  // post always goes: another viewer may have resized the shared pty while this
  // page's own size never changed, so lastPosted is not the pty's size.
  lastPosted: null, refitPending: false, refitForce: false, forcedThisFrame: false,
  // The latest GET /hq/status (Studio-only), or null when it failed this poll.
  status: null,
  noteSetAt: 0,  // when hqNote last wrote the panel note (Date.now())
};

// The pty has ONE size and this page may be one of several viewers of it (the
// Studio's own window, a second tab, a reviewer's pane). Measured 2026-10-10:
// each viewer posting its own box on resume and on resize re-wrapped HQ's
// terminal in every other viewer. Rule: the viewer the person is working in
// owns the size — a page posts only while its window is the active one
// (`document.hasFocus()`), and once more when the terminal itself takes
// keyboard focus. `force` is for the moments the person just acted here
// (Focus toggle, a click into the terminal).
function hqPostSize(force) {
  if (!hq.running || !hq.term) return false;
  if (!force && !document.hasFocus()) return false;
  const size = { cols: hq.term.cols, rows: hq.term.rows };
  const last = hq.lastPosted;
  const same = !!last && last.cols === size.cols && last.rows === size.rows;
  if (!force && same) return false;
  // Forced posts that land in one animation frame merge into one (window focus
  // and the textarea refocus fire together when the window comes forward).
  // The first one went out, so the reclaim stands.
  if (force && same && hq.forcedThisFrame) return true;
  // Set BEFORE the request goes out, so a second caller in the same frame is
  // compared against it.
  hq.lastPosted = size;
  if (force && !hq.forcedThisFrame) {
    hq.forcedThisFrame = true;
    requestAnimationFrame(() => { hq.forcedThisFrame = false; });
  }
  const clear = () => { if (hq.lastPosted === size) hq.lastPosted = null; };
  hqPostWithRetry('/hq/resize', size).then((res) => { if (!res || !res.ok) clear(); }, clear);
  return true;
}

// Refit the terminal to its box, then tell the pty — after layout settles, so
// a class toggle or a breakpoint crossing is measured, not the frame before.
// Calls inside one frame coalesce; a forced call is never downgraded by a
// pending unforced one. A host with no box (focus mode hiding it, a collapsed
// band) is skipped: the fit addon would propose 2x1 and that would reach the pty.
function hqRefit(force) {
  if (!hq.fit) return;
  hq.refitForce = hq.refitForce || !!force;
  if (hq.refitPending) return;
  hq.refitPending = true;
  requestAnimationFrame(() => {
    const f = hq.refitForce;
    hq.refitPending = false; hq.refitForce = false;
    const host = $('hq-term');
    if (!host || !host.clientWidth || !host.clientHeight) return;
    hq.fit.fit();
    hqPostSize(f);
  });
}

// FOCUS: HQ fills the cockpit and back. The button and Ctrl+Shift+F do the
// same thing; the key is caught in xterm's custom handler BEFORE the shell sees
// it (Ctrl+Shift+F is not a readline or Claude Code binding; plain Ctrl+F is).
function hqSetFocus(on) {
  hq.focused = !!on;
  document.body.classList.toggle('hq-focus', hq.focused);
  const btn = $('hq-focus');
  if (btn) {
    btn.textContent = hq.focused ? 'Unfocus' : 'Focus';
    btn.setAttribute('aria-pressed', String(hq.focused));
  }
  // Own frame, not hqRefit's coalesced one: fit FIRST, then take keyboard
  // focus, so the one forced post carries the new size instead of racing the
  // old one to the server (review 1, item 4). If the terminal does not have
  // focus yet, term.focus() fires the textarea listener, which is that post;
  // if it already has focus, post here.
  requestAnimationFrame(() => {
    const host = $('hq-term');
    if (hq.fit && host && host.clientWidth && host.clientHeight) hq.fit.fit();
    if (!hq.term) return;
    if (document.activeElement === hq.term.textarea) hqPostSize(true);
    else hq.term.focus();
  });
}

// `ev.repeat` is checked at the call sites, not here: a held key must still be
// swallowed (else xterm sends ^F to the shell), it just must not toggle again.
function hqIsFocusKey(ev) {
  return ev.type === 'keydown' && ev.ctrlKey && ev.shiftKey && !ev.altKey && !ev.metaKey && ev.code === 'KeyF';
}

// ── Terminal text size (cockpit integration, 2026-10-10) ──
// Buttons only. No key chord: Ctrl+Shift+- is Ctrl+_, the undo binding of
// Claude Code and readline, so a chord would steal it (review 2026-10-10).
// Ctrl+Shift+F (focus) is the one chord the panel keeps. Every change refits and posts the
// size through hqRefit(true): the person just acted here. Remembered per
// viewer in localStorage; a missing or blocked store falls back to 13.
const HQ_FONT_MIN = 9, HQ_FONT_MAX = 24, HQ_FONT_DEFAULT = 13;
const HQ_FONT_KEY = 'ss-hq-font';

function hqSavedFontSize() {
  let v = NaN;
  try { v = parseInt(localStorage.getItem(HQ_FONT_KEY) || '', 10); } catch (_) { /* blocked */ }
  return Number.isFinite(v) && v >= HQ_FONT_MIN && v <= HQ_FONT_MAX ? v : HQ_FONT_DEFAULT;
}

function hqSetFontSize(n) {
  if (!hq.term) return;
  const v = Math.max(HQ_FONT_MIN, Math.min(HQ_FONT_MAX, Math.round(n)));
  if (v === hq.term.options.fontSize) return;
  hq.term.options.fontSize = v;
  try { localStorage.setItem(HQ_FONT_KEY, String(v)); } catch (_) { /* blocked */ }
  hqRefit(true);
}

// ── Copy on select ──
// The selection goes to the clipboard once it settles (the event fires on
// every drag step). Write only; the page never reads the clipboard.
let hqCopyTimer = null;
function hqCopySelection() {
  clearTimeout(hqCopyTimer);
  hqCopyTimer = setTimeout(() => {
    if (!hq.term || !hq.term.hasSelection()) return;
    const sel = hq.term.getSelection();
    if (!sel || !navigator.clipboard || !navigator.clipboard.writeText) return;
    navigator.clipboard.writeText(sel).then(
      () => hqNote(`copied ${sel.length} characters`),
      () => { /* no clipboard permission: nothing copied, nothing claimed */ },
    );
  }, 150);
}

function hqB64ToBytes(b64) {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function hqBytesToB64(str) {
  const bytes = new TextEncoder().encode(str);
  let bin = '';
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin);
}

// A "refused" note outlives the poll that would overwrite it, but only for
// HQ_REFUSED_NOTE_MS (review 2026-10-10): it used to persist for the life of
// the session. The time it was set is kept so the poll can let it go.
const HQ_REFUSED_NOTE_MS = 60 * 1000;

function hqNote(text) {
  const note = $('hq-note');
  if (note) note.textContent = text;
  hq.noteSetAt = Date.now();
}

function hqHoldRefusedNote(note) {
  return !!note && /refused/.test(note.textContent) && Date.now() - hq.noteSetAt < HQ_REFUSED_NOTE_MS;
}

// POST on the substrate; on a csrf 403 re-mint the token and retry once.
async function hqPostWithRetry(path, body) {
  let res = await postAction(path, body);
  if (res && res.status === 403 && res.data && /csrf/i.test(res.data.error || '')) {
    await refreshCsrf();
    res = await postAction(path, body);
  }
  return res;
}

// Serial input queue: one POST in flight, later keystrokes coalesce into the
// next one, so bytes reach the pty in the order they were typed.
async function hqFlushInput() {
  if (hq.inflight || !hq.pending || !hq.running) return;
  const chunk = hq.pending;
  hq.pending = '';
  hq.inflight = true;
  try {
    const res = await hqPostWithRetry('/hq/input', { data: hqBytesToB64(chunk) });
    if (!res || !res.ok) hqNote(`input refused (${res ? res.status : 'no response'})`);
  } finally {
    hq.inflight = false;
    if (hq.pending) hqFlushInput();
  }
}

async function hqOpenStream() {
  if (hq.es) { hq.es.close(); hq.es = null; }
  await refreshCsrf();
  if (!csrfToken) return;
  const es = new EventSource(`/hq/stream?token=${encodeURIComponent(csrfToken)}&after=${hq.lastSeq}`);
  es.addEventListener('hello', (ev) => {
    try {
      const h = JSON.parse(ev.data);
      if (h.truncated_before && hq.lastSeq === 0 && hq.term) {
        hq.term.writeln('\x1b[2m[earlier output dropped: replay buffer cap]\x1b[0m');
      }
    } catch (e) { /* ignore */ }
  });
  es.onmessage = (ev) => {
    if (ev.lastEventId) hq.lastSeq = parseInt(ev.lastEventId, 10) || hq.lastSeq;
    if (hq.term && ev.data) hq.term.write(hqB64ToBytes(ev.data));
  };
  es.addEventListener('exit', () => {
    if (hq.term) hq.term.writeln('\r\n\x1b[2m[HQ session ended]\x1b[0m');
    es.close(); hq.es = null; hq.running = false;
    // If another viewer restarts HQ before the next poll, this page must
    // still send its resume post against the new pty.
    hq.lastPosted = null; hq.resumedResize = false;
    const btn = $('hq-start'); if (btn) btn.hidden = false;
  });
  es.onerror = () => {
    // A non-200 (an expired token) closes the source for good; drop it so the
    // status poll reopens a fresh stream with a fresh token.
    if (es.readyState === EventSource.CLOSED) { hq.es = null; }
  };
  hq.es = es;
}

async function hqStart() {
  if (!hq.term) return;
  const cols = hq.term.cols, rows = hq.term.rows;
  const res = await hqPostWithRetry('/hq/start', { cols, rows });
  if (!res || !res.ok) {
    hqNote((res && res.data && res.data.error) || 'start refused');
    return;
  }
  hq.lastPosted = { cols, rows };  // /hq/start carried the size
  hq.running = true;
  // A refit while the start was in flight could not post (not running yet);
  // carry the current size now if it moved.
  if (hq.term.cols !== cols || hq.term.rows !== rows) hqPostSize(true);
  const btn = $('hq-start'); if (btn) btn.hidden = true;
  hqOpenStream();
}

// ── File drop to path (cockpit integration, 2026-10-10) ──
// A file dragged onto the terminal used to reach HQ as a Finder icon, not the
// bytes. Each dropped File is POSTed raw to /hq/drop (same §2a substrate as
// /hq/input); the server writes it under ~/.local/share/hq-cockpit/drops/ and
// answers its absolute path, which is pasted through xterm (onData, the SAME
// input queue as the keystrokes) so it lands at the prompt in order. The server's checks are the
// rule; the ones here only spare a doomed upload.
const HQ_DROP_MAX_BYTES = 25 * 1024 * 1024;
const HQ_DROP_EXT = new Set(['txt', 'md', 'json', 'jsonl', 'csv', 'log', 'png', 'jpg', 'jpeg', 'gif',
  'pdf', 'py', 'js', 'ts', 'html', 'css', 'yaml', 'yml', 'toml', 'patch', 'diff']);

async function hqPostDrop(file) {
  const send = () => {
    const headers = {
      'Content-Type': 'application/octet-stream',
      // fetch refuses a non-Latin-1 header value; the server unquotes.
      'X-Filename': encodeURIComponent(file.name),
    };
    if (csrfToken) headers['X-CSRF-Token'] = csrfToken;
    return fetch('/hq/drop', { method: 'POST', headers, body: file, cache: 'no-store' })
      .then(async (r) => {
        let data = null;
        try { data = await r.json(); } catch (_) { /* no JSON body */ }
        return { ok: r.ok, status: r.status, data };
      });
  };
  let res = await send();
  if (res.status === 403 && res.data && /csrf/i.test(res.data.error || '')) {
    await refreshCsrf();
    res = await send();
  }
  return res;
}

async function hqDropFiles(files) {
  for (const file of files) {
    const ext = (file.name.split('.').pop() || '').toLowerCase();
    if (!file.name.includes('.') || !HQ_DROP_EXT.has(ext)) { hqNote(`drop refused: ${file.name} (file type not allowed)`); continue; }
    if (file.size > HQ_DROP_MAX_BYTES) { hqNote(`drop refused: ${file.name} (over 25 MB)`); continue; }
    if (!hq.running) { hqNote('drop refused: HQ is not running'); return; }
    let res;
    try { res = await hqPostDrop(file); } catch (_) { res = null; }
    if (!res || !res.ok || !res.data || !res.data.path) {
      const why = res && res.data && res.data.error ? res.data.error : (res ? `status ${res.status}` : 'no response');
      hqNote(`drop refused: ${file.name} (${why})`);
      continue;
    }
    // Pasted, not typed: xterm wraps it in bracketed-paste markers when the
    // app has that mode on, and it still arrives through onData and the
    // pending queue, in order with the keystrokes.
    if (hq.term) hq.term.paste(`${res.data.path} `);
    // Cleared by the next poll's renderHqPanel, which rewrites the note.
    hqNote(`dropped ${res.data.name}, ${res.data.bytes} B`);
  }
}

function hqIsFileDrag(ev) {
  const types = ev.dataTransfer && ev.dataTransfer.types;
  return !!types && Array.prototype.indexOf.call(types, 'Files') !== -1;
}

function hqInitDrop(host) {
  const over = (ev) => {
    if (!hqIsFileDrag(ev)) return;
    ev.preventDefault();  // else the browser navigates to the dropped file
    if (ev.dataTransfer) ev.dataTransfer.dropEffect = hq.running ? 'copy' : 'none';
    host.classList.add('is-drop-target');
  };
  host.addEventListener('dragenter', over);
  host.addEventListener('dragover', over);
  host.addEventListener('dragleave', (ev) => {
    if (!host.contains(ev.relatedTarget)) host.classList.remove('is-drop-target');
  });
  host.addEventListener('drop', (ev) => {
    if (!hqIsFileDrag(ev)) return;
    ev.preventDefault();
    host.classList.remove('is-drop-target');
    const files = Array.from((ev.dataTransfer && ev.dataTransfer.files) || []);
    if (files.length) hqDropFiles(files);
  });
}

function hqTokenReady() {
  return new Promise((resolve) => {
    const tick = () => { if (csrfToken) resolve(); else setTimeout(tick, 100); };
    tick();
  });
}

function initHqPanel() {
  const host = $('hq-term');
  if (!host || typeof Terminal === 'undefined') return;
  hq.term = new Terminal({ cursorBlink: true, scrollback: 5000, fontSize: hqSavedFontSize(), convertEol: false });
  if (typeof FitAddon !== 'undefined') {
    hq.fit = new FitAddon.FitAddon();
    hq.term.loadAddon(hq.fit);
  }
  hq.term.open(host);
  if (hq.fit) hq.fit.fit();
  hq.term.onData((data) => {
    if (!hq.running) return;
    hq.pending += data;
    hqFlushInput();
  });
  // Focus key, intercepted before xterm turns it into bytes for the shell.
  hq.term.attachCustomKeyEventHandler((ev) => {
    if (hqIsFocusKey(ev)) {
      ev.preventDefault();
      if (!ev.repeat) hqSetFocus(!hq.focused);
      return false;
    }
    return true;
  });
  hq.term.onSelectionChange(hqCopySelection);
  // The same key anywhere else on the page (the terminal does not have focus).
  document.addEventListener('keydown', (ev) => {
    if (hqIsFocusKey(ev) && !host.contains(ev.target)) {
      ev.preventDefault();
      if (!ev.repeat) hqSetFocus(!hq.focused);
    }
  });
  hqInitDrop(host);
  // A click into the terminal makes this viewer the size owner (see hqPostSize).
  if (hq.term.textarea) hq.term.textarea.addEventListener('focus', () => hqPostSize(true));
  window.addEventListener('resize', () => hqRefit(false));
  // Bringing the window forward reclaims the size (another viewer may own it,
  // or a resize while unfocused fitted locally but did not post): forced.
  window.addEventListener('focus', () => hqPostSize(true));
  // The box also changes without a window resize: the attention strip arriving
  // or wrapping, the incident strip, the banner, the focus toggle, breakpoint
  // crossings. hqRefit coalesces to one frame (review 1, item 1).
  if (typeof ResizeObserver !== 'undefined') {
    new ResizeObserver(() => hqRefit(false)).observe(host);
  }
  const startBtn = $('hq-start');
  if (startBtn) startBtn.addEventListener('click', hqStart);
  const fontDown = $('hq-font-down'), fontUp = $('hq-font-up');
  if (fontDown) fontDown.addEventListener('click', () => hqSetFontSize(hq.term.options.fontSize - 1));
  if (fontUp) fontUp.addEventListener('click', () => hqSetFontSize(hq.term.options.fontSize + 1));
  const focusBtn = $('hq-focus');
  if (focusBtn) focusBtn.addEventListener('click', () => hqSetFocus(!hq.focused));
  setInterval(refreshCsrf, 20 * 60 * 1000);
  // Wait for the token, then resume an existing session or auto-start when the
  // launcher asked for it. The start parameter is consumed once: a reload or a
  // browser session restore must not start HQ again on its own.
  hqTokenReady().then(() => fetch('/hq/status', { cache: 'no-store' })).then((r) => r.json()).then((st) => {
    const params = new URLSearchParams(window.location.search);
    const wantStart = params.get('start') === 'hq';
    if (wantStart) {
      params.delete('start');
      const qs = params.toString();
      history.replaceState({}, '', window.location.pathname + (qs ? `?${qs}` : ''));
    }
    if (st && st.running) {
      hq.running = true; if (startBtn) startBtn.hidden = true; hqOpenStream(); return;
    }
    if (wantStart) hqStart();
  }).catch(() => { /* status unreachable: the poll will say so */ });
}

// `listening` is the Phase 3 push link — whether the console can hand batches
// INTO HQ's session over Claude Code's cross-session socket — not the iMessage
// listener. The snapshot carries only the boolean; the reason, the waiting count,
// the last error, blind sources and the last hand-over time come from the
// Studio-only GET /hq/status `push` object (fixed server vocabulary, rendered as
// text). "handed" and "sent to HQ's review queue", never "delivered" or "held":
// the socket returns no receipt, and the console knows only what it launched
// with, not Claude Code's effective policy (HQ review A9).
function hqLinkWord(hp, detail) {
  const p = hq.status && hq.status.push;
  const waiting = p && typeof p.events_waiting === 'number' && p.events_waiting > 0
    ? ` · ${p.events_waiting} waiting` : '';
  // A source the poller could not read is said, never folded into "on" (C5).
  const blind = p && Array.isArray(p.blind) && p.blind.length
    ? ` · blind to ${p.blind.filter((b) => typeof b === 'string').join(', ')}` : '';
  const last = detail && p && typeof p.last_handed_at === 'string' ? ` · last handed ${p.last_handed_at}` : '';
  if (hp.listening) return `push link on${last}${blind}${waiting}`;
  // Inbound hold (the shipped default): measured, Claude Code holds each batch
  // with no review dialog, so it prints in the panel and never reaches HQ.
  if (p && p.inbound === 'hold' && !p.last_error) {
    return `push link held, batches print in the panel and do not reach HQ${last}${blind}${waiting}`;
  }
  const why = p && typeof p.reason === 'string' && p.reason ? ` (${p.reason})` : '';
  const err = p && typeof p.last_error === 'string' && p.last_error ? ` · last error ${p.last_error}` : '';
  return `push link off${why}${err}${blind}${waiting}`;
}

// Three missed probe cycles: HQ_PANEL_STATUS_INTERVAL_S defaults to 300 s on the server.
const HQ_PROBE_STALE_S = 900;

// WORKING / WAITING (cockpit integration, 2026-10-10). One word from the
// server's own clock: `seq_idle_seconds` is how long the pty output has been
// still, measured in the console process. Under HQ_STILL_S it is "working";
// at or past it HQ is at its prompt or holding: "waiting". No field (an older
// server, no output yet) is "unknown", never a guess from a client timer.
// Typing and resizes also produce output, so a keystroke reads as working
// for HQ_STILL_S; that is the honest cost of reading only the byte stream.
const HQ_STILL_S = 20;

// The activity stamps and the comms line come from GET /hq/status, which is
// Studio-only and Host-checked; /snapshot.json is readable from any origin and
// never carries them (adversarial review, 2026-10-10). Fetched once per poll;
// any failure leaves null, which renders "unknown" / "comms unavailable",
// never the previous value.
async function hqFetchStatus() {
  try {
    const st = await fetchJsonTimed('/hq/status');
    return st && typeof st === 'object' ? st : null;
  } catch (_) {
    return null;  // error, non-200 or the 5 s abort: the same failure path
  }
}

function hqActivityWord(st) {
  if (!st) return 'unknown';
  if (!st.running) return 'off';
  const idle = st.seq_idle_seconds;
  if (typeof idle !== 'number' || !Number.isFinite(idle)) return 'unknown';
  return idle < HQ_STILL_S ? 'working' : 'waiting';
}

// `why` names the two different absences: the console itself unreachable (the
// poll failed) versus the console up but its hq_panel section missing.
function renderHqHeader(hp, why) {
  const main = $('stat-hq-main'), sub = $('stat-hq-sub');
  if (!main || !sub) return;
  if (!hp) {
    main.textContent = why === 'section' ? 'HQ status unavailable' : 'HQ unreachable';
    sub.textContent = why === 'section' ? 'hq_panel section missing' : 'console unreachable';
    main.className = 'stat-value is-muted'; sub.className = 'stat-value is-muted';
    return;
  }
  const exit = hp.last_exit;
  const word = hqActivityWord(hq.status);
  if (hp.running) {
    main.textContent = `${word} · ${hp.age_seconds != null ? fmtAge(hp.age_seconds) : '—'} open`;
    main.className = 'stat-value ' + (word === 'working' ? 'is-live' : 'is-muted');
  } else if (typeof exit === 'number' && exit !== 0) {
    main.textContent = `DOWN · exit ${exit}`;
    main.className = 'stat-value is-down';
  } else if (exit === 0) {
    main.textContent = 'off · exit 0';
    main.className = 'stat-value is-muted';
  } else {
    main.textContent = 'off';
    main.className = 'stat-value is-muted';
  }
  // chat_db_readable is fail-closed on the server once probed (false on ANY
  // failure); null means the probe has not run yet, which is not "blocked".
  const r = hp.chat_db_readable;
  let dbWord = r === true ? 'chat.db ok' : r === false ? 'chat.db blocked' : 'chat.db not probed';
  // A dead probe thread must not read "ok" forever: an old or unparseable
  // checked_at marks the word stale (fail-closed on a bad timestamp).
  let stale = false;
  if (r === true || r === false) {
    const t = Date.parse(hp.checked_at || '');
    stale = !Number.isFinite(t) || (Date.now() - t) / 1000 > HQ_PROBE_STALE_S;
    if (stale) dbWord += ' (stale)';
  }
  sub.textContent = `${dbWord} · ${hqLinkWord(hp)}`;
  sub.className = 'stat-value is-muted' + (stale ? ' is-stale' : '');
}

// COMMS line (cockpit integration, 2026-10-10). From /hq/status's `comms`,
// which the server builds from the launchd spool files only. HH:MM is sliced
// from the spool's own Eastern stamp (no browser timezone math); the age is
// the server's. A missing section is "comms unavailable", never a zero, and an
// unreadable inbound file is its own "unavailable" state, never "none".
function renderHqComms(st) {
  const line = $('hq-comms');
  if (!line) return;
  const c = st && st.comms;
  if (!c) { line.textContent = 'comms unavailable'; line.className = 'hq-comms is-stale'; return; }
  if (c.inbound_readable !== true) {
    line.textContent = 'comms unavailable (inbound spool file unreadable)';
    line.className = 'hq-comms is-stale';
    return;
  }
  const parts = [];
  const at = typeof c.last_inbound_at === 'string' ? c.last_inbound_at : '';
  const hm = /^\d{4}-\d{2}-\d{2}[ T](\d{2}:\d{2})/.exec(at);
  if (hm) {
    const ago = typeof c.last_inbound_age_s === 'number' ? ` (${fmtAge(c.last_inbound_age_s)} ago)` : '';
    parts.push(`last text from Anthony: ${hm[1]}${ago}`);
  } else {
    parts.push('last text from Anthony: none in spool');
  }
  const spoolAge = typeof c.spool_age_s === 'number' ? `, ${fmtAge(c.spool_age_s)} old` : '';
  const spoolBad = !c.spool_ok || c.spool_stale;
  parts.push(c.spool_ok && !c.spool_stale ? 'spool ok' : `spool ${c.spool_ok ? 'stale' : 'not ok'}${spoolAge}`);
  line.textContent = parts.join(' · ');
  line.className = 'hq-comms' + (spoolBad ? ' is-stale' : '');
}

// The panel's border follows the same word as the header: accent while HQ's
// output moves, muted once it has been still for HQ_STILL_S. The transition is
// killed under prefers-reduced-motion (the global §13 rule and the panel's own).
function hqSetActivityClass(word) {
  const panel = $('hq-panel');
  if (!panel) return;
  panel.classList.toggle('is-working', word === 'working');
  panel.classList.toggle('is-waiting', word === 'waiting');
}

function renderHqPanel(snapshot) {
  renderHqComms(hq.status);
  hqSetActivityClass(hqActivityWord(hq.status));
  const hp = snapshot && snapshot.hq_panel;
  const note = $('hq-note');
  const banner = $('hq-banner');
  const startBtn = $('hq-start');
  if (note) note.classList.remove('is-muted');  // set by the poll-failure path
  renderHqHeader(hp || null, 'section');
  if (!hp) { if (note) note.textContent = 'status unavailable'; return; }
  if (hp.running) {
    const age = hp.age_seconds != null ? fmtAge(hp.age_seconds) : '';
    const kb = hp.transcript_bytes != null ? `${(hp.transcript_bytes / 1024).toFixed(0)} KB` : '';
    const parts = ['running', age ? `${age} open` : '', kb ? `${kb} transcript` : '', hqLinkWord(hp, true)];
    if (note && !hqHoldRefusedNote(note)) note.textContent = parts.filter(Boolean).join(' · ');
    if (!hq.running) { hq.running = true; if (startBtn) startBtn.hidden = true; }
    // Reopen whenever the stream is gone (a closed source after an expired
    // token, a page that resumed before its token arrived).
    if (hp.running && !hq.es && csrfToken) hqOpenStream();
    // Resume: this viewer posts its size only if it is the active window (see
    // hqPostSize) — a background tab or a second viewer must not re-wrap the
    // terminal the person is actually looking at.
    // Latched only when a post actually went out, so an unfocused resume
    // retries on the next poll (and the window focus listener covers the rest).
    if (!hq.resumedResize && hq.term && csrfToken) {
      if (hqPostSize(false)) hq.resumedResize = true;
    }
  } else {
    if (note) note.textContent = hp.last_exit != null ? `not running (last exit ${hp.last_exit})` : 'not running';
    if (startBtn) startBtn.hidden = false;
    hq.running = false;
    hq.resumedResize = false;
    hq.lastPosted = null;  // a new session's pty starts from its own size
  }
  // The banner lives in a reserved slot: showing it never resizes the terminal.
  if (banner) banner.hidden = !hp.over_24h;
}

// ── NEEDS A LOOK strip (cockpit layout, 2026-10-10) ──────────────────────
// Read-only. Every number is a snapshot field the console already carries;
// nothing here acknowledges. A source that could not be measured says so —
// never a zero that reads as "nothing waiting". The thread count is the
// open-thread backlog (one open_thread signal per open thread), so it is
// labelled open threads (design A3).
const ATT_SOURCES = [
  ['thread', 'open threads'], ['watchman', 'watchman'], ['honk', 'honk signals (ledger)'],
  ['proposal', 'proposals'], ['guardian', 'guardian'], ['halt', 'halts'], ['decision', 'decisions'],
];

function attSeg(cls, before, strong, after) {
  const seg = el('span', 'att-seg' + (cls ? ' ' + cls : ''));
  if (before) seg.append(document.createTextNode(before));
  seg.appendChild(el('b', null, strong));
  if (after) seg.append(document.createTextNode(after));
  return seg;
}

// `ingestion` in plain words; 'ok' says nothing.
const ATT_INGESTION_WORD = {
  never: 'not scanned yet', stale: 'stale', refreshing: 'refreshing', partial: 'partial sweep',
  degraded: 'degraded', error: 'error', marker_error: 'marker unreadable', config_error: 'config error',
};

function attIngestionWord(u) {
  return ATT_INGESTION_WORD[u.ingestion] || String(u.ingestion || 'unknown');
}

function renderAttentionStrip(snapshot) {
  const strip = $('attention-strip'), segs = $('att-segs');
  if (!strip || !segs) return;
  const out = [];
  if (!snapshot) {
    // The poll failed: nothing on this strip may keep its previous value.
    out.push(attSeg('is-issue', '', 'console unreachable', ''));
  } else {
    const u = snapshot.unacked_signals || null;
    const g = snapshot.guardian || null;
    const w = (snapshot.watchman && snapshot.watchman.summary) || null;
    if (!u) {
      out.push(attSeg('is-issue', 'signal ledger ', 'unavailable', ''));
    } else if (u.measured === false) {
      // The one red ledger line: nothing could be measured at all.
      out.push(attSeg('is-issue', 'signal ledger ', u.error ? 'error' : 'blind', ` (${attIngestionWord(u)})`));
    } else {
      if (u.ingestion && u.ingestion !== 'ok') {
        out.push(attSeg('is-old', 'ledger ', attIngestionWord(u), ''));
      }
      // By source only (design A3): summed across sources the count is mostly
      // the open-thread backlog, so the field carries no total and neither
      // does the strip. Each source says its own week-old count.
      for (const [key, label] of ATT_SOURCES) {
        const d = u.by_source_detail && u.by_source_detail[key];
        if (!d) continue;
        const st = u.source_status && u.source_status[key];
        if (d.open == null) {
          // Never a 0 or "null" for a source that was not read.
          out.push(st === 'not_configured'
            ? attSeg('', `${label} `, 'not configured', '')
            : attSeg('is-issue', `${label} `, 'blind', ''));
          continue;
        }
        const seg = attSeg(d.stale_7d ? 'is-old' : '', `${label} `, String(d.open), '');
        if (d.stale_7d) seg.appendChild(el('span', 'att-old', ` (${d.stale_7d} over a week)`));
        out.push(seg);
      }
    }
    if (!g) {
      out.push(attSeg('is-issue', '', 'guardian unavailable', ''));
    } else if (g.issue_count === 0) {
      out.push(attSeg('', 'guardian ', 'clear', ''));
    } else if (typeof g.issue_count === 'number') {
      out.push(attSeg('is-issue', 'guardian: ', (g.issues || []).join('; ') || `${g.issue_count} issue(s)`, ''));
    } else {
      out.push(attSeg('is-issue', 'guardian ', 'unmeasured', ''));
    }
    if (!w) {
      out.push(attSeg('is-issue', '', 'watchman unavailable', ''));
    } else {
      const age = w.last_sweep_age_seconds != null ? fmtAge(w.last_sweep_age_seconds) : null;
      out.push(attSeg('', 'watchman ', String(w.status || '—'), age ? `, last sweep ${age} ago` : ''));
    }
  }
  segs.textContent = '';
  out.forEach((seg, i) => {
    if (i) segs.appendChild(el('span', 'att-sep', '·'));
    segs.appendChild(seg);
  });
  strip.hidden = out.length === 0;
}

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
// Both page fetches (snapshot and /hq/status) abort after POLL_FETCH_TIMEOUT_MS
// (review pass 2, 2026-10-10): a hung console must reach the failure path
// ("unknown", "comms unavailable", UNREACHABLE), not freeze the poll loop.
// The timer covers the body read too, and a non-200 throws like an error.
const POLL_FETCH_TIMEOUT_MS = 5000;

async function fetchJsonTimed(url, ms = POLL_FETCH_TIMEOUT_MS) {
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), ms);
  try {
    const r = await fetch(url, { cache: 'no-store', signal: ac.signal });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return await r.json();
  } finally {
    clearTimeout(timer);
  }
}

async function poll() {
  const status = $('poll-status');
  const t0 = performance.now();
  try {
    const snapshot = await fetchJsonTimed('/snapshot.json');
    const rtt = performance.now() - t0;
    hq.status = await hqFetchStatus();

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
    renderOpenThreadsIndex(snapshot);
    renderArrival(snapshot);
    renderLineage(snapshot);
    renderHqPanel(snapshot);
    renderAttentionStrip(snapshot);

    status.className = '';
    status.textContent = `live · last poll ${fmtClock(Date.now() / 1000)} · polling /snapshot.json every 3s`;
    state.lastPollAt = Date.now();
  } catch (err) {
    status.className = 'is-err';
    status.textContent = `poll error · ${err.message} · retrying`;
    const pill = $('status-pill');
    pill.className = 'status-pill is-down';
    $('status-label').textContent = 'UNREACHABLE';
    // The page's own rule: never keep a previous value beside UNREACHABLE.
    renderHqHeader(null);
    renderAttentionStrip(null);
    hq.status = null;
    renderHqComms(null);
    hqSetActivityClass('unknown');
    // Same rule for the panel note; the terminal itself is left alone.
    const hqNoteEl = $('hq-note');
    if (hqNoteEl) { hqNoteEl.textContent = 'console unreachable'; hqNoteEl.classList.add('is-muted'); }
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
initHqPanel();
initTheme();
initAurora();
initViewportLock();
initSearch();
initBrush();
tickClock();
setInterval(tickClock, 1000);
poll();
