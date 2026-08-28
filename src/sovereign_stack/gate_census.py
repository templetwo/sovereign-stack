"""Gate census — the WRITE-side aperture.

aperture.py answers "what exists behind the surfaces you are about to READ."
This module answers its mirror image: **what did you WRITE that has not landed,
and who is it waiting on.**

Both bridge substrates let a remote seat file Ring-2 proposals for human
ratification. Those proposals then sit in a queue that drains only when Anthony
personally runs `bridge approve` / `bridge commit`. As of 2026-08-28 that queue
held 123 pending proposals — oldest 96 days — and NO surface anywhere reported
it: not the dashboard snapshot, not the heartbeat, not aperture-v1. Every seat
that filed one believed it had landed, because from the seat's side a successful
proposal and an unread proposal look identical. That is the house's oldest
failure class (a surface that cannot distinguish "delivered" from "read")
pointed at the write path instead of the read path.

DESIGN RULES, each earned:

1. NEVER A SECOND SOURCE OF TRUTH. Counts come from `bridge_core.cli._SubstrateOps`
   — the exact dispatcher the `bridge` console uses — not from a re-implemented
   glob. The two substrates run genuinely different backends (openai dispatches
   to the legacy `openai_bridge` module, grok to `bridge_core` with a registered
   BridgeContext), so re-deriving the count here would silently diverge from the
   console Anthony actually drains from. test_gate_census asserts equality with
   the console per substrate; that test is the reason to trust this block.

2. STATUS IS NOT ONE NUMBER — IT IS AN OWNERSHIP MAP. "Pending" waits on Anthony.
   "Approved" waits on the commit step (HQ's lane). "Needs_revision" waits on the
   proposing seat. Reporting a single "N pending" hides the distinction that tells
   a caller whether IT is the blocker. Statuses are read from what is actually
   present, never from a fixed key set — a new status must never vanish silently.

3. FAIL CLOSED, AND STAY VISIBLE. A substrate whose queue cannot be read is
   reported with status "unmeasured" and NO counts. It is never omitted and never
   rendered as zero: an absent substrate reads as "nothing pending there", which
   is precisely the false-clean this module exists to prevent. Discovery is by
   glob, so a third bridge added later appears here the day it exists rather than
   being invisible until someone edits a hardcoded list.

4. COUNTS AND DATES ONLY — NEVER CONTENT. This block is rendered on the PUBLIC
   unauthenticated heartbeat. Proposal bodies, domains, and source_instance
   strings never appear here. Tool-name histograms and ISO dates are the ceiling.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

GATE_POLICY_VERSION = "gate-v1"

# Discover queues by SHAPE, not by NAME. The first version of this globbed
# "*_bridge" and silently missed ~/.sovereign/antigravity_connector/pending_writes
# entirely — 2 real proposals from a real connector, 55 days old, rendering as
# absent rather than as unmeasured. A name-based glob defeats rule 3 from the
# outside: a queue that does not match the pattern is not reported as unreadable,
# it is not reported at all. Any directory holding a pending_writes/ IS a queue.
_QUEUE_GLOB = "*/pending_writes"

# Map a discovered queue directory to the console's --source token. A directory
# with no entry here is reported as unmeasured-unknown, never skipped.
_DIR_TO_SOURCE = {
    "openai_bridge": "openai",
    "grok_bridge": "grok",
}

# Who each status is waiting on. Read from data, but labelled from here; an
# unrecognised status is labelled "unknown" rather than dropped.
_STATUS_OWNER = {
    "pending": "anthony — human ratification gate",
    "approved": "HQ — approved but not yet committed to the chronicle",
    "needs_revision": "the proposing seat — but see needs_revision_is_terminal",
    "rejected": "closed — no further action",
    "committed": "closed — landed in the chronicle",
}

# ACCOUNTING LABEL ONLY — THIS IS NOT A SAFETY TIER AND MUST NEVER BECOME ONE.
#
# These tools do not assert a claim into the record, so counting them separately
# stops a single "N pending" from overstating the human's review burden: on
# 2026-08-28, 58 of grok's 103 pending were of this kind, making the claim-bearing
# load roughly half the headline. That is the ONLY thing this set means.
#
# It does NOT mean these are safe to auto-commit, and the same night proved it
# twice. `comms_acknowledge` is in this set, and the single most dangerous item
# in that backlog was a comms_acknowledge that would have written a FABRICATED
# consent record for opening the protected drawer — comms.acknowledge() performs
# zero existence validation, so it will happily ack a message_id that never
# existed. `reflection_ack` is in this set, and ack_reflection() is last-writer-
# wins (reflections.py:230), so committing one would silently overwrite a
# deliberated HQ ack and its authorship.
#
# Auto-commit eligibility would require THREE predicates, each verified by
# READING THE COMMIT PATH rather than trusting the tool name:
#   (1) claim-free — asserts nothing, supersedes nothing, cannot touch law;
#   (2) non-destructive on commit — append-only, or a no-op when state exists;
#   (3) referentially validated, fail-closed — the target must be proven to exist.
# As of 2026-08-28 NONE of the four tools below is verified on all three. A set
# like this quietly becoming a drain list is exactly the shape of a config that
# assumes a property nobody enforced.
_BOOKKEEPING_TOOLS = frozenset(
    {
        "reflection_ack",
        "thread_touch",
        "comms_acknowledge",
        "end_bridge_session",
    }
)


def _sovereign_root(root: Path | str | None) -> Path:
    return Path(root) if root is not None else Path.home() / ".sovereign"


def _parse_ts(raw: str, now: datetime) -> tuple[datetime, int]:
    """Parse a proposal timestamp and return (dt, age_days).

    Proposal timestamps are written NAIVE and are UTC (verified 2026-08-28: a
    file stamped 21:05 corresponds to 17:05 EDT). A naive value is therefore
    assumed UTC rather than local — assuming local would shift every age by the
    offset and quietly under-report the backlog.
    """
    dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    age = (now - dt).days
    if age < 0:
        # A future-dated proposal means the clock or the writer is wrong. Raise
        # rather than render "0 days", which would read as "filed just now".
        raise ValueError(
            f"proposal timestamp {raw!r} is in the future relative to {now.isoformat()}"
        )
    return dt, age


def _measure_substrate(source: str, now: datetime) -> dict:
    """Census one substrate through the console's own dispatcher."""
    from bridge_core.cli import _SubstrateOps

    rows = _SubstrateOps(source).list(None)

    by_status: dict[str, int] = {}
    for r in rows:
        by_status[r.get("status") or "<none>"] = by_status.get(r.get("status") or "<none>", 0) + 1

    pending = [r for r in rows if r.get("status") == "pending"]
    by_tool: dict[str, int] = {}
    for r in pending:
        by_tool[r.get("tool") or "<none>"] = by_tool.get(r.get("tool") or "<none>", 0) + 1

    out: dict = {
        "status": "measured",
        "total_proposals": len(rows),
        "by_status": dict(sorted(by_status.items(), key=lambda kv: -kv[1])),
        "waiting_on": {s: _STATUS_OWNER.get(s, "unknown") for s in sorted(by_status)},
        "pending_by_tool": dict(sorted(by_tool.items(), key=lambda kv: -kv[1])),
        "pending_claim_bearing": sum(n for t, n in by_tool.items() if t not in _BOOKKEEPING_TOOLS),
        "pending_bookkeeping": sum(n for t, n in by_tool.items() if t in _BOOKKEEPING_TOOLS),
    }

    stamped = [r for r in pending if r.get("timestamp")]
    if stamped:
        oldest = min(stamped, key=lambda r: r["timestamp"])
        _, age = _parse_ts(oldest["timestamp"], now)
        out["oldest_pending_date"] = str(oldest["timestamp"])[:10]
        out["oldest_pending_age_days"] = age
    elif pending:
        # Pending rows exist but carry no timestamp — say so rather than omit.
        out["oldest_pending_date"] = None
        out["oldest_pending_age_days"] = None
        out["note"] = "pending proposals present but none carry a timestamp"

    if by_status.get("needs_revision"):
        out["needs_revision_is_terminal"] = (
            "approve_pending_write() requires status=='pending' and no code path "
            "returns a proposal to 'pending', so a needs_revision proposal can "
            "only ever be rejected. Its review notes are not delivered to the "
            "proposing seat by any surface. Treat this count as stuck, not queued."
        )

    return out


