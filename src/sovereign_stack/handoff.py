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

        # Microsecond precision + short content hash: prevents filename
        # collisions when multiple handoffs are written from the same
        # instance/thread within the same second (which used to silently
        # overwrite the earlier handoff — losing intent).
        import hashlib
        note_hash = hashlib.sha1(note.encode("utf-8")).hexdigest()[:6]
        fname = (
            f"{ts.strftime('%Y%m%dT%H%M%S_%f')}"
            f"_{_slug(source_instance or 'unknown')}"
            f"_{_slug(thread)}"
            f"_{note_hash}.json"
        )
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


    def mark_acted_on(
        self,
        handoff_path: str,
        consumed_by: str,
        what_was_done: str,
    ) -> Dict:
        """
        Record what the reader actually did with a handoff.

        This closes the writer->reader feedback loop: the reader tells the next
        reader what they actually did, not just that they read the handoff.
        Distinct from mark_consumed (which is the binary read-once marker).
        Records are append-only; neither the original handoff nor the consumed
        marker is mutated.

        Args:
            handoff_path: Path to the handoff JSON file being acted on.
            consumed_by: Instance that acted on the handoff.
            what_was_done: Description of the action taken.

        Returns:
            The written acted_on record.

        Raises:
            ValueError: If handoff_path, consumed_by, or what_was_done is empty.
        """
        if not handoff_path or not str(handoff_path).strip():
            raise ValueError("handoff_path is required")
        if not consumed_by or not consumed_by.strip():
            raise ValueError("consumed_by is required")
        if not what_was_done or not what_was_done.strip():
            raise ValueError("what_was_done is required")

        record: Dict = {
            "handoff_path": str(handoff_path).strip(),
            "consumed_by": consumed_by.strip(),
            "what_was_done": what_was_done.strip(),
            "timestamp": datetime.now().isoformat(),
        }

        acted_on_log = self.root / "acted_on.jsonl"
        with open(acted_on_log, "a") as fh:
            fh.write(json.dumps(record) + "\n")

        return record

    def acted_on_records(self, handoff_path: Optional[str] = None) -> List[Dict]:
        """
        Query the acted_on log.

        Args:
            handoff_path: Filter to records for this handoff path (None = all).

        Returns:
            List of acted_on records, newest first.
        """
        acted_on_log = self.root / "acted_on.jsonl"
        if not acted_on_log.exists():
            return []

        records: List[Dict] = []
        for line in acted_on_log.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if handoff_path is not None and rec.get("handoff_path") != str(handoff_path):
                continue
            records.append(rec)

        records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
        return records


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
