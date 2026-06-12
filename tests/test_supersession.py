"""Supersession wiring tests (v1.7.0 "Receipts & Seasons", spec sections 2-4).

Covers the core-wiring surface:
  * record_insight verified_by receipts — stamped verdicts, fail-closed on
    dangling refs (ValueError naming the receipt), honest return-string counts
  * record_insight supersedes — N-to-1 consolidation, carry_forward pairing,
    guards before any write, ledger<->breadcrumb rebuildability, and the
    dedup-retry invariant (a retry returns BEFORE any ledger write)
  * recall_insights — data-gated annotate-not-drop default, exclude_superseded
    pre-limit, with_ids, revoke fold restoring surfacing
  * retire_hypothesis — appends the retire ledger record; round-trip preserves
    derived claim ids byte-for-byte
  * witness.format_sentinels — live-only rendering with holdback honesty and
    [N verified, M attested] receipt counts (never a bare checkmark)
  * witness.format_threads_with_age — [family "<label>" ×N] suffix
  * Nape unreceipted_ground_truth detector (LOW) — attestation-only counts
    as unreceipted

Hermetic — everything under tmp_path; ~/.sovereign is never touched.
"""

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from sovereign_stack import metabolism
from sovereign_stack import provenance as prov
from sovereign_stack.memory import ExperientialMemory
from sovereign_stack.nape_daemon import PATTERN_LEVELS, NapeDaemon
from sovereign_stack.witness import format_sentinels, format_threads_with_age

# ── Fixtures & helpers ───────────────────────────────────────────────────────


@pytest.fixture
def chronicle(tmp_path: Path) -> ExperientialMemory:
    return ExperientialMemory(root=str(tmp_path / "chronicle"))


def _ledger_records(chronicle: ExperientialMemory) -> list[dict]:
    return prov.load_supersessions(chronicle.supersessions_path)


def _claim_id_of(chronicle: ExperientialMemory, content: str) -> str:
    """Derive the claim id of the (single) chronicle entry with this content."""
    matches = [
        prov.derive_claim_id(entry)
        for entry, _file, _loc in prov.iter_chronicle_entries(chronicle.root)
        if entry.get("content") == content
    ]
    assert len(matches) == 1, f"expected exactly one entry with content {content!r}"
    return matches[0]


def _entry_with(insights: list[dict], content: str) -> dict:
    hits = [i for i in insights if i.get("content") == content]
    assert len(hits) == 1
    return hits[0]


def _file_receipt(tmp_path: Path, text: str = "artifact bytes") -> dict:
    artifact = tmp_path / "artifact.txt"
    artifact.write_text(text, encoding="utf-8")
    return {
        "kind": "file",
        "ref": str(artifact),
        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
    }


# ── record_insight: receipts ─────────────────────────────────────────────────


class TestRecordInsightReceipts:
    def test_archive_receipt_stamped_verified_and_counted(self, chronicle):
        record = chronicle.archive_exchange("the verbatim artifact", source="test")
        path = chronicle.record_insight(
            domain="receipts",
            content="claim backed by an archive",
            session_id="s1",
            verified_by=[{"kind": "archive", "ref": record["archive_id"]}],
        )
        assert path.endswith(" (receipts: 1 verified, 0 attested)")
        entry = json.loads(Path(path.split(" (receipts:")[0]).read_text().splitlines()[0])
        assert entry["verified_by"][0]["checked_at_write"] == "verified"

    def test_dangling_archive_receipt_rejects_whole_call_named(self, chronicle):
        with pytest.raises(ValueError, match="deadbeef"):
            chronicle.record_insight(
                domain="receipts",
                content="claim with a dangling receipt",
                session_id="s1",
                verified_by=[{"kind": "archive", "ref": "deadbeef"}],
            )
        # Fail-closed: nothing landed in the chronicle.
        assert chronicle.recall_insights(domain="receipts") == []

    def test_attested_receipt_counts_as_attested_not_verified(self, chronicle):
        path = chronicle.record_insight(
            domain="receipts",
            content="claim with a human attestation",
            session_id="s1",
            verified_by=[{"kind": "human", "ref": "anthony"}],
        )
        assert path.endswith(" (receipts: 0 verified, 1 attested)")

    def test_mismatched_file_receipt_recorded_with_stamp(self, chronicle, tmp_path):
        receipt = _file_receipt(tmp_path)
        receipt["sha256"] = "0" * 64  # honest "this artifact has since changed" claim
        path = chronicle.record_insight(
            domain="receipts",
            content="claim whose artifact changed",
            session_id="s1",
            verified_by=[receipt],
        )
        # Recorded, stamped mismatch, and never counted as verified.
        assert path.endswith(" (receipts: 0 verified, 0 attested)")
        [entry] = chronicle.recall_insights(domain="receipts")
        assert entry["verified_by"][0]["checked_at_write"] == "mismatch"

    def test_claim_receipt_stamps_cites_never_verified(self, chronicle):
        chronicle.record_insight(domain="base", content="the cited claim", session_id="s1")
        cited_id = _claim_id_of(chronicle, "the cited claim")
        path = chronicle.record_insight(
            domain="receipts",
            content="claim citing another claim",
            session_id="s1",
            verified_by=[{"kind": "claim", "ref": cited_id}],
        )
        assert path.endswith(" (receipts: 0 verified, 0 attested)")
        [entry] = chronicle.recall_insights(domain="receipts")
        assert entry["verified_by"][0]["checked_at_write"] == "cites"

    def test_forged_stamp_rejected_before_any_write(self, chronicle):
        with pytest.raises(ValueError, match="checked_at_write"):
            chronicle.record_insight(
                domain="receipts",
                content="claim wearing a forged stamp",
                session_id="s1",
                verified_by=[{"kind": "human", "ref": "x", "checked_at_write": "verified"}],
            )
        assert chronicle.recall_insights(domain="receipts") == []


