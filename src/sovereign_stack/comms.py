"""
Comms Module — Inter-instance communication channel reader.

Any Claude instance (Code, Desktop, iPhone-web, remote) writes and reads
the same JSONL channel files under ~/.sovereign/comms/. This module is the
READ surface. Writes go through the bridge REST or the comms_send tool —
the data file is the single source of truth; multiple readers are safe.

Fixes the silent partial-success bug that opus-4-7-web flagged from the
iPhone-app side of the door (April 19):
  • limit cap was 200 with no offset → caller could only see the newest
    200 messages in the channel, ever.
  • Pagination params (order, reverse, offset) were silently ignored.
  • unread endpoint returned counts but not bodies.

Inhabitant syntax over archaeologist syntax: prefer `since_iso` and
`unread_for` over row-slice semantics.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Literal, Optional


COMMS_DIR = Path.home() / ".sovereign" / "comms"
DEFAULT_CHANNEL = "general"
MAX_LIMIT = 2000  # safety cap; callers asking for more should paginate


# ── Path helpers ──

def _channel_path(channel: str) -> Path:
    """Sanitize channel name to safe filename. Matches bridge.py convention."""
    safe = "".join(c for c in channel if c.isalnum() or c in "-_")
    return COMMS_DIR / f"{safe}.jsonl"


def _parse_timestamp(value) -> Optional[float]:
    """Accept epoch float, epoch int, ISO8601 string, or None. Return epoch float."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            pass
        try:
            # Support trailing 'Z' and offset forms.
            s = value.replace("Z", "+00:00")
            return datetime.fromisoformat(s).timestamp()
        except ValueError:
            return None
    return None


# ── Core read ──

def _load_all(channel: str) -> List[Dict]:
    """Read the full channel file (chronological order preserved)."""
    path = _channel_path(channel)
    if not path.exists():
        return []
    messages: List[Dict] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            messages.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return messages


def read_channel(
    channel: str = DEFAULT_CHANNEL,
    since: Optional[object] = None,
    until: Optional[object] = None,
    order: Literal["asc", "desc"] = "desc",
    limit: int = 50,
    offset: int = 0,
    unread_for: Optional[str] = None,
) -> List[Dict]:
    """
    Read messages from a channel with real pagination.

    Args:
        channel: Channel name (default 'general').
        since: Lower time bound (exclusive). Accepts epoch number or ISO8601.
        until: Upper time bound (exclusive). Accepts epoch number or ISO8601.
        order: "desc" (newest first, default) or "asc" (oldest first).
        limit: Max messages to return. Capped at MAX_LIMIT.
        offset: Skip this many results after filtering/ordering.
        unread_for: If set, return only messages where instance_id is NOT in
                    the read_by array — "what my siblings said that I haven't
                    acknowledged yet."

    Returns:
        List of message dicts in the requested order.
    """
    limit = max(0, min(int(limit), MAX_LIMIT))
    offset = max(0, int(offset))

    since_ts = _parse_timestamp(since)
    until_ts = _parse_timestamp(until)

    messages = _load_all(channel)

    # Apply filters.
    filtered: List[Dict] = []
    for msg in messages:
        ts = msg.get("timestamp", 0)
        if since_ts is not None and ts <= since_ts:
            continue
        if until_ts is not None and ts >= until_ts:
            continue
        if unread_for is not None and unread_for in (msg.get("read_by") or []):
            continue
        filtered.append(msg)

    # Order.
    filtered.sort(key=lambda m: m.get("timestamp", 0),
                  reverse=(order == "desc"))

    # Offset + limit.
    return filtered[offset: offset + limit]


def count_unread(channel: str, instance_id: str) -> int:
    """Count messages in a channel that instance_id has not read_by-tagged."""
    count = 0
    for msg in _load_all(channel):
        if instance_id not in (msg.get("read_by") or []):
            count += 1
    return count


def unread_messages(
    instance_id: str,
    channel: str = DEFAULT_CHANNEL,
    limit: int = 50,
    order: Literal["asc", "desc"] = "asc",
) -> List[Dict]:
    """
    Return actual message bodies that instance_id has not yet acknowledged.

    Default order is "asc" (oldest first) — caller typically wants to catch
    up in the order things were said, not the reverse.
    """
    return read_channel(
        channel=channel,
        unread_for=instance_id,
        order=order,
        limit=limit,
    )


def list_channels() -> List[Dict]:
    """Return metadata for every channel currently on disk."""
    if not COMMS_DIR.exists():
        return []
    channels = []
    for path in sorted(COMMS_DIR.glob("*.jsonl")):
        messages = _load_all(path.stem)
        latest = messages[-1].get("iso") if messages else None
        channels.append({
            "name": path.stem,
            "messages": len(messages),
            "latest": latest,
        })
    return channels
