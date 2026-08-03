"""Guard-shape assert for the protected-coupling chokepoint (mesh-20260802 audit).

The surviving finding of the 2026-08-02 protected-guard audit: finalize_read's
stage B skipped coupling on ANY empty fold, and load_protected_fold returns the
same ``{}`` for a ledger that is legitimately empty and a ledger that is ABSENT
— which is what a mis-rooted caller sees. A wrong root therefore yielded zero
protection with zero signal (the fail-open shape), even though the hot path
binds the correct root today.

These tests pin the corrected shape:
  - absent ledger + entries  -> every entry annotated ``_protected_fold_state:
    "ledger-absent"`` and a warning logged (announce, never silent);
  - present-but-empty ledger -> byte-stable pass-through (the legitimate empty
    drawer, announced at boot, silent here);
  - armed path              -> designated records still couple (regression pin
    for the guard-armed behavior the audit verified live);
  - the annotation is derived-at-read only, never persisted.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from sovereign_stack.memory import ExperientialMemory, finalize_read
from sovereign_stack.protected import designate_protected
from sovereign_stack.provenance import derive_claim_id

PROTECTED_CONTENT = "protected content that must never travel decoupled"
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
        subject="father",
        emotion="loss",
    )
    return {"record": prot, "archive_id": archive["archive_id"]}


class TestAbsentLedgerAnnounces:
    def test_absent_ledger_stamps_every_entry(self, mem):
        entry = _seed_ordinary(mem)
        assert not (Path(mem.root) / "protected.jsonl").exists()
        out = finalize_read([entry], mem.root)
        assert out, "entries must still be returned"
        assert all(e.get("_protected_fold_state") == "ledger-absent" for e in out)

    def test_absent_ledger_logs_a_warning(self, mem, caplog):
        entry = _seed_ordinary(mem)
        with caplog.at_level(logging.WARNING, logger="sovereign_stack.memory"):
            finalize_read([entry], mem.root)
        assert any("protected ledger absent" in rec.message for rec in caplog.records), (
            "an absent ledger must announce itself, never skip silently"
        )

    def test_absent_ledger_does_not_mutate_caller_entries(self, mem):
        entry = _seed_ordinary(mem)
        finalize_read([entry], mem.root)
        assert "_protected_fold_state" not in entry, "annotate copies, never mutate"

    def test_empty_entry_list_stays_silent(self, mem, caplog):
        with caplog.at_level(logging.WARNING, logger="sovereign_stack.memory"):
            out = finalize_read([], mem.root)
        assert out == []
        assert not any("protected ledger absent" in rec.message for rec in caplog.records)


class TestPresentEmptyLedgerIsSilent:
    def test_empty_ledger_passes_through_unannotated(self, mem, caplog):
        entry = _seed_ordinary(mem)
        (Path(mem.root) / "protected.jsonl").touch()
        with caplog.at_level(logging.WARNING, logger="sovereign_stack.memory"):
            out = finalize_read([entry], mem.root)
        assert all("_protected_fold_state" not in e for e in out)
        assert not any("protected ledger absent" in rec.message for rec in caplog.records), (
            "a present-but-empty ledger is the legitimate empty drawer"
        )


class TestArmedPathStillCouples:
    def test_designated_record_carries_coupling_marks(self, mem):
        seeded = _seed_and_protect(mem)
        out = finalize_read([seeded["record"]], mem.root)
        (coupled,) = out
        assert coupled.get("_protected") is True
        assert coupled.get("_stakes_verdict") == "verified"
        assert coupled.get("_stakes") == STAKES_PROSE
        assert "_protected_fold_state" not in coupled

    def test_annotation_never_persisted(self, mem):
        entry = _seed_ordinary(mem)
        finalize_read([entry], mem.root)
        shard = Path(mem.root) / "insights" / "ordinary"
        raw = "".join(p.read_text() for p in shard.glob("*.jsonl"))
        assert "_protected_fold_state" not in raw
