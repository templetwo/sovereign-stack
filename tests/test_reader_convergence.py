"""
Reader convergence (v1.7.x deferred item): every raw chronicle reader goes
through the shared memory.load_entries chokepoint, so the supersession
ledger is visible to ALL readers, not just recall_insights.

Discipline, one regression per converged reader:
  * ledger-free data  -> old behavior, byte-identical (exact-text asserts)
  * ledgered data     -> superseded entries INCLUDED (never drop) and the
                         annotation VISIBLE to the downstream consumer

Converged readers:
  1. metabolize hygiene scan   (metabolism._archive_test_artifacts_impl)
  2. metabolize detect / context_retrieve (metabolism._load_all_insights)
  3. retire_hypothesis scan    (metabolism.handle_metabolism_tool)
  4. synthesis daemon readers  (daemons.synthesis_daemon.read_recent_chronicle,
                                read_spanning_chronicle)
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sovereign_stack import metabolism
from sovereign_stack.daemons.synthesis_daemon import (
    build_prompt,
    read_recent_chronicle,
    read_spanning_chronicle,
)
from sovereign_stack.memory import load_entries
from sovereign_stack.metabolism import _archive_test_artifacts_impl, handle_metabolism_tool
from sovereign_stack.provenance import derive_claim_id

# ── Helpers ─────────────────────────────────────────────────────────────────


def _write_insight(
    chronicle: Path,
    domain: str,
    content: str,
    layer: str = "hypothesis",
    timestamp: str | None = None,
    session_id: str = "test-session",
    filename: str = "2026-06.jsonl",
) -> dict:
    """Append one raw insight line; return the record for claim-id derivation."""
    record = {
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "domain": domain,
        "content": content,
        "intensity": 0.5,
        "session_id": session_id,
        "layer": layer,
    }
    domain_dir = chronicle / "insights" / domain
    domain_dir.mkdir(parents=True, exist_ok=True)
    with open(domain_dir / filename, "a") as fh:
        fh.write(json.dumps(record) + "\n")
    return record


def _ledger_record(
    chronicle: Path,
    predecessor: dict,
    action: str = "supersede",
    successor_id: str | None = "f" * 64,
    carry: str | None = "what the predecessor still teaches",
) -> dict:
    """Append one supersession ledger record for a written entry."""
    record = {
        "action": action,
        "superseded_id": derive_claim_id(predecessor),
        "successor_id": successor_id,
        "carry_forward_summary": carry,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with open(chronicle / "supersessions.jsonl", "a") as fh:
        fh.write(json.dumps(record) + "\n")
    return record


@pytest.fixture
def chronicle(tmp_path: Path) -> Path:
    root = tmp_path / "chronicle"
    (root / "insights").mkdir(parents=True)
    return root


@pytest.fixture
def patched_metabolism(chronicle: Path, tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(metabolism, "CHRONICLE_DIR", chronicle)
    monkeypatch.setattr(metabolism, "METABOLISM_LOG", tmp_path / "metabolism_log.jsonl")
    return chronicle


def _call(name: str, arguments: dict) -> str:
    result = asyncio.run(handle_metabolism_tool(name, arguments))
    return result[0].text


# ── The chokepoint itself ───────────────────────────────────────────────────


class TestLoadEntries:
    def test_missing_root_returns_empty(self, tmp_path: Path):
        assert load_entries(tmp_path / "nope") == []

    def test_ledger_free_passthrough_is_byte_identical(self, chronicle: Path):
        # No ledger -> entries come back exactly as written, no derived keys.
        rec_a = _write_insight(chronicle, "alpha", "first claim")
        rec_b = _write_insight(chronicle, "beta", "second claim")
        assert load_entries(chronicle) == [rec_a, rec_b]

    def test_with_sources_adds_exactly_the_source_markers(self, chronicle: Path):
        rec = _write_insight(chronicle, "alpha", "first claim")
        [entry] = load_entries(chronicle, with_sources=True)
        assert entry == {
            **rec,
            "_domain_dir": "alpha",
            "_file": str(chronicle / "insights" / "alpha" / "2026-06.jsonl"),
        }

    def test_data_gated_annotation_never_drops(self, chronicle: Path):
        rec_a = _write_insight(chronicle, "alpha", "superseded claim")
        _write_insight(chronicle, "beta", "live claim")
        ledger = _ledger_record(chronicle, rec_a)
        entries = load_entries(chronicle)
        assert len(entries) == 2  # annotate, never drop
        annotated = next(e for e in entries if e["content"] == "superseded claim")
        live = next(e for e in entries if e["content"] == "live claim")
        assert annotated["_superseded_by"] == ledger["successor_id"]
        assert annotated["_carry_forward_summary"] == ledger["carry_forward_summary"]
        assert "_superseded_by" not in live

    def test_revoked_supersession_restores_passthrough(self, chronicle: Path):
        rec = _write_insight(chronicle, "alpha", "contested claim")
        _ledger_record(chronicle, rec)
        _ledger_record(chronicle, rec, action="revoke", successor_id=None, carry=None)
        assert load_entries(chronicle) == [rec]

    def test_quarantine_excluded(self, chronicle: Path):
        rec = _write_insight(chronicle, "alpha", "live claim")
        qdir = chronicle / "_quarantine_2026" / "alpha"
        qdir.mkdir(parents=True)
        with open(qdir / "q.jsonl", "a") as fh:
            fh.write(json.dumps({**rec, "content": "quarantined claim"}) + "\n")
        assert load_entries(chronicle) == [rec]


# ── Reader: metabolize detect (via _load_all_insights) ──────────────────────


class TestMetabolizeDetect:
    def _seed_contradiction(self, chronicle: Path) -> tuple[dict, dict]:
        now = datetime.now(timezone.utc).isoformat()
        hyp = _write_insight(
            chronicle, "dom-h", "alpha beta gamma delta", layer="hypothesis", timestamp=now
        )
        gt = _write_insight(
            chronicle, "dom-g", "alpha beta gamma delta", layer="ground_truth", timestamp=now
        )
        return hyp, gt

    def _expected(self, hyp_mark: str = "", gt_mark: str = "") -> str:
        # Exact bytes of the pre-convergence renderer (marks empty = old text).
        return (
            "🫀 Metabolism Cycle Complete\n\n"
            "Chronicle: 2 insights (1 ground truth, 1 hypotheses)\n"
            "Open threads: 0\n\n"
            "⚠️ 1 potential contradiction(s):\n"
            f"  Hyp{hyp_mark} [dom-h]: alpha beta gamma delta\n"
            f"  vs GT{gt_mark} [dom-g]: alpha beta gamma delta\n"
            "  Overlap: 1.0\n\n"
        )

    def test_ledger_free_output_byte_identical(self, patched_metabolism: Path):
        self._seed_contradiction(patched_metabolism)
        assert _call("metabolize", {"action": "detect"}) == self._expected()

    def test_ledgered_superseded_included_and_marked(self, patched_metabolism: Path):
        hyp, _gt = self._seed_contradiction(patched_metabolism)
        _ledger_record(patched_metabolism, hyp)
        # Same stats, same contradiction (never drop) — only the mark differs.
        assert _call("metabolize", {"action": "detect"}) == self._expected(hyp_mark=" (superseded)")

    def test_ledgered_stale_hypothesis_marked(self, patched_metabolism: Path):
        old_ts = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        hyp = _write_insight(patched_metabolism, "dom-old", "ancient idea", timestamp=old_ts)
        _ledger_record(patched_metabolism, hyp)
        text = _call("metabolize", {"action": "detect", "max_age_days": 30})
        assert "  [dom-old] (superseded) ancient idea (90d)\n" in text


# ── Reader: context_retrieve (via _load_all_insights) ───────────────────────


class TestContextRetrieve:
    OLD_TS = "2026-01-01T00:00:00+00:00"  # >10 days old -> recency bonus 0

    def test_ledger_free_output_byte_identical(self, patched_metabolism: Path):
        _write_insight(patched_metabolism, "dom", "alpha beta", timestamp=self.OLD_TS)
        text = _call("context_retrieve", {"current_focus": "alpha"})
        assert text == (
            "🎯 Context-Aware Retrieval (focus: alpha)\n\n"
            "  [hypothesis] (dom) score=1.0\n"
            "  alpha beta\n\n"
        )

    def test_ledgered_superseded_retrieved_and_marked(self, patched_metabolism: Path):
        rec = _write_insight(patched_metabolism, "dom", "alpha beta", timestamp=self.OLD_TS)
        _ledger_record(patched_metabolism, rec)
        text = _call("context_retrieve", {"current_focus": "alpha"})
        # Still retrieved (never drop), same score — visibly marked.
        assert text == (
            "🎯 Context-Aware Retrieval (focus: alpha)\n\n"
            "  [hypothesis] (superseded) (dom) score=1.0\n"
            "  alpha beta\n\n"
        )


# ── Reader: retire_hypothesis scan ──────────────────────────────────────────


class TestRetireScan:
    ARGS = {
        "domain": "",
        "content_fragment": "obsolete claim",
        "reason": "cleanup",
        "replaced_by": "new truth",
    }

    def test_ledger_free_behavior_byte_identical(self, patched_metabolism: Path):
        chronicle = patched_metabolism
        _write_insight(chronicle, "dom-a", "obsolete claim one")
        _write_insight(chronicle, "dom-b", "obsolete claim two")
        # Corrupt line in a matched file must survive the rewrite verbatim.
        with open(chronicle / "insights" / "dom-a" / "2026-06.jsonl", "a") as fh:
            fh.write("{not valid json\n")
        _write_insight(chronicle, "dom-c", "unrelated fact", layer="ground_truth")
        untouched = chronicle / "insights" / "dom-c" / "2026-06.jsonl"
        untouched_bytes = untouched.read_bytes()

        text = _call("retire_hypothesis", self.ARGS)
        assert text == (
            "📦 Hypothesis retired: 'obsolete claim...'\n"
            "  Reason: cleanup\n"
            "  Replaced by: new truth"
        )
        # Both matches mutated in place.
        for domain in ("dom-a", "dom-b"):
            lines = (chronicle / "insights" / domain / "2026-06.jsonl").read_text().splitlines()
            entry = json.loads(lines[0])
            assert entry["layer"] == "retired"
            assert entry["retired_reason"] == "cleanup"
            assert entry["retired_by"] == "new truth"
        # Corrupt line preserved verbatim; non-matching file untouched bytes.
        assert (chronicle / "insights" / "dom-a" / "2026-06.jsonl").read_text().splitlines()[
            -1
        ] == "{not valid json"
        assert untouched.read_bytes() == untouched_bytes
        # One retire ledger record per retired entry (v1.7.0 behavior, kept).
        records = [
            json.loads(line)
            for line in (chronicle / "supersessions.jsonl").read_text().splitlines()
        ]
        assert len(records) == 2
        assert all(r["action"] == "retire" and r["successor_id"] is None for r in records)

    def test_no_match_message_unchanged(self, patched_metabolism: Path):
        _write_insight(patched_metabolism, "dom-a", "something else")
        text = _call("retire_hypothesis", self.ARGS)
        assert text == "No matching hypothesis found for 'obsolete claim'"

    def test_ledgered_match_still_retired_with_visible_note(self, patched_metabolism: Path):
        chronicle = patched_metabolism
        rec_a = _write_insight(chronicle, "dom-a", "obsolete claim one")
        _write_insight(chronicle, "dom-b", "obsolete claim two")
        _ledger_record(chronicle, rec_a)

        text = _call("retire_hypothesis", self.ARGS)
        # Selection identical: both retired; the ledger state is surfaced.
        assert text == (
            "📦 Hypothesis retired: 'obsolete claim...'\n"
            "  Reason: cleanup\n"
            "  Replaced by: new truth\n"
            "  Note: 1 matched entry was already superseded in the ledger "
            "(retired anyway; latest action wins)"
        )
        for domain in ("dom-a", "dom-b"):
            lines = (chronicle / "insights" / domain / "2026-06.jsonl").read_text().splitlines()
            assert json.loads(lines[0])["layer"] == "retired"


# ── Reader: metabolize hygiene scan (_archive_test_artifacts_impl) ──────────


class TestArchiveTestArtifactsScan:
    def _seed(self, chronicle: Path) -> tuple[Path, Path, bytes]:
        artifact_file = chronicle / "insights" / "dom-a" / "2026-06.jsonl"
        _write_insight(chronicle, "dom-a", "STRESS TEST artifact entry")
        _write_insight(chronicle, "dom-a", "real observation")
        with open(artifact_file, "a") as fh:
            fh.write("{corrupt line\n")
        _write_insight(chronicle, "dom-b", "clean entry")
        clean_file = chronicle / "insights" / "dom-b" / "2026-06.jsonl"
        return artifact_file, clean_file, clean_file.read_bytes()

    def test_ledger_free_behavior_identical(self, chronicle: Path):
        artifact_file, clean_file, clean_bytes = self._seed(chronicle)
        result = _archive_test_artifacts_impl(chronicle)
        assert result["archived"] == 1
        assert result["files_modified"] == 1
        assert result["domains_removed"] == 0
        # Kept lines preserved, corrupt line verbatim, clean file untouched.
        kept = artifact_file.read_text().splitlines()
        assert json.loads(kept[0])["content"] == "real observation"
        assert kept[1] == "{corrupt line"
        assert clean_file.read_bytes() == clean_bytes
        # Archive sidecar carries the original entry + the archival markers.
        [archived] = [
            json.loads(line)
            for line in (chronicle / ".archive_test_artifacts" / "dom-a__2026-06.jsonl")
            .read_text()
            .splitlines()
        ]
        assert archived["content"] == "STRESS TEST artifact entry"
        assert archived["_archived_reason"] == "test_artifact_pattern"

    def test_ledgered_scan_never_persists_read_annotations(self, chronicle: Path):
        artifact_file, _clean_file, _clean_bytes = self._seed(chronicle)
        artifact_rec = json.loads(artifact_file.read_text().splitlines()[0])
        _ledger_record(chronicle, artifact_rec)
        result = _archive_test_artifacts_impl(chronicle)
        assert result["archived"] == 1  # selection unchanged by ledger state
        [archived] = [
            json.loads(line)
            for line in (chronicle / ".archive_test_artifacts" / "dom-a__2026-06.jsonl")
            .read_text()
            .splitlines()
        ]
        # Read-derived keys must never reach the persisted sidecar.
        assert "_superseded_by" not in archived
        assert "_carry_forward_summary" not in archived
        assert "_file" not in archived
        assert "_domain_dir" not in archived

    def test_preexisting_empty_domain_still_swept(self, chronicle: Path):
        (chronicle / "insights" / "empty-dom").mkdir()
        result = _archive_test_artifacts_impl(chronicle)
        assert result == {
            "archived": 0,
            "files_modified": 0,
            "domains_removed": 1,
            "archive_dir": str(chronicle / ".archive_test_artifacts"),
        }


# ── Reader: synthesis daemon chronicle readers ──────────────────────────────


class TestSynthesisReaders:
    def _seed(self, chronicle: Path, hours_ago: float = 0.5) -> dict:
        ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
        return _write_insight(chronicle, "test-domain", "fresh content", timestamp=ts)

    def test_recent_ledger_free_projection_byte_identical(self, chronicle: Path):
        rec = self._seed(chronicle)
        entries = read_recent_chronicle(chronicle_root=chronicle / "insights")
        assert entries == [
            {
                "timestamp": rec["timestamp"],
                "domain": "test-domain",
                "layer": "hypothesis",
                "content": "fresh content",
                "tag": "test-domain",
                "session_id": "test-session",
                "ts_epoch": datetime.fromisoformat(rec["timestamp"]).timestamp(),
            }
        ]

    def test_recent_ledgered_included_with_annotation(self, chronicle: Path):
        rec = self._seed(chronicle)
        ledger = _ledger_record(chronicle, rec)
        [entry] = read_recent_chronicle(chronicle_root=chronicle / "insights")
        assert entry["content"] == "fresh content"  # never drop
        assert entry["_superseded_by"] == ledger["successor_id"]

    def test_spanning_ledgered_included_with_annotation(self, chronicle: Path):
        rec = self._seed(chronicle, hours_ago=24)
        ledger = _ledger_record(chronicle, rec)
        [entry] = read_spanning_chronicle(
            chronicle_root=chronicle / "insights", span_weeks=2, entries_per_week=2
        )
        assert entry["_superseded_by"] == ledger["successor_id"]

    def test_prompt_marks_superseded_layer(self, chronicle: Path):
        rec = self._seed(chronicle)
        _ledger_record(chronicle, rec)
        entries = read_recent_chronicle(chronicle_root=chronicle / "insights")
        prompt = build_prompt(entries)
        assert "LAYER: hypothesis (superseded)" in prompt

    def test_prompt_unmarked_without_ledger(self, chronicle: Path):
        self._seed(chronicle)
        entries = read_recent_chronicle(chronicle_root=chronicle / "insights")
        prompt = build_prompt(entries)
        assert "LAYER: hypothesis\n" in prompt
        assert "(superseded)" not in prompt
