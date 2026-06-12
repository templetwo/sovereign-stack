"""
Tests for v1.7.0 thread families (seasons.py).

Spec section 6: ledger placement (get_open_threads / resolve_thread_by_id
never surface ledger records as threads — the D2 fatal, as a test);
link/extend/unlink fold; engine fold with folded_thread_ids always
present; triage max-score + "family of N"; thread files byte-unchanged.

Hermetic — every chronicle lives under tmp_path; nothing touches
~/.sovereign live data.
"""

import hashlib
import json
import re

import pytest

from sovereign_stack.memory import ExperientialMemory
from sovereign_stack.seasons import (
    FAMILIES_FILENAME,
    SEASON_TOOL_INTENTS,
    SEASON_TOOL_TIERS,
    SEASON_TOOLS,
    FamilyError,
    append_family_record,
    build_family_record,
    coalesce_threads,
    coalesce_triaged,
    families_path_for,
    family_max_score,
    family_state,
    fold_families,
    generate_family_id,
    handle_season_tool,
    link_threads,
    load_families,
)

QUESTIONS = [
    "How should the bridge adapter handle supersession params?",
    "Does the bridge adapter need a param-exclusion test?",
    "What rotates the bridge adapter token after the grace window?",
    "Is the Jetson mirror pulling the chronicle every six hours?",
]


@pytest.fixture
def chronicle(tmp_path):
    """ExperientialMemory over a tmp chronicle root."""
    return ExperientialMemory(root=str(tmp_path / "chronicle"))


@pytest.fixture
def families_path(chronicle):
    return families_path_for(chronicle.root)


def make_threads(chronicle, questions=QUESTIONS, domain="bridge"):
    """Record threads and return their stable ids, question order."""
    by_question = {}
    for q in questions:
        chronicle.record_open_thread(question=q, domain=domain)
    for t in chronicle.get_open_threads(limit=100):
        by_question[t["question"]] = t["thread_id"]
    return [by_question[q] for q in questions]


def hash_tree(root):
    """sha256 of every file under root, keyed by relative path."""
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def link(chronicle, families_path, ids, label="bridge-adapter", **kwargs):
    return link_threads(ids, label, memory=chronicle, families_path=families_path, **kwargs)


# ── Ledger placement (the D2 fatal, as a test) ──


class TestLedgerPlacement:
    def test_ledger_lives_at_chronicle_root_not_open_threads(self, chronicle):
        path = families_path_for(chronicle.root)
        assert path == chronicle.root / FAMILIES_FILENAME
        assert path.parent != chronicle.threads_dir
        assert chronicle.threads_dir not in path.parents

    def test_get_open_threads_never_surfaces_ledger_records(self, chronicle, families_path):
        ids = make_threads(chronicle)
        before = {t["thread_id"] for t in chronicle.get_open_threads(limit=100)}

        link(chronicle, families_path, ids[:3])

        # Unfolded view: ledger records must not appear as phantom threads —
        # the thread set is exactly what it was before linking.
        unfolded = chronicle.get_open_threads(limit=100, coalesce_families=False)
        assert {t["thread_id"] for t in unfolded} == before

        # Default (folded) view: family members collapse into the primary, but
        # nothing NEW appears — still no phantoms, and no raw ledger fields.
        after = chronicle.get_open_threads(limit=100)
        assert {t["thread_id"] for t in after} <= before
        for thread in after:
            assert thread.get("action") not in ("link", "unlink")
            assert "family_id" not in thread  # raw ledger field; folded rows
            assert "member_thread_ids" not in thread  # carry `family` instead
        primaries_with_family = [t for t in after if "family" in t]
        assert len(primaries_with_family) == 1
        assert set(ids[:3]) - {primaries_with_family[0]["thread_id"]} == set(
            primaries_with_family[0]["family"]["folded_thread_ids"]
        )

    def test_resolve_thread_by_id_never_resolves_ledger_records(self, chronicle, families_path):
        ids = make_threads(chronicle)
        result = link(chronicle, families_path, ids[:2])
        ledger_bytes = families_path.read_bytes()

        # A family_id is not a thread_id — nothing to resolve, nothing rewritten.
        assert chronicle.resolve_thread_by_id(result["family_id"], "bogus") == ""
        assert families_path.read_bytes() == ledger_bytes

    def test_load_families_never_creates_the_file(self, tmp_path):
        path = tmp_path / "chronicle" / FAMILIES_FILENAME
        assert load_families(path) == []
        assert not path.exists()
        assert not path.parent.exists()

    def test_append_creates_parent_lazily(self, tmp_path):
        path = tmp_path / "deep" / "chronicle" / FAMILIES_FILENAME
        record = build_family_record(
            action="link",
            family_id=generate_family_id("x"),
            label="x",
            member_thread_ids=["thread_a", "thread_b"],
        )
        append_family_record(path, record)
        assert json.loads(path.read_text().strip()) == record


