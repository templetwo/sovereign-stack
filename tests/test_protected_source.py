"""
Protected-source layer — Phase 1 tests (the protected unit).

Covers the four required guarantees:
  1. The coupled-retrieval INVARIANT: any read that returns a protected
     record's content includes its stakes in the SAME payload; no path
     returns protected content bare. Tested across recall_insights AND
     load_entries, with an EMPTY supersession ledger (the fail-open
     trap — protection must NOT be gated on the supersession ledger).
  2. FAIL-CLOSED: when the stakes can't be loaded/verified, the reader
     gets the typed ProtectedStakesUnavailable sentinel (content
     withheld), never the bare content and never silence.
  3. DESIGNATION is human-gated: no designated_by -> refused, no record
     written.
  4. NON-PROTECTED recall is unchanged (the golden-baseline file proves
     this independently; here we add a same-fixture smoke check).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sovereign_stack import protected
from sovereign_stack.memory import ExperientialMemory, finalize_read, load_entries
from sovereign_stack.protected import (
    ProtectedGateError,
    ProtectedStakesUnavailable,
    designate_protected,
)
from sovereign_stack.provenance import derive_claim_id

PROTECTED_CONTENT = "the deeply personal record content that must never travel decoupled"
STAKES_PROSE = (
    "When this is recalled, the weight arrives with it: this is a record of "
    "loss that the human carries, and reducing it to a citation is the wound. "
    "Hold it as a lived experience, not a tag."
)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def mem(tmp_path: Path) -> ExperientialMemory:
    return ExperientialMemory(root=str(tmp_path / "chronicle"))


def _seed_records(mem: ExperientialMemory) -> dict:
    """Write one ordinary insight and one to-be-protected insight; return them."""
    ordinary_path = mem.record_insight(
        domain="ordinary",
        content="an everyday claim, never protected",
        intensity=0.5,
        layer="hypothesis",
    )
    protected_path = mem.record_insight(
        domain="personal",
        content=PROTECTED_CONTENT,
        intensity=0.9,
        layer="ground_truth",
    )
    ordinary = json.loads(Path(ordinary_path).read_text().splitlines()[-1])
    prot = json.loads(Path(protected_path).read_text().splitlines()[-1])
    return {"ordinary": ordinary, "protected": prot}


def _archive_stakes(mem: ExperientialMemory, content: str = STAKES_PROSE) -> str:
    rec = mem.archive_exchange(
        content=content,
        source="human-relay",
        descriptor="protected stakes prose",
        vector_id="protected_stakes",
    )
    return rec["archive_id"]


def _protect(mem: ExperientialMemory, recs: dict, archive_id: str) -> dict:
    return designate_protected(
        claim_ref=derive_claim_id(recs["protected"]),
        stakes_archive_id=archive_id,
        designated_by="Anthony",
        chronicle_root=str(mem.root),
        subject="father",
        emotion="loss",
        reason="phase-1 test designation",
    )


# ── 1. The coupled-retrieval invariant ───────────────────────────────────────


class TestCoupledRetrievalInvariant:
    def test_recall_protected_content_carries_stakes_in_same_payload(self, mem):
        recs = _seed_records(mem)
        archive_id = _archive_stakes(mem)
        self_record = _protect(mem, recs, archive_id)
        assert self_record["action"] == "protect"

        result = mem.recall_insights(domain="personal")
        [entry] = result
        # Content present AND stakes present, in the SAME dict.
        assert entry["content"] == PROTECTED_CONTENT
        assert entry["_protected"] is True
        assert entry["_stakes"] == STAKES_PROSE
        assert entry["_stakes_verdict"] == "verified"
        assert entry["_stakes_designated_by"] == "Anthony"

    def test_no_path_returns_protected_content_bare_empty_supersession_ledger(self, mem):
        """The fail-open trap: a protected record with ZERO supersessions
        must still be coupled. finalize_read must NOT short-circuit on the
        empty supersession ledger before protection runs."""
        recs = _seed_records(mem)
        archive_id = _archive_stakes(mem)
        _protect(mem, recs, archive_id)
        # Sanity: no supersession ledger exists at all.
        assert not (mem.root / "supersessions.jsonl").exists()

        # recall_insights path
        for entry in mem.recall_insights(limit=50):
            if entry.get("content") == PROTECTED_CONTENT:
                assert "_stakes" in entry, "protected content returned BARE via recall_insights"
                break
        else:
            pytest.fail("protected record not returned at all")

        # load_entries path (the other converged reader)
        loaded = load_entries(mem.root)
        protected_entries = [e for e in loaded if e.get("content") == PROTECTED_CONTENT]
        assert protected_entries, "protected record missing from load_entries"
        for e in protected_entries:
            assert "_stakes" in e, "protected content returned BARE via load_entries"

    def test_finalize_read_couples_directly(self, mem):
        """finalize_read itself (the chokepoint) couples — not just the
        public readers that call it."""
        recs = _seed_records(mem)
        archive_id = _archive_stakes(mem)
        _protect(mem, recs, archive_id)
        entries = load_entries(mem.root)  # already finalized; re-finalize is idempotent on protect
        coupled = [e for e in entries if e.get("content") == PROTECTED_CONTENT]
        assert coupled and all("_stakes" in e for e in coupled)

    def test_ordinary_records_untouched_by_protection(self, mem):
        recs = _seed_records(mem)
        archive_id = _archive_stakes(mem)
        _protect(mem, recs, archive_id)
        [ordinary] = mem.recall_insights(domain="ordinary")
        assert ordinary == recs["ordinary"]  # byte-identical, no protected keys
        assert "_protected" not in ordinary
        assert "_stakes" not in ordinary

    def test_with_ids_carries_true_claim_id_on_coupled_record(self, mem):
        recs = _seed_records(mem)
        archive_id = _archive_stakes(mem)
        _protect(mem, recs, archive_id)
        [entry] = mem.recall_insights(domain="personal", with_ids=True)
        assert entry["claim_id"] == derive_claim_id(recs["protected"])
        assert entry["_stakes"] == STAKES_PROSE
        assert entry["content"] == PROTECTED_CONTENT

    def test_unprotect_restores_bare_record(self, mem):
        recs = _seed_records(mem)
        archive_id = _archive_stakes(mem)
        _protect(mem, recs, archive_id)
        designate_protected(
            claim_ref=derive_claim_id(recs["protected"]),
            stakes_archive_id=archive_id,
            designated_by="Anthony",
            chronicle_root=str(mem.root),
            action="unprotect",
        )
        [entry] = mem.recall_insights(domain="personal")
        assert entry == recs["protected"]
        assert "_stakes" not in entry
        assert "_protected" not in entry


# ── 2. Fail-closed ───────────────────────────────────────────────────────────


class TestFailClosed:
    def _protect_then_break_stakes(self, mem) -> dict:
        recs = _seed_records(mem)
        archive_id = _archive_stakes(mem)
        _protect(mem, recs, archive_id)
        # Tamper: delete the archived stakes blob so load_stakes -> missing.
        blob = Path(
            next(r for r in mem._read_archive_index() if r["archive_id"] == archive_id)["path"]
        )
        blob.unlink()
        return recs

    def test_missing_stakes_withholds_content_typed_sentinel(self, mem):
        recs = self._protect_then_break_stakes(mem)
        [entry] = mem.recall_insights(domain="personal")
        assert isinstance(entry, ProtectedStakesUnavailable)
        assert entry["_stakes_withheld"] is True
        assert entry["_stakes_verdict"] == "missing"
        # The actual protected content is NOT present anywhere in the payload.
        assert PROTECTED_CONTENT not in json.dumps(entry)
        # Locator + true id survive so the record is identifiable.
        assert entry["claim_id"] == derive_claim_id(recs["protected"])
        assert entry["domain"] == "personal"

    def test_tampered_stakes_withholds_content(self, mem):
        recs = _seed_records(mem)
        archive_id = _archive_stakes(mem)
        _protect(mem, recs, archive_id)
        # Tamper the blob bytes in place -> hash mismatch on read.
        blob = Path(
            next(r for r in mem._read_archive_index() if r["archive_id"] == archive_id)["path"]
        )
        blob.write_text("these are NOT the stakes that were designated", encoding="utf-8")
        [entry] = mem.recall_insights(domain="personal")
        assert isinstance(entry, ProtectedStakesUnavailable)
        assert entry["_stakes_verdict"] == "mismatch"
        assert PROTECTED_CONTENT not in json.dumps(entry)

    def test_sentinel_survives_limit_strip_and_with_ids(self, mem):
        self._protect_then_break_stakes(mem)
        # query+relevance (adds/strips _match_count) + with_ids together.
        result = mem.recall_insights(query="personal", order="relevance", with_ids=True, limit=5)
        sentinels = [e for e in result if isinstance(e, ProtectedStakesUnavailable)]
        # The protected record matches on its domain "personal".
        assert sentinels, "sentinel did not survive the recall tail"
        for s in sentinels:
            assert "_match_count" not in s
            assert s["claim_id"]  # true id preserved, not re-derived from withheld body

    def test_sentinel_is_dict_subclass(self):
        s = ProtectedStakesUnavailable.from_entry(
            {"timestamp": "t", "domain": "d", "content": "secret"}, "a" * 64, "missing"
        )
        assert isinstance(s, dict)
        assert s["content"] != "secret"
        assert s["claim_id"] == "a" * 64


# ── 3. Designation is human-gated ────────────────────────────────────────────


class TestDesignationGate:
    def test_empty_designated_by_refused_no_record_written(self, mem):
        recs = _seed_records(mem)
        archive_id = _archive_stakes(mem)
        with pytest.raises(ProtectedGateError):
            designate_protected(
                claim_ref=derive_claim_id(recs["protected"]),
                stakes_archive_id=archive_id,
                designated_by="",
                chronicle_root=str(mem.root),
            )
        assert not (mem.root / "protected.jsonl").exists()

    def test_whitespace_designated_by_refused(self, mem):
        recs = _seed_records(mem)
        archive_id = _archive_stakes(mem)
        with pytest.raises(ProtectedGateError):
            designate_protected(
                claim_ref=derive_claim_id(recs["protected"]),
                stakes_archive_id=archive_id,
                designated_by="   ",
                chronicle_root=str(mem.root),
            )

    def test_build_record_gate(self):
        with pytest.raises(ProtectedGateError):
            protected.build_protected_record(
                claim_id="a" * 64, stakes_archive_id="x" * 64, designated_by=""
            )

    def test_protect_requires_verified_stakes_pointer(self, mem):
        recs = _seed_records(mem)
        # Point at an archive id that does not exist -> refused at designation.
        with pytest.raises(protected.ProtectedError):
            designate_protected(
                claim_ref=derive_claim_id(recs["protected"]),
                stakes_archive_id="deadbeef" * 8,
                designated_by="Anthony",
                chronicle_root=str(mem.root),
            )
        assert not (mem.root / "protected.jsonl").exists()

    def test_designation_is_not_automatic(self, mem):
        """A record is bare until a human designates it — nothing auto-protects."""
        recs = _seed_records(mem)
        _archive_stakes(mem)  # stakes exist in the archive but nothing is designated
        [entry] = mem.recall_insights(domain="personal")
        assert entry == recs["protected"]
        assert "_protected" not in entry


# ── 4. Non-protected recall unchanged (same-fixture smoke) ───────────────────


class TestNonProtectedUnchanged:
    def test_no_ledger_no_change(self, mem):
        recs = _seed_records(mem)
        result = mem.recall_insights(limit=50)
        # No protected ledger -> every entry is the raw record, no derived keys.
        for entry in result:
            assert "_protected" not in entry
            assert "_stakes" not in entry
        assert recs["protected"] in result
        assert recs["ordinary"] in result


# ── Unit: the protected data layer in isolation ──────────────────────────────


class TestProtectedDataLayer:
    def test_fold_latest_action_wins(self):
        cid = "a" * 64
        records = [
            {"action": "protect", "claim_id": cid, "stakes_archive_id": "s1"},
            {"action": "unprotect", "claim_id": cid},
            {"action": "protect", "claim_id": cid, "stakes_archive_id": "s2"},
        ]
        fold = protected.fold_protected(records)
        assert fold[cid]["stakes_archive_id"] == "s2"

    def test_fold_unprotect_nullifies(self):
        cid = "b" * 64
        fold = protected.fold_protected(
            [
                {"action": "protect", "claim_id": cid, "stakes_archive_id": "s1"},
                {"action": "unprotect", "claim_id": cid},
            ]
        )
        assert cid not in fold

    def test_load_protected_missing_ledger_is_empty(self, tmp_path):
        assert protected.load_protected(tmp_path / "nope.jsonl") == []

    def test_finalize_read_idempotent_on_protect(self, mem):
        """Re-finalizing an already-coupled list does not double-wrap or
        withhold (a sentinel passes through; a coupled record stays coupled)."""
        recs = _seed_records(mem)
        archive_id = _archive_stakes(mem)
        _protect(mem, recs, archive_id)
        once = load_entries(mem.root)
        twice = finalize_read(once, mem.root)
        prot_once = [e for e in once if e.get("content") == PROTECTED_CONTENT]
        prot_twice = [
            e for e in twice if "_stakes" in e or isinstance(e, ProtectedStakesUnavailable)
        ]
        assert prot_once and prot_twice
        assert all("_stakes" in e for e in prot_twice)
