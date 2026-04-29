"""
Stage B integration test: turn_id flows from prior_for_turn() through
record_prior_alignment() into prior_alignment_summary(), end-to-end.

Unit tests in test_prior_alignment.py and test_per_turn_priors.py cover each
function in isolation.  This file proves the plumbing actually connects.
"""

from __future__ import annotations

import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sovereign_stack.prior_alignment import (
    prior_alignment_summary,
    record_prior_alignment,
)
from sovereign_stack.reflexive import PerTurnPriors, ReflexiveSurface

# ── Shared helpers ────────────────────────────────────────────────────────────


def _now_iso(offset_seconds: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).isoformat()


def _sharp_honk(honk_id: str = "h1") -> list[dict]:
    return [
        {
            "honk_id": honk_id,
            "level": "sharp",
            "pattern": "declare_before_verify",
            "trigger_tool": "record_insight",
            "timestamp": _now_iso(),
        }
    ]


def _make_priors(root: Path, honks: list[dict] | None = None) -> PerTurnPriors:
    surface = ReflexiveSurface(sovereign_root=root)
    return PerTurnPriors(
        surface=surface,
        sovereign_root=root,
        uncertainty_fn=None,
        honks_fn=(lambda: honks) if honks is not None else None,
    )


# ── TestStageBRoundTrip ───────────────────────────────────────────────────────


class TestStageBRoundTrip:
    """Integration test: turn_id flows from prior_for_turn through
    record_prior_alignment into prior_alignment_summary, end-to-end."""

    @pytest.fixture
    def root(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "chronicle").mkdir()
        (tmp / "handoffs").mkdir()
        yield tmp
        shutil.rmtree(tmp, ignore_errors=True)

    def test_full_round_trip(self, root: Path) -> None:
        """A turn_id from prior_for_turn can be recorded and shows up in
        prior_alignment_summary with the expected counts and ratios."""
        # 1. Inject: give PerTurnPriors a sharp honk so the block is non-empty.
        honks = _sharp_honk("h-rt")
        priors = _make_priors(root, honks=honks)
        result = priors.inject(domain_tags=[], dry_run=False)

        # Sanity: the block must be non-empty for the integration to be
        # meaningful (otherwise turn_id is written but included_items is empty,
        # and summary gap counts won't reflect anything).
        assert not result["empty"], "Expected non-empty priors block from sharp honk"
        turn_id = result["turn_id"]
        sigs = result["included_items"]
        assert len(sigs) >= 1, "At least one sig must be surfaced"

        # 2. Record alignment: use the first sig as aligned, fabricate a second
        #    as contradicted, and a third as ignored (using non-surfaced sigs is
        #    allowed — they land in not_surfaced_referenced but ok is still True).
        aligned_sig = sigs[0]
        rec_result = record_prior_alignment(
            turn_id,
            aligned_with=[aligned_sig],
            contradicted=["thread:synthetic-contra"],
            ignored=["insight:synthetic-ignored"],
            notes="integration-test",
            sovereign_root=root,
        )
        assert rec_result["ok"] is True, f"record_prior_alignment failed: {rec_result}"

        # 3. Summarise.
        summary = prior_alignment_summary(sovereign_root=root)
        totals = summary["totals"]
        ratios = summary["ratios"]

        assert totals["turns_with_alignment"] == 1
        assert totals["aligned"] == 1
        assert totals["contradicted"] == 1
        assert totals["ignored"] == 1

        # All three ratio components must sum to 1.0 (within float rounding).
        ratio_sum = ratios["alignment_rate"] + ratios["contradiction_rate"] + ratios["ignore_rate"]
        assert abs(ratio_sum - 1.0) < 1e-4, f"Ratios don't sum to 1.0: {ratio_sum}"

    def test_fabricated_turn_id_rejected(self, root: Path) -> None:
        """A turn_id NOT produced by prior_for_turn is rejected with
        ok=False, error='unknown_turn_id'."""
        fake_id = "00000000-dead-beef-cafe-000000000000"
        result = record_prior_alignment(
            fake_id,
            aligned_with=["thread:anything"],
            sovereign_root=root,
        )
        assert result["ok"] is False
        assert result["error"] == "unknown_turn_id"

    def test_window_filter_respected(self, root: Path) -> None:
        """Two prior_for_turn calls with alignment records: a `since` window
        that falls between them should count only the second.

        We write both priors via inject() (to populate priors_log so the
        turn_ids are valid), then write the two alignment records directly with
        fixed timestamps so the window boundary is unambiguous.
        """
        import json

        # ── Call 1: produce a valid turn_id ──
        priors = _make_priors(root, honks=_sharp_honk("h-w1"))
        r1 = priors.inject(domain_tags=[], dry_run=False)
        assert not r1["empty"]
        t1 = r1["turn_id"]
        s1 = r1["included_items"][0]

        # ── Call 2: produce a second valid turn_id ──
        # Use a fresh PerTurnPriors so the freshness window doesn't suppress h-w2.
        priors2 = _make_priors(root, honks=_sharp_honk("h-w2"))
        r2 = priors2.inject(domain_tags=[], dry_run=False)
        assert not r2["empty"]
        t2 = r2["turn_id"]
        s2 = r2["included_items"][0]

        # Write alignment records with fixed, unambiguous timestamps.
        ts_old = "2026-01-15T00:00:00+00:00"
        ts_new = "2026-04-20T00:00:00+00:00"
        between = "2026-03-01T00:00:00+00:00"

        align_path = root / "reflexive" / "alignment_log.jsonl"
        align_path.parent.mkdir(parents=True, exist_ok=True)
        for tid, ts, sig in [(t1, ts_old, s1), (t2, ts_new, s2)]:
            align_path.open("a", encoding="utf-8").write(
                json.dumps(
                    {
                        "turn_id": tid,
                        "timestamp": ts,
                        "aligned_with": [sig],
                        "contradicted": [],
                        "ignored": [],
                    }
                )
                + "\n"
            )

        # Without a window: both records visible.
        full = prior_alignment_summary(sovereign_root=root)
        assert full["totals"]["turns_with_alignment"] == 2

        # With since=between: only the second (April) record is in window.
        windowed = prior_alignment_summary(since=between, sovereign_root=root)
        assert windowed["totals"]["turns_with_alignment"] == 1
        assert windowed["totals"]["aligned"] == 1
