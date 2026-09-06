"""Signal ledger — watch-seat instrument (mesh-20260905).

Append-only JSONL at <sovereign root>/signals/ledger.jsonl.
Latest row per signal_id wins. Never deletes. Fail-closed: an unreadable
ledger is an ERROR on the heartbeat field, never a zero count.

Do not enable ntfy from this module; SIGNAL_LEDGER_NTFY defaults off.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from mcp.types import Tool

from .provenance import default_sovereign_root

STATES = ("open", "acknowledged", "acted", "dismissed")
CLOSE_STATES = ("acknowledged", "acted", "dismissed")
SOURCES = (
    "honk",
    "watchman",
    "proposal",
    "halt",
    "decision",
    "guardian",
    "thread",
)

# Producer identity per source. closed_by must not equal this.
SOURCE_PRODUCER = {
    "honk": "nape",
    "watchman": "watchman",
    "proposal": "bridge",
    "halt": "daemon",
    "decision": "metabolize",
    "guardian": "guardian",
    "thread": "chronicle",
}

NTFY_ENV = "SIGNAL_LEDGER_NTFY"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _root(root: Path | None = None) -> Path:
    return Path(root) if root is not None else default_sovereign_root()


def ledger_path(root: Path | None = None) -> Path:
    return _root(root) / "signals" / "ledger.jsonl"


def signal_id_for(source: str, native_id: str) -> str:
    raw = f"{source}:{native_id}".encode()
    return hashlib.sha256(raw).hexdigest()


def _parse_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        raise
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            rows.append(rec)
    return rows


def load_latest(root: Path | None = None) -> dict[str, dict]:
    """signal_id -> latest row."""
    latest: dict[str, dict] = {}
    for rec in _parse_jsonl(ledger_path(root)):
        sid = rec.get("signal_id")
        if sid:
            latest[str(sid)] = rec
    return latest


def _append(row: dict, root: Path | None = None) -> dict:
    path = ledger_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(row, sort_keys=True) + "\n"
    with path.open("a+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    return row


def _row(
    *,
    signal_id: str,
    source: str,
    produced_at: str,
    owner: str,
    state: str,
    reason: str | None,
    closed_by: str | None,
    closed_at: str | None,
    updated_at: str,
) -> dict:
    return {
        "signal_id": signal_id,
        "source": source,
        "produced_at": produced_at,
        "owner": owner,
        "state": state,
        "reason": reason,
        "closed_by": closed_by,
        "closed_at": closed_at,
        "updated_at": updated_at,
    }


def open_signal(
    *,
    source: str,
    native_id: str,
    produced_at: str,
    owner: str = "watch-2/3",
    root: Path | None = None,
) -> dict | None:
    """Idempotent open: skip if latest row already exists for this id."""
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}")
    sid = signal_id_for(source, native_id)
    latest = load_latest(root)
    if sid in latest:
        return None
    now = _now()
    return _append(
        _row(
            signal_id=sid,
            source=source,
            produced_at=produced_at or now,
            owner=owner,
            state="open",
            reason=None,
            closed_by=None,
            closed_at=None,
            updated_at=now,
        ),
        root,
    )


def ack_signal(
    signal_id: str,
    owner: str,
    state: str,
    reason: str | None,
    root: Path | None = None,
) -> dict:
    """Close or acknowledge. Refuses producer self-close and closings without reason."""
    if state not in CLOSE_STATES:
        raise ValueError(f"state must be one of {CLOSE_STATES}, got {state!r}")
    if state in ("acted", "dismissed") and not (reason and str(reason).strip()):
        raise ValueError(f"{state} requires a reason")
    if state == "acknowledged" and not (reason and str(reason).strip()):
        reason = "acknowledged"
    latest = load_latest(root)
    prev = latest.get(signal_id)
    if not prev:
        raise KeyError(f"unknown signal_id {signal_id}")
    producer = SOURCE_PRODUCER.get(prev.get("source", ""), "")
    if owner and producer and owner == producer:
        raise PermissionError("producer cannot close its own signal")
    now = _now()
    return _append(
        _row(
            signal_id=signal_id,
            source=prev["source"],
            produced_at=prev.get("produced_at") or now,
            owner=owner,
            state=state,
            reason=str(reason).strip(),
            closed_by=owner,
            closed_at=now,
            updated_at=now,
        ),
        root,
    )


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        s = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def summarize(root: Path | None = None, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    latest = load_latest(root)
    by_source: dict[str, dict[str, Any]] = {
        s: {"open": 0, "stale_24h": 0, "stale_7d": 0, "oldest_unacked": None} for s in SOURCES
    }
    total = stale_24h = stale_7d = 0
    for rec in latest.values():
        if rec.get("state") != "open":
            continue
        src = rec.get("source")
        if src not in by_source:
            by_source[src] = {"open": 0, "stale_24h": 0, "stale_7d": 0, "oldest_unacked": None}
        total += 1
        by_source[src]["open"] += 1
        dt = _parse_dt(rec.get("produced_at"))
        if dt:
            age = now - dt
            oldest = by_source[src]["oldest_unacked"]
            iso = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            if oldest is None or iso < oldest:
                by_source[src]["oldest_unacked"] = iso
            if age >= timedelta(days=7):
                stale_7d += 1
                by_source[src]["stale_7d"] += 1
            if age >= timedelta(days=1):
                stale_24h += 1
                by_source[src]["stale_24h"] += 1
    return {
        "total": total,
        "stale_24h": stale_24h,
        "stale_7d": stale_7d,
        "by_source": by_source,
    }


def heartbeat_field(root: Path | None = None) -> dict:
    """Heartbeat/dashboard payload. Unreadable ledger is an error, never zero."""
    path = ledger_path(root)
    try:
        if path.exists():
            path.read_text(encoding="utf-8")
        summary = summarize(root)
        return {
            "error": None,
            "total": summary["total"],
            "stale_24h": summary["stale_24h"],
            "stale_7d": summary["stale_7d"],
            "by_source": {k: v["open"] for k, v in summary["by_source"].items()},
        }
    except OSError as exc:
        return {
            "error": f"unreadable:{exc.__class__.__name__}",
            "total": None,
            "stale_24h": None,
            "stale_7d": None,
            "by_source": None,
        }


# ── adapters (read existing surfaces; do not mutate them) ───────────────────


def _iso_from_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def scan_honks(root: Path, owner: str = "watch-2/3") -> int:
    honks = root / "nape" / "honks.jsonl"
    acks = root / "nape" / "acks.jsonl"
    ack_ids: set[str] = set()
    for rec in _parse_jsonl(acks):
        hid = rec.get("honk_id")
        if hid:
            ack_ids.add(str(hid))
    n = 0
    for rec in _parse_jsonl(honks):
        hid = rec.get("honk_id")
        if not hid:
            continue
        produced = rec.get("timestamp") or _now()
        opened = open_signal(
            source="honk", native_id=str(hid), produced_at=str(produced), owner=owner, root=root
        )
        if opened:
            n += 1
        if hid in ack_ids:
            sid = signal_id_for("honk", str(hid))
            latest = load_latest(root).get(sid)
            if latest and latest.get("state") == "open":
                with contextlib.suppress(PermissionError):
                    ack_signal(
                        sid,
                        owner="nape-ack",
                        state="acknowledged",
                        reason="nape acks.jsonl",
                        root=root,
                    )
    return n


def scan_watchman(root: Path, owner: str = "watch-2/3") -> int:
    spool = root / "watchman" / "spool.jsonl"
    n = 0
    for rec in _parse_jsonl(spool):
        sid = rec.get("sweep_id")
        if not sid:
            continue
        produced = rec.get("started_at") or rec.get("spooled_at") or _now()
        if open_signal(
            source="watchman", native_id=str(sid), produced_at=str(produced), owner=owner, root=root
        ):
            n += 1
    return n


def scan_proposals(root: Path, owner: str = "watch-2/3") -> int:
    n = 0
    for substrate in ("grok_bridge", "openai_bridge", "antigravity_connector"):
        d = root / substrate / "pending_writes"
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.json")):
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            native = rec.get("proposal_id") or f.name
            produced = rec.get("timestamp") or _iso_from_mtime(f)
            status = rec.get("status") or "pending"
            opened = open_signal(
                source="proposal",
                native_id=f"{substrate}:{native}",
                produced_at=str(produced),
                owner=owner,
                root=root,
            )
            if opened:
                n += 1
            sid = signal_id_for("proposal", f"{substrate}:{native}")
            latest = load_latest(root).get(sid)
            if not latest or latest.get("state") != "open":
                continue
            if status == "committed":
                ack_signal(
                    sid, owner="drain", state="acted", reason=f"{substrate} committed", root=root
                )
            elif status == "rejected":
                ack_signal(
                    sid, owner="drain", state="dismissed", reason=f"{substrate} rejected", root=root
                )
    return n


def scan_halts(root: Path, owner: str = "watch-2/3") -> int:
    n = 0
    d = root / "daemons" / "halts"
    if not d.is_dir():
        return 0
    for f in sorted(d.glob("*.md")):
        if open_signal(
            source="halt", native_id=f.name, produced_at=_iso_from_mtime(f), owner=owner, root=root
        ):
            n += 1
    return n


def scan_decisions(root: Path, owner: str = "watch-2/3") -> int:
    n = 0
    d = root / "decisions"
    if not d.is_dir():
        return 0
    for f in sorted(d.glob("metabolize_*.md")):
        if open_signal(
            source="decision",
            native_id=f.name,
            produced_at=_iso_from_mtime(f),
            owner=owner,
            root=root,
        ):
            n += 1
    return n


def scan_guardian(root: Path, owner: str = "watch-2/3") -> int:
    """Reads root/guardian/issues.json (list of strings) if present. No live HTTP."""
    path = root / "guardian" / "issues.json"
    if not path.exists():
        return 0
    try:
        issues = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(issues, list):
        return 0
    n = 0
    for issue in issues:
        native = str(issue)
        if open_signal(
            source="guardian", native_id=native, produced_at=_now(), owner=owner, root=root
        ):
            n += 1
    return n


def scan_threads(root: Path, owner: str = "watch-2/3") -> int:
    d = root / "chronicle" / "open_threads"
    if not d.is_dir():
        return 0
    n = 0
    for f in sorted(d.glob("*.jsonl")):
        recs = _parse_jsonl(f)
        if not recs:
            continue
        last = recs[-1]
        native = last.get("thread_id") or f.stem
        produced = last.get("timestamp") or _iso_from_mtime(f)
        opened = open_signal(
            source="thread",
            native_id=str(native),
            produced_at=str(produced),
            owner=owner,
            root=root,
        )
        if opened:
            n += 1
        if last.get("resolved") is True or last.get("status") == "resolved":
            sid = signal_id_for("thread", str(native))
            latest = load_latest(root).get(sid)
            if latest and latest.get("state") == "open":
                ack_signal(
                    sid,
                    owner="watch-2/3",
                    state="acted",
                    reason="thread resolved",
                    root=root,
                )
    return n


def scan_all(root: Path | None = None, owner: str = "watch-2/3") -> dict[str, int]:
    r = _root(root)
    return {
        "honk": scan_honks(r, owner),
        "watchman": scan_watchman(r, owner),
        "proposal": scan_proposals(r, owner),
        "halt": scan_halts(r, owner),
        "decision": scan_decisions(r, owner),
        "guardian": scan_guardian(r, owner),
        "thread": scan_threads(r, owner),
    }


# ── escalation (text only; send defaults OFF) ───────────────────────────────

TRIGGERS = (
    "new_halt",
    "arrival_left_quiet",
    "hq_reviewed_proposal_needs_tap",
    "tried_close_still_open_7d",
)


def render_escalation(trigger: str, detail: str) -> str:
    if trigger not in TRIGGERS:
        raise ValueError(f"unknown trigger {trigger!r}")
    titles = {
        "new_halt": "NEW HALT",
        "arrival_left_quiet": "ARRIVAL GATE LEFT QUIET",
        "hq_reviewed_proposal_needs_tap": "PROPOSAL NEEDS YOUR TAP",
        "tried_close_still_open_7d": "STILL OPEN AFTER A WEEK",
    }
    return f"WATCH 2/3 — {titles[trigger]}\n{detail.strip()}\nOne line. Not a queue."


def ntfy_enabled() -> bool:
    v = os.environ.get(NTFY_ENV, "").strip().lower()
    return v in ("1", "true", "on", "yes")


def send_escalation(trigger: str, detail: str, *, publish=None) -> dict:
    """Render the phone line. Sends only if SIGNAL_LEDGER_NTFY is on AND publish given."""
    text = render_escalation(trigger, detail)
    sent = False
    if ntfy_enabled() and publish is not None:
        publish(text)
        sent = True
    return {"text": text, "sent": sent, "enabled": ntfy_enabled()}


# ── Stack tools ─────────────────────────────────────────────────────────────

SIGNAL_TOOLS = [
    Tool(
        name="signals_summary",
        description=(
            "Read-only watch-seat signal ledger summary: unacked total, "
            "stale_24h, stale_7d, and per-source open counts. An unreadable "
            "ledger returns error, never a zero."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="signal_ack",
        description=(
            "Close or acknowledge one signal. state is acknowledged|acted|dismissed. "
            "acted and dismissed require a reason. The producer of a source cannot "
            "close its own signal."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "signal_id": {"type": "string"},
                "owner": {"type": "string", "description": "Who is closing (watch seat id)."},
                "state": {"type": "string", "enum": list(CLOSE_STATES)},
                "reason": {"type": "string"},
            },
            "required": ["signal_id", "owner", "state"],
        },
    ),
]

SIGNAL_TOOL_TIERS = {"signals_summary": "core", "signal_ack": "core"}
SIGNAL_TOOL_INTENTS = {"signals_summary": "read", "signal_ack": "govern"}


def handle_signal_tool(name: str, arguments: dict | None, root: Path | None = None) -> str:
    arguments = arguments or {}
    try:
        if name == "signals_summary":
            field = heartbeat_field(root)
            if field.get("error"):
                return json.dumps({"ok": False, "error": field["error"]})
            return json.dumps({"ok": True, **summarize(root)})
        if name == "signal_ack":
            sid = str(arguments.get("signal_id") or "").strip()
            owner = str(arguments.get("owner") or "").strip()
            state = str(arguments.get("state") or "").strip()
            reason = arguments.get("reason")
            if not sid or not owner or not state:
                return json.dumps(
                    {"ok": False, "error": "signal_id, owner, and state are required"}
                )
            row = ack_signal(sid, owner, state, reason, root=root)
            return json.dumps({"ok": True, "row": row})
        return json.dumps({"ok": False, "error": f"unknown tool {name}"})
    except (ValueError, KeyError, PermissionError) as exc:
        return json.dumps({"ok": False, "error": str(exc)})
