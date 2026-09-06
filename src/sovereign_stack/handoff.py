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


def _validate_author_identity(source_instance: str) -> str:
    """Reject empty / placeholder AUTHOR identities on the write path.

    The mirror of ``_validate_reader_identity``, one side of the record over.
    The reader half has refused a placeholder since 2026-08-01; the writer half
    kept substituting the literal string "unknown" (handoff.py write(), and
    server.py's ``arguments.get("source_instance", "unknown")`` default), so an
    anonymous handoff looked exactly like a signed one on every surface that
    renders it.

    Measured on the live store 2026-09-05 before this guard landed: 78 of 320
    handoffs (24%) carry source_instance "unknown", 4 of them inside the newest
    25 — the gap a gpt-6-astra (Codex) audit surfaced the same night. The
    ownerless records are NOT evenly spread: 2026-04 16, 05 15, 06 27, 07 6,
    08 13, 09 1. The trend is the argument for closing it now — the habit is
    nearly gone, so the guard costs almost nothing and stops the last of it.

    WHY A HARD REFUSAL IS SAFE HERE, checked rather than assumed: every
    automated writer already names itself. The only two call sites that reach
    HandoffEngine.write are server.py's ``handoff`` and ``close_session``
    dispatches, both driven by a seat that can name itself; the Ring-2 bridge
    drain fills source_instance from the proposal envelope
    (clients/bridge_core/dispatch.py:35 — ``args.pop("source_instance", None)
    or substrate``, so a substrate name is always present, and
    pending_writes.py:86 rejects an empty one at proposal time); and
    ~/sovereign-bridge writes no handoffs at all (its watchman only COUNTS
    them, watchman_sweep.py:346). Nothing automated depends on "unknown".

    Raises ValueError rather than defaulting: the note travels back to the
    caller inside the error, so a refused handoff is refused loudly and can be
    rewritten with a name — the opposite of the anonymous record, which
    succeeds and then cannot tell any future reader whose claim it is.
    """
    cleaned = (source_instance or "").strip()
    if not cleaned:
        raise ValueError(
            "source_instance is required — refusing to write a handoff nobody "
            "signed. A handoff is a CLAIM from one seat to the next, and an "
            "unattributed claim cannot be weighed. Name the seat that is "
            "leaving this note."
        )
    if cleaned.lower() in NON_IDENTIFYING_CONSUMERS:
        raise ValueError(
            f"source_instance={cleaned!r} does not identify an author — refusing "
            "to write the handoff. This placeholder previously made an "
            "anonymous handoff indistinguishable from a signed one on every "
            "surface that renders it (78 of 320 live records, 2026-09-05); "
            "pass the real seat name instead."
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


def _build_forward_index(records: list[dict]) -> dict[str, list[dict]]:
    """Invert the ``supersedes`` back-pointers into a forward index.

    THE GAP THIS CLOSES (gpt-6-astra / Codex audit, 2026-09-05, confirmed from
    disk): a handoff record had seven fields and no correction linkage at all,
    so an older handoff that a later one had already corrected still read as
    current on every surface. The live specimen: ``20260902T112749_*`` says
    "hq_module_audit exit 0"; ``20260902T112841_*``, 52 seconds later, corrects
    it to exit 1. Nothing connected them, and a reader arriving at the first
    one had no way to learn the second existed.

    Stub fields only (id, timestamp, author, thread). The corrector's NOTE is
    deliberately not copied in: a cached copy of another record's body is the
    stale-mirror mistake SOP #4 names — point at the source, do not mirror it.
    """
    index: dict[str, list[dict]] = {}
    for rec in records:
        target = (rec.get("supersedes") or "").strip()
        if not target:
            continue
        index.setdefault(target, []).append(
            {
                "handoff_id": Path(rec.get("_path", "")).name,
                "timestamp": rec.get("timestamp", ""),
                "source_instance": rec.get("source_instance", ""),
                "thread": rec.get("thread", ""),
            }
        )
    for stubs in index.values():
        stubs.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    return index


def _annotate_forward_links(records: list[dict]) -> list[dict]:
    """Attach ``_corrected_by`` to any record a later handoff supersedes.

    Applied at the single READ chokepoint (``_load_all``) rather than at each
    of the surfaces, so the boot door, handoff_archaeology and
    reflexive_surface all inherit it and cannot drift apart. Underscore-
    prefixed like ``_path``, and set only when a correction exists: records
    nobody corrected come back byte-identical to before, so nothing that
    round-trips a record can widen the on-disk shape.
    """
    index = _build_forward_index(records)
    if not index:
        return records
    for rec in records:
        stubs = index.get(Path(rec.get("_path", "")).name)
        if stubs:
            rec["_corrected_by"] = stubs
    return records


class HandoffEngine:
    """Intent-layer memory for instance-to-instance handoff."""

    def __init__(self, root: str):
        self.root = Path(root) / "handoffs"
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve_handoff_id(self, ref: str) -> str:
        """Turn a caller-supplied handoff reference into the store's own id.

        The canonical id is the FILENAME (``_handoff_id``, the same identity
        the signature ledger keys on). Accepted, all resolved to that one id:
        a full path, a bare filename, or the filename stem without ``.json``
        (which is how a handoff gets named in prose — the audit that prompted
        this feature cited ``20260902T112749_*``).

        Deliberately EXACT, never a glob. A prefix match would let
        ``20260902T1127`` silently pick whichever file sorted first, and a
        correction pointed at the wrong record is worse than no link: it moves
        the CORRECTED BY banner onto an innocent handoff.

        Raises ValueError if the reference resolves to nothing on disk.
        """
        cleaned = (ref or "").strip()
        if not cleaned:
            raise ValueError(
                "supersedes was provided but names nothing — pass a handoff id "
                "(the filename, with or without .json) or omit the argument. "
                "An empty correction link is refused rather than dropped: a "
                "dropped one reports success on a correction that never landed."
            )
        name = self._handoff_id(cleaned)
        if not name.endswith(".json"):
            name += ".json"
        candidate = self.root / name
        if candidate.name != name or not candidate.is_file():
            raise ValueError(
                f"supersedes={ref!r} does not name a handoff in this store "
                f"(looked for {name!r} in {self.root}). Refusing the write: a "
                "correction link that points at nothing renders as no link at "
                "all, which is exactly the silent-drop this field exists to "
                "end. List candidates with handoff_archaeology()."
            )
        return name

    def write(
        self,
        note: str,
        source_instance: str,
        source_session_id: str,
        thread: str = "general",
        supersedes: str | None = None,
    ) -> dict:
        """
        Write a handoff note for the next instance.

        Args:
            supersedes: Optional id of an EARLIER handoff this one corrects.
                Stored on the NEW record only — the superseded file is never
                touched (house rule: corrections supersede, never erase). The
                forward link is then computed at read time by ``_load_all``,
                so the old record starts rendering "CORRECTED BY <id>" without
                anything having been rewritten. Validated against the store;
                a reference that resolves to nothing is refused.

        Returns the stored record. Raises ValueError if the note exceeds the
        size limit, is empty, if source_instance names no author, or if
        supersedes names no existing handoff.
        """
        _refuse_live_store_during_tests(self.root)
        note = (note or "").strip()
        if not note:
            raise ValueError("handoff note is empty")
        if len(note.encode("utf-8")) > HANDOFF_MAX_BYTES:
            raise ValueError(
                f"handoff note exceeds {HANDOFF_MAX_BYTES} bytes — record as insight instead"
            )
        source_instance = _validate_author_identity(source_instance)
        # Resolved BEFORE the file is written, so a bad link costs nothing and
        # leaves nothing behind. Writing first and validating after would leave
        # an orphan record on disk every time a correction reference was wrong.
        superseded_id = self.resolve_handoff_id(supersedes) if supersedes is not None else None

        ts = datetime.now()
        record = {
            "timestamp": ts.isoformat(),
            "source_instance": source_instance,
            "source_session_id": source_session_id or "unknown",
            "thread": thread or "general",
            "note": note,
            "consumed_at": None,
            "consumed_by": None,
        }
        # Key present ONLY when a correction actually happened. An always-present
        # "supersedes": null would change the on-disk shape of every record for
        # the sake of the rare one, and the seven-field shape is what the
        # 2026-09-05 audit measured against.
        if superseded_id is not None:
            record["supersedes"] = superseded_id

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
        return _annotate_forward_links(records)

    def supersession_index(self) -> dict[str, list[dict]]:
        """``{superseded_handoff_id: [corrector stubs, newest first]}``.

        Computed from the records themselves on every call. There is no index
        file and no new state to keep in sync — the backward link on the new
        record IS the index, read the other way round. Deleting a corrector
        removes the link; nothing can go stale.
        """
        return _build_forward_index(self._load_all())

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

    def all_count(self, include_consumed: bool = True, thread: str | None = None) -> int:
        """
        Total handoffs matching the same filters `all()` applies, uncapped.

        Mirrors unconsumed_count() (2026-08-01), for the same reason and one
        layer over: `all()` slices to `limit` and returns a bare list, so a
        caller cannot tell a complete answer from a capped one. Measured
        2026-08-27: 287 handoffs on disk, 286 of them consumed and therefore
        unreachable through any wired path — `all()` itself had zero callers.
        Wiring it without a denominator would have recovered the records and
        added a new silent truncation in the same commit.
        """
        records = self._load_all()
        if not include_consumed:
            records = [r for r in records if not r.get("consumed_at")]
        if thread:
            records = [r for r in records if r.get("thread") == thread]
        return len(records)

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

    # ------------------------------------------------------------------
    # SIGNATURE LEDGER (2026-08-31, Anthony's design)
    #
    # `consumed_at` is destructive: the first reader to call the boot door
    # retires a handoff for EVERY future reader, and 197 real handoffs became
    # unreachable that way. It is also the one place this store contradicts
    # the Stack's own append-only rule, where corrections supersede and
    # nothing is erased.
    #
    # The replacement separates two facts that `consumed_at` conflated:
    #
    #   SIGNATURE  — "this seat received it."  Additive. Many per handoff.
    #                Never removes anything from anyone else's queue.
    #   RETIREMENT — "this is done."  A deliberate act by the author or HQ.
    #                Removes it from every queue.
    #
    # Conflating those two is what caused the bug. Keeping them apart also
    # dissolves the dilemma `unconsumed_count` documents above: there is no
    # global pending queue to grow past a limit, because each reader is
    # filtered against their OWN signatures.
    #
    # Both logs mirror `acted_on.jsonl` in this same class — append-only
    # JSONL beside the store. Nothing here mutates a handoff file, so the
    # whole feature rolls back by deleting the two .jsonl files.
    # ------------------------------------------------------------------

    SIGNATURES_LOG = "signatures.jsonl"
    RETIREMENTS_LOG = "retirements.jsonl"

    @staticmethod
    def _handoff_id(handoff_path: str) -> str:
        """Canonical id for a handoff: its FILENAME, not its full path.

        Deliberate. Full paths embed the machine's home directory, and a
        `~`-rooted path recorded on one machine resolves nowhere on another
        (the house has three). `acted_on.jsonl` keys on full path and
        inherits that fragility; this ledger does not.
        """
        return Path(str(handoff_path).strip()).name

    def sign(self, handoff_path: str, signer: str, note: str | None = None) -> dict:
        """Record that `signer` received this handoff. Additive and idempotent.

        Signing NEVER hides the handoff from another reader. Re-signing by the
        same signer returns the existing signature rather than appending a
        duplicate, so a seat that boots twice does not inflate the ledger.

        Raises ValueError on an empty or placeholder signer — same standard as
        mark_consumed. A signature naming nobody looks like an audit trail
        while carrying no information, which is how 159 handoffs came to be
        stamped "unknown".
        """
        _refuse_live_store_during_tests(self.root)
        if not handoff_path or not str(handoff_path).strip():
            raise ValueError("handoff_path is required")
        signer = _validate_reader_identity(signer, field="signer")
        hid = self._handoff_id(handoff_path)

        for existing in self.signatures(handoff_path):
            if existing.get("signer") == signer:
                return existing

        record: dict = {
            "handoff_id": hid,
            "signer": signer,
            "note": (note or "").strip() or None,
            "timestamp": datetime.now().isoformat(),
        }
        with open(self.root / self.SIGNATURES_LOG, "a") as fh:
            fh.write(json.dumps(record) + "\n")
        return record

    def _read_log(self, name: str) -> list[dict]:
        log = self.root / name
        if not log.exists():
            return []
        out: list[dict] = []
        for line in log.read_text().splitlines():
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def signatures(self, handoff_path: str | None = None, signer: str | None = None) -> list[dict]:
        """Signatures, newest first. Filter by handoff and/or signer."""
        hid = self._handoff_id(handoff_path) if handoff_path else None
        recs = [
            r
            for r in self._read_log(self.SIGNATURES_LOG)
            if (hid is None or r.get("handoff_id") == hid)
            and (signer is None or r.get("signer") == signer)
        ]
        recs.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
        return recs

    def signers_of(self, handoff_path: str) -> set[str]:
        """Every seat that has signed this handoff, legacy consumers included.

        A pre-ledger `consumed_by` counts as that seat's signature, so the
        ledger reads correctly with or without the migration having run.
        """
        signers = {r["signer"] for r in self.signatures(handoff_path) if r.get("signer")}
        hid = self._handoff_id(handoff_path)
        for rec in self._load_all():
            if Path(rec.get("_path", "")).name == hid and rec.get("consumed_by"):
                signers.add(rec["consumed_by"])
        return signers

    def retire(self, handoff_path: str, retired_by: str, reason: str) -> dict:
        """Mark a handoff DONE for everyone. Distinct from signing.

        This is the only operation that removes a handoff from queues, and it
        is deliberate rather than a side effect of reading. Append-only: the
        handoff file is untouched, so retirement is reversible by editing the
        log.
        """
        _refuse_live_store_during_tests(self.root)
        if not handoff_path or not str(handoff_path).strip():
            raise ValueError("handoff_path is required")
        retired_by = _validate_reader_identity(retired_by, field="retired_by")
        if not reason or not reason.strip():
            raise ValueError(
                "reason is required — a retirement with no stated reason is the "
                "silent erasure this ledger exists to prevent"
            )
        record: dict = {
            "handoff_id": self._handoff_id(handoff_path),
            "retired_by": retired_by,
            "reason": reason.strip(),
            "timestamp": datetime.now().isoformat(),
        }
        with open(self.root / self.RETIREMENTS_LOG, "a") as fh:
            fh.write(json.dumps(record) + "\n")
        return record

    def retired_ids(self) -> set[str]:
        return {
            r["handoff_id"] for r in self._read_log(self.RETIREMENTS_LOG) if r.get("handoff_id")
        }

    def unsigned_by(self, reader: str, thread: str | None = None, limit: int = 20) -> list[dict]:
        """Handoffs this READER has not signed and nobody has retired.

        The per-reader replacement for unconsumed(). Another seat's signature
        is invisible here: it cannot hide a handoff from you.
        """
        reader = _validate_reader_identity(reader, field="reader")
        retired = self.retired_ids()
        signed = {r["handoff_id"] for r in self.signatures(signer=reader) if r.get("handoff_id")}
        out = []
        for rec in self._load_all():
            hid = Path(rec.get("_path", "")).name
            if hid in retired or hid in signed:
                continue
            if rec.get("consumed_by") == reader:  # legacy consumption by this reader
                continue
            if thread and rec.get("thread") != thread:
                continue
            out.append(rec)
        out.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
        return out[:limit]

    def unsigned_by_count(self, reader: str, thread: str | None = None) -> int:
        """Uncapped count for unsigned_by — lets the boot surface say
        'showing N of M' rather than silently truncating (aae7281's lesson)."""
        return len(self.unsigned_by(reader, thread=thread, limit=10**9))

    def migrate_consumed_to_signatures(self, dry_run: bool = True) -> dict:
        """Turn each legacy consumed_by into that seat's signature.

        Lossless and reversible: `consumed_at`/`consumed_by` are NOT removed,
        and rollback is `rm signatures.jsonl`. Placeholder consumers
        ("unknown", "test") are counted and SKIPPED rather than written — they
        name no reader, and importing them would launder 237 meaningless rows
        into a fresh audit trail.
        """
        migrated, skipped, already = 0, 0, 0
        plan: list[tuple[str, str]] = []
        for rec in self._load_all():
            consumer = (rec.get("consumed_by") or "").strip()
            if not consumer:
                continue
            if consumer.lower() in NON_IDENTIFYING_CONSUMERS:
                skipped += 1
                continue
            path = rec.get("_path", "")
            if consumer in {s.get("signer") for s in self.signatures(path)}:
                already += 1
                continue
            plan.append((path, consumer))
        if not dry_run:
            for path, consumer in plan:
                self.sign(path, consumer, note="migrated from legacy consumed_by")
                migrated += 1
        return {
            "dry_run": dry_run,
            "would_migrate" if dry_run else "migrated": len(plan) if dry_run else migrated,
            "skipped_placeholder_consumers": skipped,
            "already_signed": already,
        }


def format_handoff_for_surface(record: dict) -> str:
    """
    Attribution-framed rendering. This is the epistemic-hygiene move:
    the new instance reads this as someone else's claim, not as its own intent.

    Renders the correction linkage in BOTH directions, which is the whole
    point of computing a forward index: the superseding record says what it
    supersedes, and — the half that was missing entirely — the superseded
    record carries a CORRECTED BY banner ABOVE its note, so a reader cannot
    reach the stale claim without first meeting the correction. Below the note
    would be too late; the reader has already believed it by then.
    """
    src = record.get("source_instance", "unknown")
    sid = record.get("source_session_id", "unknown")
    ts = record.get("timestamp", "unknown")
    thread = record.get("thread", "general")
    note = record.get("note", "")

    lines = [f"• [thread: {thread}] Previous instance {src} (session {sid}, {ts}) left this note:"]

    # Between the attribution line and the note, never after it: the banner has
    # to be read before the claim it qualifies, and it has to stay attached to
    # its own bullet rather than floating above the previous record's.
    corrections = record.get("_corrected_by") or []
    for stub in corrections:
        lines.append(
            f"    ⚠ CORRECTED BY {stub.get('handoff_id', '?')} "
            f"({stub.get('timestamp', 'unknown')})"
            + (f" — {stub['source_instance']}" if stub.get("source_instance") else "")
        )
    if corrections:
        lines.append(
            f"    ({len(corrections)} later handoff(s) correct this one. Read them before "
            "acting on the note below — it stands as written, not as current.)"
        )

    lines.append(f'    "{note}"')

    superseded = (record.get("supersedes") or "").strip()
    if superseded:
        lines.append(f"    supersedes {superseded}")

    return "\n".join(lines)
