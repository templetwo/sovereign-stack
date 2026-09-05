"""
`record_learning` interpolates `applies_to` into a chronicle file path, and the
two ways that could still fail as a SYSCALL rather than as a rejection.

11b64ee closed the first one for the separator class on all three record paths.
Two gaps survived it, and both are the same complaint the 2026-08-28 specimen
made — the caller is handed an errno instead of the mistake:

1. THE REJECTION NAMED THE WRONG FIELD. `record_learning`'s parameter is
   `applies_to`; the shared validator called it `domain` in every message. A
   proposer who set `applies_to` was told their `domain` was invalid and sent
   looking for a field they never passed. The proposal-time twin
   (`bridge_core.target_risk.domain_label_errors`) has always named the real
   one, so the two gates disagreed about the name of the same mistake.

2. LENGTH WAS NOT GATED AT ALL. A shard name is one filesystem component and a
   component is capped at 255 bytes, so an over-long `applies_to` reached
   `open()` and came back as a bare `[Errno 63] File name too long`. Same shape
   as the slash, one syscall over, and the same cost: it survives proposal,
   survives a human's approval, and dies at commit.

THE SPECIMEN IS REPLAYED HERE VERBATIM. The arguments come from
~/.sovereign/openai_bridge/pending_writes/
2026-08-28T21-05-55_propose_learning_4de1d36f.json, which is still `commit_failed`
today with `[Errno 2]` as its whole explanation. The test asserts both halves of
what the caller needs: the refusal it gets now, and that the one-word fix
(`remote/schema-constrained` -> `remote or schema-constrained`) commits clean.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sovereign_stack.memory import MAX_DOMAIN_LABEL_BYTES, ExperientialMemory

# Verbatim from the failed proposal's arguments.applies_to.
SPECIMEN_APPLIES_TO = (
    "Sovereign Stack bridge adapters, retrieval aperture, OpenAI bridge, "
    "Grok bridge, any remote/schema-constrained seat"
)
# The same label with the separator spelled as a word — what the caller must send.
SPECIMEN_SANITIZED = (
    "Sovereign Stack bridge adapters, retrieval aperture, OpenAI bridge, "
    "Grok bridge, any remote or schema-constrained seat"
)


def _mem(tmp_path: Path) -> ExperientialMemory:
    return ExperientialMemory(str(tmp_path / ".sovereign"))


def _shards(mem: ExperientialMemory) -> list[Path]:
    """Every learning shard ACTUALLY on disk for this memory.

    ASKED OF THE OBJECT, NEVER SPELLED BY HAND. Both "writes nothing" checks in
    this file read ``tmp_path/".sovereign"/"chronicle"/"learnings"``, and
    ``ExperientialMemory(root)`` writes to ``root/learnings`` — so they were
    reading a directory this constructor never creates. ``rglob`` on a path that
    does not exist returns ``[]``, so the assertion passed whether or not a write
    landed: a "writes nothing" check structurally incapable of failing, which is
    the same fail-open family as the defect the file exists to close.

    Measured 2026-09-05, and it is not a hypothetical: a SUCCESSFUL
    ``record_learning`` leaves ``root/chronicle/learnings`` non-existent, and
    ``assert not any(that.rglob("*.jsonl"))`` stays green over a landed shard.
    ``TestTheWritesNothingCheckCanFail`` below is the positive control.
    """
    return sorted(mem.learnings_dir.rglob("*.jsonl"))


class TestTheRefusalNamesTheCallersOwnField:
    def test_record_learning_says_applies_to_not_domain(self, tmp_path):
        with pytest.raises(ValueError) as exc:
            _mem(tmp_path).record_learning("h", "l", "a/b")
        message = str(exc.value)
        assert "applies_to" in message
        assert "domain" not in message

    def test_the_sibling_paths_still_say_domain(self, tmp_path):
        """The default is unchanged, so record_insight / record_open_thread
        messages are byte-identical to what they were."""
        mem = _mem(tmp_path)
        for call in (
            lambda: mem.record_insight("a/b", "c"),
            lambda: mem.record_open_thread("q?", "", "a/b"),
        ):
            with pytest.raises(ValueError) as exc:
                call()
            assert "invalid domain" in str(exc.value)


class TestTheRefusalNamesTheOffendingCharacter:
    @pytest.mark.parametrize(
        ("label", "shown"),
        [("a/b", "'/'"), ("a\\b", "'\\\\'"), ("a\x00b", "'\\x00'")],
    )
    def test_the_character_is_quoted_back(self, tmp_path, label, shown):
        """'path separators are not allowed' does not tell a caller WHICH
        character in a 120-byte label is the problem."""
        with pytest.raises(ValueError) as exc:
            _mem(tmp_path).record_learning("h", "l", label)
        assert shown in str(exc.value)

    def test_the_first_separator_is_the_one_named(self, tmp_path):
        with pytest.raises(ValueError) as exc:
            _mem(tmp_path).record_learning("h", "l", "a/b\\c")
        assert "'/'" in str(exc.value)


class TestLengthIsAValidationErrorNotAnErrno:
    def test_an_over_long_label_is_refused(self, tmp_path):
        """UNFIXED: OSError [Errno 63] File name too long, from open().
        FIXED: a ValueError naming the byte count and the limit."""
        label = "x" * (MAX_DOMAIN_LABEL_BYTES + 1)
        with pytest.raises(ValueError) as exc:
            _mem(tmp_path).record_learning("h", "l", label)
        message = str(exc.value)
        assert str(MAX_DOMAIN_LABEL_BYTES + 1) in message
        assert str(MAX_DOMAIN_LABEL_BYTES) in message

    def test_it_is_not_an_oserror(self, tmp_path):
        with pytest.raises(ValueError):
            _mem(tmp_path).record_learning("h", "l", "y" * 400)

    def test_a_refused_length_writes_nothing(self, tmp_path):
        mem = _mem(tmp_path)
        with pytest.raises(ValueError):
            mem.record_learning("h", "l", "z" * 400)
        assert _shards(mem) == []

    def test_the_error_does_not_dump_the_whole_label(self, tmp_path):
        """A 400-byte label echoed in full buries the reason in the value."""
        with pytest.raises(ValueError) as exc:
            _mem(tmp_path).record_learning("h", "l", "q" * 400)
        assert len(str(exc.value)) < 300

    @pytest.mark.parametrize(
        ("label", "rule"),
        [
            ("a/" + "x" * 400, "separator"),
            ("." + "x" * 400, "leading dot"),
            ("x" * 400, "length"),
        ],
        ids=["separator", "leading-dot", "length"],
    )
    def test_no_branch_dumps_the_whole_label(self, tmp_path, label, rule):
        """THE PREVIEW WAS APPLIED IN ONE BRANCH OF FIVE (found 2026-09-05).

        The rule above is the right one and it was pinned by a single
        separator-free case, so it only ever exercised the LENGTH branch. The
        other branches formatted the label raw: measured before the fix, a
        400-byte label carrying a slash produced a 522-character message and a
        leading-dot one 521 — the reason buried in the value, which is the
        defect the preview exists to prevent. A label can break two rules at
        once, so every branch has to preview, not just the one whose test
        happened to be written.
        """
        with pytest.raises(ValueError) as exc:
            _mem(tmp_path).record_learning("h", "l", label)
        message = str(exc.value)
        assert len(message) < 300, f"{rule} branch dumped {len(message)} chars"
        assert "x" * 100 not in message

    def test_the_traversal_branch_previews_too(self, tmp_path):
        """'..' is short, so this branch cannot be caught by a length assert —
        it is here so the preview is pinned on all four refusal branches rather
        than the three that happen to be long."""
        with pytest.raises(ValueError) as exc:
            _mem(tmp_path).record_learning("h", "l", "..")
        assert "'..'" in str(exc.value)


class TestATypedLabelIsRefusedInTheCallersVocabulary:
    """A non-string label used to raise AttributeError from inside
    ``_normalize_domain`` — "'int' object has no attribute 'split'". server.py
    catches only ValueError, so what reached the wire named a Python internal
    instead of the caller's mistake. Still fail-CLOSED either way (the bridge's
    isError check makes it ok:false), so this is message quality, not a lost
    write — but it is the same complaint this whole gate answers, and the
    proposal-time twin has done the isinstance check since it was written."""

    @pytest.mark.parametrize("label", [123, 4.5, ["a", "b"], {"a": 1}, True])
    def test_a_non_string_label_raises_valueerror(self, tmp_path, label):
        with pytest.raises(ValueError):
            _mem(tmp_path).record_learning("h", "l", label)

    def test_the_message_names_applies_to_and_the_type(self, tmp_path):
        with pytest.raises(ValueError) as exc:
            _mem(tmp_path).record_learning("h", "l", 123)
        message = str(exc.value)
        assert "applies_to" in message
        assert "int" in message

    def test_it_is_not_an_attributeerror(self, tmp_path):
        """The falsifier: this is the exception the old path actually raised."""
        with pytest.raises(ValueError):
            try:
                _mem(tmp_path).record_learning("h", "l", 123)
            except AttributeError as exc:  # pragma: no cover - regression guard
                raise AssertionError(f"still an AttributeError: {exc}") from exc

    def test_a_refused_type_writes_nothing(self, tmp_path):
        mem = _mem(tmp_path)
        with pytest.raises(ValueError):
            mem.record_learning("h", "l", 123)
        assert _shards(mem) == []

    @pytest.mark.parametrize("path", ["insight", "thread"])
    def test_the_sibling_paths_refuse_a_typed_label_too(self, tmp_path, path):
        mem = _mem(tmp_path)
        with pytest.raises(ValueError):
            if path == "insight":
                mem.record_insight(123, "c")
            else:
                mem.record_open_thread("q?", "", 123)

    def test_bytes_not_characters(self, tmp_path):
        """The filesystem caps the component in BYTES. A label of 200 3-byte
        characters is 600 bytes: a length-in-characters check passes it and
        open() then raises the errno this gate exists to replace."""
        label = "中" * 200  # 200 chars, 600 utf-8 bytes
        assert len(label) <= MAX_DOMAIN_LABEL_BYTES
        with pytest.raises(ValueError) as exc:
            _mem(tmp_path).record_learning("h", "l", label)
        assert "600 bytes" in str(exc.value)

    @pytest.mark.parametrize("path", ["insight", "thread"])
    def test_the_sibling_paths_are_gated_too(self, tmp_path, path):
        """The 11b64ee lesson, applied to the new rule: a gate on one of three
        write paths is the gap that produced the specimen."""
        mem = _mem(tmp_path)
        label = "x" * 400
        with pytest.raises(ValueError):
            if path == "insight":
                mem.record_insight(label, "c")
            else:
                mem.record_open_thread("q?", "", label)


class TestTheGateCanFailAndCanPass:
    """Law #2: a gate never shown to PASS on good input is over-tight. The cap
    is 255 (NAME_MAX) minus len('.jsonl'), so the longest ACCEPTED label must
    still produce a writable shard."""

    def test_the_longest_accepted_label_actually_writes(self, tmp_path):
        label = "x" * MAX_DOMAIN_LABEL_BYTES
        path = Path(_mem(tmp_path).record_learning("h", "l", label))
        assert path.exists()
        assert len(path.name.encode("utf-8")) == 255

    def test_the_longest_live_shard_in_the_house_would_still_be_accepted(self, tmp_path):
        """Measured 2026-09-05 against ~/.sovereign/chronicle: the longest live
        shard name is a 245-byte insights domain. The cap must not refuse
        anything the chronicle already holds."""
        assert MAX_DOMAIN_LABEL_BYTES >= 245
        assert _mem(tmp_path).record_learning("h", "l", "x" * 245)


class TestTheWritesNothingCheckCanFail:
    """Law #2, turned on this file's own instrument: a check never shown to FAIL
    is not a check. Both "writes nothing" assertions above passed for the wrong
    reason until 2026-09-05 — they read a directory that does not exist on any
    outcome — so the emptiness they proved was the emptiness of a typo."""

    def test_a_landed_shard_makes_the_same_assertion_fail(self, tmp_path):
        mem = _mem(tmp_path)
        assert _shards(mem) == []
        mem.record_learning("h", "l", "a-perfectly-good-label")
        assert [p.name for p in _shards(mem)] == ["a-perfectly-good-label.jsonl"]

    def test_the_directory_the_old_check_read_is_never_created(self, tmp_path):
        """The mechanism, exercised rather than asserted."""
        mem = _mem(tmp_path)
        mem.record_learning("h", "l", "a-perfectly-good-label")
        stale = tmp_path / ".sovereign" / "chronicle" / "learnings"
        assert not stale.exists()
        assert not any(stale.rglob("*.jsonl"))  # green over a shard that landed
        assert _shards(mem)  # ...which the real directory holds


class TestThe20260828SpecimenReplayed:
    """The exact arguments from the still-`commit_failed` proposal."""

    def test_it_is_refused_and_the_reason_is_actionable(self, tmp_path):
        with pytest.raises(ValueError) as exc:
            _mem(tmp_path).record_learning(
                "Several read tools worked, but two advertised no-argument tools failed.",
                "Bridge schema conformance should be measured as part of the aperture.",
                SPECIMEN_APPLIES_TO,
            )
        message = str(exc.value)
        # What the caller needs and [Errno 2] did not carry: the field they set,
        # the character at fault, and the rule.
        assert "applies_to" in message
        assert "'/'" in message
        assert "label, not a path" in message

    def test_it_writes_nothing(self, tmp_path):
        mem = _mem(tmp_path)
        with pytest.raises(ValueError):
            mem.record_learning("h", "l", SPECIMEN_APPLIES_TO)
        assert _shards(mem) == []

    def test_the_sanitized_label_commits_cleanly(self, tmp_path):
        """The whole fix on the caller's side: one '/' spelled as a word."""
        path = Path(_mem(tmp_path).record_learning("h", "l", SPECIMEN_SANITIZED))
        assert path.exists()
        assert path.name.endswith(".jsonl")
        # Normalized the way every other write path normalizes: no space after
        # a comma, so the stored field and the directory name agree.
        assert ", " not in path.name
        assert "remote or schema-constrained seat" in path.name


