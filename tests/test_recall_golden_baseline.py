"""
Golden-baseline regression for ExperientialMemory.recall_insights.

Captures the EXACT output of recall_insights across the full filter matrix
against a fixture chronicle, asserting list equality. Written BEFORE the
Phase-0 shared-finalizer extraction (protected-source build) so the
refactor can be proven behavior-preserving: this file must pass
byte-identically before and after the convergence.

The matrix deliberately exercises recall's intentional quirks that the
refactor must NOT disturb:
  * domain=<nonexistent> -> full-search fallback (the domain-dir-exists
    branch: a missing domain searches ALL non-dot dirs, it does not
    return empty).
  * partial end_date inclusive upper bound (the ts.startswith(end_date)
    line: a partial "2026-06-12" must keep that whole day).
  * order in {newest, oldest, relevance}, relevance with AND without a
    query (relevance falls back to newest when no query).
  * exclude_superseded=True WITH a non-empty supersessions ledger (the
    partition branch) AND the default annotate-not-drop branch.
  * since_last_reflection (resolves to the last reflection-domain entry).
  * with_ids (derived claim_id stamped on each entry).
  * min_intensity, layer_filter, domain_contains, start_date, limit.

These assertions compare against recall's own output recomputed from the
fixture, so the test is self-describing: it pins the SHAPE and CONTENT of
every entry recall returns, in order, including the read-derived
annotation keys.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sovereign_stack.memory import ExperientialMemory
from sovereign_stack.provenance import derive_claim_id

# ── Fixture chronicle ────────────────────────────────────────────────────────


def _write_entry(
    chronicle: Path,
    domain: str,
    content: str,
    *,
    timestamp: str,
    intensity: float = 0.5,
    layer: str = "hypothesis",
    session_id: str = "golden-session",
    filename: str = "golden.jsonl",
    extra: dict | None = None,
) -> dict:
    """Append one raw insight line verbatim; return the exact stored record."""
    record = {
        "timestamp": timestamp,
        "domain": domain,
        "content": content,
        "intensity": intensity,
        "layer": layer,
        "session_id": session_id,
    }
    if extra:
        record.update(extra)
    domain_dir = chronicle / "insights" / domain
    domain_dir.mkdir(parents=True, exist_ok=True)
    with open(domain_dir / filename, "a") as fh:
        fh.write(json.dumps(record) + "\n")
    return record


@pytest.fixture
def fixture_chronicle(tmp_path: Path) -> dict:
    """
    A deterministic chronicle covering the recall filter matrix.

    Returns a dict of {key: stored_record} so individual tests can build
    their expected lists by referencing the exact bytes that were written
    (no re-derivation drift).
    """
    root = tmp_path / "chronicle"
    (root / "insights").mkdir(parents=True)

    recs: dict[str, dict] = {}

    # ground_truth, high intensity, early.
    recs["alpha_gt"] = _write_entry(
        root,
        "alpha",
        "alpha foundation truth about routing and ledgers",
        timestamp="2026-06-01T08:00:00+00:00",
        intensity=0.9,
        layer="ground_truth",
    )
    # hypothesis in alpha, mid window, mentions "routing".
    recs["alpha_hyp"] = _write_entry(
        root,
        "alpha",
        "alpha hypothesis exploring routing options further",
        timestamp="2026-06-05T10:00:00+00:00",
        intensity=0.4,
        layer="hypothesis",
    )
    # beta domain, low intensity.
    recs["beta_low"] = _write_entry(
        root,
        "beta",
        "beta minor note with low significance",
        timestamp="2026-06-07T12:00:00+00:00",
        intensity=0.2,
        layer="hypothesis",
    )
    # compound domain dir to exercise domain_contains.
    recs["compound"] = _write_entry(
        root,
        "frank-jones,greene-street",
        "compound domain entry about routing through greene street",
        timestamp="2026-06-09T09:00:00+00:00",
        intensity=0.6,
        layer="hypothesis",
    )
    # entry on the partial-end-date boundary day (2026-06-12) with a time
    # component AFTER midnight — must survive an inclusive partial end_date.
    recs["boundary"] = _write_entry(
        root,
        "gamma",
        "gamma boundary-day entry with routing keyword twice routing",
        timestamp="2026-06-12T23:30:00+00:00",
        intensity=0.7,
        layer="ground_truth",
    )
    # a reflection-domain entry, latest, drives since_last_reflection.
    recs["reflection"] = _write_entry(
        root,
        "reflection",
        "reflection marker closing a session",
        timestamp="2026-06-13T06:00:00+00:00",
        intensity=0.5,
        layer="hypothesis",
    )
    # a late entry AFTER the reflection, to be surfaced by
    # since_last_reflection (timestamp strictly greater than the marker).
    recs["post_reflection"] = _write_entry(
        root,
        "delta",
        "delta entry recorded after the last reflection about routing",
        timestamp="2026-06-14T11:00:00+00:00",
        intensity=0.8,
        layer="ground_truth",
    )
    return {"root": root, "recs": recs}


def _memory(chronicle_info: dict) -> ExperientialMemory:
    return ExperientialMemory(root=str(chronicle_info["root"]))


def _append_supersession(root: Path, predecessor: dict, successor_id: str, carry: str) -> dict:
    record = {
        "action": "supersede",
        "timestamp": "2026-06-15T00:00:00+00:00",
        "superseded_id": derive_claim_id(predecessor),
        "successor_id": successor_id,
        "carry_forward_summary": carry,
        "reason": "",
        "by": "golden-test",
    }
    with open(root / "supersessions.jsonl", "a") as fh:
        fh.write(json.dumps(record) + "\n")
    return record


# ── Matrix: no-ledger paths (annotation must be invisible) ───────────────────


class TestRecallGoldenNoLedger:
    def test_default_all_newest(self, fixture_chronicle):
        mem = _memory(fixture_chronicle)
        recs = fixture_chronicle["recs"]
        result = mem.recall_insights()
        # newest-first across all non-dot domains, limit defaults to 10.
        expected = [
            recs["post_reflection"],
            recs["reflection"],
            recs["boundary"],
            recs["compound"],
            recs["beta_low"],
            recs["alpha_hyp"],
            recs["alpha_gt"],
        ]
        assert result == expected

    def test_limit(self, fixture_chronicle):
        mem = _memory(fixture_chronicle)
        recs = fixture_chronicle["recs"]
        result = mem.recall_insights(limit=3)
        assert result == [recs["post_reflection"], recs["reflection"], recs["boundary"]]

    def test_order_oldest(self, fixture_chronicle):
        mem = _memory(fixture_chronicle)
        recs = fixture_chronicle["recs"]
        result = mem.recall_insights(order="oldest")
        expected = [
            recs["alpha_gt"],
            recs["alpha_hyp"],
            recs["beta_low"],
            recs["compound"],
            recs["boundary"],
            recs["reflection"],
            recs["post_reflection"],
        ]
        assert result == expected

    def test_min_intensity(self, fixture_chronicle):
        mem = _memory(fixture_chronicle)
        recs = fixture_chronicle["recs"]
        result = mem.recall_insights(min_intensity=0.7)
        # >= 0.7: post_reflection(0.8), boundary(0.7), alpha_gt(0.9)
        assert result == [recs["post_reflection"], recs["boundary"], recs["alpha_gt"]]

    def test_layer_filter_ground_truth(self, fixture_chronicle):
        mem = _memory(fixture_chronicle)
        recs = fixture_chronicle["recs"]
        result = mem.recall_insights(layer_filter="ground_truth")
        assert result == [recs["post_reflection"], recs["boundary"], recs["alpha_gt"]]

    def test_domain_exact(self, fixture_chronicle):
        mem = _memory(fixture_chronicle)
        recs = fixture_chronicle["recs"]
        result = mem.recall_insights(domain="alpha")
        assert result == [recs["alpha_hyp"], recs["alpha_gt"]]

    def test_domain_nonexistent_falls_back_to_full_search(self, fixture_chronicle):
        """The intentional quirk: a missing domain searches ALL non-dot dirs."""
        mem = _memory(fixture_chronicle)
        recs = fixture_chronicle["recs"]
        result = mem.recall_insights(domain="does-not-exist")
        expected = [
            recs["post_reflection"],
            recs["reflection"],
            recs["boundary"],
            recs["compound"],
            recs["beta_low"],
            recs["alpha_hyp"],
            recs["alpha_gt"],
        ]
        assert result == expected

    def test_domain_contains(self, fixture_chronicle):
        mem = _memory(fixture_chronicle)
        recs = fixture_chronicle["recs"]
        result = mem.recall_insights(domain_contains="frank-jones")
        assert result == [recs["compound"]]

    def test_start_date(self, fixture_chronicle):
        mem = _memory(fixture_chronicle)
        recs = fixture_chronicle["recs"]
        result = mem.recall_insights(start_date="2026-06-12T00:00:00+00:00")
        assert result == [recs["post_reflection"], recs["reflection"], recs["boundary"]]

    def test_partial_end_date_inclusive_upper_bound(self, fixture_chronicle):
        """The boundary entry is at 2026-06-12T23:30 — a partial end_date of
        '2026-06-12' must KEEP it (the ts.startswith(end_date) quirk)."""
        mem = _memory(fixture_chronicle)
        recs = fixture_chronicle["recs"]
        result = mem.recall_insights(end_date="2026-06-12")
        expected = [
            recs["boundary"],
            recs["compound"],
            recs["beta_low"],
            recs["alpha_hyp"],
            recs["alpha_gt"],
        ]
        assert result == expected

    def test_query_substring(self, fixture_chronicle):
        mem = _memory(fixture_chronicle)
        recs = fixture_chronicle["recs"]
        result = mem.recall_insights(query="routing")
        # Entries whose content/domain contains "routing", newest first.
        expected = [
            recs["post_reflection"],
            recs["boundary"],
            recs["compound"],
            recs["alpha_hyp"],
            recs["alpha_gt"],
        ]
        assert result == expected

    def test_order_relevance_with_query(self, fixture_chronicle):
        """boundary mentions 'routing' twice but query is a single term, so
        match_count is 1 for every hit — relevance then falls to the
        timestamp-desc tiebreak. The _match_count key must be stripped."""
        mem = _memory(fixture_chronicle)
        recs = fixture_chronicle["recs"]
        result = mem.recall_insights(query="routing", order="relevance")
        for entry in result:
            assert "_match_count" not in entry
        expected = [
            recs["post_reflection"],
            recs["boundary"],
            recs["compound"],
            recs["alpha_hyp"],
            recs["alpha_gt"],
        ]
        assert result == expected

    def test_order_relevance_no_query_falls_back_to_newest(self, fixture_chronicle):
        mem = _memory(fixture_chronicle)
        recs = fixture_chronicle["recs"]
        result = mem.recall_insights(order="relevance")
        expected = [
            recs["post_reflection"],
            recs["reflection"],
            recs["boundary"],
            recs["compound"],
            recs["beta_low"],
            recs["alpha_hyp"],
            recs["alpha_gt"],
        ]
        assert result == expected

    def test_since_last_reflection(self, fixture_chronicle):
        """Resolves start_date to the latest reflection-domain timestamp
        (2026-06-13T06:00) and keeps entries at-or-after it."""
        mem = _memory(fixture_chronicle)
        recs = fixture_chronicle["recs"]
        result = mem.recall_insights(since_last_reflection=True)
        # >= 2026-06-13T06:00:00: post_reflection(06-14) and the reflection
        # marker itself (== bound, inclusive).
        assert result == [recs["post_reflection"], recs["reflection"]]

    def test_with_ids(self, fixture_chronicle):
        mem = _memory(fixture_chronicle)
        recs = fixture_chronicle["recs"]
        result = mem.recall_insights(domain="alpha", with_ids=True)
        expected = [
            {**recs["alpha_hyp"], "claim_id": derive_claim_id(recs["alpha_hyp"])},
            {**recs["alpha_gt"], "claim_id": derive_claim_id(recs["alpha_gt"])},
        ]
        assert result == expected

    def test_combined_filters(self, fixture_chronicle):
        """domain + min_intensity + layer + order=oldest together."""
        mem = _memory(fixture_chronicle)
        recs = fixture_chronicle["recs"]
        result = mem.recall_insights(
            min_intensity=0.5,
            layer_filter="ground_truth",
            order="oldest",
        )
        assert result == [recs["alpha_gt"], recs["boundary"], recs["post_reflection"]]


# ── Matrix: ledgered paths (annotation + partition must be exact) ────────────


class TestRecallGoldenWithLedger:
    def _supersede(self, fixture_chronicle, key: str, carry: str) -> tuple[dict, str]:
        root = fixture_chronicle["root"]
        rec = fixture_chronicle["recs"][key]
        successor_id = "a" * 64
        _append_supersession(root, rec, successor_id, carry)
        return rec, successor_id

    def test_annotate_not_drop_default(self, fixture_chronicle):
        """Default exclude_superseded=False: the superseded entry is RETURNED,
        annotated in place with _superseded_by and _carry_forward_summary."""
        rec, successor_id = self._supersede(
            fixture_chronicle, "alpha_gt", "still the routing foundation"
        )
        mem = _memory(fixture_chronicle)
        recs = fixture_chronicle["recs"]
        result = mem.recall_insights(domain="alpha")
        annotated = {
            **rec,
            "_superseded_by": successor_id,
            "_carry_forward_summary": "still the routing foundation",
        }
        assert result == [recs["alpha_hyp"], annotated]

    def test_exclude_superseded_partition(self, fixture_chronicle):
        """exclude_superseded=True WITH a non-empty ledger: the superseded
        entry is DROPPED pre-limit (the partition branch)."""
        self._supersede(fixture_chronicle, "alpha_gt", "still the routing foundation")
        mem = _memory(fixture_chronicle)
        recs = fixture_chronicle["recs"]
        result = mem.recall_insights(domain="alpha", exclude_superseded=True)
        assert result == [recs["alpha_hyp"]]

    def test_exclude_superseded_fills_limit_from_live(self, fixture_chronicle):
        """With exclude_superseded the limit counts live entries only —
        a successor/other live entry fills the freed slot."""
        self._supersede(fixture_chronicle, "post_reflection", "carry note")
        mem = _memory(fixture_chronicle)
        recs = fixture_chronicle["recs"]
        result = mem.recall_insights(limit=2, exclude_superseded=True)
        # post_reflection (newest) is dropped; the next two live newest are
        # reflection then boundary.
        assert result == [recs["reflection"], recs["boundary"]]

    def test_annotate_with_ids_together(self, fixture_chronicle):
        """Annotation and with_ids compose: claim_id derives from the
        ORIGINAL identity triple, unaffected by the _superseded_by key."""
        rec, successor_id = self._supersede(fixture_chronicle, "alpha_gt", "carry")
        mem = _memory(fixture_chronicle)
        recs = fixture_chronicle["recs"]
        result = mem.recall_insights(domain="alpha", with_ids=True)
        annotated_gt = {
            **rec,
            "_superseded_by": successor_id,
            "_carry_forward_summary": "carry",
            "claim_id": derive_claim_id(rec),
        }
        expected = [
            {**recs["alpha_hyp"], "claim_id": derive_claim_id(recs["alpha_hyp"])},
            annotated_gt,
        ]
        assert result == expected
