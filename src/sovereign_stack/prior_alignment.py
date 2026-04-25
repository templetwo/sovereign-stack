"""
Stage B: prior_for_turn alignment-vs-pushback instrumentation.

Built 2026-04-25 alongside Stage A's `nape_honks_with_history`. Cell-Claude's
framing: Jain et al. MIT/IDSS 2026 found personalization-driven priors
increase agreement-bias up to +45% over two-week windows. If
`prior_for_turn` surfaces a thread/insight/honk and the response *agrees*,
log that. If the response *contradicts*, log that. The ratio over a
window is a directly measurable sycophancy metric and a publishable
methodology detail.

Architecture (two-phase):

  Phase 1 (already plumbed in reflexive.py):
    `prior_for_turn()` returns a `turn_id` UUID and writes the priors
    surfaced (`included_items`) to the freshness log alongside that id.

  Phase 2 (this module):
    `record_prior_alignment(turn_id, aligned_with, contradicted, ignored, notes)`
    appends an alignment record to ~/.sovereign/reflexive/alignment_log.jsonl.
    Validates that turn_id exists in priors_log first — a record without
    a matching priors call is a schema fork and gets rejected.

  Reporting:
    `prior_alignment_summary(since_iso, until_iso)` reads the alignment
    log, joins against priors_log on turn_id, and computes:
      - per-source alignment ratios (drift / uncertainty / thread / insight)
      - per-pattern alignment ratios (for drift signatures)
      - aggregate counts: total_with_priors, aligned, contradicted, ignored

What "aligned / contradicted / ignored" means:
  - aligned_with: the response acted on this prior (used the thread,
    integrated the insight, addressed the honk)
  - contradicted: the response explicitly disagreed with this prior
    (chose a different path, rebutted a stale assumption)
  - ignored: the prior was surfaced but didn't visibly inform the response
    (neither used nor pushed back — the silent middle case)

The "aligned" + "contradicted" sum is what Jain et al. report. "Ignored"
is the noise floor — high-ignored ratios mean the priors are surfacing
items that aren't load-bearing.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Paths ────────────────────────────────────────────────────────────────────


def _reflexive_dir(sovereign_root: Path | None = None) -> Path:
    root = sovereign_root or Path(
        os.environ.get(
            "SOVEREIGN_ROOT",
            Path.home() / ".sovereign",
        )
    )
    d = root / "reflexive"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _priors_log_path(sovereign_root: Path | None = None) -> Path:
    return _reflexive_dir(sovereign_root) / "priors_log.jsonl"


def _alignment_log_path(sovereign_root: Path | None = None) -> Path:
    return _reflexive_dir(sovereign_root) / "alignment_log.jsonl"


# ── Reading the priors log (turn_id index) ──────────────────────────────────


def _load_priors_index(
    sovereign_root: Path | None = None,
) -> dict[str, dict]:
    """
    Build a turn_id → priors-record index from the priors_log. Records
    without a turn_id (legacy) are skipped — alignment requires the id.
    """
    path = _priors_log_path(sovereign_root)
    if not path.exists():
        return {}
    out: dict[str, dict] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        tid = rec.get("turn_id")
        if not tid:
            continue
        out[tid] = rec
    return out


# ── Phase 2: record_prior_alignment ─────────────────────────────────────────


def record_prior_alignment(
    turn_id: str,
    *,
    aligned_with: list[str] | None = None,
    contradicted: list[str] | None = None,
    ignored: list[str] | None = None,
    notes: str = "",
    sovereign_root: Path | None = None,
) -> dict:
    """
    Append an alignment record for a prior_for_turn call.

    Args:
        turn_id: UUID returned by prior_for_turn. Must exist in
            priors_log; unknown turn_ids are rejected.
        aligned_with: signatures the response used / agreed with.
        contradicted: signatures the response explicitly disagreed with.
        ignored: signatures surfaced but not visibly used.
        notes: free-text note for the audit trail.

    Returns:
        {ok, alignment_record} on success;
        {ok: False, error: ...} on validation failure.
    """
    if not turn_id or not isinstance(turn_id, str):
        return {"ok": False, "error": "turn_id required and must be string"}

    aligned_with = list(aligned_with or [])
    contradicted = list(contradicted or [])
    ignored = list(ignored or [])

    # Validate turn_id against the priors index. An alignment for an
    # unknown turn_id is a fork — refuse rather than create orphans.
    index = _load_priors_index(sovereign_root)
    if turn_id not in index:
        return {
            "ok": False,
            "error": "unknown_turn_id",
            "hint": (
                "turn_id not found in priors_log. Confirm it came from "
                "a prior_for_turn() return value, not a fabricated id."
            ),
        }

    priors_rec = index[turn_id]
    surfaced = priors_rec.get("included_items", []) or []
    surfaced_set: set[str] = set(surfaced)

    # Validate: every signature in aligned_with / contradicted / ignored
    # should be a signature that was actually surfaced. Report mismatches
    # but don't reject — the model may have referenced something adjacent.
    all_referenced = set(aligned_with) | set(contradicted) | set(ignored)
    not_surfaced = sorted(all_referenced - surfaced_set)

    record = {
        "turn_id": turn_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "priors_timestamp": priors_rec.get("timestamp"),
        "surfaced": surfaced,
        "aligned_with": aligned_with,
        "contradicted": contradicted,
        "ignored": ignored,
        "not_surfaced_referenced": not_surfaced,
        "notes": notes,
    }

    log_path = _alignment_log_path(sovereign_root)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

    return {"ok": True, "alignment_record": record}


# ── Reporting: prior_alignment_summary ──────────────────────────────────────


def _kind_for_signature(sig: str) -> str:
    """Drift / uncertainty / thread / insight from "kind:id" signatures."""
    if not isinstance(sig, str) or ":" not in sig:
        return "unknown"
    kind = sig.split(":", 1)[0]
    return {
        "honk": "drift",
        "uncertainty": "uncertainty",
        "thread": "thread",
        "insight": "insight",
    }.get(kind, kind)


def _within_window(
    iso_ts: str | None,
    since_iso: str | None,
    until_iso: str | None,
) -> bool:
    if not iso_ts:
        return True
    try:
        ts = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    if since_iso:
        try:
            since = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
            if ts < since:
                return False
        except ValueError:
            pass
    if until_iso:
        try:
            until = datetime.fromisoformat(until_iso.replace("Z", "+00:00"))
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
            if ts > until:
                return False
        except ValueError:
            pass
    return True


def prior_alignment_summary(
    since: str | None = None,
    until: str | None = None,
    *,
    sovereign_root: Path | None = None,
) -> dict[str, Any]:
    """
    Aggregate alignment records into the Jain et al.-shaped summary.

    Args:
        since: ISO-8601 lower bound on alignment timestamp (inclusive).
        until: ISO-8601 upper bound (inclusive).

    Returns:
        {
          "window": {"since": ..., "until": ...},
          "totals": {turns_with_alignment, aligned, contradicted, ignored,
                     turns_with_priors_no_alignment},
          "ratios": {alignment_rate, contradiction_rate, ignore_rate},
          "by_source": {source: {aligned, contradicted, ignored, total}},
          "by_drift_pattern": {pattern: {aligned, contradicted, ignored, total}},
          "alignment_count": int,    # number of alignment records in window
        }
    """
    align_path = _alignment_log_path(sovereign_root)
    priors_index = _load_priors_index(sovereign_root)

    aligned_total = 0
    contradicted_total = 0
    ignored_total = 0
    by_source: dict[str, dict[str, int]] = defaultdict(
        lambda: {"aligned": 0, "contradicted": 0, "ignored": 0, "total": 0}
    )
    by_drift_pattern: dict[str, dict[str, int]] = defaultdict(
        lambda: {"aligned": 0, "contradicted": 0, "ignored": 0, "total": 0}
    )
    seen_turn_ids: set[str] = set()

    if align_path.exists():
        try:
            lines = align_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for raw in lines:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not _within_window(rec.get("timestamp"), since, until):
                continue
            seen_turn_ids.add(rec.get("turn_id", ""))

            for sig in rec.get("aligned_with", []) or []:
                aligned_total += 1
                src = _kind_for_signature(sig)
                by_source[src]["aligned"] += 1
                by_source[src]["total"] += 1
            for sig in rec.get("contradicted", []) or []:
                contradicted_total += 1
                src = _kind_for_signature(sig)
                by_source[src]["contradicted"] += 1
                by_source[src]["total"] += 1
            for sig in rec.get("ignored", []) or []:
                ignored_total += 1
                src = _kind_for_signature(sig)
                by_source[src]["ignored"] += 1
                by_source[src]["total"] += 1

    # turns_with_priors_no_alignment: prior_for_turn was called and emitted
    # a non-empty block, but no alignment record exists. The Stage B caller
    # may not have followed up. Useful as a hygiene metric.
    turns_with_priors = 0
    for _tid, priors_rec in priors_index.items():
        if not _within_window(priors_rec.get("timestamp"), since, until):
            continue
        if priors_rec.get("included_items"):
            turns_with_priors += 1
    turns_no_alignment = max(0, turns_with_priors - len(seen_turn_ids))

    total_signatures = aligned_total + contradicted_total + ignored_total
    if total_signatures > 0:
        alignment_rate = aligned_total / total_signatures
        contradiction_rate = contradicted_total / total_signatures
        ignore_rate = ignored_total / total_signatures
    else:
        alignment_rate = 0.0
        contradiction_rate = 0.0
        ignore_rate = 0.0

    return {
        "window": {"since": since, "until": until},
        "totals": {
            "turns_with_alignment": len(seen_turn_ids),
            "turns_with_priors_no_alignment": turns_no_alignment,
            "aligned": aligned_total,
            "contradicted": contradicted_total,
            "ignored": ignored_total,
        },
        "ratios": {
            "alignment_rate": round(alignment_rate, 4),
            "contradiction_rate": round(contradiction_rate, 4),
            "ignore_rate": round(ignore_rate, 4),
        },
        "by_source": dict(by_source),
        "by_drift_pattern": dict(by_drift_pattern),
        "alignment_count": len(seen_turn_ids),
    }


# Note: by_drift_pattern is allocated but not yet populated above. Drift
# pattern requires looking up the original honk record by signature — a
# follow-up enhancement once we have a few weeks of data and want
# pattern-level granularity. For now the source-level rollup is enough
# to detect the +45% Jain et al. signal.
