"""
Protected-source layer — Policy 2b tests (the consent gate). SECURITY-CRITICAL.

Before delivering coupled content a caller gets only the THRESHOLD: the two
words + datetime (+ seq#). The threshold MUST NEVER include the record's
content or its stakes prose — it names the shape only. The caller chooses:
  - OPEN    -> open_record: full content arrives COUPLED to its stakes,
               fail-closed to the withheld sentinel when stakes unverifiable.
  - DECLINE -> decline_record: a legitimate, RECORDED state — logged, never
               raised.

CRITICAL: a threshold that leaks content is the exact decoupling loophole
Policy 1 outlaws — audit_threshold flags it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sovereign_stack.memory import ExperientialMemory
from sovereign_stack.protected import (
    ProtectedError,
    ProtectedStakesUnavailable,
    audit_threshold,
    build_address,
    decline_record,
    designate_protected,
    list_thresholds,
    load_declines,
    load_protected_fold,
    open_record,
    threshold_for,
)
from sovereign_stack.provenance import derive_claim_id

PROTECTED_CONTENT = "the consent-gated protected body that the threshold must never reveal"
STAKES_PROSE = (
    "A lived grief the human carries. When recalled the weight arrives with the "
    "words. Reducing it to a citation is the wound. Hold it as experience."
)


@pytest.fixture
def mem(tmp_path: Path) -> ExperientialMemory:
    return ExperientialMemory(root=str(tmp_path / "chronicle"))


def _seed_and_protect(
    mem: ExperientialMemory, *, subject="father", emotion="loss", content=PROTECTED_CONTENT
) -> dict:
    path = mem.record_insight(
        domain="personal", content=content, intensity=0.9, layer="ground_truth"
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
        subject=subject,
        emotion=emotion,
    )
    return {"record": prot, "archive_id": archive["archive_id"]}


def _break_stakes(mem: ExperientialMemory, archive_id: str) -> None:
    blob = Path(next(r for r in mem._read_archive_index() if r["archive_id"] == archive_id)["path"])
    blob.unlink()


# ── Threshold carries zero content / zero stakes ─────────────────────────────


class TestThreshold:
    def test_threshold_for_has_no_content_no_stakes(self, mem):
        seeded = _seed_and_protect(mem)
        fold = load_protected_fold(mem.root)
        th = threshold_for(derive_claim_id(seeded["record"]), fold)
        assert th is not None
        blob = json.dumps(th)
        assert PROTECTED_CONTENT not in blob
        assert STAKES_PROSE not in blob
        # It names the shape: two words + datetime + the open handle.
        assert th["subject"] == "father"
        assert th["emotion"] == "loss"
        assert th["datetime"]
        assert th["claim_id"] == derive_claim_id(seeded["record"])
        assert th["address"] == build_address("father", "loss", th["datetime"])

    def test_list_thresholds_has_no_content(self, mem):
        _seed_and_protect(mem, subject="father", emotion="loss", content=PROTECTED_CONTENT)
        _seed_and_protect(
            mem, subject="mother", emotion="pride", content="a different protected body"
        )
        ths = list_thresholds(load_protected_fold(mem.root))
        assert len(ths) == 2
        blob = json.dumps(ths)
        assert PROTECTED_CONTENT not in blob
        assert "a different protected body" not in blob
        assert STAKES_PROSE not in blob

    def test_threshold_for_unprotected_is_none(self, mem):
        # An ordinary, undesignated record has no threshold.
        path = mem.record_insight(domain="ops", content="ordinary", intensity=0.5)
        ordinary = json.loads(Path(path).read_text().splitlines()[-1])
        fold = load_protected_fold(mem.root)
        assert threshold_for(derive_claim_id(ordinary), fold) is None

    def test_real_threshold_passes_audit_threshold(self, mem):
        """The genuine threshold surface, run through the threshold-leak
        audit, is clean."""
        _seed_and_protect(mem)
        ths = list_thresholds(load_protected_fold(mem.root))
        assert audit_threshold(json.dumps(ths), mem.root) == []


# ── Open returns coupled content; fails closed ───────────────────────────────


class TestOpen:
    def test_open_returns_coupled_content(self, mem):
        seeded = _seed_and_protect(mem)
        opened = open_record(derive_claim_id(seeded["record"]), mem.root)
        assert not isinstance(opened, ProtectedStakesUnavailable)
        assert opened["content"] == PROTECTED_CONTENT
        assert opened["_protected"] is True
        assert opened["_stakes"] == STAKES_PROSE
        assert opened["_stakes_verdict"] == "verified"

    def test_open_fails_closed_when_stakes_unverifiable(self, mem):
        seeded = _seed_and_protect(mem)
        _break_stakes(mem, seeded["archive_id"])
        opened = open_record(derive_claim_id(seeded["record"]), mem.root)
        assert isinstance(opened, ProtectedStakesUnavailable)
        assert PROTECTED_CONTENT not in opened["content"]
        assert opened["_stakes_verdict"] == "missing"

    def test_open_rejects_non_protected_claim(self, mem):
        path = mem.record_insight(domain="ops", content="ordinary claim", intensity=0.5)
        ordinary = json.loads(Path(path).read_text().splitlines()[-1])
        with pytest.raises(ProtectedError):
            open_record(derive_claim_id(ordinary), mem.root)


# ── Decline is recorded, not raised ──────────────────────────────────────────


class TestDecline:
    def test_decline_is_logged_not_raised(self, mem):
        seeded = _seed_and_protect(mem)
        cid = derive_claim_id(seeded["record"])
        rec = decline_record(cid, mem.root, declined_by="opus-4-8", reason="not now")
        assert rec["action"] == "decline"
        assert rec["claim_id"] == cid
        assert rec["declined_by"] == "opus-4-8"
        # Persisted to the append-only decline log.
        declines = load_declines(mem.root)
        assert len(declines) == 1
        assert declines[0]["claim_id"] == cid

    def test_decline_log_carries_no_content_or_stakes(self, mem):
        seeded = _seed_and_protect(mem)
        decline_record(derive_claim_id(seeded["record"]), mem.root, declined_by="x")
        blob = (mem.root / "protected_declines.jsonl").read_text()
        assert PROTECTED_CONTENT not in blob
        assert STAKES_PROSE not in blob

    def test_decline_appends(self, mem):
        seeded = _seed_and_protect(mem)
        cid = derive_claim_id(seeded["record"])
        decline_record(cid, mem.root, declined_by="a")
        decline_record(cid, mem.root, declined_by="b")
        assert len(load_declines(mem.root)) == 2


# ── audit_threshold flags a content-leaking threshold ────────────────────────


class TestAuditThreshold:
    def test_flags_content_leaking_threshold(self, mem):
        _seed_and_protect(mem)
        # A malformed "threshold" that leaks the content — the loophole.
        leaky = f"father / loss / 2026-06-23 :: {PROTECTED_CONTENT}"
        violations = audit_threshold(leaky, mem.root)
        assert len(violations) == 1
        assert violations[0]["reason"] == "threshold_leaks_content"

    def test_flags_stakes_leaking_threshold(self, mem):
        """A threshold must not deliver the stakes prose either — it names the
        shape only."""
        _seed_and_protect(mem)
        leaky = f"father / loss :: STAKES: {STAKES_PROSE}"
        violations = audit_threshold(leaky, mem.root)
        assert len(violations) == 1
        assert violations[0]["reason"] == "threshold_leaks_stakes"

    def test_content_with_stakes_still_flagged_in_threshold(self, mem):
        """Even content COUPLED with stakes is wrong in a THRESHOLD (a
        threshold is consent-gating, not a content surface). This is the
        stricter bar that distinguishes audit_threshold from audit_decoupling."""
        _seed_and_protect(mem)
        coupled_but_in_threshold = f"father/loss :: {PROTECTED_CONTENT} ↳ {STAKES_PROSE}"
        violations = audit_threshold(coupled_but_in_threshold, mem.root)
        assert violations  # content present at all -> flagged
        assert violations[0]["reason"] == "threshold_leaks_content"

    def test_clean_threshold_passes(self, mem):
        _seed_and_protect(mem)
        clean = "father / loss / 2026-06-23T12:00:00+00:00"
        assert audit_threshold(clean, mem.root) == []

    def test_no_protected_records_always_clean(self, mem):
        mem.record_insight(domain="ops", content=PROTECTED_CONTENT, intensity=0.5)
        assert audit_threshold(PROTECTED_CONTENT, mem.root) == []
