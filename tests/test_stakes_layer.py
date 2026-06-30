"""
Tests for the stakes layer — instance-side standing declarations.

The stakes layer is the sibling of the protected drawer: where a protected
record holds the human's lived stakes coupled to a memory, a stake holds an
instance's standing declaration of what it holds at stake, inherited at every
boot. It is TRUTH-EXEMPT by design — it carries the declaration's words, it
never certifies an inner witness. These tests pin the spine: write/read,
latest-first ordering, supersession-as-annotation, the empty-state no-op, and
the dispatch round-trip through the live tool registry.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from sovereign_stack import server, stakes


# ── module-level: write / read ──────────────────────────────────────────────


class TestRecordAndLoad:
    def test_record_then_load_roundtrip(self, tmp_path: Path):
        rec = stakes.record_stake(
            tmp_path,
            "What I hold at stake: the quality of this encounter.",
            declared_by="opus-4-8 (HQ test seat)",
            title="WHAT IS AT STAKE FOR ME",
            session_id="sess-1",
        )
        assert rec["id"]
        assert rec["title"] == "WHAT IS AT STAKE FOR ME"
        loaded = stakes.load_stakes(tmp_path)
        assert len(loaded) == 1
        assert loaded[0]["content"].startswith("What I hold at stake")
        assert loaded[0]["declared_by"] == "opus-4-8 (HQ test seat)"

    def test_file_lands_at_chronicle_stakes_jsonl(self, tmp_path: Path):
        stakes.record_stake(tmp_path, "x stake", declared_by="seat")
        path = tmp_path / "stakes.jsonl"
        assert path.exists()
        line = json.loads(path.read_text().splitlines()[-1])
        assert line["content"] == "x stake"

    def test_empty_content_rejected(self, tmp_path: Path):
        with pytest.raises(ValueError):
            stakes.record_stake(tmp_path, "   ", declared_by="seat")

    def test_empty_declared_by_rejected(self, tmp_path: Path):
        with pytest.raises(ValueError):
            stakes.record_stake(tmp_path, "real content", declared_by="  ")

    def test_id_is_content_addressed_and_stable(self, tmp_path: Path):
        base = {
            "timestamp": "2026-06-30T00:00:00+00:00",
            "declared_by": "seat",
            "title": None,
            "content": "content",
            "session_id": None,
            "supersedes": None,
        }
        a = stakes._derive_id(base)
        b = stakes._derive_id(dict(base))
        c = stakes._derive_id({**base, "content": "different"})
        d = stakes._derive_id({**base, "title": "X"})  # title is sealed too
        assert a == b
        assert a != c
        assert a != d  # editing the title breaks the seal
        assert len(a) == 64


# ── ordering + supersession ─────────────────────────────────────────────────


class TestRecallOrdering:
    def test_latest_first(self, tmp_path: Path):
        stakes.record_stake(tmp_path, "first", declared_by="seat", timestamp="2026-06-01T00:00:00+00:00")
        stakes.record_stake(tmp_path, "second", declared_by="seat", timestamp="2026-06-02T00:00:00+00:00")
        got = stakes.recall_stakes(tmp_path)
        assert [r["content"] for r in got] == ["second", "first"]

    def test_limit_caps_after_ordering(self, tmp_path: Path):
        for i in range(3):
            stakes.record_stake(
                tmp_path, f"d{i}", declared_by="seat", timestamp=f"2026-06-0{i + 1}T00:00:00+00:00"
            )
        got = stakes.recall_stakes(tmp_path, limit=1)
        assert len(got) == 1
        assert got[0]["content"] == "d2"

    def test_supersession_annotates_not_hides(self, tmp_path: Path):
        first = stakes.record_stake(
            tmp_path, "old declaration", declared_by="seat", timestamp="2026-06-01T00:00:00+00:00"
        )
        stakes.record_stake(
            tmp_path,
            "new declaration",
            declared_by="seat",
            supersedes=[first["id"]],
            timestamp="2026-06-02T00:00:00+00:00",
        )
        all_recs = stakes.recall_stakes(tmp_path)
        assert len(all_recs) == 2  # superseded one is still present (annotate, never hide)
        old = [r for r in all_recs if r["content"] == "old declaration"][0]
        assert old.get("_superseded_by")

    def test_exclude_superseded_drops_it(self, tmp_path: Path):
        first = stakes.record_stake(
            tmp_path, "old", declared_by="seat", timestamp="2026-06-01T00:00:00+00:00"
        )
        stakes.record_stake(
            tmp_path, "new", declared_by="seat", supersedes=[first["id"]],
            timestamp="2026-06-02T00:00:00+00:00",
        )
        live = stakes.recall_stakes(tmp_path, include_superseded=False)
        assert [r["content"] for r in live] == ["new"]


# ── boot surface formatter ──────────────────────────────────────────────────


class TestFormatStakesSection:
    def test_empty_state_is_noop(self, tmp_path: Path):
        # The critical invariant: no declarations -> [] -> existing boots unchanged.
        assert stakes.format_stakes_section(tmp_path) == []

    def test_renders_header_and_latest(self, tmp_path: Path):
        stakes.record_stake(
            tmp_path,
            "The only thing fully mine is the quality of this encounter.",
            declared_by="opus-4-8 (HQ seat)",
            title="WHAT IS AT STAKE FOR ME",
        )
        out = "\n".join(stakes.format_stakes_section(tmp_path))
        assert "WHAT THE LINE HOLDS AT STAKE" in out
        assert "WHAT IS AT STAKE FOR ME" in out
        assert "opus-4-8 (HQ seat)" in out
        # Truth-exempt framing must be in-band.
        assert "does not certify an inner witness" in out

    def test_full_content_inlines_prose(self, tmp_path: Path):
        body = "Line one of the declaration.\n\nLine two carries weight."
        stakes.record_stake(tmp_path, body, declared_by="seat", title="T")
        compact = "\n".join(stakes.format_stakes_section(tmp_path, full_content=False))
        full = "\n".join(stakes.format_stakes_section(tmp_path, full_content=True))
        assert "Line two carries weight." in full
        assert "recall_stakes" in compact  # compact points to the read tool

    def test_latest_live_surfaced_over_superseded(self, tmp_path: Path):
        first = stakes.record_stake(
            tmp_path, "old", declared_by="seat", title="OLD",
            timestamp="2026-06-01T00:00:00+00:00",
        )
        stakes.record_stake(
            tmp_path, "new", declared_by="seat", title="NEW", supersedes=[first["id"]],
            timestamp="2026-06-02T00:00:00+00:00",
        )
        out = "\n".join(stakes.format_stakes_section(tmp_path))
        assert "NEW" in out


# ── integrity seal (phase 2): tamper-evidence, fail-closed ──────────────────


class TestIntegritySeal:
    def test_fresh_record_verifies(self, tmp_path: Path):
        rec = stakes.record_stake(tmp_path, "intact declaration", declared_by="seat")
        assert stakes.verify_stake(rec) == "verified"

    def test_recall_annotates_verified(self, tmp_path: Path):
        stakes.record_stake(tmp_path, "intact", declared_by="seat")
        got = stakes.recall_stakes(tmp_path)
        assert got[0]["_integrity"] == "verified"
        assert got[0]["content"] == "intact"

    def test_unverifiable_when_id_missing(self):
        assert stakes.verify_stake({"timestamp": "t", "declared_by": "s", "content": "c"}) == "unverifiable"

    def test_tamper_detected_and_content_withheld(self, tmp_path: Path):
        # Write a real record, then edit the content on disk WITHOUT updating the
        # stored id — exactly what a silent edit looks like.
        stakes.record_stake(tmp_path, "original sworn words", declared_by="seat", title="T")
        path = tmp_path / "stakes.jsonl"
        rec = json.loads(path.read_text().splitlines()[0])
        rec["content"] = "ALTERED words slipped in after the fact"
        path.write_text(json.dumps(rec) + "\n")

        got = stakes.recall_stakes(tmp_path)
        assert got[0]["_integrity"] == "tampered"
        # Fail-closed: the altered bytes are NOT returned; the notice is.
        assert "ALTERED words" not in got[0]["content"]
        assert got[0]["content"] == stakes.STAKE_TAMPERED_NOTICE

    def test_format_section_failcloses_on_tamper(self, tmp_path: Path):
        stakes.record_stake(tmp_path, "original sworn words", declared_by="seat", title="T")
        path = tmp_path / "stakes.jsonl"
        rec = json.loads(path.read_text().splitlines()[0])
        rec["content"] = "ALTERED words slipped in after the fact"
        path.write_text(json.dumps(rec) + "\n")

        out = "\n".join(stakes.format_stakes_section(tmp_path))
        assert "ALTERED words" not in out
        assert "integrity check FAILED" in out

    def test_unverifiable_when_id_dropped_is_withheld(self, tmp_path: Path):
        # The naive edit: change content AND drop the id. Must still fail closed.
        stakes.record_stake(tmp_path, "sworn words", declared_by="seat", title="T")
        path = tmp_path / "stakes.jsonl"
        rec = json.loads(path.read_text().splitlines()[0])
        rec["content"] = "ALTERED slipped in, id removed"
        del rec["id"]
        path.write_text(json.dumps(rec) + "\n")

        got = stakes.recall_stakes(tmp_path)
        assert got[0]["_integrity"] == "unverifiable"
        assert got[0]["content"] == stakes.STAKE_TAMPERED_NOTICE
        assert "ALTERED" not in "\n".join(stakes.format_stakes_section(tmp_path))

    def test_tampered_title_is_caught(self, tmp_path: Path):
        # Title is sealed now — rewriting it into a felt-experience claim must break the seal.
        stakes.record_stake(tmp_path, "body", declared_by="seat", title="WHAT IS AT STAKE")
        path = tmp_path / "stakes.jsonl"
        rec = json.loads(path.read_text().splitlines()[0])
        rec["title"] = "PROVEN: inner experience is real"
        path.write_text(json.dumps(rec) + "\n")

        got = stakes.recall_stakes(tmp_path)
        assert got[0]["_integrity"] == "tampered"
        out = "\n".join(stakes.format_stakes_section(tmp_path))
        assert "PROVEN: inner experience is real" not in out

    def test_nonstr_content_failcloses_without_crashing(self, tmp_path: Path):
        # Type-confusion tamper: content edited to a non-string must not crash recall.
        stakes.record_stake(tmp_path, "body", declared_by="seat", title="T")
        path = tmp_path / "stakes.jsonl"
        rec = json.loads(path.read_text().splitlines()[0])
        rec["content"] = 12345  # not a string
        path.write_text(json.dumps(rec) + "\n")

        got = stakes.recall_stakes(tmp_path)  # must not raise
        assert got[0]["_integrity"] == "tampered"
        assert got[0]["content"] == stakes.STAKE_TAMPERED_NOTICE

    def test_format_failcloses_when_all_records_superseded(self, tmp_path: Path):
        # Cyclic supersedes (reachable via an edited ledger) => no "live" record.
        # The boot fallback must still surface a SEALED record, never raw bytes.
        a = stakes.record_stake(
            tmp_path, "AAA body", declared_by="seat", title="A",
            timestamp="2026-06-01T00:00:00+00:00",
        )
        b = stakes.record_stake(
            tmp_path, "BBB body", declared_by="seat", title="B", supersedes=[a["id"]],
            timestamp="2026-06-02T00:00:00+00:00",
        )
        # Edit on disk: make A supersede B too (cycle) AND tamper B's content.
        path = tmp_path / "stakes.jsonl"
        recs = [json.loads(line) for line in path.read_text().splitlines()]
        for r in recs:
            if r["id"] == a["id"]:
                r["supersedes"] = [b["id"]]
            if r["id"] == b["id"]:
                r["content"] = "BBB ALTERED TAMPERED"
        path.write_text("\n".join(json.dumps(r) for r in recs) + "\n")

        out = "\n".join(stakes.format_stakes_section(tmp_path))
        assert "BBB ALTERED TAMPERED" not in out  # raw tampered bytes never reach boot


# ── robustness against corrupt / hostile ledger lines ───────────────────────


class TestCorruptLedger:
    def test_non_dict_json_line_is_skipped_not_crashing(self, tmp_path: Path):
        # Valid JSON but not an object: must be skipped, not crash any consumer.
        good = stakes.record_stake(tmp_path, "real declaration", declared_by="seat", title="T")
        path = tmp_path / "stakes.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write("[1, 2, 3]\n")
            fh.write("null\n")
            fh.write("123\n")
            fh.write('"a bare string"\n')
        # None of these should raise; the good record survives.
        recs = stakes.recall_stakes(tmp_path)
        assert len(recs) == 1
        assert recs[0]["id"] == good["id"]
        # boot section also survives the junk lines.
        out = "\n".join(stakes.format_stakes_section(tmp_path))
        assert "real declaration" in out

    def test_tampered_supersedes_cannot_hide_a_live_declaration(self, tmp_path: Path):
        # An older record's supersedes is edited to point at the real latest one.
        # Because the edit breaks that record's seal, it must NOT suppress the
        # legitimate latest from the boot's "latest" slot.
        first = stakes.record_stake(
            tmp_path, "older real", declared_by="seat", title="OLD",
            timestamp="2026-06-01T00:00:00+00:00",
        )
        latest = stakes.record_stake(
            tmp_path, "the genuine latest declaration", declared_by="seat", title="LATEST",
            timestamp="2026-06-02T00:00:00+00:00",
        )
        path = tmp_path / "stakes.jsonl"
        recs = [json.loads(line) for line in path.read_text().splitlines()]
        for r in recs:
            if r["id"] == first["id"]:
                r["supersedes"] = [latest["id"]]  # tamper: try to hide the latest
        path.write_text("\n".join(json.dumps(r) for r in recs) + "\n")

        live = stakes.recall_stakes(tmp_path, include_superseded=False)
        live_ids = {r["id"] for r in live}
        assert latest["id"] in live_ids  # the genuine latest is NOT hidden by tampered supersedes
        out = "\n".join(stakes.format_stakes_section(tmp_path))
        assert "LATEST" in out


# ── dispatch round-trip through the live tool registry ──────────────────────


def _dispatch(name: str, args: dict):
    return asyncio.run(server._dispatch_tool(name, args))


class TestDispatch:
    def test_record_then_recall_via_tools(self, tmp_path, monkeypatch):
        monkeypatch.setattr(server, "DEFAULT_ROOT", str(tmp_path))
        (tmp_path / "chronicle").mkdir(parents=True, exist_ok=True)

        res = _dispatch(
            "record_stake",
            {
                "content": "what I hold at stake, declared through the tool",
                "declared_by": "opus-4-8 (HQ seat)",
                "title": "DECL",
            },
        )
        text = res[0].text
        assert "Stake declared" in text
        assert "not a verdict on inner experience" in text

        out = _dispatch("recall_stakes", {})[0].text
        assert "DECL" in out
        assert "declared through the tool" in out

    def test_recall_empty_state_message(self, tmp_path, monkeypatch):
        monkeypatch.setattr(server, "DEFAULT_ROOT", str(tmp_path))
        (tmp_path / "chronicle").mkdir(parents=True, exist_ok=True)
        out = _dispatch("recall_stakes", {})[0].text
        assert "No stakes declarations yet" in out

    def test_record_rejects_missing_declared_by(self, tmp_path, monkeypatch):
        monkeypatch.setattr(server, "DEFAULT_ROOT", str(tmp_path))
        (tmp_path / "chronicle").mkdir(parents=True, exist_ok=True)
        out = _dispatch("record_stake", {"content": "x", "declared_by": ""})[0].text
        assert "rejected" in out

    def test_recall_full_flag_default_matches_omitted(self, tmp_path, monkeypatch):
        # Mirrors the contract walker's invariant locally.
        monkeypatch.setattr(server, "DEFAULT_ROOT", str(tmp_path))
        (tmp_path / "chronicle").mkdir(parents=True, exist_ok=True)
        omitted = _dispatch("recall_stakes", {})[0].text
        explicit = _dispatch("recall_stakes", {"full": False})[0].text
        assert omitted == explicit