# ── link_threads: link / extend / unlink ──


class TestLink:
    def test_family_id_format_and_label_hash(self, chronicle, families_path):
        ids = make_threads(chronicle)
        result = link(chronicle, families_path, ids[:2], label="bridge-adapter")
        assert re.fullmatch(r"fam_\d{8}_\d{6}_[0-9a-f]{8}", result["family_id"])
        assert result["family_id"].endswith(hashlib.sha1(b"bridge-adapter").hexdigest()[:8])

    def test_record_matches_spec_schema(self, chronicle, families_path):
        ids = make_threads(chronicle)
        link(chronicle, families_path, ids[:2], note="split rot", by="fable-5-hq")
        record = json.loads(families_path.read_text().strip())
        assert set(record) == {
            "action",
            "timestamp",
            "family_id",
            "label",
            "member_thread_ids",
            "primary_thread_id",
            "note",
            "by",
        }
        assert record["action"] == "link"
        assert record["member_thread_ids"] == ids[:2]
        assert record["by"] == "fable-5-hq"

    def test_unknown_thread_ids_named_in_error(self, chronicle, families_path):
        ids = make_threads(chronicle)
        with pytest.raises(FamilyError, match="thread_nope"):
            link(chronicle, families_path, [ids[0], "thread_nope"])

    def test_resolved_members_allowed(self, chronicle, families_path):
        ids = make_threads(chronicle)
        chronicle.resolve_thread_by_id(ids[0], "answered")
        result = link(chronicle, families_path, ids[:2])
        assert set(result["members"]) == set(ids[:2])

    def test_new_family_needs_two_threads(self, chronicle, families_path):
        ids = make_threads(chronicle)
        with pytest.raises(FamilyError, match="at least 2"):
            link(chronicle, families_path, [ids[0]])

    def test_link_extends_existing_family(self, chronicle, families_path):
        ids = make_threads(chronicle)
        first = link(chronicle, families_path, ids[:2])
        second = link(chronicle, families_path, [ids[1], ids[2]])
        assert second["family_id"] == first["family_id"]
        assert set(second["members"]) == set(ids[:3])
        assert second["member_count"] == 3

    def test_single_thread_extension_allowed(self, chronicle, families_path):
        ids = make_threads(chronicle)
        first = link(chronicle, families_path, ids[:2], primary_thread_id=ids[0])
        extended = link(chronicle, families_path, [ids[0], ids[2]])
        assert extended["family_id"] == first["family_id"]
        # Primary carries forward through the extension.
        assert extended["primary_thread_id"] == ids[0]

    def test_spanning_two_families_is_refused(self, chronicle, families_path):
        ids = make_threads(chronicle)
        link(chronicle, families_path, ids[:2], label="one")
        link(chronicle, families_path, ids[2:], label="two")
        with pytest.raises(FamilyError, match="merge"):
            link(chronicle, families_path, [ids[0], ids[2]], label="bad")

    def test_primary_must_be_member(self, chronicle, families_path):
        ids = make_threads(chronicle)
        with pytest.raises(FamilyError, match="not a member"):
            link(chronicle, families_path, ids[:2], primary_thread_id=ids[3])

    def test_label_required_for_link(self, chronicle, families_path):
        ids = make_threads(chronicle)
        with pytest.raises(FamilyError, match="label is required"):
            link(chronicle, families_path, ids[:2], label="  ")


