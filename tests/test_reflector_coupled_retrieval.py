"""
Reflector coupled-retrieval + coverage-honesty tests.

The synthesis daemon is a truncating surface that feeds an external API, and
its projection strips the coupling annotations finalize_read attaches — so a
designated protected record reaching its prompt would travel decoupled.
These tests prove three fixes, each of which FAILS on the unfixed daemon:

  1. The window readers EXCLUDE designated protected records entirely
     (canonical ledger membership + the chokepoint's read-time markers,
     never a domain-name string match) — exclusion, not preview, because
     marginalia about protected content is itself a decoupling risk.
  2. The written reflection record stops reporting the feed cap as the
     count: entries_in_range vs entries_count(fed) + a truncated flag
     (the aae7281 envelope class).
  3. Reflections cite the fed window by derived claim id (the canonical
     sha256(timestamp+domain+content) preimage), alongside the model's
     positional labels, so they stay citable as the chronicle grows.

Every store here is tmp_path-rooted; every "protected" record and its
stakes prose is invented fixture text. The live ~/.sovereign is never read
or written.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sovereign_stack import protected, provenance
from sovereign_stack.daemons import synthesis_daemon
from sovereign_stack.daemons.synthesis_daemon import (
    SynthesisDaemon,
    build_prompt,
    read_recent_chronicle,
    read_spanning_chronicle,
    write_reflections,
)
from sovereign_stack.reflections import ack_reflection, list_reflections

# ── Fixtures ────────────────────────────────────────────────────────────────

PROTECTED_BODY = "invented-protected-body-alpha: a wholly fictional fixture sentence"
STAKES_PROSE = "invented-stakes-prose-alpha: fictional lived weight for the fixture"


@pytest.fixture
def insights_root(tmp_path: Path) -> Path:
    """An empty chronicle insights/ dir (chronicle root is its parent)."""
    root = tmp_path / "chronicle" / "insights"
    root.mkdir(parents=True)
    return root


def _entry_record(
    domain: str,
    timestamp: datetime,
    content: str,
    layer: str = "hypothesis",
) -> dict:
    return {
        "timestamp": timestamp.isoformat(),
        "domain": domain,
        "content": content,
        "layer": layer,
        "session_id": "test-session",
    }


def _write_record(insights_root: Path, record: dict) -> dict:
    domain_dir = insights_root / record["domain"]
    domain_dir.mkdir(parents=True, exist_ok=True)
    with (domain_dir / "test.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    return record


def _archive_stakes(chronicle_root: Path, stakes_text: str) -> str:
    """Hand-build a verified stakes blob + index line; returns archive_id."""
    blobs = chronicle_root / "archives" / "blobs"
    blobs.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256(stakes_text.encode("utf-8")).hexdigest()
    blob = blobs / f"{sha}.txt"
    blob.write_text(stakes_text, encoding="utf-8")
    with (chronicle_root / "archives" / "index.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"archive_id": sha, "path": str(blob), "sha256": sha}) + "\n")
    return sha


def _designate(chronicle_root: Path, record: dict, stakes_archive_id: str) -> str:
    """Append a fake protect designation for `record`; returns its claim id."""
    claim_id = provenance.derive_claim_id(record)
    ledger_record = protected.build_protected_record(
        claim_id=claim_id,
        stakes_archive_id=stakes_archive_id,
        designated_by="test-human",
        subject="fixture",
        emotion="steady",
        entry_timestamp=record["timestamp"],
    )
    protected.append_protected(chronicle_root / "protected.jsonl", ledger_record)
    return claim_id


# ── (1) Window exclusion of designated protected records ───────────────────


class TestWindowExcludesProtected:
    def test_coupled_protected_record_never_reaches_window(self, insights_root: Path):
        """The measured leak path: verified stakes -> finalize_read returns the
        entry COUPLED (bare content + _stakes) -> the unfixed projection strips
        the stakes and hands bare content to the prompt window."""
        now = datetime.now(timezone.utc)
        chronicle_root = insights_root.parent
        _write_record(insights_root, _entry_record("plain-domain", now, "ordinary finding"))
        prot = _write_record(
            insights_root, _entry_record("feels-domain", now - timedelta(minutes=1), PROTECTED_BODY)
        )
        archive_id = _archive_stakes(chronicle_root, STAKES_PROSE)
        _designate(chronicle_root, prot, archive_id)

        entries = read_recent_chronicle(chronicle_root=insights_root)

        assert len(entries) == 1
        assert entries[0]["content"] == "ordinary finding"
        flat = json.dumps(entries)
        assert PROTECTED_BODY not in flat
        assert STAKES_PROSE not in flat

    def test_withheld_sentinel_excluded_too(self, insights_root: Path):
        """Unloadable stakes -> finalize_read yields the withheld sentinel.
        Its notice leaks no content, but the window excludes it anyway:
        marginalia about a protected record is itself a decoupling risk."""
        now = datetime.now(timezone.utc)
        chronicle_root = insights_root.parent
        _write_record(insights_root, _entry_record("plain-domain", now, "ordinary finding"))
        prot = _write_record(
            insights_root, _entry_record("feels-domain", now - timedelta(minutes=1), PROTECTED_BODY)
        )
        # Dangling stakes pointer — resolves to no archive record at all.
        _designate(chronicle_root, prot, "deadbeef" * 8)

        entries = read_recent_chronicle(chronicle_root=insights_root)

        assert len(entries) == 1
        assert entries[0]["content"] == "ordinary finding"
        assert not any(e.get("_protected") for e in entries)

    def test_prompt_never_carries_protected_content(self, insights_root: Path):
        """End to end: window read -> build_prompt. The string that would go
        to the external API must carry neither content nor stakes."""
        now = datetime.now(timezone.utc)
        chronicle_root = insights_root.parent
        _write_record(insights_root, _entry_record("plain-domain", now, "ordinary finding"))
        prot = _write_record(
            insights_root, _entry_record("feels-domain", now - timedelta(minutes=1), PROTECTED_BODY)
        )
        archive_id = _archive_stakes(chronicle_root, STAKES_PROSE)
        _designate(chronicle_root, prot, archive_id)

        entries = read_recent_chronicle(chronicle_root=insights_root)
        prompt = build_prompt(entries)

        assert "ordinary finding" in prompt
        assert PROTECTED_BODY not in prompt
        assert STAKES_PROSE not in prompt

    def test_spanning_reader_excludes_protected(self, insights_root: Path):
        """The spanning sampler is the same surface; same exclusion."""
        now = datetime.now(timezone.utc)
        chronicle_root = insights_root.parent
        _write_record(
            insights_root, _entry_record("plain-domain", now - timedelta(days=1), "week-one note")
        )
        prot = _write_record(
            insights_root, _entry_record("feels-domain", now - timedelta(days=2), PROTECTED_BODY)
        )
        archive_id = _archive_stakes(chronicle_root, STAKES_PROSE)
        _designate(chronicle_root, prot, archive_id)

        entries = read_spanning_chronicle(
            chronicle_root=insights_root, span_weeks=4, entries_per_week=3
        )

        contents = [e["content"] for e in entries]
        assert "week-one note" in contents
        assert PROTECTED_BODY not in json.dumps(entries)

    def test_exclusion_is_ledger_membership_not_domain_match(self, insights_root: Path):
        """A non-designated record in the SAME domain as a protected one must
        still be fed — the test that a domain-name string match would fail."""
        now = datetime.now(timezone.utc)
        chronicle_root = insights_root.parent
        sibling = _write_record(
            insights_root, _entry_record("feels-domain", now, "undesignated sibling entry")
        )
        prot = _write_record(
            insights_root, _entry_record("feels-domain", now - timedelta(minutes=1), PROTECTED_BODY)
        )
        archive_id = _archive_stakes(chronicle_root, STAKES_PROSE)
        _designate(chronicle_root, prot, archive_id)

        entries = read_recent_chronicle(chronicle_root=insights_root)

        assert [e["content"] for e in entries] == [sibling["content"]]


# ── (2) Coverage honesty: in_range vs fed + truncated ───────────────────────


class TestCoverageHonesty:
    def test_read_recent_window_reports_in_range_before_cap(self, insights_root: Path):
        from sovereign_stack.daemons.synthesis_daemon import read_recent_window

        now = datetime.now(timezone.utc)
        for i in range(12):
            _write_record(
                insights_root,
                _entry_record(f"d{i}", now - timedelta(minutes=i), f"content {i}"),
            )
        entries, in_range = read_recent_window(chronicle_root=insights_root, max_entries=5)
        assert len(entries) == 5
        assert in_range == 12

    def test_read_spanning_window_reports_in_range_before_sampling(self, insights_root: Path):
        from sovereign_stack.daemons.synthesis_daemon import read_spanning_window

        now = datetime.now(timezone.utc)
        for i in range(6):
            _write_record(
                insights_root,
                _entry_record(f"d{i}", now - timedelta(hours=i + 1), f"e{i}"),
            )
        entries, in_range = read_spanning_window(
            chronicle_root=insights_root, span_weeks=1, entries_per_week=2
        )
        assert len(entries) <= 2
        assert in_range == 6

    def test_protected_records_never_count_as_in_range(self, insights_root: Path):
        from sovereign_stack.daemons.synthesis_daemon import read_recent_window

        now = datetime.now(timezone.utc)
        chronicle_root = insights_root.parent
        _write_record(insights_root, _entry_record("plain-domain", now, "ordinary finding"))
        prot = _write_record(
            insights_root, _entry_record("feels-domain", now - timedelta(minutes=1), PROTECTED_BODY)
        )
        archive_id = _archive_stakes(chronicle_root, STAKES_PROSE)
        _designate(chronicle_root, prot, archive_id)

        entries, in_range = read_recent_window(chronicle_root=insights_root)
        assert len(entries) == 1
        assert in_range == 1

    def test_run_records_honest_counts_and_claims(
        self, insights_root: Path, tmp_path: Path, monkeypatch
    ):
        """Full daemon path with the model call mocked: 3 eligible entries,
        cap 2 -> the written record says fed=2, in_range=3, truncated=True,
        and resolves 'ENTRY 2' to the second fed entry's derived claim id."""
        now = datetime.now(timezone.utc)
        newest = _write_record(insights_root, _entry_record("d-a", now, "newest entry"))
        second = _write_record(
            insights_root, _entry_record("d-b", now - timedelta(minutes=1), "second entry")
        )
        _write_record(
            insights_root, _entry_record("d-c", now - timedelta(minutes=2), "third entry")
        )

        raw = (
            '{"reflections": [{"observation": "the second entry stands alone",'
            ' "entries_referenced": ["ENTRY 2"],'
            ' "connection_type": "untouched_question", "confidence": "low"}]}'
        )
        monkeypatch.setattr(synthesis_daemon, "call_anthropic", lambda *a, **k: (True, raw))

        reflections_dir = tmp_path / "reflections"
        daemon = SynthesisDaemon(
            chronicle_root=insights_root,
            reflections_dir=reflections_dir,
            handoffs_dir=tmp_path / "handoffs",
            recent_hours=48,
            max_entries=2,
        )
        result = daemon.run()
        assert result.outcome == "wrote"

        [record] = [
            json.loads(line)
            for path in reflections_dir.glob("*.jsonl")
            for line in path.read_text().splitlines()
            if line.strip()
        ]
        assert record["entries_count"] == 2
        assert record["entries_in_range"] == 3
        assert record["truncated"] is True
        # Prompt order is newest-first: ENTRY 1 = newest, ENTRY 2 = second.
        assert record["window_claim_ids"] == [
            provenance.derive_claim_id(newest),
            provenance.derive_claim_id(second),
        ]
        assert record["entries_referenced_claims"] == [provenance.derive_claim_id(second)]

    def test_run_not_truncated_when_window_fits(
        self, insights_root: Path, tmp_path: Path, monkeypatch
    ):
        now = datetime.now(timezone.utc)
        _write_record(insights_root, _entry_record("d-a", now, "only entry"))
        raw = (
            '{"reflections": [{"observation": "one thing here",'
            ' "entries_referenced": ["ENTRY 1"],'
            ' "connection_type": "other", "confidence": "low"}]}'
        )
        monkeypatch.setattr(synthesis_daemon, "call_anthropic", lambda *a, **k: (True, raw))
        reflections_dir = tmp_path / "reflections"
        daemon = SynthesisDaemon(
            chronicle_root=insights_root,
            reflections_dir=reflections_dir,
            handoffs_dir=tmp_path / "handoffs",
            recent_hours=48,
        )
        assert daemon.run().outcome == "wrote"
        [record] = [
            json.loads(line)
            for path in reflections_dir.glob("*.jsonl")
            for line in path.read_text().splitlines()
            if line.strip()
        ]
        assert record["entries_in_range"] == 1
        assert record["truncated"] is False


