"""
Sovereign Stack web dashboard.

A small stdlib http.server that serves:
  GET /                  — single-page dashboard HTML (dark, real-time)
  GET /snapshot.json     — current DashboardState as JSON (poll-friendly)
  GET /events            — SSE stream pushing snapshot updates
  GET /static/<file>     — bundled static assets

No third-party dependencies. The page polls /snapshot.json every 3s by
default; an /events SSE channel exists for clients that want push updates.

Default port: 3435 (next to the MCP-SSE server on 3434).

Design notes (frontend):
  * Minimal dark theme — GitHub-style (#0d1117 / #161b22 / mono accent).
  * Inter font stack with system fallback so the page renders before
    web fonts arrive.
  * Pill-shaped status badges with semantic colors (green/amber/red/gray).
  * Live activity feed renders as a scrollable column, newest on top,
    category-coded by a left border accent.
  * Layout collapses to single-column under 800px wide (phone-friendly).
  * No JS framework — vanilla DOM updates. Keeps the page <50KB total.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import hmac
import ipaddress
import json
import math
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
from collections import deque
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path

from . import connectivity, dashboard, dashboard_readers, hq_panel, hq_push, nape_daemon
from .signal_ledger import heartbeat_field

# ── Shared activity feed + background watcher ──────────────────────────────
#
# A single process-wide ActivityFeed populated by a daemon thread that
# watches the same filesystem signals the TUI dashboard does. The /snapshot
# endpoint includes feed.to_list(limit=N) so browser polling sees a live
# feed without needing SSE plumbing on the client.

_GLOBAL_FEED = dashboard.ActivityFeed(maxlen=200)
_FEED_LIMIT_IN_SNAPSHOT = 30
_WATCHER_INTERVAL = 2.0  # seconds — faster than client poll, so events
# land in the next snapshot poll
_watcher_started = False
_watcher_lock = threading.Lock()

# Per-service health-probe latency samples, sampled on the watcher's own
# ~2s cadence (not on client poll rate — see BUILD_SPEC.md §1b) and read
# by build_snapshot() to compute p95_probe_ms. Only endpoints with a
# health_url (sse, bridge) ever get entries; deque(maxlen=200) caps memory.
_SERVICE_P95: dict[str, deque[float]] = {
    ep.name: deque(maxlen=200) for ep in connectivity.ENDPOINTS if ep.health_url
}


def _p95(values: list[float]) -> float | None:
    """Nearest-rank 95th percentile, rounded to 2dp. None on empty input."""
    if not values:
        return None
    data = sorted(values)
    idx = max(0, math.ceil(0.95 * len(data)) - 1)
    return round(data[idx], 2)


def _watcher_loop() -> None:
    """Background watcher — populates _GLOBAL_FEED. Daemon thread, runs
    until process exit. Mirrors dashboard.run_loop's filesystem watchers,
    plus git-commit polling and launchd service-state tracking (added
    2026-04-25 to widen the live feed beyond chronicle-only activity)."""
    root = Path(
        os.environ.get(
            "SOVEREIGN_ROOT",
            Path.home() / ".sovereign",
        )
    )
    repo_path = Path(__file__).resolve().parent.parent.parent
    chronicle_index = dashboard._MtimeIndex()
    halts_index = dashboard._MtimeIndex()
    decisions_index = dashboard._MtimeIndex()
    honks_index = dashboard._MtimeIndex()

    # Git: track the SHA of the latest commit we've already surfaced so we
    # don't repeat-emit on every poll.
    last_commit_sha: str | None = None
    initial_commits = dashboard._git_recent_commits(repo_path, limit=1)
    if initial_commits:
        last_commit_sha = initial_commits[0]["sha"]

    # launchd: track the per-label state + pid so we can detect transitions.
    service_labels = [
        "com.templetwo.sovereign-sse",
        "com.templetwo.sovereign-bridge",
        "com.templetwo.cloudflared-tunnel",
        "com.templetwo.comms-dispatcher",
        "com.templetwo.comms-listener",
        "com.templetwo.sovereign.uncertainty",
        "com.templetwo.sovereign.metabolize",
    ]
    last_service_state: dict[str, dict] = dashboard._launchctl_service_states(
        service_labels,
    )

    # Seed indices so the first iteration doesn't dump everything as "new".
    chronicle_index.diff(
        dashboard._list_paths(
            root / "chronicle" / "insights",
            "*.jsonl",
            recursive=True,
        )
    )
    chronicle_index.diff(
        dashboard._list_paths(
            root / "chronicle" / "open_threads",
            "*.jsonl",
            recursive=True,
        )
    )
    halts_index.diff(
        dashboard._list_paths(
            root / "daemons" / "halts",
            "*.md",
        )
    )
    decisions_index.diff(
        dashboard._list_paths(
            root / "decisions",
            "metabolize_*.md",
        )
    )
    honks_index.diff([root / "nape" / "honks.jsonl"])

    _GLOBAL_FEED.add(
        dashboard.CAT_STARTUP,
        "watcher started — seeding from filesystem state",
    )

    while True:
        try:
            for jsonl in chronicle_index.diff(
                dashboard._list_paths(
                    root / "chronicle" / "insights",
                    "*.jsonl",
                    recursive=True,
                )
            ):
                # §5.4: insight tails pass chronicle_root so protected records
                # withhold their content before the 80-char feed slice.
                tail = dashboard.read_chronicle_tail(jsonl, root / "chronicle")
                if tail:
                    layer = tail.get("layer", "?")
                    content = (tail.get("content") or "")[:80]
                    _GLOBAL_FEED.add(
                        dashboard.CAT_INSIGHT,
                        f"[{layer}] {content}…",
                    )

            for jsonl in chronicle_index.diff(
                dashboard._list_paths(
                    root / "chronicle" / "open_threads",
                    "*.jsonl",
                    recursive=True,
                )
            ):
                tail = dashboard.read_chronicle_tail(jsonl)
                if tail:
                    q = (tail.get("question") or "")[:80]
                    _GLOBAL_FEED.add(dashboard.CAT_THREAD, q)

            for halt in halts_index.diff(
                dashboard._list_paths(
                    root / "daemons" / "halts",
                    "*.md",
                )
            ):
                _GLOBAL_FEED.add(
                    dashboard.CAT_HALT,
                    f"halt note: {halt.name}",
                )

            for dec in decisions_index.diff(
                dashboard._list_paths(
                    root / "decisions",
                    "metabolize_*.md",
                )
            ):
                _GLOBAL_FEED.add(
                    dashboard.CAT_DECISION,
                    f"new metabolize digest: {dec.name}",
                )

            if honks_index.diff([root / "nape" / "honks.jsonl"]):
                recent = dashboard.read_recent_honks(
                    root / "nape" / "honks.jsonl",
                    limit=3,
                )
                for h in recent:
                    _GLOBAL_FEED.add(
                        dashboard.CAT_HONK,
                        f"[{h.get('level', '?')}] {h.get('pattern', '?')}: "
                        f"{h.get('trigger_tool', '?')}",
                    )

            # ── Git-commit poller (Option A) ──
            recent_commits = dashboard._git_recent_commits(
                repo_path,
                limit=5,
            )
            if recent_commits:
                # Walk newest-first; emit until we hit the last seen sha.
                new_commits = []
                for c in recent_commits:
                    if c["sha"] == last_commit_sha:
                        break
                    new_commits.append(c)
                # Emit in chronological order (oldest of the new batch first).
                for c in reversed(new_commits):
                    _GLOBAL_FEED.add(
                        dashboard.CAT_COMMIT,
                        f"{c['sha'][:7]}  {c['subject'][:80]}",
                    )
                if recent_commits:
                    last_commit_sha = recent_commits[0]["sha"]

            # ── launchd service-state poller (Option B) ──
            current = dashboard._launchctl_service_states(service_labels)
            for label, now_state in current.items():
                prev = last_service_state.get(label, {})
                # Surface transitions: state changed OR pid changed (restart).
                state_changed = now_state.get("state") != prev.get("state")
                pid_changed = (
                    now_state.get("pid") is not None
                    and prev.get("pid") is not None
                    and now_state.get("pid") != prev.get("pid")
                )
                if state_changed:
                    _GLOBAL_FEED.add(
                        dashboard.CAT_DEPLOY,
                        f"{label.split('.')[-1]}: "
                        f"{prev.get('state', '—')} → {now_state.get('state', '—')}",
                    )
                elif pid_changed:
                    _GLOBAL_FEED.add(
                        dashboard.CAT_DEPLOY,
                        f"{label.split('.')[-1]} restarted: "
                        f"pid {prev.get('pid')} → {now_state.get('pid')}",
                    )
            last_service_state = current

            # ── p95 probe sampler (BUILD_SPEC.md §1b) ──
            # Moved into this ~2s loop (rather than sampled only when a
            # client happens to hit /snapshot.json) so the latency series
            # is regular, not poll-rate-coupled. Only sse/bridge have a
            # health_url; probe_latency_ms returns None for the rest.
            for probe_ep in connectivity.ENDPOINTS:
                if not probe_ep.health_url:
                    continue
                ms = connectivity.probe_latency_ms(probe_ep)
                if ms is not None:
                    _SERVICE_P95.setdefault(probe_ep.name, deque(maxlen=200)).append(ms)

            time.sleep(_WATCHER_INTERVAL)
        except Exception as e:
            _GLOBAL_FEED.add(
                dashboard.CAT_ERROR,
                f"watcher: {type(e).__name__}: {e}",
            )
            time.sleep(_WATCHER_INTERVAL)


def _ensure_watcher() -> None:
    """Start the watcher thread once per process (idempotent)."""
    global _watcher_started
    with _watcher_lock:
        if _watcher_started:
            return
        t = threading.Thread(target=_watcher_loop, daemon=True, name="sovereign-dashboard-watcher")
        t.start()
        _watcher_started = True


# ── Static directory ────────────────────────────────────────────────────────


STATIC_DIR = Path(__file__).parent / "dashboard_web_static"


def _read_static(name: str) -> bytes | None:
    p = STATIC_DIR / name
    if not p.exists() or not p.is_file():
        return None
    try:
        return p.read_bytes()
    except OSError:
        return None


# ── Snapshot builder ────────────────────────────────────────────────────────


def _safe_section(reader):
    """Call a Console-v2 reader, returning None if it fails in any way.

    Per-key fail-soft is the whole contract for keys 11..N: one broken
    reader must degrade its own panel to "source unavailable" and must not
    take /snapshot.json down with it. Returning None (rather than an empty
    dict) is deliberate — an empty structure is indistinguishable at the
    panel from a real empty result, which is the fail-open shape.
    """
    try:
        return reader()
    except Exception:
        return None


def build_snapshot() -> dict:
    """Build a DashboardState snapshot as a serializable dict.
    Pulls the live activity feed from the shared _GLOBAL_FEED that the
    watcher thread populates.

    The 8 legacy keys below (timestamp .. feed) keep their exact names,
    order, and value-structure — v4 ops-console scope C is additive-only.
    `service_telemetry` is appended strictly as the 9th key (BUILD_SPEC.md
    §1a/§1b). Note the one honest serialization delta this necessarily
    introduces: `feed` is no longer the LAST key in the dict, so its JSON
    line now ends with a comma where it previously ended the object — that
    is the unavoidable cost of "appended", not a change to any of the 8
    keys' own bytes.

    `watchman` is appended strictly as the 10th key (WATCHMAN panel, held
    for review — see dashboard.build_watchman_summary): the last 8
    already-sanitized sweep envelopes from watchman's spool.jsonl, reduced,
    plus a summary line derived from spool.jsonl + watchman.log alone. Same
    additive discipline as `service_telemetry` — the 9 keys above it are
    untouched.

    ── Console v2 keys 11..17 (spiral, self_model, guardian, open_threads,
    arrival_gate, bridge_heartbeat, lineage) ──
    Appended for the Sovereign Console v2 reskin, same additive-only
    discipline again: the 10 keys above are untouched in name, order, and
    value-structure. Sourced from `dashboard_readers`, which is token-free
    by construction — nothing below reads a bridge credential, and nothing
    below calls `metabolize` (it WRITES to metabolism_log.jsonl on every
    call, and two of the six cells it was meant to feed do not exist in
    its output; `bridge_heartbeat.aperture_surfaces` carries the same
    counts already computed).

      spiral            — ~/.sovereign/spiral_state.json: current_phase,
                          reflection_depth, tool_call_count (the ACTIVITY
                          panel's synthesized TOOLS lane differences this),
                          session age. null when the file is absent.
      self_model        — ~/.sovereign/self_model.json: newest entry per
                          category + `stale`/`stale_after_days`. Age comes
                          from the newest RECORD timestamp, never mtime.
      guardian          — guardian_tools._evaluate_status, cached ~45s
                          (three subprocesses per uncached call).
      open_threads      — chronicle/open_threads/**/*.jsonl, RECURSIVE,
                          unresolved only. The `resolved` predicate is
                          explicit rather than truthy: the field is not
                          schema-enforced and `aperture.py`'s own predicate
                          diverges from it on string values. Carries
                          `malformed_skipped` + `unreadable_files` as the
                          coverage signal.
      arrival_gate      — read-only sqlite over the bridge's
                          session_tokens.db, with the 900s pending cutoff
                          applied here (the bridge's _expire_stale only
                          runs on its own connection). Session TOKENS are
                          declared unavailable, never rendered as an empty
                          list — that list is master-token-only.
      lineage           — ~/.sovereign/comms/letters/{to_arrival,
                          breakthroughs,to_self}: TITLE AND DATE ONLY, from
                          a frontmatter read that REQUIRES a closing `---`
                          and caps its scan. Replaces the design's COMMS
                          panel, whose transport was retired 2026-06.
      bridge_heartbeat  — GET :8100/api/heartbeat, NO AUTH, cached ~20s.

    ── Key 19 (open_threads_index) ──
    Appended for the OPEN-THREADS INDEX panel, same additive-only
    discipline: the 18 keys above are untouched in name, order, and
    value-structure. (EIGHTEEN, counted — not the seventeen the "keys
    11..17" heading above implies. `unacked_signals` was later inserted as
    the 6th key and every ordinal below it in that heading has been off by
    one since. Count `list(build_snapshot())`, do not trust a heading.)

      open_threads_index — ~/.sovereign/filings/open-threads-index/
                          LATEST.json, the catalog of the whole open-thread
                          pile. It exists because `open_threads` above is
                          structurally incapable of showing that pile: that
                          reader's `limit` is 6 and there were 191 open,
                          176 of them never touched, so the oldest rows are
                          invisible from the console by construction rather
                          than by staleness. Totals only — open and
                          never-touched counts, breakdowns by category,
                          month and gate-shape, the answered-elsewhere
                          candidates, the near-duplicate cluster count, and
                          the filing's own generation stamp.

                          THE ONE KEY THAT IS NOT NULLABLE-ON-ABSENCE. It
                          returns `{"status": "absent", "reason": ...,
                          "last_seen_age_seconds": ...}` when LATEST.json is
                          missing, unreadable or malformed, because a panel
                          that can only say "source unavailable" cannot say
                          which of those happened or how stale the last
                          catalog was. The envelope carries NO counts, so it
                          is not the plausible-zero shape the rule forbids.
                          A malformed file degrades this key alone; the 18
                          above keep rendering.

    ── Keys 20..21 (cockpit Phase 2) ── honk_counts and hq_panel. 21 keys in
    all; count the dict. Phase 3 fills hq_panel.listening (one boolean) from the
    push link; the link's detail rides GET /hq/status only. The cockpit integration's timing stamps and comms
    line are deliberately NOT here: this route sends the CORS wildcard and
    has no Host check, so they are served only by the Studio-only
    GET /hq/status (adversarial review, 2026-10-10).

    EVERY ONE OF THE SEVEN IS INDIVIDUALLY NULLABLE and individually
    fail-soft: a reader that raises yields null for its own key and cannot
    take /snapshot.json down with it. Each non-null section carries a
    `source` and an `age_seconds` so the page can render staleness rather
    than presenting a dormant instrument's last word as current — three of
    these sources (self_model, the retired comms board, metabolism) were
    measurably dormant when this shipped.

    ONE FIELD IS DELIBERATELY WITHHELD: `arrival_gate.pending[].rid`. It is the
    bridge's 192-bit arrival capability token and the sole input to an
    unauthenticated poll route; /snapshot.json is served with a CORS
    wildcard, so publishing it here would hand any origin the mint. See
    dashboard_readers.read_arrival_gate."""
    restart_counts: dict[str, int | None] = {}
    state = dashboard.collect_state(_GLOBAL_FEED, restart_counts=restart_counts)

    pid_by_name = {
        ep.get("name"): ep.get("pid") for ep in state.connectivity_summary.get("endpoints", [])
    }
    log_map = dashboard.service_log_map()

    service_telemetry: dict[str, dict] = {}
    for endpoint in connectivity.ENDPOINTS:
        name = endpoint.name
        raw_runs = restart_counts.get(name)
        # null for periodic: listener's `runs` counts every 5-min tick,
        # not restarts — surfacing that as a "restart count" reads as
        # catastrophe. Restart-count is only meaningful for always_on.
        restart_count = None if endpoint.kind == connectivity.KIND_PERIODIC else raw_runs
        log_path = log_map.get(name)
        service_telemetry[name] = {
            "restart_count": restart_count,
            "current_uptime_seconds": dashboard.current_uptime_seconds(pid_by_name.get(name)),
            "p95_probe_ms": _p95(list(_SERVICE_P95.get(name, ()))),
            "recent_log_lines": dashboard.recent_log_lines(log_path) if log_path else [],
        }

    signals = heartbeat_field()
    by_source = signals.get("by_source") if isinstance(signals, dict) else None
    ledger_open = by_source.get("honk") if isinstance(by_source, dict) else None

    return {
        "timestamp": state.timestamp,
        "connectivity": state.connectivity_summary,
        "halts_count": state.halts_count,
        "decisions_count": state.decisions_count,
        "unacked_honks": state.unacked_honks,
        "unacked_signals": signals,
        "listener_stale": state.listener_stale,
        "latest": state.latest,
        "feed": _GLOBAL_FEED.to_list(limit=_FEED_LIMIT_IN_SNAPSHOT),
        "service_telemetry": service_telemetry,
        "watchman": dashboard.build_watchman_summary(),
        # ── Console v2, keys 11..16 — see the docstring above ──
        # Called through the MODULE (dashboard_readers.x), never through a
        # `from ... import x` binding: the module attribute is what a test's
        # monkeypatch can replace, and the two external readers must be
        # stubbable or the suite quietly probes the live bridge.
        "spiral": _safe_section(dashboard_readers.read_spiral_state),
        "self_model": _safe_section(dashboard_readers.read_self_model),
        "guardian": _safe_section(dashboard_readers.read_guardian),
        "open_threads": _safe_section(dashboard_readers.read_open_threads),
        "arrival_gate": _safe_section(dashboard_readers.read_arrival_gate),
        "bridge_heartbeat": _safe_section(dashboard_readers.fetch_bridge_heartbeat),
        # 17th: the LINEAGE panel that replaces the design's COMMS panel.
        # Titles and dates only — a letter's body never leaves the disk.
        "lineage": _safe_section(dashboard_readers.read_lineage_letters),
        # 19th: OPEN-THREADS INDEX. Still routed through _safe_section even
        # though the reader answers absence itself — an unforeseen raise
        # must yield null for this key alone, and the renderer branches on
        # null and on status=="absent" separately.
        "open_threads_index": _safe_section(dashboard_readers.read_open_threads_index),
        # ── Cockpit Phase 2, keys 20 and 21 ──
        # Two honk numbers, each labelled with what it counts; never folded
        # into one (Anthony, 2026-10-09). The ledger figure is None, not 0,
        # when the ledger is blind.
        #
        # 2026-10-21: both now count only honks that want an ack. The
        # informational honks (level satisfied) the console count leaves out
        # ride beside it as `informational_unacked`, labelled, so the
        # exclusion is visible. Nested here rather than as a 22nd top-level
        # key: this is the section of labelled honk numbers, and the
        # top-level key list is pinned (additive-only, see the docstring).
        "honk_counts": {
            "all_time_unacked": state.unacked_honks,
            "all_time_label": (
                "unacked nape honks since the nape daemon began "
                "(nape/honks.jsonl against acks.jsonl), only those that want "
                "an ack: informational honks are counted apart"
            ),
            "informational_unacked": state.informational_honks_unacked,
            "informational_label": (
                "unacked informational nape honks (level satisfied: a verify "
                "call came before the declaration; nothing to ack, never pushed)"
            ),
            "ledger_open": ledger_open if isinstance(ledger_open, int) else None,
            "ledger_label": "open honk signals in the signal ledger (pacemaker of 2026-09-07)",
            "ledger_since": "2026-09-07",
        },
        # Exactly the d9b5f64 field set: the activity stamps and the comms
        # line are Studio-only and live on GET /hq/status (review 2026-10-10).
        "hq_panel": _safe_section(_snapshot_hq_panel),
    }


# Fields of hq_panel.status() that never enter /snapshot.json: when Anthony
# last typed and when HQ last printed are activity data, and the snapshot is
# readable from any origin. They ride GET /hq/status only.
_HQ_STATUS_ONLY_FIELDS = ("seq_changed_at", "seq_idle_seconds", "last_input_at")

# The comms reader's fields the page renders; the rest stay on the server.
_HQ_COMMS_FIELDS = (
    "spool_ok",
    "spool_age_s",
    "spool_stale",
    "inbound_readable",
    "last_inbound_at",
    "last_inbound_age_s",
)


def _snapshot_hq_panel() -> dict:
    st = hq_panel.get_session().status()
    for key in _HQ_STATUS_ONLY_FIELDS:
        st.pop(key, None)
    # Phase 3: the push link's state as ONE boolean. Its reason, counts and the
    # last hand-over time are activity data and ride GET /hq/status only.
    st["listening"] = hq_push.get_link().listening()
    return st


def _hq_status_payload() -> dict:
    """GET /hq/status: the full session status plus a nested `comms` object
    (None when the spool reader has nothing or fails) and the `push` object
    (Phase 3: link state, reason, events waiting, last hand-over HH:MM)."""
    st = hq_panel.get_session().status()
    comms = _safe_section(dashboard_readers.read_comms)
    st["comms"] = {k: comms.get(k) for k in _HQ_COMMS_FIELDS} if isinstance(comms, dict) else None
    link = hq_push.get_link()
    st["listening"] = link.listening()
    st["push"] = _safe_section(link.status)
    return st


# ── §2a Shared security substrate (all three actions call it first) ────────
#
# The pre-v4 server was GET-only, no do_POST, zero request authentication.
# "Localhost bind" is not auth — localhost is reachable from every browser
# tab on the machine, and an Access-Control-Allow-Origin: * on JSON responses
# lets any origin read them. Every mutating route rides on top of this
# substrate: Host allowlist (DNS rebinding), Origin/Referer allowlist (CSRF),
# a page-load CSRF token verified with hmac.compare_digest (browser CSRF even
# when Origin is spoofable), POST-only (state-changing GETs), and the CORS
# wildcard dropped from action + token responses (cross-origin theft of state
# or of the token itself). See BUILD_SPEC.md §2a.

_CSRF_TTL_SECONDS = 1800  # 30 min — "short TTL" per §2a
_CSRF_TOKENS: dict[str, float] = {}  # token -> expiry epoch
_csrf_lock = threading.Lock()


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address((host or "").split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def _studio_only_reason(bound_host: str, peer_host: str) -> str | None:
    """Fix 1 of the web seat's PR #9 review (2026-10-10): the panel is Studio-only
    by rule of the code, not by the plist's bind address. Measured by that seat:
    bound to 0.0.0.0, a non-loopback client minted a token from /session, spoofed
    Host and Origin, and started the panel. So /hq/* and /session answer only when
    the server is bound to a loopback address AND the peer is loopback."""
    if not _is_loopback(bound_host):
        return "panel is Studio-only: server is not bound to loopback"
    if not _is_loopback(peer_host):
        return "panel is Studio-only: peer is not loopback"
    return None


def _mint_csrf_token() -> str:
    """Mint a 128-bit CSRF token, store it with a short TTL. Called by
    GET /session — same-origin-only by virtue of the Host check below and
    the dropped CORS wildcard on its response (a cross-origin page can
    trigger the request but the browser withholds the response body)."""
    token = secrets.token_hex(16)
    with _csrf_lock:
        _CSRF_TOKENS[token] = time.time() + _CSRF_TTL_SECONDS
    return token


def _verify_csrf(token: str | None) -> bool:
    """Constant-time verification against every live (unexpired) token,
    pruning expired entries opportunistically. Empty/missing token never
    matches."""
    if not token:
        return False
    now = time.time()
    matched = False
    with _csrf_lock:
        for candidate, expiry in list(_CSRF_TOKENS.items()):
            if expiry <= now:
                del _CSRF_TOKENS[candidate]
                continue
            if hmac.compare_digest(token, candidate):
                matched = True
    return matched


_RE_ORIGIN_FROM_REFERER = re.compile(r"^(https?://[^/]+)")


# ── NapeDaemon singleton (in-process ACK write path) ────────────────────────
#
# §2b's explicit write-path choice: call NapeDaemon.ack() in-process, not
# via the bridge. Forwarding to POST :8100/api/call would force the
# dashboard to hold the bridge Bearer token and cross a process boundary
# for a plain filesystem append — breaking the dashboard's
# read-the-filesystem-directly architecture. See BUILD_SPEC.md §2b.

_NAPE_DAEMON: nape_daemon.NapeDaemon | None = None
_nape_daemon_lock = threading.Lock()


def _get_nape_daemon() -> nape_daemon.NapeDaemon:
    global _NAPE_DAEMON
    with _nape_daemon_lock:
        if _NAPE_DAEMON is None:
            _NAPE_DAEMON = nape_daemon.NapeDaemon(root=str(dashboard._sovereign_root()))
        return _NAPE_DAEMON


# ── §2c TAIL — hardened log-tail allowlist ──────────────────────────────────
#
# Key→constant mapping, stronger than path validation: the caller passes a
# symbolic service key, never a path; this dict maps it to a hardcoded
# absolute path. The caller string is only ever a dict key — never
# concatenated into a filesystem path — so traversal is eliminated by
# construction. Unknown key -> 404 before any I/O. Computed fresh each call
# (like dashboard.service_log_map()) so SOVEREIGN_ROOT overrides apply.
# Note this is a DIFFERENT, wider set (7 entries) than
# dashboard.service_log_map()'s 5 connectivity names — TAIL also exposes
# monitor.log and dashboard-web.log, and spells the listener key
# "comms-listener" per BUILD_SPEC.md §2c's literal table.


def _tail_log_allowlist() -> dict[str, Path]:
    root = dashboard._sovereign_root()
    return {
        "sse": root / "sse.log",
        "bridge": root / "bridge-api.log",
        "dispatcher": root / "dispatcher.log",
        "comms-listener": root / "comms_listener.log",
        "monitor": root / "monitor.log",
        "tunnel": root / "tunnel.log",
        "dashboard-web": root / "dashboard-web.log",
    }


_TAIL_LINES_MIN = 1
_TAIL_LINES_MAX = 500
_TAIL_LINES_DEFAULT = 100


# ── §2d RESTART — guarded path, built now, DORMANT this pass ───────────────
#
# do_POST's /actions/restart handler (_handle_restart, below) NEVER calls
# anything in this section — it always returns the fail-closed stub
# response. This section exists to be reviewable, not to run. Flipping
# STUB -> live requires ALL of (BUILD_SPEC.md §2d):
#   (1) §2a substrate merged + proven via ACK — done, this pass.
#   (2) CORS wildcard removed from action + token responses — done, this
#       pass.
#   (3) CSRF minting/verification live — done, this pass.
#   (4) label allowlist reconfirmed against `launchctl print gui/501/<label>`
#       at enable time (labels drift).
#   (5) confirm-token two-step wired into do_POST (built below, not wired).
#   (6) Anthony's explicit enact (SOP #3) — the human gate, not a scout's go.
#
# _RESTART_ENACT_ENABLED exists so the fail-closed intent is structural, not
# just "nothing calls it yet": even if a future edit wired
# _guarded_restart_confirm into do_POST by mistake without flipping this
# flag, it raises before subprocess.run. Restart enacts nothing this pass —
# by construction, not merely by routing.

_RESTART_ENACT_ENABLED = False

# Built from connectivity.ENDPOINTS (5 labeled services). Foot-gun labels
# named in §2d (hq-pulse, log-rotate, metabolize-prune) are structurally
# excluded — they're not connectivity endpoints at all.
_RESTART_LABEL_ALLOWLIST: dict[str, str] = {
    ep.name: ep.label for ep in connectivity.ENDPOINTS if ep.label
}

_RESTART_CONFIRM_TTL_SECONDS = 60
_restart_confirm_tokens: dict[str, tuple[str, float]] = {}  # token -> (service, expiry)
_restart_confirm_lock = threading.Lock()


def _guarded_restart_request(service: str) -> dict:
    """DORMANT — no route calls this. Step 1 of the two-step confirm flow:
    mint a single-use, service-bound, short-TTL confirm token."""
    token = secrets.token_hex(16)
    with _restart_confirm_lock:
        _restart_confirm_tokens[token] = (service, time.time() + _RESTART_CONFIRM_TTL_SECONDS)
    return {"status": "confirm_required", "confirm_token": token, "service": service}


def _guarded_restart_confirm(service: str, confirm_token: str) -> connectivity.ActionResult:
    """DORMANT — no route calls this. Step 2: consume the single-use token
    (pop, not peek), verify it is bound to `service` and unexpired, then
    kickstart the launchd label via a fixed argv list (shell=False, no
    caller input in the argv). Raises RuntimeError before ever reaching
    subprocess.run while _RESTART_ENACT_ENABLED is False — fail-closed by
    construction, not merely by the fact that nothing currently calls this
    function."""
    if not _RESTART_ENACT_ENABLED:
        raise RuntimeError(
            "restart enact is disabled this pass (BUILD_SPEC.md §2d) — "
            "flip _RESTART_ENACT_ENABLED only after all six preconditions, "
            "including Anthony's explicit go, are met"
        )
    with _restart_confirm_lock:
        entry = _restart_confirm_tokens.pop(confirm_token, None)
    if entry is None:
        raise ValueError("unknown or already-consumed confirm_token")
    bound_service, expiry = entry
    if time.time() > expiry:
        raise ValueError("confirm_token expired")
    if not hmac.compare_digest(bound_service, service):
        raise ValueError("confirm_token is not bound to this service")
    label = _RESTART_LABEL_ALLOWLIST.get(service)
    if not label:
        raise ValueError(f"unknown service: {service!r}")
    target = f"gui/{os.getuid()}/{label}"
    proc = subprocess.run(  # noqa: S603 — fixed argv, label from a hardcoded allowlist, shell=False
        ["launchctl", "kickstart", "-k", target],
        capture_output=True,
        text=True,
        timeout=10.0,
        check=False,
        shell=False,
    )
    return connectivity.ActionResult(
        name=service,
        action="restart",
        ok=(proc.returncode == 0),
        stdout=(proc.stdout or "").strip(),
        stderr=(proc.stderr or "").strip(),
        returncode=proc.returncode,
    )


# ── Request handler ─────────────────────────────────────────────────────────


CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}


# ── Cockpit Phase 2 (2026-10-09): HQ inside the dashboard ───────────────────
#
# The /hq routes put a full shell on the Studio behind the §2a substrate.
# Method discipline covers them with the action routes; no /hq response
# carries the CORS wildcard; the stream verifies the page's CSRF token from
# the query string because EventSource cannot set a header.

_POST_ONLY_ROUTES = frozenset(
    {
        "/actions/ack",
        "/actions/tail",
        "/actions/restart",
        "/hq/start",
        "/hq/input",
        "/hq/resize",
        "/hq/drop",
    }
)
_HQ_STREAM_KEEPALIVE_S = 15
_HQ_INPUT_MAX_BYTES = 65536

# ── /hq/drop (cockpit integration, 2026-10-10) ──
# A file dragged onto the panel used to reach the seat as a Finder icon, not the
# bytes (measured twice). The page now POSTs each dropped file's raw bytes here
# with its name in X-Filename (percent-encoded); the server writes it under
# ~/.local/share/hq-cockpit/drops/<YYYYMMDD>/ (HQ_DROP_ROOT overrides, for
# tests) and the page pastes the absolute path into the pty. NOT under
# ~/.sovereign: that tree is mirrored to the Jetson every 6 h, and a drop can
# be anything Anthony drags in (review 2026-10-10). Files older than
# HQ_DROP_RETENTION_DAYS are swept from the root on every successful drop. The name is sanitized to [A-Za-z0-9._-] so the typed path
# needs no quoting at the prompt and can carry no control byte (a newline in a
# name typed into a pty would submit the line).
HQ_DROP_MAX_BYTES = 25 * 1024 * 1024
HQ_DROP_RETENTION_DAYS = 30
HQ_DROP_READ_TIMEOUT_S = 10.0
HQ_DROP_EXTENSIONS = frozenset(
    {
        "txt", "md", "json", "jsonl", "csv", "log", "png", "jpg", "jpeg", "gif", "pdf",
        "py", "js", "ts", "html", "css", "yaml", "yml", "toml", "patch", "diff",
    }
)  # fmt: skip
_HQ_DROP_NAME_MAX = 120
_RE_DROP_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _hq_drop_name(raw: str) -> tuple[str | None, str | None]:
    """(sanitized name, None) or (None, reason). Decodes first, then validates,
    so an encoded %2F or %00 is caught after decoding."""
    name = urllib.parse.unquote(raw or "")
    if not name.strip():
        return None, "filename is required"
    if "/" in name or "\\" in name or "\x00" in name:
        return None, "filename may not contain a path separator or NUL"
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        return None, "filename may not contain control characters"
    if name.startswith(".") or ".." in name:
        return None, "dotfiles and '..' are refused"
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem or ext.lower() not in HQ_DROP_EXTENSIONS:
        return None, "file type not allowed"
    stem = _RE_DROP_UNSAFE.sub("_", stem).strip("_.") or "file"
    stem = stem[: _HQ_DROP_NAME_MAX - len(ext) - 1]
    return f"{stem}.{ext.lower()}", None


def _hq_drop_root() -> Path:
    """Resolved per call so HQ_DROP_ROOT (tests) is honoured. A relative
    override resolves against the home directory, never the cwd."""
    raw = os.environ.get("HQ_DROP_ROOT")
    if not raw:
        return Path.home() / ".local" / "share" / "hq-cockpit" / "drops"
    root = Path(raw).expanduser()
    return root if root.is_absolute() else Path.home() / root


def _hq_drop_dir() -> Path:
    return _hq_drop_root() / time.strftime("%Y%m%d")


_RE_DROP_DATE_DIR = re.compile(r"^\d{8}$")


def _hq_drop_sweep(root: Path, max_age_days: float = HQ_DROP_RETENTION_DAYS) -> int:
    """Delete regular files older than `max_age_days` of EXACTLY the shape
    <root>/<8-digit date dir>/<regular file>, then those date dirs left empty.
    Never descends further, never removes any other folder, never follows or
    deletes a symlink (review pass 2, 2026-10-10).

    Sweeps nothing, silently, when the configured root contains a symlink
    anywhere (its realpath differs from its absolute expanded path), or when
    the resolved root is `/`, the home directory, or any parent of it.
    Returns the number of files removed; an error on one entry skips it."""
    try:
        configured = Path(os.path.abspath(os.path.expanduser(str(root))))
        resolved = Path(os.path.realpath(configured))
        home = Path(os.path.realpath(Path.home()))
    except (OSError, ValueError):
        return 0
    if resolved != configured:
        return 0
    if resolved == Path("/") or resolved == home or resolved in home.parents:
        return 0
    if not resolved.is_dir():
        return 0
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    try:
        date_dirs = [
            e
            for e in os.scandir(resolved)
            if _RE_DROP_DATE_DIR.match(e.name) and e.is_dir(follow_symlinks=False)
        ]
    except OSError:
        return 0
    for d in date_dirs:
        try:
            entries = list(os.scandir(d.path))
        except OSError:
            continue
        for f in entries:
            try:
                if not f.is_file(follow_symlinks=False):
                    continue  # sub-dirs and symlinks are never touched
                if f.stat(follow_symlinks=False).st_mtime < cutoff:
                    os.unlink(f.path)
                    removed += 1
            except OSError:
                continue
        with contextlib.suppress(OSError):
            os.rmdir(d.path)  # succeeds only when the date dir is empty
    return removed


def _hq_drop_open(directory: Path, name: str) -> tuple[int, Path]:
    """Create the file exclusively, never overwriting: name.ext, name-2.ext, ..."""
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    stem, _, ext = name.rpartition(".")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    for n in range(1, 1000):
        candidate = directory / (name if n == 1 else f"{stem}-{n}.{ext}")
        try:
            return os.open(candidate, flags, 0o600), candidate
        except FileExistsError:
            continue
    raise FileExistsError("too many files with this name today")


_HQ_STATUS_WRITER_ROOTS: set[str] = set()
_hq_status_lock = threading.Lock()


_HQ_PUSH_STARTED = False


def _ensure_hq_push() -> None:
    """Start the Phase 3 push poller once per console process. It reads the
    spool and counters, and writes only to HQ's own messaging socket, only while
    the panel child this console spawned is running (see hq_push). There is no
    HTTP route into it; HQ_PUSH_DISABLED=1 keeps it off."""
    global _HQ_PUSH_STARTED
    if os.environ.get("HQ_PUSH_DISABLED") == "1":
        return
    with _hq_status_lock:
        if _HQ_PUSH_STARTED:
            return
        _HQ_PUSH_STARTED = True
    try:
        poll = max(5.0, float(os.environ.get("HQ_PUSH_POLL_S", hq_push.DEFAULT_POLL_S)))
    except ValueError:
        poll = float(hq_push.DEFAULT_POLL_S)
    hq_push.start_poller(hq_push.get_link(hq_panel.get_session()), poll)


def _ensure_hq_status_writer() -> None:
    """Start the console's own chat.db probe writer once per SOVEREIGN_ROOT (Phase 2
    hard rule: message access is watched; the HQ module audit reads this file)."""
    root = Path(os.environ.get("SOVEREIGN_ROOT") or Path.home() / ".sovereign")
    with _hq_status_lock:
        if str(root) in _HQ_STATUS_WRITER_ROOTS:
            return
        _HQ_STATUS_WRITER_ROOTS.add(str(root))
    try:
        interval = int(os.environ.get("HQ_PANEL_STATUS_INTERVAL_S", "300"))
    except ValueError:
        interval = 300
    hq_panel.start_status_writer(root / "hq" / "cockpit" / "console_status.json", interval)


class DashboardHandler(BaseHTTPRequestHandler):
    """Serves the dashboard page + snapshot endpoint + SSE feed."""

    # Quiet the default per-request stderr logging — too noisy when the
    # browser polls every 3 seconds.
    def log_message(self, format, *args):
        return

    def _send_json(
        self,
        code: int,
        payload: dict,
        *,
        headers: dict | None = None,
        cors: bool = True,
    ) -> None:
        """`cors=False` drops Access-Control-Allow-Origin: * — required on
        every action response and the CSRF token-minting response (§2a);
        the wildcard stays on unchanged pre-existing routes (e.g.
        /snapshot.json) so a caller relying on cross-origin reads there
        isn't broken by this pass."""
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cors:
            self.send_header("Access-Control-Allow-Origin", "*")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    # ── §2a substrate helpers ──

    def _studio_only(self) -> str | None:
        return _studio_only_reason(str(self.server.server_address[0]), str(self.client_address[0]))

    def _expected_hosts(self) -> set[str]:
        port = self.server.server_address[1]
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _expected_origins(self) -> set[str]:
        port = self.server.server_address[1]
        return {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        return host in {h.lower() for h in self._expected_hosts()}

    def _substrate_check(self) -> str | None:
        """Shared gate for every mutating route: Host allowlist, then
        Origin (Referer fallback) allowlist, then CSRF token. Returns None
        when the request clears all three; otherwise a short reason string
        for the 403 body. Host/Origin are derived from the server's own
        BOUND port (not hardcoded :3435) — required for both the ephemeral
        ports pytest binds and for running this server on any port; it does
        not weaken the DNS-rebinding protection since Host is still pinned
        to 127.0.0.1/localhost, just not to one literal port number."""
        if not self._host_ok():
            return "host not allowlisted"

        origin = self.headers.get("Origin") or ""
        if not origin:
            referer = self.headers.get("Referer") or ""
            m = _RE_ORIGIN_FROM_REFERER.match(referer)
            origin = m.group(1) if m else ""
        if origin not in self._expected_origins():
            return "origin not allowlisted"

        token = self.headers.get("X-CSRF-Token") or ""
        if not _verify_csrf(token):
            return "csrf token invalid or missing"

        return None

    def _read_json_body(self) -> tuple[dict | None, str | None]:
        """Read + parse the POST body. Returns (body, None) or (None, error)."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b""
        if not raw:
            return {}, None
        try:
            body = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None, "invalid JSON body"
        if not isinstance(body, dict):
            return None, "invalid JSON body"
        return body, None

    def _send_static(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802  (BaseHTTPRequestHandler convention)
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # Method discipline (§2a): the action routes and the /hq mutating routes are POST-only.
        if path in _POST_ONLY_ROUTES:
            self._send_json(405, {"error": "method not allowed, POST only"}, cors=False)
            return

        if path in ("/hq/status", "/hq/stream", "/session"):
            reason = self._studio_only()
            if reason is not None:
                self._send_json(403, {"error": reason}, cors=False)
                return

        if path == "/hq/status":
            # Phase 2: read-only, same-machine only (Host pin), never CORS.
            if not self._host_ok():
                self._send_json(403, {"error": "host not allowlisted"}, cors=False)
                return
            self._send_json(200, _hq_status_payload(), cors=False)
            return

        if path == "/hq/stream":
            self._hq_stream(parsed)
            return

        if path == "/session":
            # CSRF token minting (§2a). Same-origin-only in practice: the
            # dropped CORS wildcard means a cross-origin page can trigger
            # this GET but the browser withholds the response body from it.
            self._send_json(200, {"csrf_token": _mint_csrf_token()}, cors=False)
            return

        if path in ("/", "/index.html"):
            body = _read_static("index.html")
            if body is None:
                self._send_json(500, {"error": "index.html missing"})
                return
            self._send_static(200, body, "text/html; charset=utf-8")
            return

        if path == "/snapshot.json":
            try:
                snapshot = build_snapshot()
                self._send_json(200, snapshot)
            except Exception as e:
                self._send_json(500, {"error": str(e)})
            return

        if path == "/events":
            self._stream_events()
            return

        if path.startswith("/static/"):
            name = path[len("/static/") :]
            # Path traversal guard. One subdirectory is allowed, `vendor/`,
            # which holds the pinned xterm.js files (Phase 2 of the cockpit);
            # any other slash, any `..`, any dotfile, and an empty segment stay 404.
            if ".." in name or name.startswith(".") or not name:
                self._send_json(404, {"error": "not found"})
                return
            if "/" in name:
                head, _, tail = name.partition("/")
                if head != "vendor" or not tail or "/" in tail or tail.startswith("."):
                    self._send_json(404, {"error": "not found"})
                    return
            body = _read_static(name)
            if body is None:
                self._send_json(404, {"error": "not found"})
                return
            ext = Path(name).suffix
            ct = CONTENT_TYPES.get(ext, "application/octet-stream")
            self._send_static(200, body, ct)
            return

        if path == "/health":
            self._send_json(200, {"status": "healthy", "service": "sovereign-dashboard-web"})
            return

        self._send_json(404, {"error": "not found", "path": path})

    def do_POST(self):  # noqa: N802  (BaseHTTPRequestHandler convention)
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path not in _POST_ONLY_ROUTES:
            self._send_json(404, {"error": "not found", "path": path}, cors=False)
            return

        if path.startswith("/hq/"):
            reason = self._studio_only()
            if reason is not None:
                self._send_json(403, {"error": reason}, cors=False)
                return

        reason = self._substrate_check()
        if reason is not None:
            self._send_json(403, {"error": reason}, cors=False)
            return

        # Raw bytes, not JSON: routed before the JSON body read.
        if path == "/hq/drop":
            self._hq_drop()
            return

        body, err = self._read_json_body()
        if err is not None:
            self._send_json(400, {"error": err}, cors=False)
            return

        if path == "/actions/ack":
            self._handle_ack(body)
        elif path == "/actions/tail":
            self._handle_tail(body)
        elif path == "/actions/restart":
            self._handle_restart(body)
        elif path == "/hq/start":
            self._hq_start(body)
        elif path == "/hq/input":
            self._hq_input(body)
        elif path == "/hq/resize":
            self._hq_resize(body)

    # ── /actions/ack — §2b, ship ENABLED ──

    def _handle_ack(self, body: dict) -> None:
        honk_id = body.get("honk_id")
        note = body.get("note")
        if not isinstance(honk_id, str) or not honk_id:
            self._send_json(400, {"error": "honk_id is required"}, cors=False)
            return
        if not isinstance(note, str) or not note.strip():
            self._send_json(400, {"error": "note is required"}, cors=False)
            return
        if len(note) > 2048:
            self._send_json(400, {"error": "note exceeds 2KB limit"}, cors=False)
            return

        daemon = _get_nape_daemon()
        try:
            ack = daemon.ack(honk_id, note)
        except ValueError as e:
            # honk_id not found, or empty — free input validation from
            # NapeDaemon.ack() itself (§2b).
            self._send_json(400, {"error": str(e)}, cors=False)
            return
        self._send_json(200, {"status": "acked", "ack": ack}, cors=False)

    # ── /actions/tail — §2c, ship ENABLED, hardened ──

    def _handle_tail(self, body: dict) -> None:
        service = body.get("service")
        allowlist = _tail_log_allowlist()
        if not isinstance(service, str) or service not in allowlist:
            self._send_json(404, {"error": "unknown service"}, cors=False)
            return

        lines_req = body.get("lines", _TAIL_LINES_DEFAULT)
        try:
            lines_n = int(lines_req)
        except (TypeError, ValueError):
            lines_n = _TAIL_LINES_DEFAULT
        lines_n = max(_TAIL_LINES_MIN, min(_TAIL_LINES_MAX, lines_n))

        path = allowlist[service]
        raw_lines, truncated = dashboard.seek_tail_lines(path, want_lines=lines_n)
        redacted_lines = [dashboard.redact_log_line(ln) for ln in raw_lines]
        self._send_json(
            200,
            {
                "service": service,
                "path": str(path),
                "lines": redacted_lines,
                "truncated": truncated,
            },
            cors=False,
        )

    # ── /actions/restart — §2d, ship as a fail-closed STUB this pass ──

    def _handle_restart(self, body: dict) -> None:
        service = body.get("service")
        if not isinstance(service, str) or service not in _RESTART_LABEL_ALLOWLIST:
            self._send_json(404, {"error": "unknown service"}, cors=False)
            return

        # Invokes no subprocess. Nothing. See the §2d guarded-path comment
        # block above _guarded_restart_confirm — that dormant code is what
        # a future flip wires in, not this handler.
        _GLOBAL_FEED.add(
            dashboard.CAT_DEPLOY,
            f"restart requested for {service} — NOT enacted (stub)",
        )
        self._send_json(
            200,
            {
                "status": "requested",
                "service": service,
                "enacted": False,
                "note": "restart not enacted — stub; no service was restarted",
            },
            cors=False,
        )

    # ── /hq/* — cockpit Phase 2: HQ inside the dashboard ──

    def _hq_stream_gate(self, parsed) -> str | None:
        """Host, then Origin (Referer fallback), then the CSRF token carried as the
        `token` query parameter; the same three checks as _substrate_check."""
        if not self._host_ok():
            return "host not allowlisted"
        origin = self.headers.get("Origin") or ""
        if not origin:
            m = _RE_ORIGIN_FROM_REFERER.match(self.headers.get("Referer") or "")
            origin = m.group(1) if m else ""
        if origin not in self._expected_origins():
            return "origin not allowlisted"
        qs = urllib.parse.parse_qs(parsed.query)
        token = (qs.get("token") or [""])[0]
        if not _verify_csrf(token):
            return "csrf token invalid or missing"
        return None

    def _hq_stream(self, parsed) -> None:
        reason = self._hq_stream_gate(parsed)
        if reason is not None:
            self._send_json(403, {"error": reason}, cors=False)
            return
        qs = urllib.parse.parse_qs(parsed.query)
        try:
            after = max(0, int((qs.get("after") or ["0"])[0]))
        except ValueError:
            after = 0
        session = hq_panel.get_session()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        def emit(event: str | None, data: str, seq: int | None = None) -> None:
            parts = []
            if event:
                parts.append(f"event: {event}")
            if seq is not None:
                parts.append(f"id: {seq}")
            parts.append(f"data: {data}")
            self.wfile.write(("\n".join(parts) + "\n\n").encode("utf-8"))
            self.wfile.flush()

        try:
            chunks, truncated_before = session.replay(after)
            st = session.status()
            hello = {"after": after, "truncated_before": truncated_before, "running": st["running"]}
            emit("hello", json.dumps(hello))
            last = after
            for seq, chunk in chunks:
                emit(None, base64.b64encode(chunk).decode("ascii"), seq)
                last = seq
            while True:
                if session.wait_for(last, _HQ_STREAM_KEEPALIVE_S):
                    chunks, _ = session.replay(last)
                    for seq, chunk in chunks:
                        emit(None, base64.b64encode(chunk).decode("ascii"), seq)
                        last = seq
                    if not chunks:
                        last = session.last_seq  # the final tick carries no bytes
                else:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                if not session.running:
                    emit("exit", json.dumps({"last_exit": session.status()["last_exit"]}))
                    return
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            return

    def _hq_start(self, body: dict) -> None:
        try:
            cols, rows = int(body.get("cols", 80)), int(body.get("rows", 24))
        except (TypeError, ValueError):
            self._send_json(400, {"error": "cols and rows must be integers"}, cors=False)
            return
        session = hq_panel.get_session()
        ok, reason = session.start(cols, rows)
        if not ok:
            self._send_json(409, {"error": reason}, cors=False)
            return
        _GLOBAL_FEED.add(dashboard.CAT_DEPLOY, "HQ panel session started")
        # The pid start() spawned, not a second status() read: a child that exits
        # in that window answered null (seat 2/3, mesh-20261009).
        self._send_json(200, {"status": "started", "pid": session.last_started_pid}, cors=False)

    def _hq_input(self, body: dict) -> None:
        session = hq_panel.get_session()
        if not session.running:
            self._send_json(409, {"error": "no panel session is running"}, cors=False)
            return
        raw = body.get("data")
        if not isinstance(raw, str):
            self._send_json(400, {"error": "data (base64) is required"}, cors=False)
            return
        try:
            data = base64.b64decode(raw, validate=True)
        except (ValueError, TypeError):
            self._send_json(400, {"error": "data is not valid base64"}, cors=False)
            return
        if len(data) > _HQ_INPUT_MAX_BYTES:
            self._send_json(400, {"error": "input exceeds 64 KB"}, cors=False)
            return
        session.write(data)
        self._send_json(200, {"status": "ok", "bytes": len(data)}, cors=False)

    def _hq_drop(self) -> None:
        """Write one dropped file under the drops dir; answer {path, bytes, sha256}.
        Every refusal happens before a byte of the body is read, and a refused
        request closes the connection, so an unread body cannot be misparsed as
        the next request."""

        def refuse(code: int, error: str) -> None:
            self.close_connection = True
            self._send_json(code, {"error": error}, cors=False)

        if (self.headers.get("Transfer-Encoding") or "").strip():
            refuse(411, "Transfer-Encoding is not supported; send Content-Length")
            return
        raw_len = self.headers.get("Content-Length")
        try:
            length = int(raw_len) if raw_len is not None else -1
        except ValueError:
            length = -1
        if length < 0:
            refuse(411, "Content-Length is required")
            return
        if length > HQ_DROP_MAX_BYTES:
            refuse(413, f"file exceeds {HQ_DROP_MAX_BYTES // (1024 * 1024)} MB")
            return
        name, why = _hq_drop_name(self.headers.get("X-Filename") or "")
        if name is None:
            refuse(400, why)
            return
        if not hq_panel.get_session().running:
            refuse(409, "no panel session is running")
            return
        try:
            fd, target = _hq_drop_open(_hq_drop_dir(), name)
        except OSError as e:
            refuse(500, f"could not create the drop file: {type(e).__name__}")
            return
        digest = hashlib.sha256()
        remaining = length
        # A stalled sender must not hold a handler thread and a half-written
        # file forever: the body read times out and the partial file goes.
        with contextlib.suppress(OSError):
            self.connection.settimeout(HQ_DROP_READ_TIMEOUT_S)
        try:
            with os.fdopen(fd, "wb") as f:
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    digest.update(chunk)
                    f.write(chunk)
                    remaining -= len(chunk)
        except TimeoutError:
            with contextlib.suppress(OSError):
                target.unlink()
            refuse(408, "timed out reading the file body")
            return
        except OSError as e:
            with contextlib.suppress(OSError):
                target.unlink()
            refuse(500, f"could not write the drop file: {type(e).__name__}")
            return
        if remaining > 0:
            with contextlib.suppress(OSError):
                target.unlink()
            refuse(400, "body shorter than Content-Length")
            return
        _hq_drop_sweep(_hq_drop_root())
        # Phase 3: a notice, not a second paste. The byte count only; the name
        # and path never enter a push batch.
        with contextlib.suppress(Exception):
            hq_push.get_link().note_drop(length)
        self._send_json(
            200,
            {
                "path": str(target),
                "name": target.name,
                "bytes": length,
                "sha256": digest.hexdigest(),
            },
            cors=False,
        )

    def _hq_resize(self, body: dict) -> None:
        try:
            cols, rows = int(body.get("cols")), int(body.get("rows"))
        except (TypeError, ValueError):
            self._send_json(400, {"error": "cols and rows must be integers"}, cors=False)
            return
        hq_panel.get_session().resize(cols, rows)
        self._send_json(200, {"status": "ok"}, cors=False)

    def _stream_events(self) -> None:
        """SSE stream that pushes snapshot updates every 3 seconds."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            while True:
                snapshot = build_snapshot()
                payload = f"data: {json.dumps(snapshot)}\n\n"
                self.wfile.write(payload.encode("utf-8"))
                self.wfile.flush()
                time.sleep(3)
        except (BrokenPipeError, ConnectionResetError):
            return
        # best-effort SSE loop: any other transport-layer error (ssl, socket,
        # encoding) should silently drop the stream rather than crash the thread.
        except Exception:
            return


# ── Server entrypoint ───────────────────────────────────────────────────────


DEFAULT_PORT = 3435


def serve(host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> HTTPServer:
    """Build and return a ThreadingHTTPServer (caller is responsible for
    serve_forever / shutdown). Threading lets SSE streams run in
    parallel with poll requests. Starts the activity-watcher thread on
    first call."""
    _ensure_watcher()
    _ensure_hq_status_writer()
    _ensure_hq_push()
    return ThreadingHTTPServer((host, port), DashboardHandler)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sovereign-dashboard-web",
        description="Web dashboard for Sovereign Stack.",
    )
    p.add_argument("--host", default="127.0.0.1", help="bind host (default: %(default)s)")
    p.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help="bind port (default: %(default)s)"
    )
    return p


def main(argv: list | None = None) -> int:
    args = _build_parser().parse_args(argv)
    server = serve(args.host, args.port)
    actual_host, actual_port = server.server_address[:2]
    print(f"sovereign-dashboard-web listening on http://{actual_host}:{actual_port}")
    print("  GET /                serves the dashboard")
    print("  GET /snapshot.json   current state")
    print("  GET /events          SSE feed")
    print("  GET /health          health check")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
        print("\ndashboard-web stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
