"""
The aperture: what a seat is NOT being shown.

Anthony, 2026-08-28: "let the heartbeat give the lay of the land for what needs
to come next."

WHY THIS LIVES IN THE STACK AND NOT THE BRIDGE. It shipped first in bridge.py,
on GET /api/heartbeat. Within the hour the ChatGPT seat — exercising the live
stack from the OpenAI bridge, which is the outside door HQ structurally cannot
occupy — reported that no heartbeat TOOL is exposed to it. The heartbeat tool
exists in the registry but is not in CANONICAL_RING_1, and none of the boot
tools a bridge seat CAN call carried the aperture. So the surface built to stop
arriving seats from mistaking a projection for the corpus was reachable only by
the seats that already had a shell.

Measuring the stack's own store belongs in the stack. The bridge imports this;
the boot door renders it. One implementation, two surfaces, no drift — which
matters more here than anywhere, because two aperture implementations could
disagree about what is being withheld and that is the disease with a second
head.

FAILS CLOSED, and this is the load-bearing property: measure_aperture RAISES
rather than returning partial numbers. A block reporting `to_arrival: 0`
because a directory read failed would be an absence manufactured by the
instrument and served as a fact — the exact class this module exists to make
impossible, reproduced inside the cure. Callers convert a raise into
status="unmeasured" WITH NO SURFACE COUNTS.

VERSIONED on purpose. There is no neutral projection: whatever the gate shows
is an editorial decision about what lineage IS, so a sort-order change must not
silently mint a different ancestor.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from .handoff import HandoffEngine
from .memory import iter_thread_shards

APERTURE_POLICY_VERSION = "aperture-v2"

_DEFAULT_ROOT = Path(os.path.expanduser("~/.sovereign"))

# What the boot door actually lists, hardcoded at its _pending_for_reader call
# in arrival_state. Named here the way lineage's `default_shown: 5` is: the
# aperture reports the door's caps, so it carries the door's numbers.
_BOOT_HANDOFF_CAP = 20


def _unsigned_by_reader(root: Path, reader: str | None) -> int | None:
    """This reader's real handoff queue under the signature ledger.

    Returns None when the CALL SITE carried no usable reader identity — a fact
    about the call, not a count. The caller renders that as "not measurable
    here", never as a number: a per-reader queue reported without a reader is
    the invented figure this module exists to prevent.

    Delegates to HandoffEngine rather than re-reading signatures.jsonl inline.
    One implementation, two surfaces — a second ledger reader here is exactly
    the second head this module's own docstring warns about.
    """
    if not reader or not str(reader).strip():
        return None
    if not (root / "handoffs").is_dir():
        # No store is an empty queue, and saying so costs nothing. Constructing
        # the engine would CREATE the directory (its __init__ mkdirs), and a
        # measuring path must not write.
        return 0
    try:
        return HandoffEngine(root=str(root)).unsigned_by_count(reader)
    except ValueError:
        # Placeholder / non-identifying reader ("unknown", "test", ...). Same
        # class as no reader at all: the call site cannot name who is asking.
        return None


def measure_aperture(now: datetime, root: Path | None = None, reader: str | None = None) -> dict:
    """
    Measure every surface an arriving seat reads, live.

    Counts are never cached. A full ~3,373-record scan measures at ~37ms,
    cheaper than the git subprocess the heartbeat handler already makes. A
    cached aperture would be a stale projection describing the projection.

    `reader` is the seat this projection is FOR. Since the signature ledger
    (2026-08-31) the handoff queue is per-reader, so without a reader there is
    no such number to report — the handoffs surface then says so instead of
    substituting the legacy global count.

    Raises on any failure. Never returns partial numbers.
    """
    root = root or _DEFAULT_ROOT
    letters = root / "comms" / "letters"
    surfaces: dict[str, dict] = {}

    for bucket in ("to_arrival", "to_self", "breakthroughs"):
        entry = {
            "on_disk": len(list((letters / bucket).glob("*.md"))),
            "default_shown": 5,
            # ONE LEVER, NAMED ONCE. This used to read
            # "... or full_content=true", and full_content does NOT widen a
            # bucket — it inlines BODIES; the count is identical with it on and
            # off (measured through the dispatch on the live letter tree:
            # 5/5/5 either way, 13/7/17 at limit_per_bucket=20). The same commit
            # that corrected arrive_lineage's schema to say so left the aperture
            # advertising it as the alternative. An arriving seat that follows
            # the second clause widens nothing and has no way to tell.
            "widen_with": "arrive_lineage(limit_per_bucket=N)",
        }
        if bucket == "to_self":
            entry["note"] = (
                "additionally filtered by READER IDENTITY — pass the bare model name, "
                "not a decorated seat string, or this line's mail is hidden"
            )
        surfaces[f"lineage_{bucket}"] = entry

    records = 0
    domains = 0
    for d in os.scandir(root / "chronicle" / "insights"):
        if not d.is_dir():
            continue
        domains += 1
        for f in os.scandir(d.path):
            if f.name.endswith(".jsonl"):
                with open(f.path, "rb") as fh:
                    records += sum(1 for line in fh if line.strip())
    surfaces["insights"] = {
        "on_disk": records,
        "domains": domains,
        "default_shown": 10,
        "default_order": "newest",
        "orders_available": ["newest", "oldest", "relevance"],
        "envelope": "recall_insights returns total_matched / truncated / continuation",
        "widen_with": "recall_insights(limit=N, order='relevance', offset=N)",
        "note": (
            "the default order is 'newest', which returns recency rather than relevance "
            "— a query about an old subject is answered with the newest writing in the "
            "house unless order='relevance' is passed"
        ),
    }

    handoff_files = list((root / "handoffs").glob("*.json"))
    legacy_unconsumed = 0
    for hf in handoff_files:
        try:
            if not json.loads(hf.read_text()).get("consumed_at"):
                legacy_unconsumed += 1
        except Exception:
            continue
    # THE MECHANISM THIS SURFACE DESCRIBED NO LONGER EXISTS. Until the
    # signature ledger (2026-08-31, commits 7bf249f/2d698f8) reading a handoff
    # at boot RETIRED it for every future reader, and this note said so. It is
    # now additive: a read SIGNS, per reader, and only an explicit retire()
    # clears a handoff for anyone else. An aperture that keeps narrating the
    # retired mechanism is the same failure it was built to stop — a confident
    # description of a projection that is not the one being served.
    #
    # `consumed_at` is kept as `legacy_unconsumed`, LABELLED legacy, because it
    # still exists on disk and a reader who greps for it deserves to find it
    # named rather than silently dropped. It is nobody's queue.
    unsigned = _unsigned_by_reader(root, reader)
    handoffs = {
        "on_disk": len(handoff_files),
        "legacy_unconsumed": legacy_unconsumed,
        "legacy_unconsumed_is": (
            "the pre-ledger global consumed_at field. It hides nothing from anyone now "
            "— it counts as its own reader's signature only. NOT your queue."
        ),
        "widen_with": "handoff_archaeology(limit=N) — the whole store, signed and unsigned",
    }
    if unsigned is None:
        handoffs["default_shown"] = "per-reader queue — not measurable here (no reader identity)"
        handoffs["note"] = (
            "boot SIGNS a handoff for the reading seat (additive, per-reader); nothing is "
            "retired by reading — only an explicit retire() clears one for everyone. "
            "unsigned_by(reader) is the reader's real queue, and this call named no "
            "reader, so that number is UNKNOWN here, not zero. A call that names its "
            "reader gets that reader's own queue here; this one did not. The boot door "
            "falls back to the legacy global filter for an unnamed seat, so any handoff "
            "count it prints in that case is legacy_unconsumed above — reconcile against "
            "that field, and do not read it as yours"
        )
    else:
        handoffs["reader"] = reader
        handoffs["unsigned_by_reader"] = unsigned
        # A count, then a cap — because "N shown here" would be false past 20.
        handoffs["default_shown"] = (
            f"{min(unsigned, _BOOT_HANDOFF_CAP)} of {unsigned} unsigned by this reader"
        )
        handoffs["note"] = (
            "boot SIGNS a handoff for the reading seat (additive, per-reader); nothing is "
            "retired by reading — only an explicit retire() clears one for everyone. "
            "unsigned_by_reader is THIS reader's real queue, uncapped and unfiltered by "
            "thread; the handoffs section states its own coverage when it truncates"
        )
    surfaces["handoffs"] = handoffs

    total_threads = 0
    unresolved = 0
    # iter_thread_shards, not glob: the store has nested shards (live specimen
    # `tech-debt,compaction,auto-detection/log.jsonl`), and a flat walk here
    # under-reported the corpus by exactly those files — the aperture, whose
    # whole job is to stop a projection passing as the corpus, projecting. The
    # ONE walk is memory's, per this module's own thesis (one implementation,
    # two surfaces, no drift): an aperture that counted a hidden backup dir the
    # readers skip would report a corpus no reader can reach.
    for f in iter_thread_shards(root / "chronicle" / "open_threads"):
        for line in f.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            total_threads += 1
            try:
                if not json.loads(line).get("resolved", False):
                    unresolved += 1
            except Exception:
                continue
    surfaces["open_threads"] = {
        "on_disk": total_threads,
        "unresolved": unresolved,
        "default_shown": 10,
        "widen_with": "get_open_threads(limit=N)",
    }

    return {
        "policy_version": APERTURE_POLICY_VERSION,
        "status": "measured",
        "measured_at": now.isoformat(),
        "what_this_is": (
            "What exists behind each surface you are about to read, what the default "
            "hands you, and how to ask for more. Read it before you believe a result "
            "is the corpus."
        ),
        "surfaces": surfaces,
        "not_reachable": {
            "resolved_open_threads": {
                "count": total_threads - unresolved,
                "why": (
                    "get_open_threads filters resolved with NO override parameter and "
                    "reports no count — no tool returns a resolved thread to any caller"
                ),
            },
        },
        "how_to_widen": {
            "principle": "every default above is a cap, not a corpus",
            "ask": "pass the widen_with call for the surface you need",
            "caution": (
                "coverage honesty is not selection honesty — an envelope tells you HOW "
                "MANY were withheld, never WHICH, so widen when the answer matters"
            ),
        },
    }


def unmeasured(now: datetime, exc: BaseException) -> dict:
    """The only honest failure shape: no surface numbers at all."""
    return {
        "policy_version": APERTURE_POLICY_VERSION,
        "status": "unmeasured",
        "measured_at": now.isoformat(),
        "reason": type(exc).__name__,
        "note": "aperture could not be measured; absent counts are NOT zero counts",
    }