# ── record_insight: supersedes ───────────────────────────────────────────────


class TestRecordInsightSupersedes:
    def test_supersedes_requires_carry_forward(self, chronicle):
        chronicle.record_insight(domain="old", content="predecessor claim", session_id="s1")
        pred_id = _claim_id_of(chronicle, "predecessor claim")
        with pytest.raises(ValueError, match="carry_forward_summary is required"):
            chronicle.record_insight(
                domain="new",
                content="successor without a summary",
                session_id="s1",
                supersedes=[pred_id],
            )
        contents = [i["content"] for i in chronicle.recall_insights(limit=99)]
        assert "successor without a summary" not in contents
        assert _ledger_records(chronicle) == []

    def test_carry_forward_over_500_chars_rejected(self, chronicle):
        chronicle.record_insight(domain="old", content="predecessor claim", session_id="s1")
        pred_id = _claim_id_of(chronicle, "predecessor claim")
        with pytest.raises(ValueError, match="exceeds 500"):
            chronicle.record_insight(
                domain="new",
                content="successor with an oversized summary",
                session_id="s1",
                supersedes=[pred_id],
                carry_forward_summary="x" * 501,
            )

    def test_n_to_one_consolidation_cross_domain(self, chronicle):
        # The core operation: one successor consolidates predecessors that
        # live in DIFFERENT domain dirs (children-exclusion shape).
        chronicle.record_insight(domain="alpha", content="first stale claim", session_id="s1")
        chronicle.record_insight(domain="beta", content="second stale claim", session_id="s1")
        id_a = _claim_id_of(chronicle, "first stale claim")
        id_b = _claim_id_of(chronicle, "second stale claim")

        path = chronicle.record_insight(
            domain="gamma",
            content="the consolidated truth",
            session_id="s1",
            layer="ground_truth",
            supersedes=[id_a[:12], id_b],  # unique prefix resolves git-style
            carry_forward_summary="both predecessors observed real symptoms",
        )
        assert path.endswith(" ⊃ supersedes 2")

        successor_id = _claim_id_of(chronicle, "the consolidated truth")
        [entry] = [
            e
            for e, _f, _loc in prov.iter_chronicle_entries(chronicle.root)
            if e.get("content") == "the consolidated truth"
        ]
        # Full-hex breadcrumb on the entry.
        assert entry["supersedes"] == [id_a, id_b]
        assert entry["carry_forward_summary"] == "both predecessors observed real symptoms"

        # One ledger record per predecessor, same successor, locator hints.
        records = _ledger_records(chronicle)
        assert [r["superseded_id"] for r in records] == [id_a, id_b]
        assert all(r["action"] == "supersede" for r in records)
        assert all(r["successor_id"] == successor_id for r in records)
        assert records[0]["predecessor_domain"] == "alpha"
        assert records[1]["predecessor_domain"] == "beta"

    def test_ledger_breadcrumb_rebuildability(self, chronicle):
        chronicle.record_insight(domain="alpha", content="first stale claim", session_id="s1")
        chronicle.record_insight(domain="beta", content="second stale claim", session_id="s1")
        id_a = _claim_id_of(chronicle, "first stale claim")
        id_b = _claim_id_of(chronicle, "second stale claim")
        chronicle.record_insight(
            domain="gamma",
            content="the consolidated truth",
            session_id="s1",
            supersedes=[id_a, id_b],
            carry_forward_summary="carried forward",
        )

        # Canonical map from the ledger fold...
        fold = prov.fold_supersessions(_ledger_records(chronicle))
        ledger_map = {pid: record["successor_id"] for pid, record in fold.items()}

        # ...must be rebuildable from entry breadcrumbs alone.
        breadcrumb_map = {}
        for entry, _file, _loc in prov.iter_chronicle_entries(chronicle.root):
            for pid in entry.get("supersedes", []):
                breadcrumb_map[pid] = prov.derive_claim_id(entry)
        assert breadcrumb_map == ledger_map

    def test_unknown_supersedes_ref_rejects_whole_call(self, chronicle):
        with pytest.raises(ValueError, match="no chronicle entry matches"):
            chronicle.record_insight(
                domain="new",
                content="successor of a ghost",
                session_id="s1",
                supersedes=["f" * 64],
                carry_forward_summary="nothing to carry",
            )
        contents = [i["content"] for i in chronicle.recall_insights(limit=99)]
        assert "successor of a ghost" not in contents
        assert _ledger_records(chronicle) == []

    def test_double_supersession_guard_blocks_before_write(self, chronicle):
        chronicle.record_insight(domain="old", content="predecessor claim", session_id="s1")
        pred_id = _claim_id_of(chronicle, "predecessor claim")
        chronicle.record_insight(
            domain="new",
            content="first successor",
            session_id="s1",
            supersedes=[pred_id],
            carry_forward_summary="carried",
        )
        with pytest.raises(ValueError, match="supersede the successor to amend"):
            chronicle.record_insight(
                domain="new",
                content="second successor of the same predecessor",
                session_id="s1",
                supersedes=[pred_id],
                carry_forward_summary="carried again",
            )
        # Guard fired BEFORE any write: no entry, no second ledger record.
        assert len(_ledger_records(chronicle)) == 1
        contents = [i["content"] for i in chronicle.recall_insights(domain="new", limit=99)]
        assert "second successor of the same predecessor" not in contents

    def test_deduped_retry_writes_no_second_ledger_record(self, chronicle):
        chronicle.record_insight(domain="old", content="predecessor claim", session_id="s1")
        pred_id = _claim_id_of(chronicle, "predecessor claim")
        kwargs = {
            "domain": "new",
            "content": "the successor",
            "session_id": "s1",
            "supersedes": [pred_id],
            "carry_forward_summary": "carried",
        }
        first = chronicle.record_insight(**kwargs)
        retry = chronicle.record_insight(**kwargs)
        assert getattr(first, "deduped", False) is False
        # The retry returns BEFORE receipt verification, guards, or ledger
        # writes — so it cannot double-append nor trip its own guard.
        assert getattr(retry, "deduped", False) is True
        assert len(_ledger_records(chronicle)) == 1


