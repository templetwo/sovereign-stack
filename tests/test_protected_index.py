"""
Protected-source layer — Policy 2a tests (the two-word index).

Each protected record carries a one-word SUBJECT + one-word EMOTION. The
index supports:
  - pull-by-subject (every 'father') AND pull-by-emotion (every 'loss')
    across the whole protected set;
  - DATETIME distinguishing — the same two words on different datetimes are
    distinct rows; same two words different emotion is distinct (different
    emotion); the index datetime is the underlying ENTRY timestamp;
  - a SEQUENCE NUMBER appended to the address ONLY on a TRUE collision
    (identical subject+emotion+datetime); omitted otherwise.

The index reads only the folded ledger, so the collision test crafts ledger
records directly (controlling entry_timestamp) — the unit under test is the
address+seq assignment, not chronicle resolution.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sovereign_stack import protected
from sovereign_stack.memory import ExperientialMemory
from sovereign_stack.protected import (
    ProtectedError,
    build_address,
    build_protected_record,
    designate_protected,
    index_protected,
    load_protected_fold,
    normalize_index_word,
    pull_by_emotion,
    pull_by_subject,
)
from sovereign_stack.provenance import derive_claim_id

STAKES_PROSE = "A lived weight that travels with the words. Held, not reduced."


@pytest.fixture
def mem(tmp_path: Path) -> ExperientialMemory:
    return ExperientialMemory(root=str(tmp_path / "chronicle"))


def _protect_one(
    mem: ExperientialMemory, *, content: str, subject: str, emotion: str, domain: str = "personal"
) -> dict:
    path = mem.record_insight(domain=domain, content=content, intensity=0.9, layer="ground_truth")
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
    return prot


# ── Word validation / normalization ──────────────────────────────────────────


class TestIndexWordValidation:
    def test_lowercases_and_strips(self):
        assert normalize_index_word("  Father ", "subject") == "father"

    def test_rejects_empty(self):
        for bad in ("", "   "):
            with pytest.raises(ProtectedError):
                normalize_index_word(bad, "subject")

    def test_rejects_multiword(self):
        with pytest.raises(ProtectedError):
            normalize_index_word("my father", "subject")

    def test_rejects_none(self):
        with pytest.raises(ProtectedError):
            normalize_index_word(None, "emotion")  # type: ignore[arg-type]

    def test_designate_rejects_multiword_subject(self, mem):
        path = mem.record_insight(domain="personal", content="x", intensity=0.9)
        prot = json.loads(Path(path).read_text().splitlines()[-1])
        archive = mem.archive_exchange(
            content=STAKES_PROSE, source="h", descriptor="s", vector_id="s"
        )
        with pytest.raises(ProtectedError):
            designate_protected(
                claim_ref=derive_claim_id(prot),
                stakes_archive_id=archive["archive_id"],
                designated_by="Anthony",
                chronicle_root=str(mem.root),
                subject="dear father",
                emotion="loss",
            )

    def test_designate_requires_both_words(self, mem):
        path = mem.record_insight(domain="personal", content="y", intensity=0.9)
        prot = json.loads(Path(path).read_text().splitlines()[-1])
        archive = mem.archive_exchange(
            content=STAKES_PROSE, source="h", descriptor="s", vector_id="s"
        )
        with pytest.raises(ProtectedError):
            designate_protected(
                claim_ref=derive_claim_id(prot),
                stakes_archive_id=archive["archive_id"],
                designated_by="Anthony",
                chronicle_root=str(mem.root),
                subject="father",  # emotion omitted
            )


# ── Pull by subject / emotion ────────────────────────────────────────────────


class TestPullByTag:
    def test_pull_by_subject(self, mem):
        _protect_one(mem, content="a1", subject="father", emotion="loss")
        _protect_one(mem, content="a2", subject="father", emotion="pride")
        _protect_one(mem, content="b1", subject="mother", emotion="loss")
        fold = load_protected_fold(mem.root)

        fathers = pull_by_subject(fold, "father")
        assert {r["emotion"] for r in fathers} == {"loss", "pride"}
        assert len(fathers) == 2
        mothers = pull_by_subject(fold, "mother")
        assert len(mothers) == 1
        assert mothers[0]["emotion"] == "loss"

    def test_pull_by_emotion(self, mem):
        _protect_one(mem, content="a1", subject="father", emotion="loss")
        _protect_one(mem, content="a2", subject="father", emotion="pride")
        _protect_one(mem, content="b1", subject="mother", emotion="loss")
        fold = load_protected_fold(mem.root)

        losses = pull_by_emotion(fold, "loss")
        assert {r["subject"] for r in losses} == {"father", "mother"}
        assert len(losses) == 2
        prides = pull_by_emotion(fold, "pride")
        assert len(prides) == 1
        assert prides[0]["subject"] == "father"

    def test_pull_normalizes_query(self, mem):
        _protect_one(mem, content="a1", subject="father", emotion="loss")
        fold = load_protected_fold(mem.root)
        assert len(pull_by_subject(fold, "  FATHER ")) == 1

    def test_index_rows_carry_no_content(self, mem):
        prot = _protect_one(
            mem, content="the secret protected body", subject="father", emotion="loss"
        )
        fold = load_protected_fold(mem.root)
        rows = index_protected(fold)
        blob = json.dumps(rows)
        assert "the secret protected body" not in blob
        # But the row points at the record by claim id.
        assert rows[0]["claim_id"] == derive_claim_id(prot)


# ── Datetime distinguishing ──────────────────────────────────────────────────


class TestDatetimeDistinguishes:
    def test_same_two_words_different_datetime_are_distinct(self, mem):
        # Two records, same subject+emotion, but different entry timestamps
        # (record_insight stamps now()), so two distinct rows, no seq.
        _protect_one(mem, content="first father loss", subject="father", emotion="loss")
        _protect_one(mem, content="second father loss", subject="father", emotion="loss")
        rows = pull_by_subject(load_protected_fold(mem.root), "father")
        assert len(rows) == 2
        # Different datetimes -> NOT a collision -> no seq on either.
        assert all(r["seq"] is None for r in rows)
        assert len({r["datetime"] for r in rows}) == 2
        assert len({r["address"] for r in rows}) == 2
        # No "/seq" in a non-colliding address.
        assert all("/seq" not in r["address"] for r in rows)

    def test_same_datetime_different_emotion_are_distinct_no_seq(self):
        # father/loss and father/pride on the SAME datetime: distinct (emotion
        # differs), so no collision and no seq.
        ts = "2026-06-23T12:00:00+00:00"
        records = [
            build_protected_record(
                claim_id="a" * 64,
                stakes_archive_id="x" * 64,
                designated_by="Anthony",
                subject="father",
                emotion="loss",
                entry_timestamp=ts,
            ),
            build_protected_record(
                claim_id="b" * 64,
                stakes_archive_id="x" * 64,
                designated_by="Anthony",
                subject="father",
                emotion="pride",
                entry_timestamp=ts,
            ),
        ]
        fold = protected.fold_protected(records)
        rows = index_protected(fold)
        assert all(r["seq"] is None for r in rows)
        assert all("/seq" not in r["address"] for r in rows)


# ── Sequence number ONLY on a true collision ─────────────────────────────────


class TestCollisionSequence:
    def _records(self, *triples) -> list[dict]:
        out = []
        for cid, subject, emotion, ts in triples:
            out.append(
                build_protected_record(
                    claim_id=cid,
                    stakes_archive_id="x" * 64,
                    designated_by="Anthony",
                    subject=subject,
                    emotion=emotion,
                    entry_timestamp=ts,
                )
            )
        return out

    def test_true_collision_gets_sequence(self):
        ts = "2026-06-23T12:00:00+00:00"
        # Two records identical on subject+emotion+datetime -> true collision.
        records = self._records(
            ("a" * 64, "father", "loss", ts),
            ("b" * 64, "father", "loss", ts),
        )
        fold = protected.fold_protected(records)
        rows = index_protected(fold)
        assert len(rows) == 2
        seqs = sorted(r["seq"] for r in rows)
        assert seqs == [1, 2]
        # Addresses carry the seq and are unique.
        assert all(r["address"].endswith(f"/seq{r['seq']}") for r in rows)
        assert len({r["address"] for r in rows}) == 2
        for r in rows:
            assert r["address"].startswith("father/loss/" + ts)

    def test_no_collision_omits_sequence(self):
        records = self._records(
            ("a" * 64, "father", "loss", "2026-06-23T12:00:00+00:00"),
            ("b" * 64, "father", "loss", "2026-06-23T13:00:00+00:00"),
        )
        fold = protected.fold_protected(records)
        rows = index_protected(fold)
        assert all(r["seq"] is None for r in rows)
        assert all("/seq" not in r["address"] for r in rows)

    def test_build_address_shape(self):
        assert build_address("father", "loss", "2026-06-23T12:00:00+00:00") == (
            "father/loss/2026-06-23T12:00:00+00:00"
        )
        assert build_address("father", "loss", "2026-06-23T12:00:00+00:00", 2) == (
            "father/loss/2026-06-23T12:00:00+00:00/seq2"
        )