class TestUnlink:
    def test_unlink_appends_and_fold_removes(self, chronicle, families_path):
        ids = make_threads(chronicle)
        link(chronicle, families_path, ids[:3])
        result = link(chronicle, families_path, [ids[2]], label="", action="unlink")

        records = load_families(families_path)
        assert len(records) == 2  # append-only: nothing rewritten
        assert records[1]["action"] == "unlink"
        assert records[1]["label"] == "bridge-adapter"  # inherited from the family

        fold = fold_families(records)
        assert ids[2] not in fold
        assert set(result["members"]) == set(ids[:2])

    def test_unlink_thread_not_in_family_is_refused(self, chronicle, families_path):
        ids = make_threads(chronicle)
        link(chronicle, families_path, ids[:2])
        with pytest.raises(FamilyError, match="not in any family"):
            link(chronicle, families_path, [ids[3]], action="unlink")

    def test_relink_after_unlink(self, chronicle, families_path):
        ids = make_threads(chronicle)
        link(chronicle, families_path, ids[:3])
        link(chronicle, families_path, [ids[2]], action="unlink")
        relinked = link(chronicle, families_path, [ids[0], ids[2]])
        fold = fold_families(load_families(families_path))
        assert fold[ids[2]]["family_id"] == relinked["family_id"]


# ── fold semantics ──


class TestFold:
    def test_latest_action_per_thread_wins(self):
        fam = generate_family_id("x")
        records = [
            build_family_record(
                action="link", family_id=fam, label="x", member_thread_ids=["t1", "t2"]
            ),
            build_family_record(
                action="unlink", family_id=fam, label="x", member_thread_ids=["t1"]
            ),
            build_family_record(
                action="link", family_id=fam, label="x2", member_thread_ids=["t1", "t3"]
            ),
        ]
        fold = fold_families(records)
        assert set(fold) == {"t1", "t2", "t3"}
        assert fold["t1"]["label"] == "x2"
        assert fold["t2"]["label"] == "x"

    def test_corrupt_lines_skipped(self, tmp_path):
        path = tmp_path / FAMILIES_FILENAME
        record = build_family_record(
            action="link",
            family_id=generate_family_id("x"),
            label="x",
            member_thread_ids=["t1", "t2"],
        )
        path.write_text("not json\n" + json.dumps(record) + "\n\n")
        assert fold_families(load_families(path)).keys() == {"t1", "t2"}

    def test_family_state_takes_latest_label_and_primary(self):
        fam = generate_family_id("x")
        records = [
            build_family_record(
                action="link",
                family_id=fam,
                label="old",
                member_thread_ids=["t1", "t2"],
                primary_thread_id="t1",
                timestamp="2026-06-01T00:00:00+00:00",
            ),
            build_family_record(
                action="link",
                family_id=fam,
                label="new",
                member_thread_ids=["t3"],
                primary_thread_id="t3",
                timestamp="2026-06-02T00:00:00+00:00",
            ),
        ]
        state = family_state(fold_families(records))[fam]
        assert state["label"] == "new"
        assert state["primary_thread_id"] == "t3"
        assert state["member_count"] == 3


# ── engine coalescing ──


def _thread(tid, score=None, ts="2026-06-01T00:00:00+00:00", question="q"):
    t = {"thread_id": tid, "question": question, "domain": "d", "timestamp": ts}
    if score is not None:
        t["triage_score"] = score
        t["triage_reason"] = "10 days old, no recent touches"
    return t


class TestCoalesce:
    def test_non_primary_members_fold_into_primary(self):
        fam = generate_family_id("x")
        fold = fold_families(
            [
                build_family_record(
                    action="link",
                    family_id=fam,
                    label="bridge",
                    member_thread_ids=["t1", "t2", "t3"],
                    primary_thread_id="t2",
                )
            ]
        )
        threads = [_thread("t1"), _thread("t2"), _thread("t3"), _thread("t9")]
        folded = coalesce_threads(threads, fold)
        assert [t["thread_id"] for t in folded] == ["t2", "t9"]
        fam_note = folded[0]["family"]
        assert fam_note["family_id"] == fam
        assert fam_note["label"] == "bridge"
        assert fam_note["member_count"] == 3
        assert sorted(fam_note["folded_thread_ids"]) == ["t1", "t3"]

    def test_folded_thread_ids_always_present(self):
        fam = generate_family_id("x")
        fold = fold_families(
            [
                build_family_record(
                    action="link", family_id=fam, label="x", member_thread_ids=["t1", "t2"]
                )
            ]
        )
        # Only one member visible (the other resolved): nothing folds away,
        # but the key is still there.
        folded = coalesce_threads([_thread("t1")], fold)
        assert folded[0]["family"]["folded_thread_ids"] == []
        assert folded[0]["family"]["member_count"] == 2

    def test_no_primary_recorded_first_row_survives(self):
        fam = generate_family_id("x")
        fold = fold_families(
            [
                build_family_record(
                    action="link", family_id=fam, label="x", member_thread_ids=["t1", "t2"]
                )
            ]
        )
        folded = coalesce_threads([_thread("t2"), _thread("t1")], fold)
        assert folded[0]["thread_id"] == "t2"

    def test_empty_fold_is_data_gated_no_change(self):
        threads = [_thread("t1"), _thread("t2")]
        assert coalesce_threads(threads, {}) == threads

    def test_input_threads_never_mutated(self):
        fam = generate_family_id("x")
        fold = fold_families(
            [
                build_family_record(
                    action="link", family_id=fam, label="x", member_thread_ids=["t1", "t2"]
                )
            ]
        )
        threads = [_thread("t1"), _thread("t2")]
        coalesce_threads(threads, fold)
        assert all("family" not in t for t in threads)


