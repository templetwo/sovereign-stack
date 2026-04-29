"""
Sovereign Stack Connectivity Manager.

Single source of truth for the always-on and periodic services that make
up the Sovereign Stack on a Mac. Built 2026-04-25 after `scripts/manage`
(bash, ps-grep) was found to know only 2 of the 5 actual launchd services
and was silently missing a real outage on com.templetwo.comms-listener.

Design:
  * ENDPOINTS is the canonical registry. Add a new service by adding a
    record — the CLI, status reporter, and tests pick it up automatically.
  * Status is derived from launchctl as the source of truth, plus an
    optional HTTP health probe. NO `ps aux | grep`-style heuristics.
  * Periodic services (StartInterval) are healthy if they ran within
    ~2x their cadence, even if currently `state=not running`. Always-on
    services must be `state=running`.
  * Pure-Python helpers; subprocess and HTTP are isolated in thin wrappers
    so tests can patch them.
  * No third-party deps. Stdlib only — keeps the manager runnable even
    when the venv is broken.

Public API:
  - ENDPOINTS: list[Endpoint]
  - get_endpoint(name) -> Endpoint
  - check_status(endpoint, *, now=None) -> EndpointStatus
  - check_all() -> list[EndpointStatus]
  - start(endpoint), stop(endpoint), restart(endpoint) -> ActionResult
"""

from __future__ import annotations

import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ── Status constants ────────────────────────────────────────────────────────

# Aggregate status codes — stable strings, callers branch on them.
STATUS_OK = "ok"  # service is in expected state
STATUS_DEGRADED = "degraded"  # running but health probe failed / stale
STATUS_DOWN = "down"  # expected running, not running
STATUS_STALE = "stale"  # periodic, ran but >2x cadence ago
STATUS_UNKNOWN = "unknown"  # launchctl reports unknown / parse failed
STATUS_DISABLED = "disabled"  # plist marked disabled — informational

# Endpoint kinds
KIND_ALWAYS_ON = "always_on"
KIND_PERIODIC = "periodic"


# ── Endpoint registry ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class Endpoint:
    """
    A managed endpoint in the Sovereign Stack.

    Attributes:
        name: Short human-readable identifier (CLI uses this).
        label: launchctl Label (com.templetwo.*). None if managed
            outside launchd.
        kind: KIND_ALWAYS_ON or KIND_PERIODIC.
        description: One-line description.
        health_url: Optional HTTP URL probed alongside launchctl status.
            HTTP 2xx + (optionally) `health_match` substring required.
        health_match: Optional substring required in HTTP body for OK.
        cadence_seconds: For periodic kinds: expected run interval.
        log_path: For periodic kinds: file whose mtime is used as the
            "last run" indicator (stdout/stderr file from launchd).
    """

    name: str
    label: str | None
    kind: str
    description: str
    health_url: str | None = None
    health_match: str | None = None
    cadence_seconds: int | None = None
    log_path: str | None = None


ENDPOINTS: list[Endpoint] = [
    Endpoint(
        name="sse",
        label="com.templetwo.sovereign-sse",
        kind=KIND_ALWAYS_ON,
        description="MCP-over-SSE server (port 3434)",
        health_url="http://127.0.0.1:3434/health",
        health_match="healthy",
    ),
    Endpoint(
        name="bridge",
        label="com.templetwo.sovereign-bridge",
        kind=KIND_ALWAYS_ON,
        description="REST/JSON bridge over MCP (port 8100)",
        health_url="http://127.0.0.1:8100/api/heartbeat",
    ),
    Endpoint(
        name="tunnel",
        label="com.templetwo.cloudflared-tunnel",
        kind=KIND_ALWAYS_ON,
        description="Cloudflare tunnel exposing SSE to internet",
        # Tunnel itself is opaque from the host side; rely on launchctl.
    ),
    Endpoint(
        name="dispatcher",
        label="com.templetwo.comms-dispatcher",
        kind=KIND_ALWAYS_ON,
        description="Comms message router (no HTTP surface)",
    ),
    Endpoint(
        name="listener",
        label="com.templetwo.comms-listener",
        kind=KIND_PERIODIC,
        description="Comms inbox poll-style listener (every 5 min)",
        cadence_seconds=300,
        log_path=str(Path.home() / ".sovereign" / "comms_listener.log"),
    ),
    Endpoint(
        name="ollama",
        label="com.ollama.server",
        kind=KIND_ALWAYS_ON,
        description="Ollama model server (port 11434, localhost-only)",
        health_url="http://127.0.0.1:11434/",
    ),
]