def measure_gate(now: datetime, root: Path | str | None = None) -> dict:
    """Full write-side census. Raises on failure — never returns a partial map.

    Per-substrate failures degrade to that substrate's own "unmeasured" entry
    (rule 3); only a failure of discovery itself propagates, and the caller
    renders unmeasured() so no numbers are shown at all.
    """
    base = _sovereign_root(root)
    substrates: dict[str, dict] = {}

    for pending_dir in sorted(base.glob(_QUEUE_GLOB)):
        if not pending_dir.is_dir():
            continue
        name = pending_dir.parent.name
        source = _DIR_TO_SOURCE.get(name)
        if source is None:
            # No console route, so no STATUS counts. But a readable directory
            # still proves how many records exist, and reporting nothing at all
            # would hide a real connector's real proposals behind a blank. The
            # floor is labelled so it can never be mistaken for a status count.
            try:
                # os.listdir RAISES on an unreadable directory. Path.glob does
                # NOT — it swallows the PermissionError and yields nothing, so a
                # locked queue would count as 0 files and read as empty. That is
                # the same false-clean this whole module exists to prevent, and
                # it shipped here first; the regression test locks it closed.
                import os

                on_disk = sum(1 for n in os.listdir(pending_dir) if n.endswith(".json"))
            except OSError:
                on_disk = None
            entry = {
                "status": "unmeasured",
                "reason": (
                    "queue directory found but no console dispatcher routes this "
                    "substrate — status counts are UNAVAILABLE, which is not zero"
                ),
            }
            if on_disk is not None:
                entry["files_on_disk"] = on_disk
                entry["files_on_disk_note"] = (
                    "an existence floor counted directly from *.json — NOT a status "
                    "count. Some of these may already be committed or rejected. It is "
                    "here so a routable-by-nobody queue cannot render as empty."
                )
            substrates[name] = entry
            continue
        try:
            substrates[name] = _measure_substrate(source, now)
        except Exception as exc:  # noqa: BLE001 — any failure is unmeasured, never zero
            substrates[name] = {
                "status": "unmeasured",
                "reason": f"{type(exc).__name__}: {exc}",
            }

    if not substrates:
        raise FileNotFoundError(f"no bridge queues discovered under {base}")

    measured = [s for s in substrates.values() if s.get("status") == "measured"]
    total_pending = sum(s["by_status"].get("pending", 0) for s in measured)
    total_claim_bearing = sum(s["pending_claim_bearing"] for s in measured)

    return {
        "policy_version": GATE_POLICY_VERSION,
        "status": "measured",
        "measured_at": now.isoformat(),
        "what_this_is": (
            "What you PROPOSED that has not landed, and who it is waiting on. "
            "A filed proposal and an unread proposal look identical from the "
            "proposing seat — this block is the difference."
        ),
        "total_pending_all_substrates": total_pending,
        "total_pending_claim_bearing": total_claim_bearing,
        "burden_note": (
            "total_pending counts every queued write. total_pending_claim_bearing "
            "excludes by-kind bookkeeping (acks, touches, session closes) that "
            "asserts nothing into the record, so it reflects actual review burden "
            "where the headline overstates it. BURDEN ACCOUNTING ONLY — the "
            "excluded kinds are NOT a safety tier and NOT a drain list: their "
            "commit paths are not verified non-destructive and not verified to "
            "validate that their target exists."
        ),
        "any_unmeasured": any(s.get("status") == "unmeasured" for s in substrates.values()),
        "substrates": substrates,
        "drain": {
            "who": "Anthony only — ratification is the human gate, never a seat's call",
            "console": "~/sovereign-stack/venv/bin/bridge --source <substrate> list-pending",
            "hq_role": "HQ reviews and recommends; it does not approve, commit, or reject",
        },
    }


def unmeasured(now: datetime, exc: BaseException) -> dict:
    """Honest failure shape. Carries NO counts — absent is not zero."""
    return {
        "policy_version": GATE_POLICY_VERSION,
        "status": "unmeasured",
        "measured_at": now.isoformat(),
        "reason": f"{type(exc).__name__}: {exc}",
        "note": "gate census could not be measured; absent counts are NOT zero counts",
    }
