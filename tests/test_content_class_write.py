"""
Content-class WRITE tag (Phase 1, feat/chronicle-content-class).

Locks the FIX-1 write-side guarantees:

  - unset content_class -> the stored entry has NO content_class field and is
    byte-identical (modulo volatile ts/session) to a no-class write: the tag
    is purely additive, an untagged write is unchanged;
  - a valid class from the CLOSED vocabulary is stored first-class;
  - an out-of-vocab class raises ValueError and writes NOTHING (reject, never
    coerce) — no file lands on disk;
  - the tag stays OUTSIDE the claim_id preimage (timestamp+domain+content), so
    a tagged entry derives the SAME id as its untagged twin;
  - recall tolerates a domain mixing tagged and untagged entries.
"""

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from sovereign_stack import provenance
from sovereign_stack.memory import ExperientialMemory

SESSION = "content-class-write"
BASELINE_KEYS = {"timestamp", "domain", "content", "intensity", "layer", "session_id"}


@pytest.fixture()
def mem():
    tmp = Path(tempfile.mkdtemp(prefix="cc-write-"))
    yield ExperientialMemory(root=str(tmp / "chronicle"))
    shutil.rmtree(tmp, ignore_errors=True)


def _lines(mem, domain, session=SESSION):
    path = mem.insights_dir / domain / f"{session}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class TestUnsetIsUntouched:
    def test_unset_omits_field_and_matches_baseline_keys(self, mem):
        mem.record_insight("d", "untagged alpha", 0.5, SESSION)
        (rec,) = _lines(mem, "d")
        assert "content_class" not in rec
        assert set(rec.keys()) == BASELINE_KEYS

    def test_explicit_none_equals_omitted(self, mem):
        # Omitting the param and passing content_class=None must yield the same
        # record shape (nothing injected, no null field). Normalize the two
        # volatile fields, then compare the rest byte-for-byte.
        mem.record_insight("d", "omit-form", 0.5, SESSION)
        mem.record_insight("d", "none-form", 0.5, SESSION, content_class=None)
        omit, none = _lines(mem, "d")
        for rec in (omit, none):
            assert "content_class" not in rec

        def strip(r):
            return {k: v for k, v in r.items() if k not in ("content", "timestamp")}

        assert strip(omit) == strip(none)


class TestValidStored:
    @pytest.mark.parametrize("cls", sorted(ExperientialMemory.CONTENT_CLASSES))
    def test_each_valid_class_stored(self, mem, cls):
        mem.record_insight("d", f"tagged {cls}", 0.5, SESSION, content_class=cls)
        (rec,) = _lines(mem, "d")
        assert rec["content_class"] == cls


class TestInvalidRejected:
    def test_invalid_raises_and_writes_nothing(self, mem):
        with pytest.raises(ValueError) as exc:
            mem.record_insight("bad", "should not land", 0.5, SESSION, content_class="results")
        assert "content_class" in str(exc.value)
        # No file — the reject fired before any filesystem call.
        assert not (mem.insights_dir / "bad").exists()

    def test_empty_string_rejected(self, mem):
        with pytest.raises(ValueError):
            mem.record_insight("bad2", "x", 0.5, SESSION, content_class="")
        assert not (mem.insights_dir / "bad2").exists()


class TestClaimIdUnaffectedByTag:
    def test_tagged_entry_derives_same_id_as_untagged_twin(self, mem):
        path = mem.record_insight(
            "d", "identity probe", 0.5, SESSION, content_class="outcome", return_claim_id=True
        )
        (stored,) = _lines(mem, "d")
        # Returned id equals the stored entry's derived id...
        assert path.claim_id == provenance.derive_claim_id(stored)
        assert len(path.claim_id) == 64
        # ...and stripping the tag does not change the derived id (tag is
        # outside the timestamp+domain+content preimage).
        untagged_twin = {k: v for k, v in stored.items() if k != "content_class"}
        assert provenance.derive_claim_id(untagged_twin) == path.claim_id


class TestRecallToleratesMixed:
    def test_mixed_tagged_and_untagged_recall_ok(self, mem):
        mem.record_insight("mix", "mix one untagged", 0.5, SESSION)
        mem.record_insight("mix", "mix two outcome", 0.5, SESSION, content_class="outcome")
        mem.record_insight("mix", "mix three process", 0.5, SESSION, content_class="process")
        got = mem.recall_insights(domain="mix", limit=50)
        assert len(got) == 3
        classes = {r.get("content_class") for r in got}
        assert classes == {None, "outcome", "process"}
