"""Attribution census — WHO wrote the record, measured rather than assumed.

aperture.py answers what you are not being SHOWN. gate_census.py answers what
you WROTE that has not landed. This answers the third question, and it is the
one the chronicle could not answer about itself: **who wrote this?**

WHAT WENT WRONG. `record_insight` declared fifteen properties and
`source_instance` was not among them. `additionalProperties` was unset, so the
tool accepted the argument, discarded it, and returned ok:true — verified by a
live probe on 2026-08-28 that wrote a record carrying a wholly fabricated
argument name. Every seat following this house's own documented convention was
therefore attributing nothing while being told it had succeeded. `session_id` is
no help: every entry carries the BRIDGE's spiral session, identical for every
writer on the machine.

The storage layer supported it the whole time. `memory.py` splats `**metadata`
into the record and reads `metadata.get("source_instance")` for the supersession
ledger's `by` field — which has consequently been writing empty strings. A
comment at memory.py:944 states the cause outright: "metadata is dropped by the
server before it reaches here." The field was built, the wire was missing, and
nobody could see it because the failure reported success.

HOW IT SURFACED. Two Claude Code sessions on one machine began writing bodies
opening with the byte-identical string "HQ Mac Studio, claude-opus-5 seat".
Nothing in the record could tell them apart. They were separable only by domain
and timestamp, and only because their domains happened not to overlap — which is
luck, not architecture.

WHY THIS IS A HEARTBEAT SURFACE AND NOT A DOC. Anthony, 2026-08-28, on being
shown the fix: "i agree with both but it must be visable at heartbeat." A
convention is not enforceable and a fix nobody can see from the door is
indistinguishable from a fix nobody made — which is precisely how this house
accumulated its backlog of written-but-unconnected repairs. So the door now
reports the attribution RATE, measured from disk, rather than asserting that
attribution works.

FAILS CLOSED. An unmeasurable census reports status "unmeasured" with NO
percentages. A zero attribution rate manufactured by a failed read would be the
same class of lie this module exists to expose.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

ATTRIBUTION_POLICY_VERSION = "attribution-v1"

# The moment record_insight began FORWARDING source_instance to storage. Before
# this, no insight could carry a writer field no matter what its author passed —
# so an anonymous entry from before the cutover is history, not negligence, and a
# reader must be able to tell those apart. Without this line the measured rate
# indicts every past seat for a defect none of them could have avoided.
ATTRIBUTION_AVAILABLE_SINCE = "2026-08-28T23:00:00+00:00"

# Fields that identify a WRITER. `vantage` is the seat/evidence-mode field that
# already existed and already persisted — it names the KIND of seat
# (hq_filesystem, local_jetson, grok_bridge), which is weaker than an instance
# identifier but is real attribution and must be counted as such.
_AUTHOR_FIELDS = ("source_instance", "vantage")

# Scanning every shard on every heartbeat would put an unbounded filesystem walk
# on a public endpoint. The window is stated in the output so a reader never
# mistakes a sample for the corpus — the aperture lesson applied to this module.
_RECENT_DAYS = 7
_MAX_FILES = 400


def _sovereign_root(root: Path | str | None) -> Path:
    return Path(root) if root is not None else Path.home() / ".sovereign"


def measure_attribution(now: datetime, root: Path | str | None = None) -> dict:
    """Measure how much of the recent record can name its own author.

    Raises on failure — the caller renders unmeasured() so no rate is shown.
    """
    base = _sovereign_root(root)
    insights_dir = base / "chronicle" / "insights"
    if not insights_dir.is_dir():
        raise FileNotFoundError(f"no insights store at {insights_dir}")

    cutoff = (now - timedelta(days=_RECENT_DAYS)).isoformat()

    # os.listdir raises on an unreadable directory. Path.glob swallows
    # PermissionError and yields nothing, which would report a locked store as a
    # zero-entry one and hand back a 0% attribution rate as if measured.
    # Collect shard paths, then order by MODIFICATION TIME, newest first. The
    # first version walked domains alphabetically and hit the file cap long
    # before reaching the recently-written ones — a "recent window" measure whose
    # sample was selected by filename. That is the aperture failure in miniature:
    # a bounded read whose bound silently decides what you see.
    candidates: list[tuple[float, Path]] = []
    for dom in sorted(os.listdir(insights_dir)):
        d = insights_dir / dom
        if not d.is_dir():
            continue
        for name in os.listdir(d):
            if not name.endswith(".jsonl"):
                continue
            f = d / name
            try:
                candidates.append((f.stat().st_mtime, f))
            except OSError:
                continue
    candidates.sort(key=lambda t: -t[0])
    truncated = len(candidates) > _MAX_FILES
    selected = [f for _, f in candidates[:_MAX_FILES]]
    scanned_files = len(selected)
    total_shards = len(candidates)

    total = attributed = post_cutover = 0
    by_field: dict[str, int] = dict.fromkeys(_AUTHOR_FIELDS, 0)
    authors: set[str] = set()

    for shard in selected:
        with open(shard, errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(rec, dict):
                    continue
                if str(rec.get("timestamp", "")) < cutoff:
                    continue
                total += 1
                if str(rec.get("timestamp", "")) >= ATTRIBUTION_AVAILABLE_SINCE:
                    post_cutover += 1
                named = False
                for fld in _AUTHOR_FIELDS:
                    v = rec.get(fld)
                    if isinstance(v, str) and v.strip():
                        by_field[fld] += 1
                        named = True
                        if fld == "source_instance":
                            authors.add(v.strip()[:60])
                if named:
                    attributed += 1

    anonymous = total - attributed
    pct = round(100.0 * attributed / total, 1) if total else None

    return {
        "policy_version": ATTRIBUTION_POLICY_VERSION,
        "status": "measured",
        "measured_at": now.isoformat(),
        "what_this_is": (
            "How much of the recent record can name its own author. The chronicle "
            "stores no writer identity unless the writer supplies one, and "
            "session_id is the BRIDGE's spiral session — identical for every "
            "writer, so it identifies nobody."
        ),
        "window_days": _RECENT_DAYS,
        "entries_in_window": total,
        "attributed": attributed,
        "anonymous": anonymous,
        "attributed_pct": pct,
        "attribution_available_since": ATTRIBUTION_AVAILABLE_SINCE,
        "entries_after_cutover": post_cutover,
        "cutover_note": (
            "record_insight began FORWARDING source_instance to storage at the "
            "timestamp above. Entries older than it could not carry a writer "
            "field no matter what their author passed — the argument was accepted "
            "and discarded while the write returned ok:true. Read the anonymous "
            "count before the cutover as history, not as negligence."
        ),
        "by_field": by_field,
        "distinct_source_instances": len(authors),
        "scan": {
            "files_scanned": scanned_files,
            "shards_on_disk": total_shards,
            "truncated": truncated,
            "ordering": "modification time, newest first",
            "note": (
                f"bounded at {_MAX_FILES} shard files so a public endpoint never "
                "walks the store without limit, and ordered by mtime so the bound "
                "selects the RECENT shards rather than the alphabetically first. "
                "A truncated scan is a SAMPLE, not the corpus."
            ),
        },
        "how_to_attribute": {
            "source_instance": (
                "pass source_instance on record_insight — who you are, precisely "
                "enough to distinguish you from a concurrent writer "
                "(e.g. 'HQ 1/2, opus, a7619408'). Stored as a first-class field."
            ),
            "vantage": (
                "pass vantage for the KIND of seat and evidence mode "
                "(hq_filesystem, bridge_runtime, local_jetson, grok_bridge, "
                "openai_bridge, gemini_connector, human_attestation, ...)"
            ),
            "unknown_arguments": (
                "are now REJECTED rather than silently discarded. Before "
                "2026-08-28 record_insight accepted any argument, stored none of "
                "them, and returned ok:true — so a documented convention produced "
                "zero attribution and nobody found out."
            ),
        },
    }


def unmeasured(now: datetime, exc: BaseException) -> dict:
    """Honest failure shape. Carries NO rate — a 0% manufactured by a failed read
    would be the same lie this module exists to expose."""
    return {
        "policy_version": ATTRIBUTION_POLICY_VERSION,
        "status": "unmeasured",
        "measured_at": now.isoformat(),
        "reason": f"{type(exc).__name__}: {exc}",
        "note": ("attribution could not be measured; an absent rate is NOT a zero rate"),
    }