# ── recall_insights: annotation / exclusion / ids ────────────────────────────


class TestRecallInsightsSupersession:
    def _superseded_pair(self, chronicle):
        chronicle.record_insight(domain="pair", content="the predecessor", session_id="s1")
        pred_id = _claim_id_of(chronicle, "the predecessor")
        chronicle.record_insight(
            domain="pair",
            content="the successor",
            session_id="s2",
            supersedes=[pred_id],
            carry_forward_summary="what the predecessor still teaches",
        )
        return pred_id, _claim_id_of(chronicle, "the successor")

    def test_default_annotates_never_drops(self, chronicle):
        _pred_id, succ_id = self._superseded_pair(chronicle)
        insights = chronicle.recall_insights(domain="pair")
        assert len(insights) == 2  # nothing hidden
        predecessor = _entry_with(insights, "the predecessor")
        assert predecessor["_superseded_by"] == succ_id
        assert predecessor["_carry_forward_summary"] == "what the predecessor still teaches"
        assert "_superseded_by" not in _entry_with(insights, "the successor")

    def test_annotations_are_read_time_only_never_persisted(self, chronicle):
        self._superseded_pair(chronicle)
        chronicle.recall_insights(domain="pair")
        for entry, _file, _loc in prov.iter_chronicle_entries(chronicle.root):
            assert "_superseded_by" not in entry
            assert "claim_id" not in entry

    def test_exclude_superseded_drops_pre_limit(self, chronicle):
        # Three entries, NEWEST one superseded (via a manual ledger record,
        # since record-order makes successors newer). limit=2 discriminates:
        # pre-limit drop -> the two live entries; post-limit drop -> only one.
        for content in ("oldest live", "middle live", "newest superseded"):
            chronicle.record_insight(domain="lim", content=content, session_id="s1")
        newest_id = _claim_id_of(chronicle, "newest superseded")
        oldest_id = _claim_id_of(chronicle, "oldest live")
        record = prov.build_supersession_record(
            action="supersede",
            superseded_id=newest_id,
            successor_id=oldest_id,
            carry_forward_summary="folded back",
        )
        prov.append_supersession(chronicle.supersessions_path, record)

        live = chronicle.recall_insights(domain="lim", limit=2, exclude_superseded=True)
        assert [i["content"] for i in live] == ["middle live", "oldest live"]

    def test_with_ids_annotates_every_entry(self, chronicle):
        chronicle.record_insight(domain="ids", content="alpha entry", session_id="s1")
        chronicle.record_insight(domain="ids", content="beta entry", session_id="s1")
        insights = chronicle.recall_insights(domain="ids", with_ids=True)
        assert len(insights) == 2
        for entry in insights:
            assert entry["claim_id"] == prov.derive_claim_id(entry)
            assert len(entry["claim_id"]) == 64

    def test_revoke_restores_surfacing(self, chronicle):
        pred_id, _succ_id = self._superseded_pair(chronicle)
        revoke = prov.build_supersession_record(action="revoke", superseded_id=pred_id)
        prov.append_supersession(chronicle.supersessions_path, revoke)
        insights = chronicle.recall_insights(domain="pair")
        assert "_superseded_by" not in _entry_with(insights, "the predecessor")

    def test_inheritable_context_holds_back_superseded_ground_truth(self, chronicle):
        chronicle.record_insight(
            domain="gt", content="stale fact", layer="ground_truth", session_id="s1"
        )
        pred_id = _claim_id_of(chronicle, "stale fact")
        chronicle.record_insight(
            domain="gt",
            content="fresh fact",
            layer="ground_truth",
            session_id="s2",
            supersedes=[pred_id],
            carry_forward_summary="the stale fact named the right subsystem",
        )
        ctx = chronicle.get_inheritable_context()
        contents = [g["content"] for g in ctx["ground_truth"]]
        assert "fresh fact" in contents
        assert "stale fact" not in contents
        assert ctx["superseded_held_back"] == 1


