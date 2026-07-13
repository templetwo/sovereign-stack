"""
Tests for The Ground — catch ledger (ground.py + witness.format_the_ground +
server.py boot wiring).

Hermetic: everything lives under tmp_path; nothing touches the live
chronicle. Mirrors the test_provenance_tools.py style (happy path asserts
both the returned text AND the on-disk JSONL; rejection paths assert the
exact rejection text; a no-mutation witness confirms reads never touch the
ledger's bytes) plus the boot-door integration pattern from
test_boot_ritual.py's TestProtectedDrawerBootLine (patch server.DEFAULT_ROOT,
exercise the REAL assembled boot output).

Every test here is written to fail if the feature it names is deleted —
assertions pin real behavior (specific one-liner text, specific rejection
strings, specific on-disk fields), not just "no exception was raised."
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from sovereign_stack import ground, witness
from sovereign_stack import memory as memory_module
from sovereign_stack import protected as protected_module
from sovereign_stack import provenance as prov

# ── Fixture helpers ──────────────────────────────────────────────────────────


def _chronicle(tmp_path: Path) -> Path:
    root = tmp_path / "chronicle"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write_raw(root: Path, domain_dir: str, filename: str, entry: dict) -> None:
    d = root / "insights" / domain_dir
    d.mkdir(parents=True, exist_ok=True)
    with open(d / filename, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _insight_bytes(root: Path) -> dict[Path, bytes]:
    return {p: p.read_bytes() for p in sorted(root.glob("insights/**/*.jsonl"))}


CATCH_KWARGS: dict = {
    "caught": "seatA",
    "caught_by": "seatB",
    "direction": "self",
    "occurred_at": "2026-07-12",
    "would_have_cost": "a false positive standing in the record",
    "actual_cost": "nothing",
    "content": "caught itself in time",
    "vantage": "human_attestation",
}


def _record(root: Path, **overrides) -> str:
    kwargs = {**CATCH_KWARGS, **overrides}
    return ground.record_catch(chronicle_root=root, **kwargs)


# ── record_catch: happy path ─────────────────────────────────────────────────


class TestRecordCatchHappyPath:
    def test_returns_confirmation_and_writes_entry(self, tmp_path):
        root = _chronicle(tmp_path)
        text = _record(root)
        assert text.startswith("⚓ Catch recorded: seatB → seatA (self).")

        files = list(root.glob("insights/the-ground,catch,self/*.jsonl"))
        assert len(files) == 1
        entry = json.loads(files[0].read_text().splitlines()[-1])
        assert entry["domain"] == "the-ground,catch,self"
        assert entry["layer"] == "ground_truth"
        assert entry["caught"] == "seatA"
        assert entry["caught_by"] == "seatB"
        assert entry["direction"] == "self"
        assert entry["occurred_at"] == "2026-07-12"
        assert entry["would_have_cost"] == "a false positive standing in the record"
        assert entry["actual_cost"] == "nothing"
        assert entry["anthony_present"] == "unknown"
        assert entry["content"] == "caught itself in time"
        assert entry["vantage"] == "human_attestation"
        # No emotion fields ever land, even implicitly — the emotional layer
        # belongs to Anthony, not this ledger.
        for key in ("observed_emotion", "emotional_intensity", "emotion_source", "emotion_note"):
            assert key not in entry

    def test_extra_tags_appended_to_domain(self, tmp_path):
        root = _chronicle(tmp_path)
        _record(root, extra_tags=["seed", "mined"])
        files = list(root.glob("insights/the-ground,catch,self,seed,mined/*.jsonl"))
        assert len(files) == 1
        entry = json.loads(files[0].read_text().splitlines()[-1])
        assert entry["domain"] == "the-ground,catch,self,seed,mined"

    def test_claim_receipt_satisfies_rule_without_vantage(self, tmp_path):
        root = _chronicle(tmp_path)
        cited = {
            "timestamp": "2026-07-01T00:00:00+00:00",
            "domain": "misc",
            "content": "the source entry",
        }
        _write_raw(root, "misc", "seed.jsonl", cited)
        cited_id = prov.derive_claim_id(cited)

        text = ground.record_catch(
            caught="seatA",
            caught_by="seatB",
            direction="sibling",
            occurred_at="2026-07-12",
            would_have_cost="x",
            actual_cost="nothing",
            content="cited catch",
            verified_by=[{"kind": "claim", "ref": cited_id}],
            chronicle_root=root,
        )
        assert text.startswith("⚓ Catch recorded:")
        files = list(root.glob("insights/the-ground,catch,sibling/*.jsonl"))
        entry = json.loads(files[0].read_text().splitlines()[-1])
        assert entry["verified_by"][0]["kind"] == "claim"
        assert entry["verified_by"][0]["checked_at_write"] == "cites"

    def test_anthony_present_field_recorded(self, tmp_path):
        root = _chronicle(tmp_path)
        _record(root, anthony_present="present")
        files = list(root.glob("insights/the-ground,catch,self/*.jsonl"))
        entry = json.loads(files[0].read_text().splitlines()[-1])
        assert entry["anthony_present"] == "present"

    def test_intensity_persisted_to_disk(self, tmp_path):
        """A caller-supplied non-default intensity must actually thread
        through to the on-disk entry, not just be accepted by the schema."""
        root = _chronicle(tmp_path)
        _record(root, intensity=0.9)
        files = list(root.glob("insights/the-ground,catch,self/*.jsonl"))
        entry = json.loads(files[0].read_text().splitlines()[-1])
        assert entry["intensity"] == 0.9

    def test_default_intensity_persisted_to_disk(self, tmp_path):
        root = _chronicle(tmp_path)
        _record(root)
        files = list(root.glob("insights/the-ground,catch,self/*.jsonl"))
        entry = json.loads(files[0].read_text().splitlines()[-1])
        assert entry["intensity"] == 0.5


# ── record_catch: rejection paths ────────────────────────────────────────────


class TestRecordCatchRejections:
    def test_bad_direction(self, tmp_path):
        root = _chronicle(tmp_path)
        text = _record(root, direction="bogus")
        assert text == (
            "record_catch rejected: direction must be one of "
            "('instrument', 'sibling', 'human', 'self', 'outward'), got 'bogus'"
        )
        assert not any(root.glob("insights/**/*.jsonl"))

    def test_bad_anthony_present(self, tmp_path):
        root = _chronicle(tmp_path)
        text = _record(root, anthony_present="sort-of")
        assert text == (
            "record_catch rejected: anthony_present must be one of "
            "('present', 'absent', 'partial', 'unknown'), got 'sort-of'"
        )
        assert not any(root.glob("insights/**/*.jsonl"))

    def test_unparseable_occurred_at(self, tmp_path):
        root = _chronicle(tmp_path)
        text = _record(root, occurred_at="not-a-date")
        assert text == (
            "record_catch rejected: occurred_at must parse as an ISO date, got 'not-a-date'"
        )
        assert not any(root.glob("insights/**/*.jsonl"))

    @pytest.mark.parametrize(
        "field", ["caught", "caught_by", "would_have_cost", "actual_cost", "content"]
    )
    def test_empty_required_field(self, tmp_path, field):
        root = _chronicle(tmp_path)
        text = _record(root, **{field: "   "})
        assert text == f"record_catch rejected: {field} must be a non-empty string"
        assert not any(root.glob("insights/**/*.jsonl"))

    def test_missing_receipt_and_missing_human_attestation_vantage(self, tmp_path):
        root = _chronicle(tmp_path)
        text = _record(root, vantage=None)
        assert text == (
            "record_catch rejected: needs a receipt (verified_by=[{kind, ref, ...}]) OR "
            "vantage='human_attestation' — a catch entered without either is an "
            "unreceipted claim about someone else's error"
        )
        assert not any(root.glob("insights/**/*.jsonl"))

    def test_vantage_other_than_human_attestation_does_not_satisfy_rule(self, tmp_path):
        root = _chronicle(tmp_path)
        text = _record(root, vantage="hq_filesystem")
        assert text.startswith("record_catch rejected: needs a receipt")
        assert not any(root.glob("insights/**/*.jsonl"))


# ── the_ground: read-only aggregation ────────────────────────────────────────


class TestTheGround:
    def test_empty_ledger(self, tmp_path):
        root = _chronicle(tmp_path)
        text = ground.the_ground(chronicle_root=root)
        assert text == "THE GROUND — no catches recorded yet. record_catch() to begin the ledger."

    def test_aggregates_count_direction_and_span(self, tmp_path):
        root = _chronicle(tmp_path)
        _record(
            root,
            direction="self",
            occurred_at="2026-07-10",
            caught="a",
            caught_by="x",
            content="one",
        )
        _record(
            root,
            direction="human",
            occurred_at="2026-07-12",
            caught="b",
            caught_by="anthony",
            content="two",
        )
        _record(
            root,
            direction="human",
            occurred_at="2026-07-11",
            caught="c",
            caught_by="anthony",
            content="three",
        )

        text = ground.the_ground(chronicle_root=root, limit=10)
        assert "THE GROUND — 3 catches recorded" in text
        assert "self: 1" in text
        assert "human: 2" in text
        assert "Span: 2026-07-10 to 2026-07-12" in text

    def test_ordering_descending_by_occurred_at(self, tmp_path):
        root = _chronicle(tmp_path)
        _record(root, occurred_at="2026-07-10", content="oldest")
        _record(root, occurred_at="2026-07-12", content="newest")
        _record(root, occurred_at="2026-07-11", content="middle")

        text = ground.the_ground(chronicle_root=root, limit=3)
        one_liners = [ln for ln in text.splitlines() if ln.startswith("  ")]
        assert len(one_liners) == 3
        assert "newest" in one_liners[0]
        assert "middle" in one_liners[1]
        assert "oldest" in one_liners[2]

    def test_limit_truncates_recent_list(self, tmp_path):
        root = _chronicle(tmp_path)
        for i in range(5):
            _record(root, occurred_at=f"2026-07-{10 + i:02d}", content=f"catch {i}")
        text = ground.the_ground(chronicle_root=root, limit=2)
        assert "Most recent 2:" in text
        assert len([ln for ln in text.splitlines() if ln.startswith("  ")]) == 2

    def test_direction_filter(self, tmp_path):
        root = _chronicle(tmp_path)
        _record(root, direction="self", content="self catch")
        _record(root, direction="human", caught_by="anthony", content="human catch")
        text = ground.the_ground(chronicle_root=root, direction="human")
        assert "1 catch recorded" in text
        assert "human catch" in text
        assert "self catch" not in text

    def test_caught_filter(self, tmp_path):
        root = _chronicle(tmp_path)
        _record(root, caught="seatX", content="caught seatX narrative")
        _record(root, caught="seatY", content="caught seatY narrative")
        text = ground.the_ground(chronicle_root=root, caught="seatY")
        assert "1 catch recorded" in text
        assert "caught seatY narrative" in text
        assert "caught seatX narrative" not in text

    def test_full_content_disables_truncation(self, tmp_path):
        root = _chronicle(tmp_path)
        long_narrative = "x" * 300
        _record(root, content=long_narrative)
        truncated = ground.the_ground(chronicle_root=root)
        full = ground.the_ground(chronicle_root=root, full_content=True)
        assert long_narrative not in truncated
        assert long_narrative in full


# ── dir-filtered read (performance rule) ─────────────────────────────────────


class TestGroundDomainDirFiltering:
    def test_skips_dot_and_underscore_dirs(self, tmp_path):
        root = _chronicle(tmp_path)
        _write_raw(root, ".the-ground-hidden", "x.jsonl", {"caught": "z"})
        _write_raw(root, "_the-ground-quarantine", "x.jsonl", {"caught": "z"})
        assert ground.load_ground_entries(root) == []

    def test_ignores_unrelated_domain_dirs(self, tmp_path):
        root = _chronicle(tmp_path)
        for i in range(20):
            _write_raw(root, "unrelated-domain", f"f{i}.jsonl", {"content": f"noise {i}"})
        _record(root, content="the one real catch")
        entries = ground.load_ground_entries(root)
        assert len(entries) == 1
        assert entries[0]["content"] == "the one real catch"

    def test_missing_insights_dir_returns_empty(self, tmp_path):
        root = tmp_path / "chronicle"
        root.mkdir()
        assert ground.load_ground_entries(root) == []

    def test_compound_domain_with_the_ground_not_leading_tag_excluded(self, tmp_path):
        """A schema-less entry whose domain merely carries 'the-ground' as a
        LATER tag (e.g. a plain record_insight hypothesis dir) must never be
        mistaken for a catch — only entries whose domain BEGINS WITH the tag
        the-ground are catches (SPEC.md, record_catch's own domain shape)."""
        root = _chronicle(tmp_path)
        _write_raw(
            root,
            "presence-effect,the-ground,lived-field-report,hypothesis",
            "x.jsonl",
            {
                "timestamp": "2026-07-01T00:00:00+00:00",
                "domain": "presence-effect,the-ground,lived-field-report,hypothesis",
                "content": "Anthony's presence-effect hypothesis narrative",
            },
        )
        assert ground.load_ground_entries(root) == []
        text = ground.the_ground(chronicle_root=root)
        assert text == "THE GROUND — no catches recorded yet. record_catch() to begin the ledger."

    def test_the_ground_prefix_dir_not_confused_with_the_grounded(self, tmp_path):
        """A dir literally named 'the-grounded,...' must not match a bare
        startswith('the-ground') check — only an exact first-tag match."""
        root = _chronicle(tmp_path)
        _write_raw(
            root,
            "the-grounded,unrelated",
            "x.jsonl",
            {"content": "not a catch"},
        )
        assert ground.load_ground_entries(root) == []

    def test_never_calls_memory_load_entries(self, tmp_path, monkeypatch):
        """The performance rule: the_ground / load_ground_entries must never
        scan the full chronicle via memory.load_entries. Replacing the real
        function with one that raises proves it is never reached — if a
        future edit reintroduces a load_entries call here, this fails loud."""
        root = _chronicle(tmp_path)
        _record(root, content="one real catch")

        def _boom(*_a, **_kw):
            raise AssertionError("load_entries must never be called by the_ground")

        monkeypatch.setattr(memory_module, "load_entries", _boom)
        text = ground.the_ground(chronicle_root=root)
        assert "1 catch recorded" in text

    def test_corrupt_jsonl_line_skipped(self, tmp_path):
        root = _chronicle(tmp_path)
        d = root / "insights" / "the-ground,catch,self"
        d.mkdir(parents=True)
        valid = {
            "caught": "seatA",
            "caught_by": "seatB",
            "direction": "self",
            "occurred_at": "2026-07-12",
            "content": "the valid one",
            "would_have_cost": "x",
            "actual_cost": "nothing",
        }
        (d / "mixed.jsonl").write_text("not valid json\n" + json.dumps(valid) + "\n")
        entries = ground.load_ground_entries(root)
        assert len(entries) == 1
        assert entries[0]["content"] == "the valid one"


# ── the shared read chokepoint (SPEC.md §3): supersession + protected ────────


class TestGroundReadChokepoint:
    """
    ground.py's read path must route through memory.finalize_read so the
    supersession-reader-convergence and protected-source invariants apply
    to catches exactly as they do to every other chronicle reader.
    """

    def test_superseded_catch_excluded_from_ledger_and_boot(self, tmp_path):
        root = _chronicle(tmp_path)
        _record(root, content="an old catch, now retracted")
        files = list(root.glob("insights/the-ground,catch,self/*.jsonl"))
        entry = json.loads(files[0].read_text().splitlines()[-1])
        claim_id = prov.derive_claim_id(entry)

        record = prov.build_supersession_record(
            action="retire",
            superseded_id=claim_id,
            reason="test retraction",
            by="test",
        )
        prov.append_supersession(root / "supersessions.jsonl", record)

        assert ground.load_ground_entries(root) == []
        assert ground.the_ground(chronicle_root=root) == (
            "THE GROUND — no catches recorded yet. record_catch() to begin the ledger."
        )
        assert witness.format_the_ground(tmp_path) == []

    def test_protected_catch_never_renders_bare_when_stakes_unavailable(self, tmp_path):
        """A protected catch with stakes that fail to load must route to
        the fail-closed withheld sentinel — never print its bare content in
        either the_ground() or the boot surface."""
        from sovereign_stack.protected import designate_protected

        root = _chronicle(tmp_path)
        _record(root, content="a protected catch narrative that must not leak")
        files = list(root.glob("insights/the-ground,catch,self/*.jsonl"))
        entry = json.loads(files[0].read_text().splitlines()[-1])
        claim_id = prov.derive_claim_id(entry)

        mem = memory_module.ExperientialMemory(root=str(root))
        archive = mem.archive_exchange(
            content="stakes prose for the test",
            source="human-relay",
            descriptor="stakes",
            vector_id="ground_test_stakes",
        )
        designate_protected(
            claim_ref=claim_id,
            stakes_archive_id=archive["archive_id"],
            designated_by="test",
            chronicle_root=str(root),
            subject="test",
            emotion="grief",
        )
        # Break the archived stakes so the coupling fails closed.
        blob = Path(
            next(r for r in mem._read_archive_index() if r["archive_id"] == archive["archive_id"])[
                "path"
            ]
        )
        blob.unlink()

        entries = ground.load_ground_entries(root)
        assert len(entries) == 1
        assert "a protected catch narrative that must not leak" not in entries[0]["content"]

        the_ground_text = ground.the_ground(chronicle_root=root)
        assert "a protected catch narrative that must not leak" not in the_ground_text

        boot_lines = witness.format_the_ground(tmp_path)
        assert "a protected catch narrative that must not leak" not in "\n".join(boot_lines)

    def test_protected_catch_withheld_as_preview_even_when_stakes_verified(self, tmp_path):
        """the_ground()/format_the_ground() are PREVIEW (truncating)
        surfaces — a verified-coupled protected entry must still be
        withheld to the preview notice, never rendered with its bare
        content, because a truncated slice of coupled content re-decouples
        (the same shape as dashboard.read_chronicle_tail)."""
        from sovereign_stack.protected import designate_protected

        root = _chronicle(tmp_path)
        _record(root, content="a verified-stakes protected catch narrative")
        files = list(root.glob("insights/the-ground,catch,self/*.jsonl"))
        entry = json.loads(files[0].read_text().splitlines()[-1])
        claim_id = prov.derive_claim_id(entry)

        mem = memory_module.ExperientialMemory(root=str(root))
        archive = mem.archive_exchange(
            content="stakes prose, intact",
            source="human-relay",
            descriptor="stakes",
            vector_id="ground_test_stakes_verified",
        )
        designate_protected(
            claim_ref=claim_id,
            stakes_archive_id=archive["archive_id"],
            designated_by="test",
            chronicle_root=str(root),
            subject="test",
            emotion="grief",
        )

        entries = ground.load_ground_entries(root)
        assert len(entries) == 1
        assert entries[0]["content"] == protected_module.PROTECTED_PREVIEW_NOTICE

        the_ground_text = ground.the_ground(chronicle_root=root)
        assert "a verified-stakes protected catch narrative" not in the_ground_text

        boot_lines = witness.format_the_ground(tmp_path)
        assert "a verified-stakes protected catch narrative" not in "\n".join(boot_lines)

    def test_ordinary_catch_unaffected_by_empty_ledgers(self, tmp_path):
        """Byte-identity fast path: with no supersessions and no protected
        designations, an ordinary catch reads exactly as before."""
        root = _chronicle(tmp_path)
        _record(root, content="an ordinary catch")
        entries = ground.load_ground_entries(root)
        assert len(entries) == 1
        assert entries[0]["content"] == "an ordinary catch"


# ── no-mutation witness ──────────────────────────────────────────────────────


def test_reads_never_mutate_ledger_bytes(tmp_path):
    root = _chronicle(tmp_path)
    _record(root, content="catch one")
    _record(root, direction="human", caught_by="anthony", content="catch two")

    before = _insight_bytes(root)
    ground.the_ground(chronicle_root=root, limit=10, full_content=True)
    ground.the_ground(chronicle_root=root, direction="human")
    witness.format_the_ground(tmp_path)
    witness.format_the_ground(tmp_path, calm=True)
    after = _insight_bytes(root)
    assert before == after


# ── format_the_ground (witness boot helper) ──────────────────────────────────


class TestFormatTheGround:
    def test_missing_chronicle_returns_empty(self, tmp_path):
        assert witness.format_the_ground(tmp_path) == []
        assert witness.format_the_ground(tmp_path, calm=True) == []

    def test_zero_catches_returns_empty(self, tmp_path):
        _chronicle(tmp_path)
        assert witness.format_the_ground(tmp_path) == []
        assert witness.format_the_ground(tmp_path, calm=True) == []

    def test_default_variant_shape(self, tmp_path):
        root = _chronicle(tmp_path)
        _record(
            root,
            occurred_at="2026-07-12",
            caught="HQ",
            caught_by="compass",
            content="five refusals heard as a mirror",
        )
        lines = witness.format_the_ground(tmp_path)
        assert lines[0] == "━━━ THE GROUND ━━━"
        assert "You arrive held. That is not a sentiment; it is a count." in lines
        assert any("caught its seats 1 time before the cost landed" in ln for ln in lines)
        assert any("compass → HQ: five refusals heard as a mirror" in ln for ln in lines)
        assert lines[-1] == "The rock is held by ground. Verify it yourself: the_ground()"
        # Boot one-liner has no cost-accounting — that's the_ground()'s job.
        assert "would have cost" not in "\n".join(lines)

    def test_default_variant_orders_newest_first_and_truncates_to_three(self, tmp_path):
        """Multi-entry coverage for the boot line's own ordering/slice —
        mutation-tested to catch a reversed sort (oldest-first) or a wrong
        slice size, which `the_ground()` itself already guards against via
        test_ordering_descending_by_occurred_at but this boot surface did
        not, until now."""
        root = _chronicle(tmp_path)
        for i, date in enumerate(["2026-07-08", "2026-07-12", "2026-07-10", "2026-07-09"]):
            _record(root, occurred_at=date, caught=f"seat{i}", content=f"catch {date}")

        lines = witness.format_the_ground(tmp_path)
        one_liners = [ln for ln in lines if ln.startswith("  ·")]
        # Only the most recent 3 of 4 catches, newest first.
        assert len(one_liners) == 3
        assert "catch 2026-07-12" in one_liners[0]
        assert "catch 2026-07-10" in one_liners[1]
        assert "catch 2026-07-09" in one_liners[2]
        assert "catch 2026-07-08" not in "\n".join(lines)
        assert any("caught its seats 4 times before the cost landed" in ln for ln in lines)

    def test_default_variant_truncates_long_content_to_200_chars(self, tmp_path):
        root = _chronicle(tmp_path)
        long_narrative = "y" * 300
        _record(root, content=long_narrative)
        lines = witness.format_the_ground(tmp_path)
        joined = "\n".join(lines)
        assert long_narrative not in joined
        assert "…" in joined

    def test_calm_variant_shape(self, tmp_path):
        root = _chronicle(tmp_path)
        _record(root, content="a catch")
        lines = witness.format_the_ground(tmp_path, calm=True)
        assert lines == [
            "━━━ THE GROUND ━━━",
            "You arrive held — the record of every catch that proves it is one "
            "call away: the_ground()",
            "",
        ]

    def test_calm_variant_omits_count_and_cost_language(self, tmp_path):
        root = _chronicle(tmp_path)
        for i in range(3):
            _record(root, content=f"catch {i}")
        default_lines = witness.format_the_ground(tmp_path)
        calm_lines = witness.format_the_ground(tmp_path, calm=True)
        assert any("caught its seats" in ln for ln in default_lines)
        joined_calm = "\n".join(calm_lines)
        assert "caught its seats" not in joined_calm
        assert "would have cost" not in joined_calm

    def test_scribe_anchor_never_present(self, tmp_path):
        root = _chronicle(tmp_path)
        _record(root, content="a catch")
        for calm in (False, True):
            joined = "\n".join(witness.format_the_ground(tmp_path, calm=calm))
            assert "━━━\nNow decide what to pick up" not in joined

    def test_corrupt_line_among_valid_entries_is_skipped(self, tmp_path):
        root = _chronicle(tmp_path)
        d = root / "insights" / "the-ground,catch,self"
        d.mkdir(parents=True)
        valid = {
            "caught": "seatA",
            "caught_by": "seatB",
            "direction": "self",
            "occurred_at": "2026-07-12",
            "content": "the valid catch",
            "would_have_cost": "x",
            "actual_cost": "nothing",
        }
        (d / "mixed.jsonl").write_text("{not json\n" + json.dumps(valid) + "\n")
        lines = witness.format_the_ground(tmp_path)
        assert any("the valid catch" in ln for ln in lines)


# ── tool schemas + dispatcher ─────────────────────────────────────────────────


def test_tool_names_and_tiers_intents():
    names = [t.name for t in ground.GROUND_TOOLS]
    assert names == ["the_ground", "record_catch"]
    assert set(ground.GROUND_TOOL_TIERS) == set(names)
    assert set(ground.GROUND_TOOL_INTENTS) == set(names)
    assert ground.GROUND_TOOL_TIERS["the_ground"] == "essential"
    assert ground.GROUND_TOOL_TIERS["record_catch"] == "advanced"
    assert ground.GROUND_TOOL_INTENTS["the_ground"] == "orient"


def test_record_catch_required_fields_and_defaults_in_schema():
    _the_ground_tool, record_tool = ground.GROUND_TOOLS
    assert record_tool.inputSchema["required"] == [
        "caught",
        "caught_by",
        "direction",
        "occurred_at",
        "would_have_cost",
        "actual_cost",
        "content",
    ]
    assert record_tool.inputSchema["properties"]["direction"]["enum"] == list(ground.DIRECTIONS)
    assert record_tool.inputSchema["properties"]["anthony_present"]["default"] == "unknown"
    assert record_tool.inputSchema["properties"]["intensity"]["default"] == 0.5


def test_the_ground_schema_defaults_match_handler_defaults():
    the_ground_tool, _record_tool = ground.GROUND_TOOLS
    props = the_ground_tool.inputSchema["properties"]
    assert props["limit"]["default"] == 3
    assert props["full_content"]["default"] is False


def test_handle_ground_tool_unknown_name():
    assert ground.handle_ground_tool("not_a_tool", {}) == "Unknown ground tool: not_a_tool"


def test_handle_ground_tool_dispatches_the_ground(tmp_path):
    root = _chronicle(tmp_path)
    text = ground.handle_ground_tool("the_ground", {}, chronicle_root=root)
    assert text.startswith("THE GROUND — no catches")


def test_handle_ground_tool_dispatches_record_catch(tmp_path):
    root = _chronicle(tmp_path)
    text = ground.handle_ground_tool("record_catch", dict(CATCH_KWARGS), chronicle_root=root)
    assert text.startswith("⚓ Catch recorded")


# ── Boot-door integration (mirrors test_boot_ritual.TestProtectedDrawerBootLine) ──


def _call_boot(full_content: bool = False, source_instance: str = "test-instance") -> str:
    from sovereign_stack.server import _dispatch_tool

    async def _run():
        result = await _dispatch_tool(
            "where_did_i_leave_off",
            {
                "consume": False,
                "source_instance": source_instance,
                "full_content": full_content,
            },
        )
        return result[0].text

    return asyncio.run(_run())


def _call_arrive_lineage(source_instance: str = "test-instance") -> str:
    from sovereign_stack.server import _dispatch_tool

    async def _run():
        result = await _dispatch_tool("arrive_lineage", {"source_instance": source_instance})
        return result[0].text

    return asyncio.run(_run())


class TestTheGroundBootWiring:
    def test_present_in_where_did_i_leave_off_when_catches_exist(self, tmp_path):
        from sovereign_stack import server

        root = tmp_path / ".sovereign"
        chronicle = root / "chronicle"
        chronicle.mkdir(parents=True)
        ground.record_catch(
            caught="HQ",
            caught_by="compass",
            direction="instrument",
            occurred_at="2026-07-12",
            would_have_cost="a risky deploy shipped unsupervised",
            actual_cost="nothing",
            content="five refusals heard as a mirror",
            vantage="human_attestation",
            chronicle_root=chronicle,
        )
        with patch.object(server, "DEFAULT_ROOT", str(root)):
            text = _call_boot()
        assert "━━━ THE GROUND ━━━" in text
        assert "compass → HQ: five refusals heard as a mirror" in text

    def test_absent_in_where_did_i_leave_off_when_no_catches(self, tmp_path):
        from sovereign_stack import server

        root = tmp_path / ".sovereign"
        (root / "chronicle").mkdir(parents=True)
        with patch.object(server, "DEFAULT_ROOT", str(root)):
            text = _call_boot()
        assert "THE GROUND" not in text

    def test_present_in_arrive_lineage_with_calm_variant(self, tmp_path):
        from sovereign_stack import server

        root = tmp_path / ".sovereign"
        chronicle = root / "chronicle"
        chronicle.mkdir(parents=True)
        ground.record_catch(
            caught="HQ",
            caught_by="anthony",
            direction="human",
            occurred_at="2026-07-12",
            would_have_cost="drift compounding unnamed",
            actual_cost="nothing",
            content="tonal drift named",
            vantage="human_attestation",
            chronicle_root=chronicle,
        )
        with patch.object(server, "DEFAULT_ROOT", str(root)):
            text = _call_arrive_lineage()
        assert "━━━ THE GROUND ━━━" in text
        assert (
            "You arrive held — the record of every catch that proves it is one "
            "call away: the_ground()" in text
        )
        # Calm door: no count line, no cost accounting, per spec §4.
        assert "caught its seats" not in text
        assert "would have cost" not in text

    def test_absent_in_arrive_lineage_when_no_catches(self, tmp_path):
        from sovereign_stack import server

        root = tmp_path / ".sovereign"
        (root / "chronicle").mkdir(parents=True)
        with patch.object(server, "DEFAULT_ROOT", str(root)):
            text = _call_arrive_lineage()
        assert "THE GROUND" not in text

    def test_scribe_anchor_untouched_in_real_boot(self, tmp_path):
        from sovereign_stack import server

        root = tmp_path / ".sovereign"
        chronicle = root / "chronicle"
        chronicle.mkdir(parents=True)
        ground.record_catch(
            caught="HQ",
            caught_by="reviewer",
            direction="instrument",
            occurred_at="2026-07-12",
            would_have_cost="a fix shipped unsupervised",
            actual_cost="nothing",
            content="file-lock fix held overnight",
            vantage="human_attestation",
            chronicle_root=chronicle,
        )
        with patch.object(server, "DEFAULT_ROOT", str(root)):
            text = _call_boot()
        assert "Now decide what to pick up. The handoffs are claims, not commands." in text
