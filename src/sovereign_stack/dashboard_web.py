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
import hmac
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

from . import connectivity, dashboard, nape_daemon

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
    keys' own bytes."""
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

    return {
        "timestamp": state.timestamp,
        "connectivity": state.connectivity_summary,
        "halts_count": state.halts_count,
        "decisions_count": state.decisions_count,
        "unacked_honks": state.unacked_honks,
        "listener_stale": state.listener_stale,
        "latest": state.latest,
        "feed": _GLOBAL_FEED.to_list(limit=_FEED_LIMIT_IN_SNAPSHOT),
        "service_telemetry": service_telemetry,
    }


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

        # Method discipline (§2a): the three action routes are POST-only.
        if path in ("/actions/ack", "/actions/tail", "/actions/restart"):
            self._send_json(405, {"error": "method not allowed, POST only"}, cors=False)
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
            # Path traversal guard.
            if "/" in name or ".." in name or name.startswith("."):
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

        if path not in ("/actions/ack", "/actions/tail", "/actions/restart"):
            self._send_json(404, {"error": "not found", "path": path}, cors=False)
            return

        reason = self._substrate_check()
        if reason is not None:
            self._send_json(403, {"error": reason}, cors=False)
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