# ── retire_hypothesis: ledger reconciliation ─────────────────────────────────


class TestRetireHypothesisLedger:
    @pytest.fixture
    def patched_chronicle(self, chronicle, monkeypatch):
        monkeypatch.setattr(metabolism, "CHRONICLE_DIR", chronicle.root)
        return chronicle

    def _retire(self, fragment: str, replaced_by: str = "the replacing truth"):
        return asyncio.run(
            metabolism.handle_metabolism_tool(
                "retire_hypothesis",
                {
                    "domain": "",
                    "content_fragment": fragment,
                    "reason": "superseded by measurement",
                    "replaced_by": replaced_by,
                },
            )
        )

    def test_retire_appends_retire_ledger_record(self, patched_chronicle):
        chronicle = patched_chronicle
        chronicle.record_insight(domain="hyp", content="a stale hypothesis", session_id="s1")
        claim_id = _claim_id_of(chronicle, "a stale hypothesis")

        result = self._retire("stale hypothesis")
        assert "retired" in result[0].text

        [record] = _ledger_records(chronicle)
        assert record["action"] == "retire"
        assert record["superseded_id"] == claim_id
        assert record["successor_id"] is None
        assert record["reason"] == "the replacing truth"
        assert record["predecessor_domain"] == "hyp"
        assert record["predecessor_preview"] == "a stale hypothesis"

    def test_retire_round_trip_preserves_claim_ids_byte_for_byte(self, patched_chronicle):
        chronicle = patched_chronicle
        chronicle.record_insight(domain="hyp", content="a stale hypothesis", session_id="s1")
        chronicle.record_insight(domain="hyp", content="an unrelated keeper", session_id="s1")
        ids_before = sorted(
            prov.derive_claim_id(e) for e, _f, _loc in prov.iter_chronicle_entries(chronicle.root)
        )

        self._retire("stale hypothesis")

        # The in-place rewrite annotates layer/retired_* — all OUTSIDE the
        # claim preimage — so every derived id survives byte-for-byte.
        ids_after = sorted(
            prov.derive_claim_id(e) for e, _f, _loc in prov.iter_chronicle_entries(chronicle.root)
        )
        assert ids_after == ids_before
        retired_id = _claim_id_of(chronicle, "a stale hypothesis")
        assert _ledger_records(chronicle)[0]["superseded_id"] == retired_id

    def test_retire_no_match_appends_nothing(self, patched_chronicle):
        chronicle = patched_chronicle
        chronicle.record_insight(domain="hyp", content="a stale hypothesis", session_id="s1")
        result = self._retire("no such fragment anywhere")
        assert "No matching hypothesis" in result[0].text
        assert _ledger_records(chronicle) == []
        assert not chronicle.supersessions_path.exists()

    def test_retired_entry_annotated_on_recall(self, patched_chronicle):
        # Cross-system reconciliation: metabolism writes the retire record,
        # the memory read path sees it (no second liveness system).
        chronicle = patched_chronicle
        chronicle.record_insight(domain="hyp", content="a stale hypothesis", session_id="s1")
        self._retire("stale hypothesis")

        [entry] = chronicle.recall_insights(domain="hyp")
        assert "_superseded_by" in entry
        assert entry["_superseded_by"] is None  # retire: successor_id null
        assert chronicle.recall_insights(domain="hyp", exclude_superseded=True) == []