class TestTriage:
    def test_family_max_score_helper(self):
        members = [_thread("t1", 0.4), _thread("t2", 1.3), _thread("t3", 0.9)]
        assert family_max_score(members) == 1.3
        assert family_max_score([]) == 0.0

    def test_family_row_takes_max_member_score_and_reason_suffix(self):
        fam = generate_family_id("x")
        fold = fold_families(
            [
                build_family_record(
                    action="link",
                    family_id=fam,
                    label="x",
                    member_thread_ids=["t1", "t2", "t3"],
                    primary_thread_id="t1",
                )
            ]
        )
        threads = [
            _thread("t1", 0.4),
            _thread("t2", 1.3),
            _thread("t9", 0.8),
            _thread("t3", 0.9),
        ]
        folded = coalesce_triaged(threads, fold)
        family_row = next(t for t in folded if t.get("family"))
        assert family_row["thread_id"] == "t1"
        assert family_row["triage_score"] == 1.3
        assert family_row["triage_reason"].endswith(", family of 3")
        # Re-sorted: the family row (1.3) now outranks the loner (0.8).
        assert [t["thread_id"] for t in folded] == ["t1", "t9"]


# ── thread files byte-unchanged ──


class TestDisplaySideOnly:
    def test_link_and_unlink_never_touch_thread_files(self, chronicle, families_path):
        ids = make_threads(chronicle)
        before = hash_tree(chronicle.threads_dir)

        link(chronicle, families_path, ids[:3])
        link(chronicle, families_path, [ids[2]], action="unlink")

        assert hash_tree(chronicle.threads_dir) == before


# ── tool surface ──


class TestToolSurface:
    def test_schemas_well_formed(self):
        assert [t.name for t in SEASON_TOOLS] == ["link_threads", "season_review"]
        link_schema = SEASON_TOOLS[0].inputSchema
        assert link_schema["required"] == ["thread_ids", "label"]
        assert link_schema["properties"]["action"]["enum"] == ["link", "unlink"]
        review_schema = SEASON_TOOLS[1].inputSchema
        assert review_schema["properties"]["window_days"]["default"] == 90
        assert review_schema["properties"]["max_candidates"]["default"] == 10
        for tool in SEASON_TOOLS:
            assert tool.name in SEASON_TOOL_TIERS
            assert tool.name in SEASON_TOOL_INTENTS

    def test_handle_link_threads_returns_text_with_family_id(self, chronicle, families_path):
        ids = make_threads(chronicle)
        text = handle_season_tool(
            "link_threads",
            {"thread_ids": ids[:2], "label": "bridge-adapter", "primary_thread_id": ids[0]},
            chronicle_root=chronicle.root,
        )
        assert "fam_" in text
        assert f"{ids[0]} (primary)" in text
        assert "thread files untouched" in text

    def test_handle_link_threads_rejection_is_text_not_raise(self, chronicle):
        text = handle_season_tool(
            "link_threads",
            {"thread_ids": ["thread_nope", "thread_also_nope"], "label": "x"},
            chronicle_root=chronicle.root,
        )
        assert text.startswith("⚠️ link_threads rejected:")
        assert "thread_nope" in text

    def test_handle_unknown_tool(self, chronicle):
        assert "Unknown season tool" in handle_season_tool("bogus", {}, chronicle.root)