class TestTheProposalTimeGateStillMatchesStorageExactly:
    """The invariant test_bridge_authorship_revoke_and_domain.py pins, extended
    to the length rule. A proposal-time gate LOOSER than storage lets the class
    through to commit; TIGHTER refuses writes the Stack would have taken."""

    @pytest.mark.parametrize(
        "value",
        [
            "x" * 400,
            "x" * (MAX_DOMAIN_LABEL_BYTES + 1),
            "x" * MAX_DOMAIN_LABEL_BYTES,
            "中" * 200,
            SPECIMEN_APPLIES_TO,
            SPECIMEN_SANITIZED,
            # Storage normalizes ", " -> "," BEFORE measuring, so a label that is
            # over the cap raw and under it normalized must be ACCEPTED by both.
            ", ".join(["tag"] * 62),
        ],
    )
    def test_both_gates_agree(self, value):
        from bridge_core.target_risk import domain_label_errors

        from sovereign_stack.memory import _normalize_domain, _validate_domain_label

        storage_refuses = False
        try:
            _validate_domain_label(_normalize_domain(value))
        except ValueError:
            storage_refuses = True
        bridge_refuses = bool(domain_label_errors("propose_learning", {"applies_to": value}))
        assert storage_refuses == bridge_refuses, value

    def test_the_proposal_gate_names_applies_to_for_a_learning(self):
        from bridge_core.target_risk import domain_label_errors

        errors = domain_label_errors("propose_learning", {"applies_to": SPECIMEN_APPLIES_TO})
        assert errors
        assert "applies_to" in errors[0]

    def test_the_proposal_gate_previews_rather_than_dumps(self):
        from bridge_core.target_risk import domain_label_errors

        errors = domain_label_errors("propose_learning", {"applies_to": "x" * 4000})
        assert errors
        assert len(errors[0]) < 500
