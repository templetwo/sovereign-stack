"""The signature ledger: consumption becomes additive, never destructive.

`consumed_at` retired a handoff for EVERY future reader the moment one seat
read it — 197 real handoffs became unreachable that way. These tests pin the
replacement: signing records receipt and hides nothing; retirement is a
separate, deliberate act; and no handoff FILE is ever mutated, so the whole
feature rolls back by deleting two .jsonl files.
"""

import json
from pathlib import Path

import pytest

from sovereign_stack.handoff import HandoffEngine


@pytest.fixture
def engine(tmp_path):
    return HandoffEngine(str(tmp_path))


def _write(engine, note="n", who="seat-a", thread="general"):
    rec = engine.write(note=note, source_instance=who, source_session_id="s1", thread=thread)
    return rec.get("_path") or rec.get("path") or str(next(engine.root.glob("*.json")))


class TestSigningIsAdditive:
    def test_two_signers_both_recorded_neither_hides_the_other(self, engine):
        p = _write(engine)
        engine.sign(p, "seat-a")
        engine.sign(p, "seat-b")
        assert engine.signers_of(p) == {"seat-a", "seat-b"}
        # THE WHOLE POINT: a third seat still sees it.
        assert len(engine.unsigned_by("seat-c")) == 1

    def test_a_signature_does_not_hide_the_handoff_from_anyone_else(self, engine):
        p = _write(engine)
        engine.sign(p, "seat-a")
        assert engine.unsigned_by("seat-a") == []
        assert len(engine.unsigned_by("seat-b")) == 1

    def test_signing_is_idempotent_per_signer(self, engine):
        p = _write(engine)
        first = engine.sign(p, "seat-a")
        again = engine.sign(p, "seat-a")
        assert first == again
        assert len(engine.signatures(p)) == 1

    def test_handoff_file_is_never_mutated(self, engine):
        p = _write(engine)
        before = json.loads(Path(p).read_text())
        engine.sign(p, "seat-a")
        engine.retire(p, "hq", "done")
        assert json.loads(Path(p).read_text()) == before


class TestRetirementIsSeparate:
    def test_retirement_removes_it_from_everyone(self, engine):
        p = _write(engine)
        assert len(engine.unsigned_by("seat-z")) == 1
        engine.retire(p, "hq", "answered")
        assert engine.unsigned_by("seat-z") == []
        assert engine.unsigned_by("seat-q") == []

    def test_retirement_requires_a_stated_reason(self, engine):
        p = _write(engine)
        with pytest.raises(ValueError, match="reason is required"):
            engine.retire(p, "hq", "")

    def test_signing_alone_never_retires(self, engine):
        p = _write(engine)
        engine.sign(p, "seat-a")
        assert engine.retired_ids() == set()


class TestIdentityIsEnforced:
    @pytest.mark.parametrize("bad", ["", "   ", "unknown", "test", "placeholder"])
    def test_placeholder_signers_are_refused(self, engine, bad):
        p = _write(engine)
        with pytest.raises(ValueError):
            engine.sign(p, bad)

    def test_unsigned_by_refuses_an_unnamed_reader(self, engine):
        _write(engine)
        with pytest.raises(ValueError):
            engine.unsigned_by("unknown")


class TestLegacyCompatibility:
    def test_legacy_consumed_by_counts_as_that_readers_signature(self, engine):
        p = _write(engine)
        engine.mark_consumed([p], "old-seat")
        # No migration run: the ledger still reads correctly.
        assert "old-seat" in engine.signers_of(p)
        assert engine.unsigned_by("old-seat") == []
        # And it does NOT hide the handoff from anyone else — the bug, fixed.
        assert len(engine.unsigned_by("new-seat")) == 1


class TestMigration:
    def test_dry_run_changes_nothing(self, engine):
        p = _write(engine)
        engine.mark_consumed([p], "old-seat")
        out = engine.migrate_consumed_to_signatures(dry_run=True)
        assert out["would_migrate"] == 1
        assert engine.signatures() == []

    def test_migration_is_lossless_and_idempotent(self, engine):
        p = _write(engine)
        engine.mark_consumed([p], "old-seat")
        before = json.loads(Path(p).read_text())
        engine.migrate_consumed_to_signatures(dry_run=False)
        assert json.loads(Path(p).read_text()) == before  # consumed_at preserved
        assert {s["signer"] for s in engine.signatures(p)} == {"old-seat"}
        second = engine.migrate_consumed_to_signatures(dry_run=False)
        assert second["migrated"] == 0
        assert len(engine.signatures(p)) == 1

    def test_placeholder_consumers_are_skipped_not_imported(self, engine):
        p = _write(engine)
        # Bypass validation the way the pre-2026-08-01 code could.
        d = json.loads(Path(p).read_text())
        d["consumed_at"] = "2026-01-01T00:00:00"
        d["consumed_by"] = "unknown"
        Path(p).write_text(json.dumps(d))
        out = engine.migrate_consumed_to_signatures(dry_run=False)
        assert out["skipped_placeholder_consumers"] == 1
        assert engine.signatures() == []


class TestPortability:
    def test_handoff_id_is_the_filename_not_the_machine_path(self, engine):
        assert HandoffEngine._handoff_id("/Users/someone/.sovereign/handoffs/x.json") == "x.json"
        assert HandoffEngine._handoff_id("/srv/other/x.json") == "x.json"

    def test_count_is_uncapped_so_the_boot_can_say_showing_n_of_m(self, engine):
        for i in range(25):
            _write(engine, note=f"n{i}")
        assert len(engine.unsigned_by("seat-a", limit=20)) == 20
        assert engine.unsigned_by_count("seat-a") == 25