def get_endpoint(name: str) -> Endpoint:
    """Lookup by name. Raises KeyError on miss."""
    for e in ENDPOINTS:
        if e.name == name:
            return e
    raise KeyError(f"unknown endpoint: {name!r}")


# ── Status result ───────────────────────────────────────────────────────────


@dataclass
class EndpointStatus:
    name: str
    label: str | None
    kind: str
    status: str  # one of STATUS_*
    launchctl_state: str | None = None  # "running" | "not running" | None
    pid: int | None = None
    last_exit_code: int | None = None
    http_status: int | None = None
    http_ok: bool | None = None
    http_error: str | None = None
    log_age_seconds: float | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Subprocess + HTTP helpers (thin, mockable) ──────────────────────────────


def _run(cmd: list[str], timeout: float = 5.0) -> subprocess.CompletedProcess:
    """Thin subprocess wrapper. Tests patch this."""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _http_probe(url: str, timeout: float = 2.0) -> dict:
    """
    GET `url` with a short timeout. Returns:
      {"http_status": int|None, "body": str, "error": str|None}
    Stdlib-only (urllib.request) — no httpx dep so the manager runs even
    when the venv is broken.
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # nosec
            body = resp.read(4096).decode("utf-8", errors="replace")
            return {"http_status": resp.status, "body": body, "error": None}
    except urllib.error.HTTPError as e:
        try:
            body = e.read(4096).decode("utf-8", errors="replace")
        except OSError:
            body = ""
        return {"http_status": e.code, "body": body, "error": None}
    except urllib.error.URLError as e:
        return {"http_status": None, "body": "", "error": f"url_error: {e.reason}"}
    except (TimeoutError, OSError) as e:
        return {"http_status": None, "body": "", "error": f"socket: {e}"}
    except Exception as e:
        return {"http_status": None, "body": "", "error": f"{type(e).__name__}: {e}"}


# ── launchctl parsing ───────────────────────────────────────────────────────


def _launchctl_print_text(label: str) -> str | None:
    """
    Run `launchctl print gui/<uid>/<label>` and return stdout, or None if
    the service isn't loaded.
    """
    uid = os.getuid()
    proc = _run(["launchctl", "print", f"gui/{uid}/{label}"], timeout=3.0)
    if proc.returncode != 0:
        return None
    return proc.stdout


_RE_STATE = re.compile(r"^\s*state\s*=\s*(\S+)", re.MULTILINE)
_RE_PID = re.compile(r"^\s*pid\s*=\s*(\d+)", re.MULTILINE)
_RE_LAST_EXIT = re.compile(r"^\s*last exit code\s*=\s*(-?\d+)", re.MULTILINE)


def parse_launchctl_print(text: str) -> dict:
    """
    Parse the relevant fields from `launchctl print` output. Returns a
    dict with keys: state, pid, last_exit_code (each Optional).

    Parsing is line-anchored regex against the documented field names —
    forgiving of extra fields, robust to ordering changes.
    """
    state = None
    m = _RE_STATE.search(text)
    if m:
        state = m.group(1).strip().rstrip(",")

    pid = None
    m = _RE_PID.search(text)
    if m:
        try:
            pid = int(m.group(1))
        except ValueError:
            pid = None

    last_exit = None
    m = _RE_LAST_EXIT.search(text)
    if m:
        try:
            last_exit = int(m.group(1))
        except ValueError:
            last_exit = None

    return {"state": state, "pid": pid, "last_exit_code": last_exit}


# ── Per-endpoint status check ───────────────────────────────────────────────


def _log_age_seconds(path: str, now: float) -> float | None:
    p = Path(path)
    if not p.exists():
        return None
    return now - p.stat().st_mtime


def check_status(
    endpoint: Endpoint,
    *,
    now: float | None = None,
) -> EndpointStatus:
    """
    Return the current status of one endpoint.

    Decision tree:
      1. If endpoint has a launchctl label, query it. If unknown, return
         STATUS_UNKNOWN unless we have an HTTP health probe (in which case
         the probe alone is the source of truth).
      2. ALWAYS_ON: state must be "running" — otherwise STATUS_DOWN.
      3. PERIODIC: state may legitimately be "not running" between ticks.
         Healthy if log_path's mtime is within 2x cadence. Stale otherwise.
      4. If health_url set, probe it. Failure on an otherwise-healthy
         service downgrades to STATUS_DEGRADED.
    """
    now = now if now is not None else time.time()
    status = EndpointStatus(
        name=endpoint.name,
        label=endpoint.label,
        kind=endpoint.kind,
        status=STATUS_UNKNOWN,
    )

    # ── launchctl probe ──
    launchctl_text = None
    if endpoint.label:
        launchctl_text = _launchctl_print_text(endpoint.label)

    if launchctl_text is None and endpoint.label:
        status.notes.append("launchctl: service not loaded")
        # Fall through — HTTP probe might still tell us something.
    elif launchctl_text is not None:
        parsed = parse_launchctl_print(launchctl_text)
        status.launchctl_state = parsed["state"]
        status.pid = parsed["pid"]
        status.last_exit_code = parsed["last_exit_code"]

    # Decide by kind.
    if endpoint.kind == KIND_ALWAYS_ON:
        if status.launchctl_state == "running":
            status.status = STATUS_OK
        elif status.launchctl_state in ("not running", None):
            # Could be unloaded (no launchctl text) or stopped.
            status.status = STATUS_DOWN
        else:
            status.status = STATUS_UNKNOWN
            status.notes.append(f"unrecognized launchctl state: {status.launchctl_state!r}")
    elif endpoint.kind == KIND_PERIODIC:
        if endpoint.log_path:
            age = _log_age_seconds(endpoint.log_path, now)
            status.log_age_seconds = age
            cadence = endpoint.cadence_seconds or 0
            tolerance = max(60.0, 2.0 * cadence)
            if age is None:
                status.status = STATUS_STALE
                status.notes.append("log_path missing — never run?")
            elif age <= tolerance:
                status.status = STATUS_OK
            else:
                status.status = STATUS_STALE
                status.notes.append(f"last run {age:.0f}s ago > {tolerance:.0f}s tolerance")
        else:
            # Periodic without a log_path — best we can do is launchctl state.
            # Periodic services frequently report state=not running between
            # ticks; without a log signal we can't distinguish healthy from
            # broken. Mark UNKNOWN rather than guess OK.
            status.status = STATUS_UNKNOWN
            status.notes.append("periodic without log_path; cannot verify")

    # ── HTTP probe (downgrade on failure, do not upgrade) ──
    if endpoint.health_url:
        # Self-probe guard: if the PID of this service matches our own
        # process, the responding tool call proves liveness. Probing our
        # own HTTP port via blocking urllib inside an async event loop
        # deadlocks — urlopen blocks the thread, the loop can't serve
        # the /health request, timeout, DEGRADED. Skip and trust launchctl.
        if status.pid is not None and status.pid == os.getpid():
            status.http_ok = True
            status.notes.append("self-probe skipped — tool response proves liveness")
        else:
            probe = _http_probe(endpoint.health_url)
            status.http_status = probe["http_status"]
            if probe["error"]:
                status.http_error = probe["error"]
                status.http_ok = False
            elif probe["http_status"] and 200 <= probe["http_status"] < 300:
                if endpoint.health_match:
                    status.http_ok = endpoint.health_match in probe["body"]
                    if not status.http_ok:
                        status.notes.append(f"health body missing match {endpoint.health_match!r}")
                else:
                    status.http_ok = True
            else:
                status.http_ok = False

            # Failure on an otherwise-OK service -> degraded.
            if status.http_ok is False and status.status == STATUS_OK:
                status.status = STATUS_DEGRADED
                status.notes.append("launchctl OK but health probe failed")

    return status


def check_all(*, now: float | None = None) -> list[EndpointStatus]:
    """Check every endpoint in the registry. Order matches ENDPOINTS."""
    return [check_status(e, now=now) for e in ENDPOINTS]


# ── Action helpers ──────────────────────────────────────────────────────────


@dataclass
class ActionResult:
    name: str
    action: str
    ok: bool
    stdout: str = ""
    stderr: str = ""
    returncode: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _action(label: str, args: list[str], action_name: str, name: str) -> ActionResult:
    proc = _run(args, timeout=10.0)
    return ActionResult(
        name=name,
        action=action_name,
        ok=(proc.returncode == 0),
        stdout=(proc.stdout or "").strip(),
        stderr=(proc.stderr or "").strip(),
        returncode=proc.returncode,
    )


def restart(endpoint: Endpoint) -> ActionResult:
    """
    Restart via `launchctl kickstart -k`. The -k flag kills the running
    service before restarting. Idempotent and safe for both running and
    stopped services.
    """
    if not endpoint.label:
        return ActionResult(
            name=endpoint.name,
            action="restart",
            ok=False,
            stderr="endpoint has no launchctl label",
        )
    target = f"gui/{os.getuid()}/{endpoint.label}"
    return _action(
        endpoint.label,
        ["launchctl", "kickstart", "-k", target],
        "restart",
        endpoint.name,
    )


def start(endpoint: Endpoint) -> ActionResult:
    """
    Start a service. Uses `kickstart` which loads + starts in one shot
    if the plist is already bootstrapped, or fails cleanly otherwise.
    """
    if not endpoint.label:
        return ActionResult(
            name=endpoint.name,
            action="start",
            ok=False,
            stderr="endpoint has no launchctl label",
        )
    target = f"gui/{os.getuid()}/{endpoint.label}"
    return _action(
        endpoint.label,
        ["launchctl", "kickstart", target],
        "start",
        endpoint.name,
    )


def stop(endpoint: Endpoint) -> ActionResult:
    """
    Stop a service. Uses `kill SIGTERM` so the plist remains bootstrapped
    (KeepAlive=true services will be restarted by launchd; that's by
    design — use `bootout` to truly disable).
    """
    if not endpoint.label:
        return ActionResult(
            name=endpoint.name,
            action="stop",
            ok=False,
            stderr="endpoint has no launchctl label",
        )
    target = f"gui/{os.getuid()}/{endpoint.label}"
    return _action(
        endpoint.label,
        ["launchctl", "kill", "SIGTERM", target],
        "stop",
        endpoint.name,
    )


# ── Aggregation helpers ─────────────────────────────────────────────────────


def aggregate(statuses: list[EndpointStatus]) -> dict:
    """
    Roll up a list of statuses into a top-level summary suitable for
    dashboards, JSON output, or alerting hooks.
    """
    counts: dict[str, int] = {}
    for s in statuses:
        counts[s.status] = counts.get(s.status, 0) + 1

    overall = STATUS_OK
    if counts.get(STATUS_DOWN):
        overall = STATUS_DOWN
    elif counts.get(STATUS_DEGRADED) or counts.get(STATUS_STALE) or counts.get(STATUS_UNKNOWN):
        overall = STATUS_DEGRADED

    return {
        "overall": overall,
        "counts": counts,
        "endpoints": [s.to_dict() for s in statuses],
        "timestamp": time.time(),
    }
