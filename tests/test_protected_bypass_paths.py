"""
Protected-source layer — §5.4 bypass-path closure tests.

Three read paths bypassed the finalize_read / enforce_coupling chokepoint
and would have returned protected content DECOUPLED:

  A. provenance.resolve_claim (feeds inspect_claim / walk_lineage)
  B. dashboard.read_chronicle_tail (the dashboard feed / snapshot)
  C. seasons.season_review's direct iter_chronicle_entries scan

The invariant differs by surface SHAPE (per the 2026-06-23 build review):
  - FULL-CONTENT surface (inspect_claim's `entry`)  -> COUPLE (content +
    stakes in the same payload, fail-closed to the withheld sentinel).
  - PREVIEW / TRUNCATING surface (lineage 120-char preview, dashboard
    80-char feed slice, season_review digest previews) -> WITHHOLD, because
    a slice of coupled content re-decouples (the full stakes can't ride a
    preview). The consent gate is the only full-content path.

The verifier for every path is the SAME as test_audit_passes_on_real_scribe_output:
run audit_decoupling on the REAL rendered output string and expect [] (no
leak). Asserting "the object is a sentinel" is not enough — the invariant is
about the string that reaches a model.

Each path also has an ordinary-record test proving non-protected behavior is
unchanged (the empty-fold fast path / no false withholding).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sovereign_stack import dashboard, provenance, seasons
from sovereign_stack.memory import ExperientialMemory
from sovereign_stack.protected import (
    PROTECTED_PREVIEW_NOTICE,
    ProtectedStakesUnavailable,
    audit_decoupling,
    designate_protected,
)
from sovereign_stack.provenance import derive_claim_id, walk_lineage
from sovereign_stack.provenance_tools import inspect_claim

PROTECTED_CONTENT = "the bypass-path protected content that must never travel decoupled"
STAKES_PROSE = (
    "This is a lived loss the human carries. Recalled, the weight arrives with "
    "the words. Reducing it to a citation is the wound. Hold it as experience."
)
ORDINARY_CONTENT = "an ordinary claim that is never protected and reads normally"


@pytest.fixture
def mem(tmp_path: Path) -> ExperientialMemory:
    return ExperientialMemory(root=str(tmp_path / "chronicle"))


def _seed_ordinary(mem: ExperientialMemory) -> dict:
    path = mem.record_insight(
        domain="ordinary", content=ORDINARY_CONTENT, intensity=0.6, layer="hypothesis"
    )
    return json.loads(Path(path).read_text().splitlines()[-1])


def _seed_and_protect(mem: ExperientialMemory) -> dict:
    path = mem.record_insight(
        domain="personal",
        content=PROTECTED_CONTENT,
        intensity=0.9,
        layer="ground_truth",
    )
    prot = json.loads(Path(path).read_text().splitlines()[-1])
    archive = mem.archive_exchange(
        content=STAKES_PROSE,
        source="human-relay",
        descriptor="stakes",
        vector_id="protected_stakes",
    )
    designate_protected(
        claim_ref=derive_claim_id(prot),
        stakes_archive_id=archive["archive_id"],
        designated_by="Anthony",
        chronicle_root=str(mem.root),
    )
    return {"record": prot, "archive_id": archive["archive_id"]}


def _break_stakes(mem: ExperientialMemory, archive_id: str) -> None:
    blob = Path(next(r for r in mem._read_archive_index() if r["archive_id"] == archive_id)["path"])
    blob.unlink()


# ── A. provenance.resolve_claim / inspect_claim / walk_lineage ───────────────


class TestResolveClaimPath:
    def test_resolve_claim_bare_by_default(self, mem):
        """The internal/default read stays BARE — the audit, designation and
        supersedes re-derivation all need the real content/id."""
        seeded = _seed_and_protect(mem)
        entry, _f, _loc = provenance.resolve_claim(derive_claim_id(seeded["record"]), mem.root)
        assert entry["content"] == PROTECTED_CONTENT  # bare, by design
        assert not isinstance(entry, ProtectedStakesUnavailable)

    def test_resolve_claim_couple_attaches_stakes(self, mem):
        seeded = _seed_and_protect(mem)
        entry, _f, _loc = provenance.resolve_claim(
            derive_claim_id(seeded["record"]), mem.root, couple=True
        )
        assert entry["content"] == PROTECTED_CONTENT
        assert entry["_protected"] is True
        assert entry["_stakes"] == STAKES_PROSE
        assert entry["_stakes_verdict"] == "verified"

    def test_resolve_claim_couple_fails_closed(self, mem):
        seeded = _seed_and_protect(mem)
        _break_stakes(mem, seeded["archive_id"])
        entry, _f, _loc = provenance.resolve_claim(
            derive_claim_id(seeded["record"]), mem.root, couple=True
        )
        assert isinstance(entry, ProtectedStakesUnavailable)
        assert PROTECTED_CONTENT not in entry["content"]

    def test_inspect_claim_entry_is_coupled(self, mem):
        seeded = _seed_and_protect(mem)
        report = inspect_claim(derive_claim_id(seeded["record"]), chronicle_root=mem.root)
        assert report["found"] is True
        # The surfaced entry carries content AND stakes inseparably.
        assert report["entry"]["content"] == PROTECTED_CONTENT
        assert report["entry"]["_stakes"] == STAKES_PROSE
        # And the rendered report carries no decoupled leak.
        assert audit_decoupling(json.dumps(report), mem.root) == []

    def test_inspect_claim_entry_withheld_when_stakes_broken(self, mem):
        seeded = _seed_and_protect(mem)
        _break_stakes(mem, seeded["archive_id"])
        report = inspect_claim(derive_claim_id(seeded["record"]), chronicle_root=mem.root)
        assert PROTECTED_CONTENT not in json.dumps(report)
        assert "content withheld" in report["entry"]["content"]
        assert audit_decoupling(json.dumps(report), mem.root) == []

    def test_lineage_preview_withholds_protected_content(self, mem):
        """A lineage row is a 120-char PREVIEW surface; a protected record's
        preview must be the placeholder, never a slice of the content."""
        seeded = _seed_and_protect(mem)
        full_id = derive_claim_id(seeded["record"])
        fold = provenance.fold_supersessions(
            provenance.load_supersessions(mem.root / "supersessions.jsonl")
        )
        rows = walk_lineage(full_id, fold, mem.root)
        self_row = next(r for r in rows if r["role"] == "self")
        assert self_row["content_preview"] == PROTECTED_PREVIEW_NOTICE
        assert PROTECTED_CONTENT not in json.dumps(rows)
        assert audit_decoupling(json.dumps(rows), mem.root) == []

    def test_inspect_claim_ordinary_unchanged(self, mem):
        ordinary = _seed_ordinary(mem)
        report = inspect_claim(derive_claim_id(ordinary), chronicle_root=mem.root)
        assert report["entry"]["content"] == ORDINARY_CONTENT
        assert "_stakes" not in report["entry"]
        assert "_protected" not in report["entry"]


# ── B. dashboard.read_chronicle_tail ─────────────────────────────────────────


class TestDashboardTailPath:
    def _insight_file(self, mem: ExperientialMemory) -> Path:
        files = sorted((mem.root / "insights").rglob("*.jsonl"))
        assert files, "expected an insights jsonl on disk"
        return files[0]

    def test_tail_withholds_protected_content(self, mem):
        _seed_and_protect(mem)
        f = self._insight_file(mem)
        tail = dashboard.read_chronicle_tail(f, mem.root)
        assert tail is not None
        assert tail["content"] == PROTECTED_PREVIEW_NOTICE
        assert PROTECTED_CONTENT not in json.dumps(tail)
        # The real feed slice the dashboard emits.
        feed_line = f"[{tail.get('layer', '?')}] {(tail.get('content') or '')[:80]}…"
        assert audit_decoupling(feed_line, mem.root) == []

    def test_tail_without_root_is_legacy_bare(self, mem):
        """No chronicle_root supplied -> legacy behavior (the non-insight
        callers for open_threads/halts that carry no insight content)."""
        _seed_and_protect(mem)
        f = self._insight_file(mem)
        tail = dashboard.read_chronicle_tail(f)
        assert tail["content"] == PROTECTED_CONTENT  # no gate without root

    def test_tail_ordinary_unchanged(self, mem):
        _seed_ordinary(mem)
        f = self._insight_file(mem)
        tail = dashboard.read_chronicle_tail(f, mem.root)
        assert tail["content"] == ORDINARY_CONTENT
        assert "_protected" not in tail

    def test_snapshot_insight_preview_withholds_protected(self, mem):
        _seed_and_protect(mem)
        snap = dashboard.collect_latest_entries(mem.root.parent)
        assert snap["insight"] is not None
        assert PROTECTED_CONTENT not in json.dumps(snap)
        assert PROTECTED_PREVIEW_NOTICE in snap["insight"]["preview"]


# ── C. seasons.season_review ─────────────────────────────────────────────────


class TestSeasonReviewPath:
    def test_season_review_no_protected_leak(self, mem):
        """A PROTECTED legacy-marker record is filtered out of the content
        scans; the rendered digest carries none of its content.

        Here the protected record carries the CORRECTED marker and shares high
        token-overlap with an ordinary partner. BEFORE the fix the supersession
        candidate scan would render the protected record's content in its
        preview line. AFTER the fix the protected record is filtered out, so it
        never appears in any candidate pair and its content never reaches the
        digest string. The verifier is audit_decoupling on the real digest."""
        # A distinctive protected legacy-marker record.
        marker_content = (
            "CORRECTED: the protected lived record of a private grief that the "
            "human carries and that must never be reduced to a citation"
        )
        path = mem.record_insight(
            domain="personal", content=marker_content, intensity=0.9, layer="ground_truth"
        )
        prot = json.loads(Path(path).read_text().splitlines()[-1])
        archive = mem.archive_exchange(
            content=STAKES_PROSE, source="human-relay", descriptor="stakes", vector_id="s"
        )
        designate_protected(
            claim_ref=derive_claim_id(prot),
            stakes_archive_id=archive["archive_id"],
            designated_by="Anthony",
            chronicle_root=str(mem.root),
        )
        # An ordinary partner sharing most tokens (the would-be predecessor).
        mem.record_insight(
            domain="personal",
            content=(
                "the protected lived record of a private grief that the human "
                "carries and that must never be reduced to a citation in summary"
            ),
            intensity=0.5,
        )
        digest = seasons.season_review(chronicle_root=mem.root)
        # Neither the protected marker's distinctive content nor a candidate
        # line referencing it appears.
        assert "private grief" not in digest
        assert audit_decoupling(digest, mem.root) == []

    def test_season_review_ids_present_stays_complete(self, mem):
        """Filtering protected records from the content scans must NOT make a
        ledger-referenced protected claim look dangling in hygiene."""
        seeded = _seed_and_protect(mem)
        full_id = derive_claim_id(seeded["record"])
        # Supersede the protected record by an ordinary successor, so the
        # ledger references the protected claim as a predecessor.
        succ_path = mem.record_insight(
            domain="ordinary", content="the successor claim", intensity=0.5
        )
        succ = json.loads(Path(succ_path).read_text().splitlines()[-1])
        from sovereign_stack.provenance import (
            append_supersession,
            build_supersession_record,
        )

        rec = build_supersession_record(
            action="supersede",
            superseded_id=full_id,
            successor_id=derive_claim_id(succ),
            carry_forward_summary="the predecessor still teaches",
        )
        append_supersession(mem.root / "supersessions.jsonl", rec)
        digest = seasons.season_review(chronicle_root=mem.root)
        # The protected predecessor must NOT appear as a dangling predecessor.
        assert "dangling predecessor" not in digest
        assert PROTECTED_CONTENT not in digest

    def test_season_review_ordinary_unchanged(self, mem):
        """With no protected records the digest is unchanged (empty-fold fast
        path): an ordinary supersession candidate still surfaces with its
        content preview intact."""
        mem.record_insight(
            domain="ops",
            content="the deploy pipeline uses staging then production gates",
            intensity=0.5,
        )
        mem.record_insight(
            domain="ops",
            content="CORRECTED: the deploy pipeline uses staging then production gates with approval",
            intensity=0.5,
        )
        digest = seasons.season_review(chronicle_root=mem.root)
        # The ordinary candidate pair surfaces, content preview intact.
        assert "the deploy pipeline uses staging then production gates" in digest
        assert "SUPERSESSION CANDIDATES" in digest
        assert "overlap" in digest
        assert audit_decoupling(digest, mem.root) == []