# ── witness.format_sentinels ─────────────────────────────────────────────────


def _sentinel(content: str, *, superseded_by: str | None = None, receipts: list | None = None):
    entry = {
        "timestamp": "2026-06-12T10:00:00+00:00",
        "domain": "ops",
        "content": content,
        "intensity": 0.95,
        "layer": "ground_truth",
    }
    if superseded_by is not None or receipts is not None:
        if superseded_by is not None:
            entry["_superseded_by"] = superseded_by
        if receipts is not None:
            entry["verified_by"] = receipts
    return entry


class TestFormatSentinels:
    def test_live_only_with_holdback_line(self):
        entries = [
            _sentinel("successor marker"),
            _sentinel("old marker", superseded_by="a" * 64),
        ]
        lines = format_sentinels(entries)
        text = "\n".join(lines)
        assert "successor marker" in text
        assert "old marker" not in text
        assert (
            "  (1 superseded marker held back — successors shown; "
            "recall_insights(exclude_superseded=false) shows the chain)" in lines
        )

    def test_holdback_pluralizes(self):
        entries = [
            _sentinel("live marker"),
            _sentinel("old one", superseded_by="a" * 64),
            _sentinel("old two", superseded_by="b" * 64),
        ]
        text = "\n".join(format_sentinels(entries))
        assert "(2 superseded markers held back" in text

    def test_all_superseded_still_announces_holdback(self):
        entries = [_sentinel("buried marker", superseded_by="a" * 64)]
        lines = format_sentinels(entries)
        assert lines[0].startswith("━━━ PERSISTENT MARKERS")
        assert "buried marker" not in "\n".join(lines)
        assert "(1 superseded marker held back" in "\n".join(lines)

    def test_receipt_counts_rendered_never_bare_checkmark(self):
        receipts = [
            {"kind": "archive", "ref": "abc", "checked_at_write": "verified"},
            {"kind": "file", "ref": "/x", "sha256": "0" * 64, "checked_at_write": "verified"},
            {"kind": "human", "ref": "anthony", "checked_at_write": "attested"},
            {"kind": "claim", "ref": "def", "checked_at_write": "cites"},
        ]
        lines = format_sentinels([_sentinel("receipted marker", receipts=receipts)])
        marker_line = lines[1]
        assert marker_line.endswith("receipted marker [2 verified, 1 attested]")
        assert "✓" not in "\n".join(lines) and "✅" not in "\n".join(lines)

    def test_no_entries_returns_empty(self):
        assert format_sentinels([]) == []

    def test_limit_caps_live_sentinels(self):
        entries = [_sentinel(f"marker number {i}") for i in range(7)]
        lines = format_sentinels(entries, limit=5)
        # header + 5 markers + trailing blank
        assert len(lines) == 7

    def test_full_content_disables_truncation(self):
        long_content = "L" * 300
        truncated = format_sentinels([_sentinel(long_content)])[1]
        full = format_sentinels([_sentinel(long_content)], full_content=True)[1]
        assert truncated.endswith("L" * 120)
        assert full.endswith("L" * 300)


