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

Acknowledgment split: comms_acknowledge split proposed by opus-4-7-web, 2026-04-20.
A glance is not the same as integration. Reading a message (populating read_by via
the bridge) and acknowledging it (writing an ack record here) are now separate acts.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Literal, Optional


COMMS_DIR = Path.home() / ".sovereign" / "comms"
DEFAULT_CHANNEL = "general"
MAX_LIMIT = 2000  # safety cap; callers asking for more should paginate
ACKS_FILE = "acks.jsonl"  # relative to COMMS_DIR


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
    mark_seen: bool = True,
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
        mark_seen: When False, do NOT write read_by on returned messages.
                   The read_by field is written by the bridge REST layer, not
                   by this module directly — this flag is a forward-compatibility
                   guard that prevents future bridge integrations from
                   auto-marking messages as seen when the caller only wants to
                   browse. Default True preserves current behavior.

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
    page = filtered[offset: offset + limit]

    # Tag each returned message with whether auto-marking-seen is suppressed.
    # The bridge layer reads this field before deciding to mutate read_by.
    # When mark_seen=False the message dicts get a _mark_seen=False sentinel
    # so any downstream bridge integration knows not to write read_by.
    if not mark_seen:
        page = [{**m, "_mark_seen": False} for m in page]

    return page


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
        if path.name == ACKS_FILE:
            continue
        messages = _load_all(path.stem)
        latest = messages[-1].get("iso") if messages else None
        channels.append({
            "name": path.stem,
            "messages": len(messages),
            "latest": latest,
        })
    return channels


# ── Acknowledgment layer ──

def acknowledge(
    message_id: str,
    instance_id: str,
    note: str = "",
    channel: str = DEFAULT_CHANNEL,
) -> Dict:
    """
    Record that an instance has integrated a message — distinct from read_by.

    A glance (read_by, written by the bridge) is not the same as integration.
    This call records the deliberate act of engaging with a message and optionally
    noting what was taken from it. Records are append-only JSONL; the original
    message is never mutated.

    Args:
        message_id: The id field of the message being acknowledged.
        instance_id: The acknowledging instance (e.g. "claude-code-macbook").
        note: Optional note on what was integrated or acted on.
        channel: Channel the message lives in (default "general").

    Returns:
        The written acknowledgment record.
    """
    if not message_id or not message_id.strip():
        raise ValueError("message_id is required")
    if not instance_id or not instance_id.strip():
        raise ValueError("instance_id is required")

    record: Dict = {
        "message_id": message_id.strip(),
        "instance_id": instance_id.strip(),
        "note": (note or "").strip(),
        "channel": channel or DEFAULT_CHANNEL,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    COMMS_DIR.mkdir(parents=True, exist_ok=True)
    acks_path = COMMS_DIR / ACKS_FILE
    with open(acks_path, "a") as fh:
        fh.write(json.dumps(record) + "\n")

    return record


def get_acknowledgments(
    message_id: Optional[str] = None,
    instance_id: Optional[str] = None,
) -> List[Dict]:
    """
    Query the acknowledgments log.

    Args:
        message_id: Filter to acks for this message (None = all).
        instance_id: Filter to acks from this instance (None = all).

    Returns:
        List of ack records, newest first.
    """
    acks_path = COMMS_DIR / ACKS_FILE
    if not acks_path.exists():
        return []

    acks: List[Dict] = []
    for line in acks_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            ack = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message_id is not None and ack.get("message_id") != message_id:
            continue
        if instance_id is not None and ack.get("instance_id") != instance_id:
            continue
        acks.append(ack)

    acks.sort(key=lambda a: a.get("timestamp", ""), reverse=True)
    return acks
