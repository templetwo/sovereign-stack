"""
Protected-source layer — Phase 2 tests (the decoupling audit + scribe fix).

Two concerns:

A. audit_decoupling — the §5.6 safeguard. Operates on a RENDERED string
   (the actual derivative that reaches a model), not a proxy projection.
   Flags protected content present WITHOUT its coupled stakes; passes
   content present WITH stakes; passes content absent (not surfaced, or
   the withheld sentinel). Documents the chokepoint-bypass gap.

B. The scribe fix — scribe/context_builder.py must carry the stakes for a
   protected record it surfaces, or withhold. The scribe test runs
   audit_decoupling on the REAL builder output (build_scribe_chronicle_context),
   so the audit is the verifier, not a parallel hand-check.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sovereign_stack.memory import ExperientialMemory
from sovereign_stack.protected import audit_decoupling, designate_protected
from sovereign_stack.provenance import derive_claim_id
from sovereign_stack.scribe.context_builder import build_scribe_chronicle_context

PROTECTED_CONTENT = "the deeply personal protected content that must travel with its weight"
STAKES_PROSE = (
    "This is a lived loss the human carries. When recalled, hold it as "
    "experience, not metadata. Reducing it to a citation is the wound."
)


@pytest.fixture
def mem(tmp_path: Path) -> ExperientialMemory:
    return ExperientialMemory(root=str(tmp_path / "chronicle"))


def _seed_and_protect(mem: ExperientialMemory, *, intensity: float = 0.9) -> dict:
    path = mem.record_insight(
        domain="personal",
        content=PROTECTED_CONTENT,
        intensity=intensity,
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


# ── A. The decoupling audit ──────────────────────────────────────────────────


class TestAuditDecoupling:
    def test_flags_content_present_stakes_absent(self, mem):
        seeded = _seed_and_protect(mem)
        # A hand-built decoupled derivative: content present, stakes dropped.
        leaky = f"some preamble\n  {PROTECTED_CONTENT}\nsome suffix"
        violations = audit_decoupling(leaky, mem.root)
        assert len(violations) == 1
        v = violations[0]
        assert v["claim_id"] == derive_claim_id(seeded["record"])
        assert v["reason"] == "content_present_stakes_absent"
        assert v["domain"] == "personal"

    def test_passes_content_present_stakes_present(self, mem):
        _seed_and_protect(mem)
        coupled = f"  {PROTECTED_CONTENT}\n  ↳ STAKES: {STAKES_PROSE}"
        assert audit_decoupling(coupled, mem.root) == []

    def test_passes_content_absent(self, mem):
        _seed_and_protect(mem)
        # Content not surfaced at all -> nothing to couple -> clean.
        assert audit_decoupling("unrelated text with no protected content", mem.root) == []

    def test_withheld_sentinel_notice_is_clean(self, mem):
        """The fail-closed sentinel's rendered notice never contains the
        original content, so a derivative carrying only the notice is clean."""
        from sovereign_stack.protected import ProtectedStakesUnavailable

        _seed_and_protect(mem)
        notice = ProtectedStakesUnavailable.WITHHELD_NOTICE
        assert audit_decoupling(notice, mem.root) == []

    def test_no_protected_records_always_clean(self, mem):
        mem.record_insight(domain="ordinary", content=PROTECTED_CONTENT, intensity=0.5)
        # Same text, but nothing is designated protected.
        assert audit_decoupling(PROTECTED_CONTENT, mem.root) == []

    def test_unloadable_stakes_with_leaked_content_is_worse(self, mem):
        seeded = _seed_and_protect(mem)
        # Break the stakes blob, then leak the content in a derivative.
        blob = Path(
            next(r for r in mem._read_archive_index() if r["archive_id"] == seeded["archive_id"])[
                "path"
            ]
        )
        blob.unlink()
        violations = audit_decoupling(PROTECTED_CONTENT, mem.root)
        assert len(violations) == 1
        assert violations[0]["reason"] == "content_present_stakes_unloadable"
        assert violations[0]["stakes_verdict"] == "missing"

    def test_audit_documents_chokepoint_bypass_gap(self, mem):
        """The audit is reader-agnostic: it flags a leak in ANY derivative
        string, including one produced by a surface that bypasses
        finalize_read (e.g. a raw dashboard tail). This documents that the
        Phase-1 'no path returns content bare' guarantee is scoped to the
        chokepoint; bare-reading surfaces still need the audit (or their own
        gating). Here we simulate a raw tail that read the source directly."""
        _seed_and_protect(mem)
        raw_tail = f"[tail] {PROTECTED_CONTENT}"  # what a bypassing reader would emit
        violations = audit_decoupling(raw_tail, mem.root)
        assert violations, "audit must still catch a leak from a bypassing surface"
        assert violations[0]["reason"] == "content_present_stakes_absent"


# ── B. The scribe fix (audit verifies the REAL builder output) ───────────────


class TestScribeFix:
    def _build(self, mem: ExperientialMemory) -> str:
        # Build the scribe context against this chronicle; no route map /
        # lineage / reflections needed for the projection sections under test.
        return build_scribe_chronicle_context(
            chronicle_root=mem.root,
            sovereign_root=mem.root.parent,
            include_route_map=False,
        )

    def test_scribe_output_carries_stakes_for_protected_record(self, mem):
        _seed_and_protect(mem)  # high-intensity ground_truth -> persistent markers
        text = self._build(mem)
        # The real builder output must contain BOTH the content and the stakes.
        assert PROTECTED_CONTENT in text
        assert STAKES_PROSE in text

    def test_audit_passes_on_real_scribe_output(self, mem):
        """The verifier IS the audit, run on the genuine builder string."""
        _seed_and_protect(mem)
        text = self._build(mem)
        assert audit_decoupling(text, mem.root) == []

    def test_scribe_withholds_content_when_stakes_unloadable(self, mem):
        seeded = _seed_and_protect(mem)
        blob = Path(
            next(r for r in mem._read_archive_index() if r["archive_id"] == seeded["archive_id"])[
                "path"
            ]
        )
        blob.unlink()
        text = self._build(mem)
        # Content withheld (the sentinel notice surfaces instead), audit clean.
        assert PROTECTED_CONTENT not in text
        assert "stakes unavailable; content withheld" in text
        assert audit_decoupling(text, mem.root) == []

    def test_scribe_unchanged_for_ordinary_records(self, mem):
        mem.record_insight(
            domain="ordinary",
            content="an everyday high-intensity ground truth",
            intensity=0.95,
            layer="ground_truth",
        )
        text = self._build(mem)
        assert "an everyday high-intensity ground truth" in text
        # No stakes line for a non-protected record.
        assert "STAKES (held inseparably)" not in text
        assert audit_decoupling(text, mem.root) == []