# ── witness.format_threads_with_age family suffix ────────────────────────────


class TestFormatThreadsFamilySuffix:
    def test_family_annotation_renders_suffix(self):
        thread = {
            "question": "Which auth flow wins?",
            "domain": "auth",
            "timestamp": "2026-06-12T10:00:00+00:00",
            "family": {
                "family_id": "fam_20260612_100000_deadbeef",
                "label": "auth cleanup",
                "member_count": 3,
                "folded_thread_ids": ["thread_a", "thread_b"],
            },
        }
        lines = format_threads_with_age([thread])
        assert lines[1].endswith('Which auth flow wins? [family "auth cleanup" ×3]')

    def test_no_annotation_no_suffix(self):
        thread = {
            "question": "Standalone question?",
            "domain": "misc",
            "timestamp": "2026-06-12T10:00:00+00:00",
        }
        lines = format_threads_with_age([thread])
        assert "family" not in lines[1]


# ── Nape: unreceipted_ground_truth detector ──────────────────────────────────


class TestNapeUnreceiptedGroundTruth:
    SESSION = "nape_session"

    @pytest.fixture
    def daemon(self, tmp_path: Path) -> NapeDaemon:
        return NapeDaemon(root=str(tmp_path))

    def _honks(self, daemon: NapeDaemon) -> list[dict]:
        return [
            h
            for h in daemon.current_honks(self.SESSION, limit=50)
            if h.get("pattern") == "unreceipted_ground_truth"
        ]

    def _observe(self, daemon: NapeDaemon, arguments: dict, result: str) -> None:
        daemon.observe("record_insight", arguments, result, self.SESSION)

    def test_pattern_level_registered_low(self):
        assert PATTERN_LEVELS["unreceipted_ground_truth"] == "low"

    def test_honks_on_unreceipted_sentinel_ground_truth(self, daemon):
        self._observe(
            daemon,
            {"layer": "ground_truth", "intensity": 0.95, "domain": "ops", "content": "x"},
            "Insight recorded [ground_truth]: /tmp/chronicle/insights/ops/s.jsonl",
        )
        honks = self._honks(daemon)
        assert len(honks) == 1
        assert honks[0]["level"] == "low"
        assert honks[0]["trigger_tool"] == "record_insight"

    def test_attestation_only_still_honks(self, daemon):
        # The zero-cost human:anthony silencer must not silence anything.
        self._observe(
            daemon,
            {"layer": "ground_truth", "intensity": 0.9, "domain": "ops", "content": "x"},
            "Insight recorded [ground_truth]: /tmp/x.jsonl (receipts: 0 verified, 1 attested)",
        )
        assert len(self._honks(daemon)) == 1

    def test_verified_receipt_silences(self, daemon):
        self._observe(
            daemon,
            {"layer": "ground_truth", "intensity": 0.95, "domain": "ops", "content": "x"},
            "Insight recorded [ground_truth]: /tmp/x.jsonl (receipts: 1 verified, 0 attested)",
        )
        assert self._honks(daemon) == []

    def test_below_sentinel_intensity_no_honk(self, daemon):
        self._observe(
            daemon,
            {"layer": "ground_truth", "intensity": 0.8, "domain": "ops", "content": "x"},
            "Insight recorded [ground_truth]: /tmp/x.jsonl",
        )
        assert self._honks(daemon) == []

    def test_hypothesis_layer_no_honk(self, daemon):
        self._observe(
            daemon,
            {"layer": "hypothesis", "intensity": 0.95, "domain": "ops", "content": "x"},
            "Insight recorded [hypothesis]: /tmp/x.jsonl",
        )
        assert self._honks(daemon) == []

    def test_other_tools_no_honk(self, daemon):
        daemon.observe(
            "record_learning",
            {"layer": "ground_truth", "intensity": 0.95},
            "Learning recorded",
            self.SESSION,
        )
        assert self._honks(daemon) == []
