"""
Sovereign Stack Dashboard — Real-Time Activity Monitor.

Built 2026-04-25 by upgrading an earlier Apr-6 dashboard
(`sovereign-bridge/sovereign_dashboard.py`) that predated the v1.3.2
daemons, Nape, and the connectivity manager. The data sources changed
faster than the dashboard did. This rewrite:

  * Uses connectivity.check_all() as the canonical service-status source
    (replacing fragile `os.popen("launchctl list | grep")` parsing).
  * Surfaces v1.3.2 events that didn't exist before:
      - Nape honks (~/.sovereign/nape/honks.jsonl)
      - Daemon halt notes (~/.sovereign/daemons/halts/*.md)
      - Metabolize decision files (~/.sovereign/decisions/metabolize_*.md)
  * Watches chronicle insight + open_thread mtimes for new writes.
  * Bridge polling is optional. Spiral status / comms-unread come from
    bridge if it's up, but the dashboard still renders without it.
  * Data layer is pure functions (testable) — render and async loop
    are isolated for substitutability.

Public API:
  - ActivityEvent dataclass
  - ActivityFeed deque-backed collector
  - DashboardState / collect_state() (pure snapshot)
  - render_state(state) (string) — for the human view
  - run_loop(...) (async) — the main TUI loop
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import connectivity, protected
from .scribe.redactor import redact as _scribe_redact

# ── Defaults / paths ────────────────────────────────────────────────────────


DEFAULT_POLL_SECONDS = 3
DEFAULT_FEED_MAX = 50
DEFAULT_FEED_RENDER_LIMIT = 15


def _sovereign_root() -> Path:
    return Path(os.environ.get("SOVEREIGN_ROOT", Path.home() / ".sovereign"))


def _chronicle_dir() -> Path:
    return _sovereign_root() / "chronicle"


def _nape_honks_path() -> Path:
    return _sovereign_root() / "nape" / "honks.jsonl"


def _halts_dir() -> Path:
    return _sovereign_root() / "daemons" / "halts"


def _decisions_dir() -> Path:
    return _sovereign_root() / "decisions"


# ── Service telemetry (v4 ops-console, BUILD_SPEC.md §1b/§4) ───────────────
#
# Per-service log tail + uptime helpers backing the `service_telemetry`
# snapshot key that dashboard_web.build_snapshot() assembles. Two hard
# requirements throughout this section: never full-read a log file (some
# run 7MB+), and never let a raw line reach a caller before the credential
# redaction pass below.


def service_log_map() -> dict[str, Path]:
    """
    Per-service stdout/log path for the 5 connectivity endpoints, keyed by
    the same `name`s as connectivity.ENDPOINTS. Computed fresh against the
    live `_sovereign_root()` on every call (not a module-level constant)
    so SOVEREIGN_ROOT overrides in tests are honored.
    """
    root = _sovereign_root()
    return {
        "sse": root / "sse.log",
        "bridge": root / "bridge-api.log",
        "tunnel": root / "tunnel.log",
        "dispatcher": root / "dispatcher.log",
        "listener": root / "comms_listener.log",
    }


# ── Credential redaction for log lines ──────────────────────────────────────
#
# Baseline: the scribe's pattern-based redactor (scribe/redactor.py) —
# Bearer tokens, sk-ant-/sk-/pk-/api- key shapes, private-key blocks,
# UPPERCASE_ENV=credential assignments, long hex tokens, sensitive paths.
# Layered on top: shapes specific to THIS server's own logs that the
# scribe redactor doesn't cover —
#   * a lowercase `?token=` query parameter. Native /sse accepts the
#     bearer this way (sse_server.py); a raw uvicorn access-log line can
#     carry a live token that the scribe's uppercase-only env_credential
#     pattern does not match.
#   * a bare `Authorization:` header value regardless of scheme (the
#     scribe pattern only fires on the literal word "Bearer").
#   * a bare, unlabeled `Bearer <token>` of ANY length. The scribe's own
#     bearer_token pattern requires >=20 chars (`{20,}`) and a narrow
#     charset (`[A-Za-z0-9_\-\.]`), so it fail-opens on two real shapes:
#     a short token (`Bearer sk-test123`, no "Authorization:" label) and
#     a base64-shaped token containing `+`, `/`, or `=` — the scribe
#     pattern stops at the first such char, masking only a PREFIX of the
#     token and leaving the rest exposed. This pattern is length-agnostic
#     and case-insensitive, and its charset covers base64/JWT-safe chars
#     so the entire token is masked, never a partial match.
#   * a length-agnostic sk-/pk-/api-/xai- API-key shape. The scribe's own
#     sk-ant-/sk-/pk-/api- patterns require >=20 chars (and it lacks xai-
#     entirely), so short/test/rotated keys fail-open. This closes the
#     whole prefix class regardless of length. Charset is base64url
#     ([A-Za-z0-9_-], no +/=), matching real sk/pk/api/xai key encoding,
#     so a single post-scribe pass has no partial-tail leak.
_RE_TOKEN_PARAM = re.compile(r"(?i)\btoken=[^\s&\"'<>]+")
_RE_AUTH_HEADER = re.compile(
    r'(?i)authorization["\']?\s*[:=]\s*["\']?[^\s,"\'}]+(?:\s+[^\s,"\'}]+)?'
)
_RE_BARE_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-_.+/=]+")
_RE_KEY_PREFIX = re.compile(r"(?i)\b(?:sk|pk|api|xai)-[A-Za-z0-9_\-]+")


def redact_log_line(line: str) -> str:
    """
    Mandatory credential-redaction pass for one log line, applied BEFORE
    the line can enter a snapshot or a TAIL response (BUILD_SPEC.md §1b,
    §2c). See module-level comment above for the pattern set.
    """
    if not line:
        return line
    # _RE_BARE_BEARER MUST run first, on the raw line, before _scribe_redact.
    # Scribe's own bearer_token pattern (>=20 chars, narrower charset) fires
    # on any token that clears its 20-char floor and stops at the first
    # char outside [A-Za-z0-9_-.] (e.g. a base64 `+`/`/`/`=`), replacing
    # only the truncated PREFIX and leaving the tail (e.g. "+b/c=") in the
    # text — a partial match this function must never report as safe. By
    # then, the tail sits right after "<redacted-token>" where this
    # broader-charset pattern can no longer see the original "Bearer " to
    # anchor on. Running first on the untouched line lets the full-width
    # charset claim the ENTIRE token before scribe's narrower one ever
    # gets a chance to truncate it.
    text = _RE_BARE_BEARER.sub("Bearer <redacted-token>", line)
    text = _scribe_redact(text).text
    text = _RE_AUTH_HEADER.sub("authorization: <redacted>", text)
    text = _RE_TOKEN_PARAM.sub("token=<redacted>", text)
    return _RE_KEY_PREFIX.sub("<redacted-key>", text)


# ── Seek-tail (never full-read) ─────────────────────────────────────────────


def seek_tail_lines(
    path: Path,
    *,
    want_lines: int,
    initial_chunk: int = 8192,
    max_chunk: int = 2_000_000,
) -> tuple[list[str], bool]:
    """
    Return the last `want_lines` non-empty lines of `path` without reading
    the whole file, plus a `truncated` flag (True whenever content before
    the returned window still exists on disk — the common case for any
    log bigger than the window).

    Starts with an `initial_chunk`-byte read from EOF (seek(-initial_chunk,
    SEEK_END) equivalent); if that doesn't contain enough newlines and more
    of the file remains, grows the window (x4) up to `max_chunk` — a safety
    ceiling well below the 7MB+ size some of these logs reach, so a caller
    asking for the full 500-line TAIL clamp still gets a real seek-tail,
    never a full read.

    Missing file, 0-byte file, or any OSError -> ([], False).
    """
    try:
        if not path.exists() or not path.is_file():
            return [], False
        size = path.stat().st_size
    except OSError:
        return [], False
    if size == 0:
        return [], False

    chunk = initial_chunk
    seek_from = 0
    data = b""
    try:
        with path.open("rb") as f:
            while True:
                seek_from = max(0, size - chunk)
                f.seek(seek_from)
                data = f.read()
                if data.count(b"\n") >= want_lines or seek_from == 0 or chunk >= max_chunk:
                    break
                chunk = min(chunk * 4, max_chunk)
    except OSError:
        return [], False

    text = data.decode("utf-8", errors="replace")
    all_lines = [ln for ln in text.splitlines() if ln.strip()]
    tail = all_lines[-want_lines:] if want_lines > 0 else []
    truncated = seek_from > 0 or len(all_lines) > want_lines
    return tail, truncated


def recent_log_lines(path: Path, *, n: int = 3) -> list[str]:
    """
    Seek-tail the last `n` non-empty, credential-redacted lines of a
    service log for the `service_telemetry` snapshot field. 0-byte or
    missing file -> []. Never full-reads.
    """
    lines, _truncated = seek_tail_lines(path, want_lines=n)
    return [redact_log_line(ln) for ln in lines]


# ── Process uptime ───────────────────────────────────────────────────────────


def _parse_ps_etime(raw: str) -> float | None:
    """
    Parse `ps -o etime=` output — format `[[dd-]hh:]mm:ss` — into elapsed
    seconds. `etime` (not `etimes`) is deliberate: BSD/macOS `ps` has no
    `etimes` keyword at all (verified live on this machine — `ps: etimes:
    keyword not found`, exit 1, which would have made this helper silently
    return None for every service, always, on Darwin). `etime` in this
    bracketed form is what both BSD ps (macOS) and GNU ps (Linux) actually
    support, so parsing it here is also more portable than the
    integer-seconds `etimes` the spec named.
    """
    raw = raw.strip()
    if not raw:
        return None
    days = 0
    if "-" in raw:
        day_part, rest = raw.split("-", 1)
        try:
            days = int(day_part)
        except ValueError:
            return None
    else:
        rest = raw
    parts = rest.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 2:
        hh, mm, ss = 0, nums[0], nums[1]
    elif len(nums) == 3:
        hh, mm, ss = nums
    else:
        return None
    return float(days * 86400 + hh * 3600 + mm * 60 + ss)


def current_uptime_seconds(pid: int | None) -> float | None:
    """
    Elapsed process uptime in seconds via `ps -o etime= -p <pid>`. None
    when there is no live pid (e.g. a periodic service between ticks, or
    an always-on service that's down) or when `ps` can't find it (already
    exited between the launchctl read and this call).
    """
    if not pid:
        return None
    import subprocess

    try:
        proc = subprocess.run(
            ["ps", "-o", "etime=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if proc.returncode != 0:
        return None
    return _parse_ps_etime(proc.stdout)


# ── Watchman panel (ops-console) ────────────────────────────────────────────
#
# The watchman (successor to comms-listener/comms-dispatcher, 2026-08-03 —
# see connectivity.ENDPOINTS) spools one already-sanitized JSON envelope per
# ACTIVE sweep (deltas > 0) to ~/.sovereign/watchman/spool.jsonl. A sweep
# with zero deltas ("quiet") is logged to watchman.log but never spooled, so
# the freshest "last sweep" signal lives in the log, not the spool — this
# section reads BOTH. Sanitization (denylist, content-flagging, preview
# truncation) already happened upstream inside the watchman process itself
# (an envelope's own `surfaces_sanitized` flag records that); this reader
# never opens queue or chronicle content, only the two files the watchman
# already wrote for exactly this purpose.

_WATCHMAN_FLAGGED_CEILINGS = {"attend", "urgent"}
_RE_WATCHMAN_LOG_LINE = re.compile(r"^(?P<ts>\S+)\s+sweep\s+(?P<sweep_id>\S+)\s+(?P<rest>.*)$")
_RE_WATCHMAN_SURFACES_OK = re.compile(r"(\d+)\s+surfaces\s+ok")


def _watchman_dir(root: Path) -> Path:
    return root / "watchman"


def _parse_iso_epoch(text: Any) -> float | None:
    """ISO-8601 string -> epoch seconds. None on anything that isn't a
    parseable string (missing field, wrong type, malformed timestamp) —
    never raises."""
    if not isinstance(text, str) or not text:
        return None
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _reduce_watchman_sweep(env: dict) -> dict:
    """Reduce one sanitized watchman sweep envelope to the panel's fields.
    Defensive key-by-key: an envelope with a missing or wrong-shaped field
    degrades that one field to None/empty rather than the whole sweep being
    dropped. Field set is deliberately narrow — sweep_id, timestamp,
    items_seen, grok_scope, grok_reply_state, severity_ceiling, and up to 3
    flagged/attend reasons (already-sanitized text, capped at 140 chars)."""
    counts = env.get("counts")
    items_seen = counts.get("items_seen") if isinstance(counts, dict) else None

    scope = env.get("grok_scope")
    grok_scope = {
        "classified": scope.get("classified") if isinstance(scope, dict) else None,
        "mechanical_only": scope.get("mechanical_only") if isinstance(scope, dict) else None,
    }

    reasons: list[str] = []
    reply = env.get("grok_reply")
    reply_items = reply.get("items") if isinstance(reply, dict) else None
    if isinstance(reply_items, list):
        for it in reply_items:
            if not isinstance(it, dict):
                continue
            flagged = it.get("severity") in _WATCHMAN_FLAGGED_CEILINGS or bool(
                it.get("flagged_for_richer_review")
            )
            if not flagged:
                continue
            # surfaces_sanitized attests to the SURFACES (queue/halt/honk/
            # comms previews), not to Grok's own free-text judgment about
            # them — run the same credential-redaction pass every other
            # text path in this module runs (redact_log_line, §1b/§2c)
            # before an unbounded Grok-authored string reaches a snapshot.
            reason = _preview_text(redact_log_line(it.get("reason") or ""), limit=140)
            if reason:
                reasons.append(reason)
            if len(reasons) >= 3:
                break

    timestamp = _parse_iso_epoch(env.get("finished_at")) or _parse_iso_epoch(env.get("started_at"))

    return {
        "sweep_id": env.get("sweep_id"),
        "timestamp": timestamp,
        "items_seen": items_seen,
        "grok_scope": grok_scope,
        "grok_reply_state": env.get("grok_reply_state"),
        "severity_ceiling": env.get("severity_ceiling"),
        "reasons": reasons,
    }


def _flagged_trend(sweeps_newest_first: list[dict]) -> str:
    """Cheap, envelope-only proxy for whether attend/urgent judgments are
    rising or falling across the loaded window — NOT an ack-state trend
    (that would require reading nape's honks/acks stores, which this reader
    deliberately never touches). 'flagged' means severity_ceiling in
    {attend, urgent} on the already-reduced sweep. Fewer than 2 sweeps in
    the window -> 'unknown' (nothing to compare); zero flagged anywhere ->
    'none'; otherwise the second half of the (chronological) window is
    compared against the first half."""
    if len(sweeps_newest_first) < 2:
        return "unknown"
    chronological = list(reversed(sweeps_newest_first))
    flags = [s.get("severity_ceiling") in _WATCHMAN_FLAGGED_CEILINGS for s in chronological]
    if not any(flags):
        return "none"
    mid = len(flags) // 2
    older, newer = flags[:mid], flags[mid:]
    older_rate = (sum(older) / len(older)) if older else 0.0
    newer_rate = sum(newer) / len(newer)
    if newer_rate > older_rate:
        return "rising"
    if newer_rate < older_rate:
        return "falling"
    return "flat"


def _parse_watchman_log_line(line: str) -> dict | None:
    """Parse one watchman.log line — either the 'quiet' form ('quiet — N
    surfaces ok, M deltas; ...') or the active form ('— M deltas,
    grok_process=..., reply=...'). Returns None for a line that doesn't
    match the 'sweep <id>' shape at all (never raises)."""
    m = _RE_WATCHMAN_LOG_LINE.match(line.strip())
    if not m:
        return None
    rest = m.group("rest")
    surfaces_m = _RE_WATCHMAN_SURFACES_OK.search(rest)
    return {
        "timestamp": _parse_iso_epoch(m.group("ts")),
        "quiet": rest.startswith("quiet"),
        "surfaces_watched": int(surfaces_m.group(1)) if surfaces_m else None,
    }


def read_watchman_sweeps(spool_path: Path, *, limit: int = 8) -> tuple[list[dict], int, int | None]:
    """Tail-read the last `limit` sweep envelopes from watchman's
    spool.jsonl and reduce each to panel shape, newest-first. Uses
    seek_tail_lines rather than a naive `.read_text().splitlines()`, so a
    spool that holds MORE than `limit` sweeps never needs a full read —
    the common case as the file grows over time. CORRECTION: this is not
    an unconditional guarantee — seek_tail_lines grows its read window
    toward `max_chunk` whenever it can't find `limit` newlines yet, so a
    spool holding FEWER lines than `limit` (true of the live file as of
    this writing: 6 lines, 150-414KB each) gets read in full today, same
    as every other seek_tail_lines caller in this module in that
    situation — this reader inherits that behavior rather than changing
    it. Missing/empty file -> ([], 0, None), the same present-but-empty
    semantics every reader in this module uses. A malformed JSON line (or
    a line that parses but isn't a JSON object) is skipped and counted,
    never raised. Also returns the newest envelope's raw `surfaces` key
    count, as a fallback source for the snapshot's `surfaces_watched` when
    watchman.log can't supply one."""
    lines, _truncated = seek_tail_lines(spool_path, want_lines=limit)
    sweeps: list[dict] = []
    malformed = 0
    newest_surfaces_count: int | None = None
    for line in lines:  # ascending: oldest of the window first
        try:
            env = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if not isinstance(env, dict):
            malformed += 1
            continue
        sweeps.append(_reduce_watchman_sweep(env))
        surfaces = env.get("surfaces")
        if isinstance(surfaces, dict):
            newest_surfaces_count = len(surfaces)  # last valid envelope wins == newest
    sweeps.reverse()  # panel wants newest-first
    return sweeps[:limit], malformed, newest_surfaces_count


def build_watchman_summary(*, sovereign_root: Path | None = None, limit: int = 8) -> dict:
    """Build the `watchman` snapshot key: the last `limit` sweeps plus a
    summary line. Two data sources, both already-sanitized by the watchman
    process itself: spool.jsonl (active sweeps only) and watchman.log
    (every sweep, including quiet ones — the only place a fully-current
    'last sweep age' / quiet-vs-active read can come from, since a quiet
    sweep never reaches the spool)."""
    root = sovereign_root or _sovereign_root()
    wm_dir = _watchman_dir(root)

    sweeps, malformed, spool_surfaces_count = read_watchman_sweeps(
        wm_dir / "spool.jsonl", limit=limit
    )

    log_lines, _t = seek_tail_lines(wm_dir / "watchman.log", want_lines=1)
    log_status = _parse_watchman_log_line(log_lines[-1]) if log_lines else None

    if log_status is not None:
        last_age = (
            max(0.0, time.time() - log_status["timestamp"])
            if log_status["timestamp"] is not None
            else None
        )
        status = "quiet" if log_status["quiet"] else "active"
        surfaces_watched = log_status["surfaces_watched"]
    else:
        last_age = None
        status = "unknown"
        surfaces_watched = None

    if surfaces_watched is None:
        surfaces_watched = spool_surfaces_count

    return {
        "sweeps": sweeps,
        "malformed_skipped": malformed,
        "summary": {
            "last_sweep_age_seconds": last_age,
            "status": status,
            "surfaces_watched": surfaces_watched,
            "flagged_trend": _flagged_trend(sweeps),
        },
    }


# ── Activity feed ───────────────────────────────────────────────────────────

# Categories — fixed vocabulary so the renderer can color-code consistently.
CAT_TOOLS = "TOOLS"
CAT_CHRONICLE = "CHRONICLE"
CAT_INSIGHT = "INSIGHT"
CAT_THREAD = "THREAD"
CAT_HONK = "HONK"
CAT_HALT = "HALT"
CAT_DECISION = "DECISION"
CAT_SERVICE = "SERVICE"  # service lifecycle (start/stop/restart)
CAT_COMMS = "COMMS"
CAT_ERROR = "ERROR"
CAT_STARTUP = "STARTUP"
CAT_COMMIT = "COMMIT"  # git commits landing on the repo
CAT_DEPLOY = "DEPLOY"  # service kickstarts / status changes

ALL_CATEGORIES = (
    CAT_TOOLS,
    CAT_CHRONICLE,
    CAT_INSIGHT,
    CAT_THREAD,
    CAT_HONK,
    CAT_HALT,
    CAT_DECISION,
    CAT_SERVICE,
    CAT_COMMS,
    CAT_ERROR,
    CAT_STARTUP,
    CAT_COMMIT,
    CAT_DEPLOY,
)


@dataclass
class ActivityEvent:
    timestamp: float
    category: str
    message: str

    @property
    def time_str(self) -> str:
        return datetime.fromtimestamp(self.timestamp).strftime("%H:%M:%S")


class ActivityFeed:
    """Bounded deque of ActivityEvent. Newest first when iterated."""

    def __init__(self, maxlen: int = DEFAULT_FEED_MAX):
        self._events: deque[ActivityEvent] = deque(maxlen=maxlen)

    def add(self, category: str, message: str, *, ts: float | None = None) -> None:
        if ts is None:
            ts = time.time()
        self._events.appendleft(ActivityEvent(ts, category, message))

    def __len__(self) -> int:
        return len(self._events)

    def __iter__(self):
        return iter(self._events)

    def to_list(self, limit: int | None = None) -> list[dict]:
        items = list(self._events)
        if limit is not None:
            items = items[:limit]
        return [
            {"time": e.time_str, "ts": e.timestamp, "category": e.category, "message": e.message}
            for e in items
        ]


# ── Mtime watchers (pure data, testable) ────────────────────────────────────


@dataclass
class _MtimeIndex:
    """Tracks per-path mtime to detect new writes between polls."""

    seen: dict[str, float] = field(default_factory=dict)

    def diff(self, paths: list[Path]) -> list[Path]:
        """Return the subset of `paths` whose mtime is newer than the last
        recorded value, then update the index. New paths count as 'changed'."""
        changed: list[Path] = []
        for p in paths:
            try:
                mtime = p.stat().st_mtime
            except (OSError, FileNotFoundError):
                continue
            key = str(p)
            prev = self.seen.get(key)
            if prev is None or mtime > prev:
                changed.append(p)
            self.seen[key] = mtime
        return changed


def _list_paths(directory: Path, glob: str = "*", recursive: bool = False) -> list[Path]:
    if not directory.exists():
        return []
    if recursive:
        return sorted(directory.rglob(glob))
    return sorted(directory.glob(glob))


# ── Git activity poller ────────────────────────────────────────────────────


def _git_recent_commits(
    repo_path: Path,
    *,
    since: str | None = None,
    limit: int = 5,
) -> list[dict]:
    """
    Return recent commits as [{sha, subject, author_iso}]. If `since` is
    given (a unix timestamp string), only commits after that are returned.
    Quiet on any git failure (no repo, git not installed, network glitch).
    """
    if not (repo_path / ".git").exists():
        return []
    import subprocess

    args = [
        "git",
        "-C",
        str(repo_path),
        "log",
        "--pretty=format:%H%x09%aI%x09%s",
        "-n",
        str(int(limit)),
    ]
    if since:
        args += [f"--since={since}"]
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []
    if proc.returncode != 0:
        return []
    out: list[dict] = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        out.append(
            {
                "sha": parts[0],
                "iso": parts[1],
                "subject": parts[2],
            }
        )
    return out


# ── launchd service-state poller ────────────────────────────────────────────


def _launchctl_service_states(labels: list[str]) -> dict[str, dict]:
    """
    For each launchd label, return its current state via `launchctl print`.
    Returns {label: {state, pid, last_exit_code}}. Errors are absorbed:
    missing labels return state=None and pid=None.
    """
    import os
    import re
    import subprocess

    out: dict[str, dict] = {}
    uid = os.getuid()
    re_state = re.compile(r"^\s*state\s*=\s*(\S+)", re.MULTILINE)
    re_pid = re.compile(r"^\s*pid\s*=\s*(\d+)", re.MULTILINE)
    for label in labels:
        try:
            proc = subprocess.run(
                ["launchctl", "print", f"gui/{uid}/{label}"],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            out[label] = {"state": None, "pid": None}
            continue
        if proc.returncode != 0:
            out[label] = {"state": None, "pid": None}
            continue
        m_state = re_state.search(proc.stdout)
        m_pid = re_pid.search(proc.stdout)
        out[label] = {
            "state": m_state.group(1).strip().rstrip(",") if m_state else None,
            "pid": int(m_pid.group(1)) if m_pid else None,
        }
    return out


# ── Source readers (pure: take a path, return events) ───────────────────────


def read_recent_honks(path: Path, *, limit: int = 5) -> list[dict]:
    """
    Read the last N UNACKED entries from nape honks.jsonl, with cross-file
    ack lookup against acks.jsonl in the same directory.

    Nape's canonical layout writes honks to honks.jsonl and acks to a
    SIBLING acks.jsonl — two files. An earlier dashboard implementation
    only checked the `ack_id` field within honks.jsonl, which missed
    every ack made through the standard nape_daemon.acknowledge() path
    (which writes to acks.jsonl, not back into honks.jsonl). This
    function now reads both files and excludes honk_ids that appear in
    acks.jsonl as well.
    """
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    # Sibling acks file. Build the set of acked honk_ids once.
    acks_path = path.parent / "acks.jsonl"
    acked_ids: set = set()
    if acks_path.exists():
        try:
            for line in acks_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                hid = rec.get("honk_id")
                if hid:
                    acked_ids.add(hid)
        except OSError:
            pass

    out: list[dict] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        # Two ways an ack can be recorded:
        #   - inline (ack_id field present in the honk record itself)
        #   - cross-file (honk_id appears as a record in acks.jsonl)
        if rec.get("ack_id"):
            continue
        if rec.get("honk_id") in acked_ids:
            continue
        out.append(rec)
        if len(out) >= limit:
            break
    return out


def read_chronicle_tail(path: Path, chronicle_root: Path | None = None) -> dict | None:
    """
    Read the last record from a chronicle JSONL file, or None.

    Protected-source gate (spec §5.4): every caller of this tail reader
    truncates the content (``content[:80]`` in the feed/TUI), so the dashboard
    tail is a PREVIEW surface — it can never carry the full stakes, and an
    80-char slice of coupled content would re-decouple. When ``chronicle_root``
    is supplied and the tail record is protected, the content is WITHHELD to
    the placeholder (locator fields survive, so the feed still shows that a
    protected record landed). ``chronicle_root`` defaults to None for the
    non-insight callers (open_threads/halts tails carry no insight content and
    are not in the protected claim space); the empty-fold fast path keeps
    ordinary records byte-identical.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    last = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            last = json.loads(line)
        except json.JSONDecodeError:
            continue
    if last is not None and chronicle_root is not None:
        fold = protected.load_protected_fold(chronicle_root)
        if protected.is_protected(last, fold):
            return protected.withhold_preview(last)
    return last


# ── Spiral / comms via bridge (optional) ────────────────────────────────────


@dataclass
class BridgeStats:
    phase: str = "unknown"
    tool_calls: int = 0
    reflection_depth: int = 0
    duration_seconds: float = 0.0
    comms_unread: int = 0
    bridge_reachable: bool = False


def _format_uptime(seconds: float) -> str:
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    mins = int((seconds % 3600) // 60)
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def parse_spiral_status_text(text: str) -> dict:
    """Parse the spiral_status MCP tool's text output into a dict."""
    out: dict[str, Any] = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("Phase:"):
            out["phase"] = line.split(":", 1)[1].strip()
        elif line.startswith("Tool Calls:"):
            with contextlib.suppress(ValueError):
                out["tool_calls"] = int(line.split(":", 1)[1].strip())
        elif line.startswith("Reflection Depth:"):
            with contextlib.suppress(ValueError):
                out["reflection_depth"] = int(line.split(":", 1)[1].strip())
        elif line.startswith("Duration:"):
            raw = line.split(":", 1)[1].strip().replace("s", "")
            with contextlib.suppress(ValueError):
                out["duration_seconds"] = float(raw)
    return out


# ── Snapshot state (pure) ───────────────────────────────────────────────────


@dataclass
class DashboardState:
    timestamp: float
    connectivity_summary: dict
    bridge_stats: BridgeStats
    feed: list[dict]
    listener_stale: bool = False
    halts_count: int = 0
    decisions_count: int = 0
    unacked_honks: int = 0
    # Latest entries by type. Each value is a small preview dict or None.
    # Rendered in the dashboard's "Latest" panel so a watcher sees the
    # most recent substantive content alongside the pulse-of-services.
    latest: dict[str, dict | None] = field(default_factory=dict)


def _newest_jsonl_record(
    directory: Path,
    *,
    recursive: bool = False,
    glob: str = "*.jsonl",
    chronicle_root: Path | None = None,
) -> dict | None:
    """
    Find the most-recently-modified JSONL file under `directory` and
    return its tail record (newest line). Returns None if no JSONL
    files exist or every file is empty/malformed.

    `chronicle_root` is forwarded to read_chronicle_tail so a protected
    insight tail withholds its content (§5.4); pass it for the insight
    snapshot, leave None for non-insight directories.
    """
    if not directory.exists():
        return None
    files = sorted(
        (directory.rglob(glob) if recursive else directory.glob(glob)),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )
    for f in files:
        rec = read_chronicle_tail(f, chronicle_root)
        if rec is not None:
            return rec
    return None


def _newest_file(directory: Path, *, glob: str = "*", recursive: bool = False) -> Path | None:
    """Return the newest file matching `glob` under `directory`, or None."""
    if not directory.exists():
        return None
    files = directory.rglob(glob) if recursive else directory.glob(glob)
    files = [f for f in files if f.is_file()]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def _preview_text(text: str, limit: int = 160) -> str:
    text = (text or "").strip().replace("\n", " ")
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def collect_latest_entries(sovereign_root: Path) -> dict[str, dict | None]:
    """
    Collect the most-recent record of each notable type, formatted for
    dashboard display. Each value is None if no record exists for that
    type, or a dict with at minimum `timestamp` + `preview` keys.

    Types covered:
      insight       — newest record across chronicle/insights/
      open_thread   — newest record across chronicle/open_threads/
      learning      — newest record across chronicle/learnings/
      handoff       — newest JSON file in handoffs/
      decision      — newest metabolize_*.md in decisions/
      halt          — newest *.md in daemons/halts/
      honk          — newest unacked honk in nape/honks.jsonl
    """
    out: dict[str, dict | None] = {}

    insight = _newest_jsonl_record(
        sovereign_root / "chronicle" / "insights",
        recursive=True,
        chronicle_root=sovereign_root / "chronicle",
    )
    if insight:
        out["insight"] = {
            "timestamp": insight.get("timestamp"),
            "domain": insight.get("domain") or insight.get("_domain_dir"),
            "layer": insight.get("layer", "?"),
            "preview": _preview_text(insight.get("content", "")),
        }
    else:
        out["insight"] = None

    thread = _newest_jsonl_record(
        sovereign_root / "chronicle" / "open_threads",
        recursive=True,
    )
    if thread:
        out["open_thread"] = {
            "timestamp": thread.get("timestamp"),
            "domain": thread.get("domain"),
            "thread_id": thread.get("thread_id"),
            "preview": _preview_text(thread.get("question", "")),
        }
    else:
        out["open_thread"] = None

    learning = _newest_jsonl_record(
        sovereign_root / "chronicle" / "learnings",
        recursive=True,
    )
    if learning:
        out["learning"] = {
            "timestamp": learning.get("timestamp"),
            "applies_to": learning.get("applies_to"),
            "preview": _preview_text(
                learning.get("what_learned") or learning.get("what_happened", "")
            ),
        }
    else:
        out["learning"] = None

    handoff_path = _newest_file(sovereign_root / "handoffs", glob="*.json")
    if handoff_path:
        try:
            data = json.loads(handoff_path.read_text(encoding="utf-8"))
            out["handoff"] = {
                "timestamp": data.get("timestamp"),
                "thread": data.get("thread"),
                "source_instance": data.get("source_instance"),
                "preview": _preview_text(data.get("note", "")),
                "consumed_by": data.get("consumed_by"),
            }
        except (OSError, json.JSONDecodeError):
            out["handoff"] = {
                "timestamp": None,
                "preview": f"(unreadable: {handoff_path.name})",
            }
    else:
        out["handoff"] = None

    decision_path = _newest_file(sovereign_root / "decisions", glob="metabolize_*.md")
    if decision_path:
        try:
            text = decision_path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        # First non-header line as preview.
        preview_line = ""
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                preview_line = line
                break
        out["decision"] = {
            "timestamp": _file_iso(decision_path),
            "filename": decision_path.name,
            "preview": _preview_text(preview_line or "(no body)"),
        }
    else:
        out["decision"] = None

    halt_path = _newest_file(sovereign_root / "daemons" / "halts", glob="*.md")
    if halt_path:
        try:
            text = halt_path.read_text(encoding="utf-8")
        except Exception:
            text = ""
        # Pull the Reason: line for the preview — that's the headline.
        reason = ""
        for line in text.splitlines():
            if line.startswith("Reason:"):
                reason = line.split(":", 1)[1].strip()
                break
        out["halt"] = {
            "timestamp": _file_iso(halt_path),
            "filename": halt_path.name,
            "preview": _preview_text(reason or halt_path.stem),
        }
    else:
        out["halt"] = None

    honks = read_recent_honks(sovereign_root / "nape" / "honks.jsonl", limit=1)
    if honks:
        h = honks[0]
        out["honk"] = {
            # Additive (BUILD_SPEC.md scope-C shape unchanged otherwise):
            # the id NapeDaemon.ack() targets (see nape_daemon.py `ack`),
            # so the frontend can actually address this honk in a real
            # POST /actions/ack instead of falling back to a fail-open
            # session-global local hide.
            "honk_id": h.get("honk_id"),
            "timestamp": h.get("timestamp"),
            "level": h.get("level"),
            "pattern": h.get("pattern"),
            "trigger_tool": h.get("trigger_tool"),
            "preview": _preview_text(h.get("observation", "")),
        }
    else:
        out["honk"] = None

    return out


def _file_iso(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(
            path.stat().st_mtime,
            tz=timezone.utc,
        ).isoformat()
    except Exception:
        return None


def collect_state(
    feed: ActivityFeed,
    bridge_stats: BridgeStats | None = None,
    *,
    sovereign_root: Path | None = None,
    connectivity_check: Any | None = None,
    restart_counts: dict[str, int | None] | None = None,
) -> DashboardState:
    """
    Build a one-shot snapshot of the stack state. Pure-data — no
    rendering, no async polling. Composable with whatever the caller
    has already collected (feed / bridge_stats injected).

    Args:
        feed: ActivityFeed instance — included as a list snapshot.
        bridge_stats: Pre-fetched bridge stats; if None, defaults are used.
        sovereign_root: Override for the data root (tests use this).
        connectivity_check: Override for connectivity check (tests inject).
        restart_counts: Out-param dict forwarded to connectivity.check_all()
            when `connectivity_check` is None — see check_status's docstring.
            Ignored (left as the caller passed it, unfilled) when a custom
            connectivity_check override is supplied, since that override
            doesn't know about the out-param protocol.
    """
    root = sovereign_root or _sovereign_root()

    if connectivity_check is None:
        statuses = connectivity.check_all(restart_counts=restart_counts)
    else:
        statuses = connectivity_check()

    summary = connectivity.aggregate(statuses)

    # The "listener" badge now tracks the house's actual listening organ: the
    # watchman (successor to comms-listener AND comms-dispatcher, 2026-08-03).
    listener_stale = any(
        s["name"] == "watchman" and s["status"] == connectivity.STATUS_STALE
        for s in summary["endpoints"]
    )

    halts_count = len(_list_paths(root / "daemons" / "halts", "*.md"))
    decisions_count = len(_list_paths(root / "decisions", "metabolize_*.md"))
    honks = read_recent_honks(root / "nape" / "honks.jsonl", limit=100)
    unacked = len(honks)

    latest = collect_latest_entries(root)

    return DashboardState(
        timestamp=time.time(),
        connectivity_summary=summary,
        bridge_stats=bridge_stats or BridgeStats(),
        feed=feed.to_list(),
        listener_stale=listener_stale,
        halts_count=halts_count,
        decisions_count=decisions_count,
        unacked_honks=unacked,
        latest=latest,
    )


# ── Renderer (string in, string out — testable) ─────────────────────────────


_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_GOLD = "\033[38;5;220m"
_PURPLE = "\033[38;5;141m"
_TEAL = "\033[38;5;80m"
_RED = "\033[38;5;203m"
_GREEN = "\033[38;5;114m"
_BLUE = "\033[38;5;111m"
_GRAY = "\033[38;5;245m"
_HEADER_BG = "\033[48;5;236m"

_CAT_COLOR = {
    CAT_TOOLS: _TEAL,
    CAT_CHRONICLE: _PURPLE,
    CAT_INSIGHT: _BLUE,
    CAT_THREAD: _PURPLE,
    CAT_HONK: _GOLD,
    CAT_HALT: _RED,
    CAT_DECISION: _GOLD,
    CAT_SERVICE: _GREEN,
    CAT_COMMS: _GOLD,
    CAT_ERROR: _RED,
    CAT_STARTUP: _GREEN,
}

_STATUS_COLOR = {
    connectivity.STATUS_OK: _GREEN,
    connectivity.STATUS_DEGRADED: _GOLD,
    connectivity.STATUS_DOWN: _RED,
    connectivity.STATUS_STALE: _GOLD,
    connectivity.STATUS_UNKNOWN: _GRAY,
}


def render_state(
    state: DashboardState,
    *,
    width: int = 100,
    feed_limit: int = DEFAULT_FEED_RENDER_LIMIT,
    color: bool = True,
) -> str:
    """Render a DashboardState as a string. Color codes optional."""

    def _c(s: str, code: str) -> str:
        return f"{code}{s}{_RESET}" if color else s

    lines: list[str] = []
    lines.append(
        _c(f"  {'†⟡†  SOVEREIGN STACK DASHBOARD':^{width - 4}}  ", _HEADER_BG + _BOLD + _GOLD)
    )
    lines.append("")

    # Header line — bridge stats
    bs = state.bridge_stats
    phase_color = _TEAL if bs.phase != "unknown" else _GRAY
    bridge_glyph = _c("●", _GREEN) if bs.bridge_reachable else _c("○", _RED)
    header = (
        f"  {_c('Phase:', _BOLD)} {_c(bs.phase, phase_color)}  "
        f"{_c('|', _GRAY)}  Tools: {_c(str(bs.tool_calls), _TEAL)}  "
        f"{_c('|', _GRAY)}  Up: {_c(_format_uptime(bs.duration_seconds), _GRAY)}  "
        f"{_c('|', _GRAY)}  Depth: {_c(str(bs.reflection_depth), _PURPLE)}  "
        f"{_c('|', _GRAY)}  Comms: "
        f"{_c(f'{bs.comms_unread} unread', _GOLD if bs.comms_unread else _GRAY)}  "
        f"{_c('|', _GRAY)}  Bridge: {bridge_glyph}"
    )
    lines.append(header)
    lines.append("")

    # Services
    summary = state.connectivity_summary
    overall = summary["overall"]
    overall_color = _STATUS_COLOR.get(overall, _GRAY)
    counts_str = "  ".join(f"{k}={v}" for k, v in sorted(summary["counts"].items()))
    lines.append(
        f"  {_c('SERVICES', _BOLD)}  {_c(overall.upper(), overall_color)}  {_c(counts_str, _DIM)}"
    )
    lines.append(_c("  " + "─" * (width - 4), _GRAY))
    for ep in summary["endpoints"]:
        sc = _STATUS_COLOR.get(ep["status"], _GRAY)
        glyph = "●" if ep["status"] == connectivity.STATUS_OK else "○"
        pid = f"pid={ep['pid']}" if ep.get("pid") else "—"
        extra: list[str] = []
        if ep.get("http_status") is not None:
            extra.append(f"http={ep['http_status']}")
        if ep.get("log_age_seconds") is not None:
            extra.append(f"log_age={int(ep['log_age_seconds'])}s")
        if ep.get("notes"):
            extra.append(ep["notes"][0])
        extra_str = "  " + " | ".join(extra) if extra else ""
        lines.append(
            f"  {_c(glyph, sc)} {ep['name']:<12} "
            f"{_c(ep['status'].upper(), sc):<18} {pid}{extra_str}"
        )
    lines.append("")

    # v1.3.2 indicators
    indicators: list[str] = []
    if state.unacked_honks:
        indicators.append(_c(f"⚠ {state.unacked_honks} unacked honk(s)", _GOLD))
    if state.halts_count:
        indicators.append(_c(f"⛔ {state.halts_count} halt note(s)", _RED))
    if state.decisions_count:
        indicators.append(_c(f"📋 {state.decisions_count} metabolize decision(s)", _BLUE))
    if state.listener_stale:
        indicators.append(_c("⏰ listener stale", _GOLD))
    if indicators:
        lines.append("  " + "  ".join(indicators))
        lines.append("")

    # Live feed
    lines.append(_c("  LIVE ACTIVITY", _BOLD))
    lines.append(_c("  " + "─" * (width - 4), _GRAY))
    if not state.feed:
        lines.append(_c("  Watching…", _DIM))
    else:
        for entry in state.feed[:feed_limit]:
            cat = entry["category"]
            color_code = _CAT_COLOR.get(cat, _GRAY)
            lines.append(
                f"  {_c(entry['time'], _DIM)} {_c(cat, color_code + _BOLD):<22} {entry['message']}"
            )

    lines.append("")
    lines.append(
        _c(
            f"  Refresh: {DEFAULT_POLL_SECONDS}s  |  Ctrl+C to exit  |  "
            f"{datetime.now().strftime('%H:%M:%S')}",
            _DIM,
        )
    )
    return "\n".join(lines)


# ── Async loop (the live TUI) ───────────────────────────────────────────────


async def _bridge_get_spiral(bridge_url: str, headers: dict) -> dict | None:
    """Best-effort bridge call. Returns None on any failure."""
    try:
        import httpx  # local import — bridge is optional
    except ImportError:
        return None
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            r = await client.post(
                f"{bridge_url}/api/call",
                headers=headers,
                json={"tool": "spiral_status", "arguments": {}},
            )
            if r.status_code != 200:
                return None
            data = r.json()
            return parse_spiral_status_text(data.get("result", ""))
    except Exception:
        return None


async def _bridge_get_unread(bridge_url: str, headers: dict, instance_id: str) -> int | None:
    try:
        import httpx
    except ImportError:
        return None
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            r = await client.get(
                f"{bridge_url}/api/comms/unread?instance_id={instance_id}",
                headers=headers,
            )
            if r.status_code != 200:
                return None
            return int(r.json().get("total", 0))
    except Exception:
        return None


async def run_loop(
    *,
    interval: int = DEFAULT_POLL_SECONDS,
    bridge_url: str | None = None,
    bridge_token: str | None = None,
    instance_id: str = "dashboard",
    once: bool = False,
    color: bool = True,
) -> None:
    """
    Main TUI loop. Set `once=True` to render a single frame and return —
    used by `--once` and tests.
    """
    feed = ActivityFeed()
    feed.add(CAT_STARTUP, "dashboard starting…")

    root = _sovereign_root()
    chronicle_index = _MtimeIndex()
    halts_index = _MtimeIndex()
    decisions_index = _MtimeIndex()
    honks_index = _MtimeIndex()

    # Seed the indices so we don't immediately spam the feed with everything
    # already on disk on the first tick.
    chronicle_index.diff(_list_paths(root / "chronicle" / "insights", "*.jsonl", recursive=True))
    chronicle_index.diff(
        _list_paths(root / "chronicle" / "open_threads", "*.jsonl", recursive=True)
    )
    halts_index.diff(_list_paths(root / "daemons" / "halts", "*.md"))
    decisions_index.diff(_list_paths(root / "decisions", "metabolize_*.md"))
    honks_index.diff([root / "nape" / "honks.jsonl"])

    bridge_headers = {"Authorization": f"Bearer {bridge_token}"} if bridge_token else {}
    bs = BridgeStats()

    cycle = 0
    while True:
        try:
            # ── Filesystem watchers ──
            for jsonl in chronicle_index.diff(
                _list_paths(root / "chronicle" / "insights", "*.jsonl", recursive=True),
            ):
                # §5.4: insight tails pass chronicle_root so protected records
                # withhold their content before the 80-char feed slice.
                tail = read_chronicle_tail(jsonl, root / "chronicle")
                if tail:
                    layer = tail.get("layer", "?")
                    content = (tail.get("content") or "")[:80]
                    feed.add(CAT_INSIGHT, f"[{layer}] {content}…")

            for jsonl in chronicle_index.diff(
                _list_paths(root / "chronicle" / "open_threads", "*.jsonl", recursive=True),
            ):
                tail = read_chronicle_tail(jsonl)
                if tail:
                    q = (tail.get("question") or "")[:80]
                    feed.add(CAT_THREAD, q)

            for halt in halts_index.diff(
                _list_paths(root / "daemons" / "halts", "*.md"),
            ):
                feed.add(CAT_HALT, f"halt note: {halt.name}")

            for dec in decisions_index.diff(
                _list_paths(root / "decisions", "metabolize_*.md"),
            ):
                feed.add(CAT_DECISION, f"new metabolize digest: {dec.name}")

            if honks_index.diff([root / "nape" / "honks.jsonl"]):
                recent = read_recent_honks(root / "nape" / "honks.jsonl", limit=3)
                for h in recent:
                    feed.add(
                        CAT_HONK,
                        f"[{h.get('level', '?')}] {h.get('pattern', '?')}: "
                        f"{h.get('trigger_tool', '?')}",
                    )

            # ── Bridge polling (optional, every 3 cycles for spiral) ──
            if bridge_url and cycle % 3 == 0:
                spiral = await _bridge_get_spiral(bridge_url, bridge_headers)
                if spiral is not None:
                    bs.bridge_reachable = True
                    if "phase" in spiral and spiral["phase"] != bs.phase:
                        feed.add(CAT_TOOLS, f"phase advanced: {bs.phase} → {spiral['phase']}")
                    if (
                        "tool_calls" in spiral
                        and bs.tool_calls > 0
                        and spiral["tool_calls"] > bs.tool_calls
                    ):
                        delta = spiral["tool_calls"] - bs.tool_calls
                        feed.add(CAT_TOOLS, f"+{delta} tool call(s)")
                    bs.phase = spiral.get("phase", bs.phase)
                    bs.tool_calls = spiral.get("tool_calls", bs.tool_calls)
                    bs.reflection_depth = spiral.get("reflection_depth", bs.reflection_depth)
                    bs.duration_seconds = spiral.get("duration_seconds", bs.duration_seconds)
                else:
                    bs.bridge_reachable = False

            if bridge_url and cycle % 5 == 0:
                unread = await _bridge_get_unread(
                    bridge_url,
                    bridge_headers,
                    instance_id,
                )
                if unread is not None:
                    if bs.comms_unread > 0 and unread > bs.comms_unread:
                        feed.add(CAT_COMMS, f"{unread - bs.comms_unread} new")
                    bs.comms_unread = unread

            # ── Render ──
            state = collect_state(feed, bs)
            print("\033[2J\033[H", end="")  # clear screen + home cursor
            print(render_state(state, color=color))

            if once:
                return

            cycle += 1
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            return
        except Exception as e:
            feed.add(CAT_ERROR, f"loop error: {type(e).__name__}: {e}")
            await asyncio.sleep(interval)
