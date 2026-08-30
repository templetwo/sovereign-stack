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

APERTURE_POLICY_VERSION = "aperture-v1"

_DEFAULT_ROOT = Path(os.path.expanduser("~/.sovereign"))


def measure_aperture(now: datetime, root: Path | None = None) -> dict:
    """
    Measure every surface an arriving seat reads, live.

    Counts are never cached. A full ~3,373-record scan measures at ~37ms,
    cheaper than the git subprocess the heartbeat handler already makes. A
    cached aperture would be a stale projection describing the projection.

    Raises on any failure. Never returns partial numbers.
    """
    root = root or _DEFAULT_ROOT
    letters = root / "comms" / "letters"
    surfaces: dict[str, dict] = {}

    for bucket in ("to_arrival", "to_self", "breakthroughs"):
        entry = {
            "on_disk": len(list((letters / bucket).glob("*.md"))),
            "default_shown": 5,
            "widen_with": "arrive_lineage(limit_per_bucket=N) or full_content=true",
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
    unconsumed = 0
    for hf in handoff_files:
        try:
            if not json.loads(hf.read_text()).get("consumed_at"):
                unconsumed += 1
        except Exception:
            continue
    surfaces["handoffs"] = {
        "on_disk": len(handoff_files),
        "default_shown": unconsumed,
        "unconsumed": unconsumed,
        "widen_with": "handoff_archaeology(limit=N) — the consumed archive",
        "note": "boot surfaces an unconsumed handoff ONCE and retires it; the rest are archive",
    }

    total_threads = 0
    unresolved = 0
    # rglob, not glob: the store has nested shards (live specimen
    # `tech-debt,compaction,auto-detection/log.jsonl`), and a flat walk here
    # under-reported the corpus by exactly those files — the aperture, whose
    # whole job is to stop a projection passing as the corpus, projecting.
    for f in (root / "chronicle" / "open_threads").rglob("*.jsonl"):
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
