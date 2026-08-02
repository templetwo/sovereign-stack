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
import os
import re
from datetime import datetime
from pathlib import Path

HANDOFF_MAX_BYTES = 2048  # ~2KB per note

# Values that name no one. A handoff consumed_by (or acted_on consumed_by) of
# one of these is functionally the same as leaving the field blank — it just
# LOOKS filled in. Verified against the live store 2026-08-01: 159/251 (63%)
# of handoffs carried "unknown", 78/251 (31%) carried "test" (all traced to a
# test suite that was writing through to the live ~/.sovereign store — see
# _refuse_live_store_during_tests below), leaving only 14/251 naming an actual
# reader. A gate that accepts these silently is the fail-open this module
# exists to close (house rule: a surface must be incapable of reporting
# success on a meaningless operation).
NON_IDENTIFYING_CONSUMERS = frozenset(
    {
        "unknown",
        "test",
        "none",
        "null",
        "n/a",
        "na",
        "anonymous",
        "todo",
        "tbd",
        "placeholder",
        "xxx",
        "unset",
        "default",
    }
)


def _validate_reader_identity(consumed_by: str, *, field: str = "consumed_by") -> str:
    """Reject empty / placeholder reader identities. Returns the stripped value.

    Raises ValueError rather than silently substituting a default — a
    consumption record with a meaningless consumer is worse than no record,
    because it LOOKS like an audit trail while carrying no information a
    future reader can act on.
    """
    cleaned = (consumed_by or "").strip()
    if not cleaned:
        raise ValueError(
            f"{field} is required — refusing to record a handoff as consumed by "
            "an empty/unnamed reader. Pass the actual source_instance."
        )
    if cleaned.lower() in NON_IDENTIFYING_CONSUMERS:
        raise ValueError(
            f"{field}={cleaned!r} does not identify a reader — refusing to mark "
            "consumed. This placeholder previously let consumption look "
            "successful while erasing WHO consumed it; pass the real "
            "source_instance instead."
        )
    return cleaned


def _refuse_live_store_during_tests(root: Path) -> None:
    """Defense in depth: a pytest run must never be able to mutate the real
    ~/.sovereign store, no matter which module-level singleton it inherited.

    ``PYTEST_CURRENT_TEST`` is set by pytest for the duration of every test's
    setup/call/teardown phase (pytest docs, not our convention). Checking it
    here — at the moment of the actual write — catches the case a test's
    fixture set SOVEREIGN_ROOT or patched DEFAULT_ROOT *after* a module-level
    HandoffEngine singleton had already been constructed from the real root
    at import time (server.py:129 does exactly this). Verified on the live
    store 2026-08-01: this was not hypothetical — two separate test fixtures
    (tests/test_resume_in_context.py, tests/test_nape_autohook.py
    ``_isolated_server``) were doing exactly this and had respectively
    consumed 249/251 real handoffs and written 51 "healthy probe" records
    into ~/.sovereign/handoffs/ before this guard existed.
    """
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        return
    try:
        resolved = root.resolve()
    except OSError:
        resolved = root
    real_root = (Path.home() / ".sovereign" / "handoffs").resolve()
    if resolved == real_root or real_root in resolved.parents or resolved in real_root.parents:
        raise RuntimeError(
            f"Refusing to write to the live Sovereign Stack handoff store "
            f"({real_root}) from inside a pytest run (root resolved to "
            f"{resolved}). This HandoffEngine was almost certainly a stale "
            "module-level singleton bound before a test fixture's "
            "SOVEREIGN_ROOT/DEFAULT_ROOT override took effect — patch "
            "`server.handoff_engine` (or equivalent) directly with a "
            "HandoffEngine rooted at a tmp_path, not just the env var."
        )


def _slug(s: str, max_len: int = 40) -> str:
    s = re.sub(r"[^\w\-]+", "_", s.strip())
    return s[:max_len].strip("_") or "thread"


