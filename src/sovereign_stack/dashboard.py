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
import json
import os
import sys
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

from . import connectivity


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


# ── Activity feed ───────────────────────────────────────────────────────────

# Categories — fixed vocabulary so the renderer can color-code consistently.
CAT_TOOLS = "TOOLS"
CAT_CHRONICLE = "CHRONICLE"
CAT_INSIGHT = "INSIGHT"
CAT_THREAD = "THREAD"
CAT_HONK = "HONK"
CAT_HALT = "HALT"
CAT_DECISION = "DECISION"
CAT_SERVICE = "SERVICE"
CAT_COMMS = "COMMS"
CAT_ERROR = "ERROR"
CAT_STARTUP = "STARTUP"

ALL_CATEGORIES = (
    CAT_TOOLS, CAT_CHRONICLE, CAT_INSIGHT, CAT_THREAD,
    CAT_HONK, CAT_HALT, CAT_DECISION, CAT_SERVICE,
    CAT_COMMS, CAT_ERROR, CAT_STARTUP,
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
        self._events: Deque[ActivityEvent] = deque(maxlen=maxlen)

    def add(self, category: str, message: str, *, ts: Optional[float] = None) -> None:
        if ts is None:
            ts = time.time()
        self._events.appendleft(ActivityEvent(ts, category, message))

    def __len__(self) -> int:
        return len(self._events)

    def __iter__(self):
        return iter(self._events)

    def to_list(self, limit: Optional[int] = None) -> List[Dict]:
        items = list(self._events)
        if limit is not None:
            items = items[:limit]
        return [
            {"time": e.time_str, "ts": e.timestamp,
             "category": e.category, "message": e.message}
            for e in items
        ]


# ── Mtime watchers (pure data, testable) ────────────────────────────────────


@dataclass
class _MtimeIndex:
    """Tracks per-path mtime to detect new writes between polls."""
    seen: Dict[str, float] = field(default_factory=dict)

    def diff(self, paths: List[Path]) -> List[Path]:
        """Return the subset of `paths` whose mtime is newer than the last
        recorded value, then update the index. New paths count as 'changed'."""
        changed: List[Path] = []
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


def _list_paths(directory: Path, glob: str = "*", recursive: bool = False) -> List[Path]:
    if not directory.exists():
        return []
    if recursive:
        return sorted(directory.rglob(glob))
    return sorted(directory.glob(glob))


# ── Source readers (pure: take a path, return events) ───────────────────────


def read_recent_honks(path: Path, *, limit: int = 5) -> List[Dict]:
    """
    Read the last N entries from nape honks.jsonl. Skips acked honks
    (Nape writes both the honk and ack records to the same file in some
    layouts; we keep this conservative — only return those without an
    "ack_id" field).
    """
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: List[Dict] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("ack_id"):
            continue
        out.append(rec)
        if len(out) >= limit:
            break
    return out


def read_chronicle_tail(path: Path) -> Optional[Dict]:
    """Read the last record from a chronicle JSONL file, or None."""
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


def parse_spiral_status_text(text: str) -> Dict:
    """Parse the spiral_status MCP tool's text output into a dict."""
    out: Dict[str, Any] = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("Phase:"):
            out["phase"] = line.split(":", 1)[1].strip()
        elif line.startswith("Tool Calls:"):
            try:
                out["tool_calls"] = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif line.startswith("Reflection Depth:"):
            try:
                out["reflection_depth"] = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif line.startswith("Duration:"):
            raw = line.split(":", 1)[1].strip().replace("s", "")
            try:
                out["duration_seconds"] = float(raw)
            except ValueError:
                pass
    return out


# ── Snapshot state (pure) ───────────────────────────────────────────────────


@dataclass
class DashboardState:
    timestamp: float
    connectivity_summary: Dict
    bridge_stats: BridgeStats
    feed: List[Dict]
    listener_stale: bool = False
    halts_count: int = 0
    decisions_count: int = 0
    unacked_honks: int = 0
    # Latest entries by type. Each value is a small preview dict or None.
    # Rendered in the dashboard's "Latest" panel so a watcher sees the
    # most recent substantive content alongside the pulse-of-services.
    latest: Dict[str, Optional[Dict]] = field(default_factory=dict)


def _newest_jsonl_record(directory: Path, *, recursive: bool = False,
                         glob: str = "*.jsonl") -> Optional[Dict]:
    """
    Find the most-recently-modified JSONL file under `directory` and
    return its tail record (newest line). Returns None if no JSONL
    files exist or every file is empty/malformed.
    """
    if not directory.exists():
        return None
    files = sorted(
        (directory.rglob(glob) if recursive else directory.glob(glob)),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )
    for f in files:
        rec = read_chronicle_tail(f)
        if rec is not None:
            return rec
    return None


def _newest_file(directory: Path, *, glob: str = "*",
                 recursive: bool = False) -> Optional[Path]:
    """Return the newest file matching `glob` under `directory`, or None."""
    if not directory.exists():
        return None
    files = (directory.rglob(glob) if recursive else directory.glob(glob))
    files = [f for f in files if f.is_file()]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def _preview_text(text: str, limit: int = 160) -> str:
    text = (text or "").strip().replace("\n", " ")
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def collect_latest_entries(sovereign_root: Path) -> Dict[str, Optional[Dict]]:
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
    out: Dict[str, Optional[Dict]] = {}

    insight = _newest_jsonl_record(
        sovereign_root / "chronicle" / "insights", recursive=True,
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
        sovereign_root / "chronicle" / "open_threads", recursive=True,
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
        sovereign_root / "chronicle" / "learnings", recursive=True,
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
        except Exception:
            out["handoff"] = {
                "timestamp": None,
                "preview": f"(unreadable: {handoff_path.name})",
            }
    else:
        out["handoff"] = None

    decision_path = _newest_file(sovereign_root / "decisions",
                                 glob="metabolize_*.md")
    if decision_path:
        try:
            text = decision_path.read_text(encoding="utf-8")
        except Exception:
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

    honks = read_recent_honks(sovereign_root / "nape" / "honks.jsonl",
                              limit=1)
    if honks:
        h = honks[0]
        out["honk"] = {
            "timestamp": h.get("timestamp"),
            "level": h.get("level"),
            "pattern": h.get("pattern"),
            "trigger_tool": h.get("trigger_tool"),
            "preview": _preview_text(h.get("observation", "")),
        }
    else:
        out["honk"] = None

    return out


def _file_iso(path: Path) -> Optional[str]:
    try:
        return datetime.fromtimestamp(
            path.stat().st_mtime, tz=timezone.utc,
        ).isoformat()
    except Exception:
        return None


def collect_state(
    feed: ActivityFeed,
    bridge_stats: Optional[BridgeStats] = None,
    *,
    sovereign_root: Optional[Path] = None,
    connectivity_check: Optional[Any] = None,
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
    """
    root = sovereign_root or _sovereign_root()

    if connectivity_check is None:
        statuses = connectivity.check_all()
    else:
        statuses = connectivity_check()

    summary = connectivity.aggregate(statuses)

    listener_stale = any(
        s["name"] == "listener" and s["status"] == connectivity.STATUS_STALE
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

    lines: List[str] = []
    lines.append(_c(f"  {'†⟡†  SOVEREIGN STACK DASHBOARD':^{width-4}}  ",
                    _HEADER_BG + _BOLD + _GOLD))
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
        f"  {_c('SERVICES', _BOLD)}  "
        f"{_c(overall.upper(), overall_color)}  "
        f"{_c(counts_str, _DIM)}"
    )
    lines.append(_c("  " + "─" * (width - 4), _GRAY))
    for ep in summary["endpoints"]:
        sc = _STATUS_COLOR.get(ep["status"], _GRAY)
        glyph = "●" if ep["status"] == connectivity.STATUS_OK else "○"
        pid = f"pid={ep['pid']}" if ep.get("pid") else "—"
        extra: List[str] = []
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
    indicators: List[str] = []
    if state.unacked_honks:
        indicators.append(_c(f"⚠ {state.unacked_honks} unacked honk(s)", _GOLD))
    if state.halts_count:
        indicators.append(_c(f"⛔ {state.halts_count} halt note(s)", _RED))
    if state.decisions_count:
        indicators.append(
            _c(f"📋 {state.decisions_count} metabolize decision(s)", _BLUE)
        )
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
                f"  {_c(entry['time'], _DIM)} "
                f"{_c(cat, color_code + _BOLD):<22} "
                f"{entry['message']}"
            )

    lines.append("")
    lines.append(_c(
        f"  Refresh: {DEFAULT_POLL_SECONDS}s  |  Ctrl+C to exit  |  "
        f"{datetime.now().strftime('%H:%M:%S')}",
        _DIM,
    ))
    return "\n".join(lines)


# ── Async loop (the live TUI) ───────────────────────────────────────────────


async def _bridge_get_spiral(bridge_url: str, headers: Dict) -> Optional[Dict]:
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


async def _bridge_get_unread(bridge_url: str, headers: Dict,
                             instance_id: str) -> Optional[int]:
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
    bridge_url: Optional[str] = None,
    bridge_token: Optional[str] = None,
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
    chronicle_index.diff(_list_paths(root / "chronicle" / "insights",
                                     "*.jsonl", recursive=True))
    chronicle_index.diff(_list_paths(root / "chronicle" / "open_threads",
                                     "*.jsonl", recursive=True))
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
                _list_paths(root / "chronicle" / "insights",
                            "*.jsonl", recursive=True),
            ):
                tail = read_chronicle_tail(jsonl)
                if tail:
                    layer = tail.get("layer", "?")
                    content = (tail.get("content") or "")[:80]
                    feed.add(CAT_INSIGHT, f"[{layer}] {content}…")

            for jsonl in chronicle_index.diff(
                _list_paths(root / "chronicle" / "open_threads",
                            "*.jsonl", recursive=True),
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
                recent = read_recent_honks(root / "nape" / "honks.jsonl",
                                           limit=3)
                for h in recent:
                    feed.add(
                        CAT_HONK,
                        f"[{h.get('level','?')}] {h.get('pattern','?')}: "
                        f"{h.get('trigger_tool','?')}",
                    )

            # ── Bridge polling (optional, every 3 cycles for spiral) ──
            if bridge_url and cycle % 3 == 0:
                spiral = await _bridge_get_spiral(bridge_url, bridge_headers)
                if spiral is not None:
                    bs.bridge_reachable = True
                    if "phase" in spiral and spiral["phase"] != bs.phase:
                        feed.add(CAT_TOOLS,
                                 f"phase advanced: {bs.phase} → {spiral['phase']}")
                    if "tool_calls" in spiral and bs.tool_calls > 0 \
                            and spiral["tool_calls"] > bs.tool_calls:
                        delta = spiral["tool_calls"] - bs.tool_calls
                        feed.add(CAT_TOOLS, f"+{delta} tool call(s)")
                    bs.phase = spiral.get("phase", bs.phase)
                    bs.tool_calls = spiral.get("tool_calls", bs.tool_calls)
                    bs.reflection_depth = spiral.get("reflection_depth",
                                                     bs.reflection_depth)
                    bs.duration_seconds = spiral.get("duration_seconds",
                                                     bs.duration_seconds)
                else:
                    bs.bridge_reachable = False

            if bridge_url and cycle % 5 == 0:
                unread = await _bridge_get_unread(
                    bridge_url, bridge_headers, instance_id,
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
