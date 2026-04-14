"""
Handoff Module - Intent for the Next Instance

The chronicle stores what happened. Handoffs store what was about to happen.
Those are different layers. Insights are past-tense; handoffs are future-tense.

Design principles:
- Per-instance, per-thread: a session can leave multiple handoffs for different threads
- Read-once surface, preserved in archive: handoffs appear in where_did_i_leave_off
  exactly once, then flip to consumed. They stay queryable but don't re-surface and pile up.
- Attribution-framed: surfaced as "previous instance (id, time) left this note" — not as
  the new instance's own intent. Epistemic hygiene against injection by compromised/drifted
  sessions.
- Size-bounded: ~2KB per note. Longer than that isn't intent, it's a memoir.

Layout:
    ~/.sovereign/handoffs/
        {iso_ts}_{source_instance}_{thread}.json
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


HANDOFF_MAX_BYTES = 2048  # ~2KB per note


def _slug(s: str, max_len: int = 40) -> str:
    s = re.sub(r'[^\w\-]+', '_', s.strip())
    return s[:max_len].strip('_') or "thread"


class HandoffEngine:
    """Intent-layer memory for instance-to-instance handoff."""

    def __init__(self, root: str):
        self.root = Path(root) / "handoffs"
        self.root.mkdir(parents=True, exist_ok=True)

    def write(self, note: str, source_instance: str, source_session_id: str,
              thread: str = "general") -> Dict:
        """
        Write a handoff note for the next instance.

        Returns the stored record. Raises ValueError if note exceeds size limit.
        """
        note = (note or "").strip()
        if not note:
            raise ValueError("handoff note is empty")
        if len(note.encode("utf-8")) > HANDOFF_MAX_BYTES:
            raise ValueError(
                f"handoff note exceeds {HANDOFF_MAX_BYTES} bytes — record as insight instead"
            )

        ts = datetime.now()
        record = {
            "timestamp": ts.isoformat(),
            "source_instance": source_instance or "unknown",
            "source_session_id": source_session_id or "unknown",
            "thread": thread or "general",
            "note": note,
            "consumed_at": None,
            "consumed_by": None,
        }

        fname = f"{ts.strftime('%Y%m%dT%H%M%S')}_{_slug(source_instance or 'unknown')}_{_slug(thread)}.json"
        path = self.root / fname
        path.write_text(json.dumps(record, indent=2))
        record["_path"] = str(path)
        return record

    def _load_all(self) -> List[Dict]:
        records = []
        for fp in sorted(self.root.glob("*.json")):
            try:
                data = json.loads(fp.read_text())
                data["_path"] = str(fp)
                records.append(data)
            except (json.JSONDecodeError, IOError):
                continue
        return records

    def unconsumed(self, thread: Optional[str] = None, limit: int = 20) -> List[Dict]:
        """Return handoffs that have not yet been surfaced to a reader."""
        records = [r for r in self._load_all() if not r.get("consumed_at")]
        if thread:
            records = [r for r in records if r.get("thread") == thread]
        records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
        return records[:limit]

    def mark_consumed(self, paths: List[str], consumed_by: str) -> int:
        """Flip consumed_at on the given handoff files. Returns count marked."""
        count = 0
        ts = datetime.now().isoformat()
        for p in paths:
            fp = Path(p)
            if not fp.exists():
                continue
            try:
                data = json.loads(fp.read_text())
                if data.get("consumed_at"):
                    continue
                data["consumed_at"] = ts
                data["consumed_by"] = consumed_by or "unknown"
                fp.write_text(json.dumps(data, indent=2))
                count += 1
            except (json.JSONDecodeError, IOError):
                continue
        return count

    def all(self, include_consumed: bool = True, thread: Optional[str] = None,
            limit: int = 50) -> List[Dict]:
        """All handoffs (for archaeology), newest first."""
        records = self._load_all()
        if not include_consumed:
            records = [r for r in records if not r.get("consumed_at")]
        if thread:
            records = [r for r in records if r.get("thread") == thread]
        records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
        return records[:limit]


def format_handoff_for_surface(record: Dict) -> str:
    """
    Attribution-framed rendering. This is the epistemic-hygiene move:
    the new instance reads this as someone else's claim, not as its own intent.
    """
    src = record.get("source_instance", "unknown")
    sid = record.get("source_session_id", "unknown")
    ts = record.get("timestamp", "unknown")
    thread = record.get("thread", "general")
    note = record.get("note", "")
    return (
        f"• [thread: {thread}] Previous instance {src} (session {sid}, {ts}) left this note:\n"
        f"    \"{note}\""
    )