class HandoffEngine:
    """Intent-layer memory for instance-to-instance handoff."""

    def __init__(self, root: str):
        self.root = Path(root) / "handoffs"
        self.root.mkdir(parents=True, exist_ok=True)

    def write(
        self, note: str, source_instance: str, source_session_id: str, thread: str = "general"
    ) -> dict:
        """
        Write a handoff note for the next instance.

        Returns the stored record. Raises ValueError if note exceeds size limit.
        """
        _refuse_live_store_during_tests(self.root)
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

    def _load_all(self) -> list[dict]:
        records = []
        for fp in sorted(self.root.glob("*.json")):
            try:
                data = json.loads(fp.read_text())
                data["_path"] = str(fp)
                records.append(data)
            except (OSError, json.JSONDecodeError):
                continue
        return records

    def unconsumed(self, thread: str | None = None, limit: int = 20) -> list[dict]:
        """Return handoffs that have not yet been surfaced to a reader."""
        records = [r for r in self._load_all() if not r.get("consumed_at")]
        if thread:
            records = [r for r in records if r.get("thread") == thread]
        records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
        return records[:limit]

    def unconsumed_count(self, thread: str | None = None) -> int:
        """Total count of not-yet-consumed handoffs, uncapped by unconsumed()'s
        limit=20. Added alongside consumed_count() (2026-08-01) after the
        consumed_by fix changed who can actually retire a handoff: callers
        that don't pass source_instance (the documented
        where_did_i_leave_off boot call has none) now leave handoffs
        pending forever instead of consuming them under "unknown". That's
        correct — better pending than falsely erased — but it means the
        pending queue can now grow past unconsumed()'s limit=20 in a way it
        rarely did before (when "unknown" drained it every boot). Once that
        happens, unconsumed(limit=20) truncates and the OLDEST pending
        handoffs go missing from the boot text with no signal — the same
        absence-vs-emptiness failure this fix closes, in a new spot. The
        boot surface uses this to say 'showing 20 of N' instead of silently
        dropping the rest."""
        records = [r for r in self._load_all() if not r.get("consumed_at")]
        if thread:
            records = [r for r in records if r.get("thread") == thread]
        return len(records)

    def consumed_count(self, thread: str | None = None) -> int:
        """Count of handoffs that HAVE been consumed — the complement of
        unconsumed(). Lets a caller (the boot surface) say "N consumed
        handoffs exist, not shown" instead of leaving an empty unconsumed()
        list indistinguishable from "no handoffs were ever written" — consumed
        records are not returned here, only their count, so this stays cheap
        to call on every boot."""
        records = [r for r in self._load_all() if r.get("consumed_at")]
        if thread:
            records = [r for r in records if r.get("thread") == thread]
        return len(records)

    def mark_consumed(self, paths: list[str], consumed_by: str) -> int:
        """Flip consumed_at on the given handoff files. Returns count marked.

        Raises ValueError if consumed_by is empty or a non-identifying
        placeholder (see NON_IDENTIFYING_CONSUMERS) — validated once, up
        front, for the whole batch: a caller that can't name itself gets a
        loud refusal, not a silent 'unknown' stamp that permanently erases
        the handoff from every future boot while recording nothing useful
        about who erased it.
        """
        _refuse_live_store_during_tests(self.root)
        consumed_by = _validate_reader_identity(consumed_by)
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
                data["consumed_by"] = consumed_by
                fp.write_text(json.dumps(data, indent=2))
                count += 1
            except (OSError, json.JSONDecodeError):
                continue
        return count

    def all(
        self, include_consumed: bool = True, thread: str | None = None, limit: int = 50
    ) -> list[dict]:
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
    ) -> dict:
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
            ValueError: If handoff_path or what_was_done is empty, or if
                consumed_by is empty / a non-identifying placeholder (see
                NON_IDENTIFYING_CONSUMERS on mark_consumed — the acted_on
                log is an audit trail same as mark_consumed's consumed_by,
                and is held to the same standard).
        """
        _refuse_live_store_during_tests(self.root)
        if not handoff_path or not str(handoff_path).strip():
            raise ValueError("handoff_path is required")
        consumed_by = _validate_reader_identity(consumed_by)
        if not what_was_done or not what_was_done.strip():
            raise ValueError("what_was_done is required")

        record: dict = {
            "handoff_path": str(handoff_path).strip(),
            "consumed_by": consumed_by,
            "what_was_done": what_was_done.strip(),
            "timestamp": datetime.now().isoformat(),
        }

        acted_on_log = self.root / "acted_on.jsonl"
        with open(acted_on_log, "a") as fh:
            fh.write(json.dumps(record) + "\n")

        return record

    def acted_on_records(self, handoff_path: str | None = None) -> list[dict]:
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

        records: list[dict] = []
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


def format_handoff_for_surface(record: dict) -> str:
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
        f'    "{note}"'
    )
