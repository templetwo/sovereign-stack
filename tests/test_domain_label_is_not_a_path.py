"""
A domain is a LABEL, not a path — on every write path, not just one.

FAILURE SPECIMEN, 2026-08-28 (live, not synthesized): a `propose_learning`
proposal was filed with a domain containing a "/", was approved, and died at
commit. `record_learning` addresses its shard as `learnings_dir/{applies_to}
.jsonl`, so the separator asked for a SUBDIRECTORY nobody had created and the
write came back as a bare ENOENT. The proposal sits in `commit_failed` to this
day. `record_insight` has refused this since P1 (mesh-20260719); its two
sibling write paths — `record_learning` and `record_open_thread` — never got
the gate, so the same class of loss stayed open on both of them.

TWO PLACES, ON PURPOSE. The proposal-time gate (tested in
test_bridge_proposal_domain_gate.py) tells the PROPOSER, at the moment they can
still fix it. This gate is the last line: a caller that reaches storage by any
other route still cannot write an unreachable record, and the error it gets
names the value rather than a syscall.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sovereign_stack.memory import ExperientialMemory


def _mem(tmp_path: Path) -> ExperientialMemory:
    return ExperientialMemory(str(tmp_path / ".sovereign"))


BAD_LABELS = [
    "a/b",
    "tech-debt/compaction",
    "..",
    ".",
    "../escape",
    "a\\b",
    ".hidden",
    ".pre-md-backup-20260609",
]


class TestRecordLearningRefusesAPath:
    @pytest.mark.parametrize("bad", BAD_LABELS)
    def test_refused(self, tmp_path, bad):
        mem = _mem(tmp_path)
        with pytest.raises(ValueError, match="domain"):
            mem.record_learning("happened", "learned", bad)

    def test_the_error_is_a_validation_error_not_an_oserror(self, tmp_path):
        """THE ACTUAL 2026-08-28 DEFECT. An OSError names a syscall; the
        proposer needs to be told which value was wrong and why."""
        mem = _mem(tmp_path)
        with pytest.raises(ValueError) as exc:
            mem.record_learning("h", "l", "a/b")
        assert "a/b" in str(exc.value)
        assert "not a path" in str(exc.value)

    def test_a_refused_label_writes_nothing(self, tmp_path):
        mem = _mem(tmp_path)
        with pytest.raises(ValueError):
            mem.record_learning("h", "l", "a/b")
        learnings = tmp_path / ".sovereign" / "chronicle" / "learnings"
        assert not any(learnings.rglob("*.jsonl"))

    def test_a_good_label_still_writes(self, tmp_path):
        mem = _mem(tmp_path)
        path = mem.record_learning("h", "l", "a,b,c")
        assert Path(path).name == "a,b,c.jsonl"

    def test_compound_label_is_normalized_like_record_insight(self, tmp_path):
        mem = _mem(tmp_path)
        assert Path(mem.record_learning("h", "l", "a, b")).name == "a,b.jsonl"


class TestRecordOpenThreadRefusesAPath:
    @pytest.mark.parametrize("bad", BAD_LABELS)
    def test_refused(self, tmp_path, bad):
        mem = _mem(tmp_path)
        with pytest.raises(ValueError, match="domain"):
            mem.record_open_thread("q?", "", bad)

    def test_a_refused_label_writes_nothing(self, tmp_path):
        mem = _mem(tmp_path)
        with pytest.raises(ValueError):
            mem.record_open_thread("q?", "", "a/b")
        threads = tmp_path / ".sovereign" / "chronicle" / "open_threads"
        assert not any(threads.rglob("*.jsonl"))

    def test_a_good_label_still_writes(self, tmp_path):
        mem = _mem(tmp_path)
        assert Path(mem.record_open_thread("q?", "", "a,b")).name == "a,b.jsonl"


class TestLeadingDotIsRefusedEverywhere:
    """A dotted shard is HIDDEN from `iter_thread_shards` — the house's ONE
    walk skips dotted paths deliberately, so that a `.pre-md-backup-…/`
    migration copy can never be folded back into the live corpus. A domain
    starting with a dot therefore writes a record that is on disk and
    reachable by nobody: SOP #10's exact shape, with an ok:true receipt.

    Measured before this was tightened: ZERO live domains under
    ~/.sovereign/chronicle/{insights,open_threads} begin with a dot."""

    def test_record_insight_refuses_it(self, tmp_path):
        mem = _mem(tmp_path)
        with pytest.raises(ValueError, match="must not start with"):
            mem.record_insight(".hidden", "c")

    def test_the_hidden_shard_would_have_been_unreachable(self, tmp_path):
        """The gate's justification, exercised rather than asserted: a dotted
        directory placed by hand is invisible to the walk every reader uses."""
        from sovereign_stack.memory import iter_thread_shards

        threads = tmp_path / ".sovereign" / "chronicle" / "open_threads"
        (threads / ".hidden").mkdir(parents=True)
        (threads / ".hidden" / "log.jsonl").write_text('{"resolved": false}\n')
        assert iter_thread_shards(threads) == []


class TestTheGateCanFail:
    """Law #2: a gate never shown to PASS on good input is over-tight."""

    @pytest.mark.parametrize("good", ["general", "a,b", "sovereign-stack", "v4.4-scout", "a.b"])
    def test_ordinary_labels_pass_all_three_write_paths(self, tmp_path, good):
        mem = _mem(tmp_path)
        assert mem.record_insight(good, "c")
        assert mem.record_learning("h", "l", good)
        assert mem.record_open_thread("q?", "", good)