# ── (3) Positional-label -> claim-id resolution ─────────────────────────────


class TestResolveEntryRefs:
    WINDOW = ["a" * 64, "b" * 64, "c" * 64]

    def test_entry_label_variants_resolve(self):
        from sovereign_stack.daemons.synthesis_daemon import resolve_entry_refs

        assert resolve_entry_refs(["ENTRY 2"], self.WINDOW) == ["b" * 64]
        assert resolve_entry_refs(["[ENTRY 1]"], self.WINDOW) == ["a" * 64]
        assert resolve_entry_refs(["entry #3"], self.WINDOW) == ["c" * 64]
        assert resolve_entry_refs(["2"], self.WINDOW) == ["b" * 64]

    def test_unresolvable_labels_map_to_none_not_guesses(self):
        from sovereign_stack.daemons.synthesis_daemon import resolve_entry_refs

        out = resolve_entry_refs(["sovereign-stack", "HANDOFF 2", "ENTRY 99"], self.WINDOW)
        # "HANDOFF 2" contains a number but names no chronicle position;
        # the bare-number fallback only fires on a bare number.
        assert out == [None, None, None]

    def test_empty_window_resolves_all_to_none(self):
        from sovereign_stack.daemons.synthesis_daemon import resolve_entry_refs

        assert resolve_entry_refs(["ENTRY 1"], []) == [None]
        assert resolve_entry_refs(["ENTRY 1"], None) == [None]


# ── Schema back-compat ──────────────────────────────────────────────────────


class TestSchemaBackCompat:
    def test_write_without_new_kwargs_keeps_old_shape(self, tmp_path: Path):
        from sovereign_stack.daemons.synthesis_daemon import Reflection

        path = write_reflections(
            [
                Reflection(
                    observation="old-style write",
                    entries_referenced=["e1"],
                    connection_type="other",
                    confidence="low",
                )
            ],
            run_id="r",
            model="m",
            prompt_version="v",
            entries_window_hours=1,
            entries_count=1,
            out_dir=tmp_path,
        )
        record = json.loads(path.read_text().strip())
        for absent in (
            "entries_in_range",
            "truncated",
            "window_claim_ids",
            "entries_referenced_claims",
        ):
            assert absent not in record

    def test_old_records_parse_with_new_fields_none(self, tmp_path: Path):
        old = {
            "id": "reflection_old_1",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": "m",
            "prompt_version": "v",
            "run_id": "old",
            "observation": "an old-schema record",
            "entries_referenced": [],
            "connection_type": "other",
            "confidence": "low",
            "ack_status": "unread",
        }
        (tmp_path / "2026-01-01.jsonl").write_text(json.dumps(old) + "\n")
        [rec] = list_reflections(reflections_dir=tmp_path)
        assert rec.entries_in_range is None
        assert rec.truncated is None
        assert rec.window_claim_ids is None
        assert rec.entries_referenced_claims is None

    def test_ack_preserves_new_fields(self, tmp_path: Path):
        from sovereign_stack.daemons.synthesis_daemon import Reflection

        write_reflections(
            [
                Reflection(
                    observation="citable observation",
                    entries_referenced=["ENTRY 1"],
                    connection_type="convergence",
                    confidence="low",
                )
            ],
            run_id="r2",
            model="m",
            prompt_version="v",
            entries_window_hours=1,
            entries_count=1,
            entries_in_range=4,
            truncated=True,
            window_claim_ids=["f" * 64],
            out_dir=tmp_path,
        )
        [rec] = list_reflections(reflections_dir=tmp_path)
        acked = ack_reflection(rec.id, "confirm", note="held", reflections_dir=tmp_path)
        assert acked.ack_status == "confirm"
        [reread] = list_reflections(reflections_dir=tmp_path)
        assert reread.entries_in_range == 4
        assert reread.truncated is True
        assert reread.window_claim_ids == ["f" * 64]
        assert reread.entries_referenced_claims == ["f" * 64]
