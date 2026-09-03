"""
The authorship-time backfill — exercised, including the write path it is not
allowed to run on the live store.

"DEFAULT IS DRY-RUN, DON'T RUN --APPLY ON LIVE" IS NOT A LICENCE TO SHIP AN
UNEXERCISED WRITE PATH. That is exactly SOP #12's shape: a fix written, never
connected, and discovered to be wrong at the worst possible moment. So --apply
runs here in full against a synthetic tmp store — backups, rewrite, alias file
and changelog — and the live store is never touched by anything in this file.

THE ASSERTIONS THAT MATTER MOST are the refusals: an ambiguous match is
SKIPPED, an unmatched proposal is SKIPPED, and the claim-id citation report is
non-empty when a citation exists. This script rewrites the primary record, and
a wrong match afterwards is indistinguishable from a right one.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "backfill_occurred_at.py"


def _load():
    spec = importlib.util.spec_from_file_location("backfill_occurred_at", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


backfill = _load()

FILED = "2026-05-25T14:00:00+00:00"
COMMITTED = "2026-08-30T02:11:00+00:00"


def _store(tmp_path: Path) -> Path:
    root = tmp_path / ".sovereign"
    (root / "chronicle" / "insights").mkdir(parents=True)
    (root / "grok_bridge" / "pending_writes").mkdir(parents=True)
    (root / "openai_bridge" / "pending_writes").mkdir(parents=True)
    return root


def _proposal(root, pid, *, content, domain, status="committed", queue="grok_bridge", ts=FILED):
    (root / queue / "pending_writes" / f"{pid}.json").write_text(
        json.dumps(
            {
                "proposal_id": pid,
                "timestamp": ts,
                "status": status,
                "tool": "propose_insight",
                "commit_target": "record_insight",
                "arguments": {"domain": domain, "content": content},
            }
        )
    )


def _entry(root, *, content, domain, ts=COMMITTED, shard=None, **extra):
    d = root / "chronicle" / "insights" / domain
    d.mkdir(parents=True, exist_ok=True)
    path = d / (shard or "session_x.jsonl")
    row = {"timestamp": ts, "domain": domain, "content": content, "layer": "hypothesis"}
    row.update(extra)
    with path.open("a") as f:
        f.write(json.dumps(row) + "\n")
    return path


class TestTheIdFormulaHasNotDrifted:
    def test_local_copy_agrees_with_provenance(self):
        """The script carries its own copy so it runs without the package. That
        copy is only safe while this test holds."""
        from sovereign_stack import provenance

        entry = {"timestamp": COMMITTED, "domain": "a,b", "content": "hello — world"}
        assert backfill.derive_claim_id(entry) == provenance.derive_claim_id(entry)

    def test_normalize_domain_agrees_with_memory(self):
        from sovereign_stack.memory import _normalize_domain

        for value in ["a, b", "a,b", "general", " x , y "]:
            assert backfill.normalize_domain(value) == _normalize_domain(value)


class TestTheDryRunPlan:
    def test_a_unique_match_is_planned(self, tmp_path):
        root = _store(tmp_path)
        _proposal(root, "p1", content="c1", domain="dom")
        _entry(root, content="c1", domain="dom")
        plan = backfill.build_plan(root)
        assert len(plan["matched"]) == 1
        row = plan["matched"][0]
        assert row["old_timestamp"] == COMMITTED
        assert row["new_timestamp"] == FILED

    def test_the_plan_backdates_and_keeps_both_axes(self, tmp_path):
        root = _store(tmp_path)
        _proposal(root, "p1", content="c1", domain="dom")
        _entry(root, content="c1", domain="dom")
        new = backfill.build_plan(root)["matched"][0]["new_entry"]
        assert new["timestamp"] == FILED
        assert new["occurred_at"] == COMMITTED
        assert new["timestamp_source"] == "bridge_backfill_20260902"

    def test_an_existing_occurred_at_is_not_overwritten(self, tmp_path):
        """Whatever wrote it knew something this script does not."""
        root = _store(tmp_path)
        _proposal(root, "p1", content="c1", domain="dom")
        _entry(root, content="c1", domain="dom", occurred_at="2026-01-02")
        new = backfill.build_plan(root)["matched"][0]["new_entry"]
        assert new["occurred_at"] == "2026-01-02"

    def test_two_identical_entries_are_AMBIGUOUS_not_guessed(self, tmp_path):
        """A wrong match here is indistinguishable from a right one afterwards."""
        root = _store(tmp_path)
        _proposal(root, "p1", content="c1", domain="dom")
        _entry(root, content="c1", domain="dom", shard="a.jsonl")
        _entry(root, content="c1", domain="dom", ts="2026-08-31T00:00:00+00:00", shard="b.jsonl")
        plan = backfill.build_plan(root)
        assert plan["matched"] == []
        assert len(plan["ambiguous"]) == 1
        assert plan["ambiguous"][0]["candidates"] == 2

    def test_no_chronicle_entry_is_UNMATCHED_not_invented(self, tmp_path):
        root = _store(tmp_path)
        _proposal(root, "p1", content="never landed", domain="dom")
        plan = backfill.build_plan(root)
        assert plan["matched"] == []
        assert len(plan["unmatched"]) == 1

    def test_matching_is_exact_never_fuzzy(self, tmp_path):
        """One character of difference is a different claim, not a near miss."""
        root = _store(tmp_path)
        _proposal(root, "p1", content="c1", domain="dom")
        _entry(root, content="c1 ", domain="dom")
        assert backfill.build_plan(root)["matched"] == []

    def test_the_domain_must_match_too(self, tmp_path):
        root = _store(tmp_path)
        _proposal(root, "p1", content="c1", domain="dom")
        _entry(root, content="c1", domain="other")
        assert backfill.build_plan(root)["matched"] == []

    def test_a_compound_domain_matches_through_normalization(self, tmp_path):
        """ "a, b" and "a,b" are the same domain; the store already holds both
        spellings from before the normalizers were unified."""
        root = _store(tmp_path)
        _proposal(root, "p1", content="c1", domain="a, b")
        _entry(root, content="c1", domain="a,b")
        assert len(backfill.build_plan(root)["matched"]) == 1

    def test_non_committed_proposals_are_not_considered(self, tmp_path):
        root = _store(tmp_path)
        _proposal(root, "p1", content="c1", domain="dom", status="approved")
        _entry(root, content="c1", domain="dom")
        plan = backfill.build_plan(root)
        assert plan["proposals_committed"] == 0
        assert plan["matched"] == []

    def test_non_insight_targets_are_named_not_dropped(self, tmp_path):
        """A denominator that quietly excludes rows is the fail-open shape."""
        root = _store(tmp_path)
        (root / "grok_bridge" / "pending_writes" / "h.json").write_text(
            json.dumps(
                {
                    "proposal_id": "h1",
                    "timestamp": FILED,
                    "status": "committed",
                    "commit_target": "handoff",
                    "arguments": {"note": "n"},
                }
            )
        )
        plan = backfill.build_plan(root)
        assert plan["proposals_committed"] == 1
        assert len(plan["not_applicable"]) == 1
        assert plan["not_applicable"][0]["target"] == "handoff"

    def test_an_already_stamped_entry_is_left_alone(self, tmp_path):
        """Idempotent: a second run must not re-backdate what it backdated."""
        root = _store(tmp_path)
        _proposal(root, "p1", content="c1", domain="dom")
        _entry(root, content="c1", domain="dom", timestamp_source="bridge_backfill_20260902")
        plan = backfill.build_plan(root)
        assert plan["matched"] == []
        assert len(plan["already_stamped"]) == 1

    def test_an_aligned_entry_needs_no_change(self, tmp_path):
        root = _store(tmp_path)
        _proposal(root, "p1", content="c1", domain="dom", ts=COMMITTED)
        _entry(root, content="c1", domain="dom", ts=COMMITTED)
        plan = backfill.build_plan(root)
        assert plan["matched"] == []
        assert len(plan["already_aligned"]) == 1

    def test_both_queues_are_scanned(self, tmp_path):
        """Enumerate the QUEUES, not just the records — the 2026-08-03 lesson:
        the earliest pause acknowledgement of all sat one queue over."""
        root = _store(tmp_path)
        _proposal(root, "g1", content="cg", domain="dom", queue="grok_bridge")
        _proposal(root, "o1", content="co", domain="dom", queue="openai_bridge")
        _entry(root, content="cg", domain="dom")
        _entry(root, content="co", domain="dom")
        plan = backfill.build_plan(root)
        assert {r["queue"] for r in plan["matched"]} == {"grok_bridge", "openai_bridge"}

    def test_an_unreadable_proposal_is_reported_not_swallowed(self, tmp_path):
        root = _store(tmp_path)
        (root / "grok_bridge" / "pending_writes" / "bad.json").write_text("{not json")
        assert backfill.build_plan(root)["problems"]


class TestTheCitationReport:
    """The number that decides whether --apply is safe to run at all."""

    def test_a_cited_entry_is_reported(self, tmp_path):
        root = _store(tmp_path)
        _proposal(root, "p1", content="c1", domain="dom")
        _entry(root, content="c1", domain="dom")
        plan = backfill.build_plan(root)
        old_id = plan["matched"][0]["old_claim_id"]
        (root / "chronicle" / "supersessions.jsonl").write_text(
            json.dumps({"superseded_id": old_id[:16], "note": "points here"}) + "\n"
        )
        again = backfill.build_plan(root)
        assert again["citations"], "a citation on disk must be reported"
        assert any("supersessions" in w for w in again["citations"][old_id])

    def test_an_uncited_entry_reports_no_citation(self, tmp_path):
        """A claim id is derived on read and never stored, so an entry cannot
        contain its own id. No citation anywhere means an empty report."""
        root = _store(tmp_path)
        _proposal(root, "p1", content="c1", domain="dom")
        _entry(root, content="c1", domain="dom")
        assert backfill.build_plan(root)["citations"] == {}

    def test_a_citation_in_the_entrys_OWN_shard_is_counted(self, tmp_path):
        """THE CORRECTION, and the reason the first draft was wrong. A draft
        excluded the entry's own shard on the reasoning that an entry is not a
        citation of itself — but ids are never stored, so the only way the id
        appears in that shard is a DIFFERENT entry citing it, which is the
        commonest citation shape there is (a same-domain supersession). The
        exclusion suppressed exactly the rows that matter and understated the
        one number gating --apply."""
        root = _store(tmp_path)
        _proposal(root, "p1", content="c1", domain="dom")
        _entry(root, content="c1", domain="dom")
        old_id = backfill.build_plan(root)["matched"][0]["old_claim_id"]
        _entry(
            root,
            content=f"superseding the earlier finding, claim {old_id[:16]}",
            domain="dom",
            ts="2026-08-31T00:00:00+00:00",
        )
        plan = backfill.build_plan(root)
        assert old_id in plan["citations"], "an in-shard citation must be counted"

    def test_a_full_64_hex_citation_is_caught_by_the_16_hex_probe(self, tmp_path):
        root = _store(tmp_path)
        _proposal(root, "p1", content="c1", domain="dom")
        _entry(root, content="c1", domain="dom")
        old_id = backfill.build_plan(root)["matched"][0]["old_claim_id"]
        (root / "chronicle" / "supersessions.jsonl").write_text(
            json.dumps({"superseded_id": old_id}) + "\n"
        )
        assert old_id in backfill.build_plan(root)["citations"]

    def test_backdating_actually_changes_the_claim_id(self, tmp_path):
        """The premise of the whole citation report, asserted rather than
        assumed: if this ever became false the report would be theatre."""
        root = _store(tmp_path)
        _proposal(root, "p1", content="c1", domain="dom")
        _entry(root, content="c1", domain="dom")
        row = backfill.build_plan(root)["matched"][0]
        assert row["old_claim_id"] != row["new_claim_id"]


class TestApply:
    """The write path, exercised in full on a synthetic store."""

    def _applied(self, tmp_path):
        root = _store(tmp_path)
        _proposal(root, "p1", content="c1", domain="dom")
        _entry(root, content="untouched", domain="dom")
        _entry(root, content="c1", domain="dom")
        _entry(root, content="also untouched", domain="dom")
        plan = backfill.build_plan(root)
        backup = backfill.apply_plan(root, plan)
        shard = root / "chronicle" / "insights" / "dom" / "session_x.jsonl"
        rows = [json.loads(x) for x in shard.read_text().splitlines() if x.strip()]
        return root, plan, backup, rows

    def test_the_matched_line_is_rewritten(self, tmp_path):
        _root, _plan, _backup, rows = self._applied(tmp_path)
        target = next(r for r in rows if r["content"] == "c1")
        assert target["timestamp"] == FILED
        assert target["occurred_at"] == COMMITTED
        assert target["timestamp_source"] == "bridge_backfill_20260902"

    def test_the_neighbouring_lines_are_untouched(self, tmp_path):
        """Rewriting a shard WHOLE is how this house has lost writes before."""
        _root, _plan, _backup, rows = self._applied(tmp_path)
        assert len(rows) == 3
        for other in (r for r in rows if r["content"] != "c1"):
            assert other["timestamp"] == COMMITTED
            assert "timestamp_source" not in other

    def test_a_backup_of_the_shard_exists_and_is_the_original(self, tmp_path):
        root, _plan, backup, _rows = self._applied(tmp_path)
        copied = backup / "chronicle" / "insights" / "dom" / "session_x.jsonl"
        assert copied.exists()
        rows = [json.loads(x) for x in copied.read_text().splitlines() if x.strip()]
        assert all(r["timestamp"] == COMMITTED for r in rows)
        assert not any("timestamp_source" in r for r in rows)

    def test_a_changelog_names_every_rewrite(self, tmp_path):
        _root, plan, backup, _rows = self._applied(tmp_path)
        text = (backup / "CHANGELOG.txt").read_text()
        assert plan["matched"][0]["old_claim_id"][:16] in text
        assert plan["matched"][0]["new_claim_id"][:16] in text

    def test_the_alias_file_maps_old_id_to_new(self, tmp_path):
        root, plan, _backup, _rows = self._applied(tmp_path)
        aliases = root / "chronicle" / "claim_aliases.jsonl"
        rows = [json.loads(x) for x in aliases.read_text().splitlines() if x.strip()]
        assert len(rows) == 1
        assert rows[0]["old_claim_id"] == plan["matched"][0]["old_claim_id"]
        assert rows[0]["new_claim_id"] == plan["matched"][0]["new_claim_id"]
        assert rows[0]["reason"]
        assert rows[0]["ts"]

    def test_the_rewritten_entry_hashes_to_the_new_claim_id(self, tmp_path):
        """The alias is only useful if the id it names is the id the entry now
        actually derives to."""
        from sovereign_stack import provenance

        _root, plan, _backup, rows = self._applied(tmp_path)
        target = next(r for r in rows if r["content"] == "c1")
        assert provenance.derive_claim_id(target) == plan["matched"][0]["new_claim_id"]

    def test_running_it_twice_changes_nothing_the_second_time(self, tmp_path):
        root, _plan, _backup, _rows = self._applied(tmp_path)
        assert backfill.build_plan(root)["matched"] == []


class TestTheDefaultIsADryRun:
    def test_main_without_apply_writes_nothing(self, tmp_path, capsys):
        root = _store(tmp_path)
        _proposal(root, "p1", content="c1", domain="dom")
        shard = _entry(root, content="c1", domain="dom")
        before = shard.read_bytes()
        assert backfill.main(["--root", str(root)]) == 0
        assert shard.read_bytes() == before
        assert not (root / "backups").exists()
        assert not (root / "chronicle" / "claim_aliases.jsonl").exists()
        assert "DRY RUN" in capsys.readouterr().out

    def test_the_report_states_all_four_counts(self, tmp_path, capsys):
        root = _store(tmp_path)
        _proposal(root, "p1", content="c1", domain="dom")
        _entry(root, content="c1", domain="dom")
        backfill.main(["--root", str(root)])
        out = capsys.readouterr().out
        for label in ("MATCHED", "AMBIGUOUS", "UNMATCHED", "CITED ELSEWHERE"):
            assert label in out

    def test_a_missing_store_exits_nonzero(self, tmp_path):
        assert backfill.main(["--root", str(tmp_path / "nope")]) == 2

    def test_the_json_mode_is_serializable(self, tmp_path, capsys):
        root = _store(tmp_path)
        _proposal(root, "p1", content="c1", domain="dom")
        _entry(root, content="c1", domain="dom")
        backfill.main(["--root", str(root), "--json"])
        parsed = json.loads(capsys.readouterr().out)
        assert len(parsed["matched"]) == 1


class TestItNeverReadsTheLiveStore:
    def test_no_default_root_is_used_by_any_test_here(self, tmp_path):
        """Belt and braces: build_plan must be root-parameterised end to end,
        so nothing in this file can reach ~/.sovereign by omission."""
        import inspect

        assert "root" in inspect.signature(backfill.build_plan).parameters
        assert "root" in inspect.signature(backfill.apply_plan).parameters
        assert "root" in inspect.signature(backfill.find_citations).parameters
        with pytest.raises((SystemExit, TypeError)):
            backfill.build_plan()  # type: ignore[call-arg]


class TestTheGapFilter:
    """A claim-id rewrite buys something only in proportion to how wrong the
    stamp was. On the live store the MEDIAN correction is 0.02 days and the
    max is 98 — one undifferentiated total of 46 hides that difference, and
    it is the difference a human is being asked to decide on."""

    def _two_gaps(self, tmp_path):
        root = _store(tmp_path)
        _proposal(root, "quick", content="cq", domain="dom", ts="2026-08-30T02:00:00+00:00")
        _entry(root, content="cq", domain="dom", ts="2026-08-30T02:30:00+00:00")
        _proposal(root, "slow", content="cs", domain="dom", ts="2026-05-25T14:00:00+00:00")
        _entry(root, content="cs", domain="dom", ts="2026-08-30T02:30:00+00:00")
        return root

    def test_gap_days_measures_the_backdate_distance(self, tmp_path):
        root = self._two_gaps(tmp_path)
        gaps = sorted(backfill.gap_days(r) for r in backfill.build_plan(root)["matched"])
        assert gaps[0] == pytest.approx(0.5 / 24, abs=1e-6)
        assert gaps[1] > 90

    def test_min_gap_sets_rows_aside_and_still_counts_them(self, tmp_path):
        """A filtered row is not a dropped row. A report that shrinks its own
        denominator to look tidy is the fail-open shape in a report's costume."""
        root = self._two_gaps(tmp_path)
        plan = backfill.build_plan(root, min_gap_days=1.0)
        assert len(plan["matched"]) == 1
        assert plan["matched"][0]["proposal"] == "slow"
        assert len(plan["below_threshold"]) == 1
        assert plan["below_threshold"][0]["proposal"] == "quick"

    def test_min_gap_zero_is_the_default_and_filters_nothing(self, tmp_path):
        root = self._two_gaps(tmp_path)
        plan = backfill.build_plan(root)
        assert len(plan["matched"]) == 2
        assert plan["below_threshold"] == []

    def test_citations_are_computed_after_the_filter(self, tmp_path):
        """The citation report must describe what would ACTUALLY be rewritten,
        not a superset — otherwise the number gating --apply is for a different
        operation than the one about to run."""
        root = self._two_gaps(tmp_path)
        quick_id = next(
            r["old_claim_id"]
            for r in backfill.build_plan(root)["matched"]
            if r["proposal"] == "quick"
        )
        (root / "chronicle" / "supersessions.jsonl").write_text(
            json.dumps({"superseded_id": quick_id[:16]}) + "\n"
        )
        assert quick_id in backfill.build_plan(root)["citations"]
        assert quick_id not in backfill.build_plan(root, min_gap_days=1.0)["citations"]

    def test_the_report_buckets_the_distances(self, tmp_path, capsys):
        root = self._two_gaps(tmp_path)
        backfill.main(["--root", str(root)])
        out = capsys.readouterr().out
        assert "HOW FAR BACK" in out
        assert "under 1 hour" in out
        assert "over 7 days" in out


class TestTheShardIsRevalidatedBeforeItIsRewritten:
    """apply_plan rewrites by LINE POSITION captured in build_plan, and holds no
    lock any other process respects. If the shard moved in between — a
    compaction, an archive pass, a metabolism whole-file rewrite — the script
    would overwrite whatever innocent entry slid into line N with the backdated
    content of a different one, SILENTLY and indistinguishably from a correct
    run. Every test here has an inverse: a guard only shown to fire is half a
    guard.
    """

    @staticmethod
    def _plan_then_disturb(tmp_path, disturb):
        root = _store(tmp_path)
        _proposal(root, "p1", content="c1", domain="dom")
        _entry(root, content="before", domain="dom")
        _entry(root, content="c1", domain="dom")
        _entry(root, content="after", domain="dom")
        plan = backfill.build_plan(root)
        assert len(plan["matched"]) == 1
        shard = root / "chronicle" / "insights" / "dom" / "session_x.jsonl"
        disturb(shard)
        before = shard.read_bytes()
        backup = backfill.apply_plan(root, plan)
        return root, plan, shard, before, backup

    def test_an_inserted_line_shifts_the_target_and_the_shard_is_refused(self, tmp_path):
        def insert_first(shard):
            body = shard.read_text()
            shard.write_text(json.dumps({"timestamp": COMMITTED, "content": "new"}) + "\n" + body)

        _root, plan, shard, before, _backup = self._plan_then_disturb(tmp_path, insert_first)
        assert len(plan["refused_shards"]) == 1
        assert shard.read_bytes() == before, "a shifted shard was rewritten anyway"

    def test_a_removed_line_is_refused(self, tmp_path):
        def drop_first(shard):
            # The line BEFORE the target. Dropping the trailing line shifts
            # nothing and is correctly NOT refused — see
            # test_a_removal_AFTER_the_target_does_not_refuse below.
            lines = [x for x in shard.read_text().splitlines() if x.strip()]
            shard.write_text("\n".join(lines[1:]) + "\n")

        _root, plan, shard, before, _backup = self._plan_then_disturb(tmp_path, drop_first)
        assert len(plan["refused_shards"]) == 1
        assert shard.read_bytes() == before

    def test_truncation_past_the_target_lineno_is_refused(self, tmp_path):
        def truncate(shard):
            shard.write_text(json.dumps({"timestamp": COMMITTED, "content": "only"}) + "\n")

        _root, plan, shard, before, _backup = self._plan_then_disturb(tmp_path, truncate)
        assert len(plan["refused_shards"]) == 1
        assert "no longer exists" in plan["refused_shards"][0]["reason"]
        assert shard.read_bytes() == before

    def test_a_line_that_drifted_in_a_field_OUTSIDE_the_claim_preimage_is_refused(self, tmp_path):
        """THE REASON THE COMPARISON IS THE PARSED DICT AND NOT THE CLAIM ID.

        derive_claim_id hashes only timestamp + domain + content. A line whose
        `layer` or `verified_by` changed has the SAME id and is a different
        record; an id-based check would wave it through and the rewrite would
        drop the drifted fields on the floor.
        """

        def edit_layer(shard):
            lines = [x for x in shard.read_text().splitlines() if x.strip()]
            row = json.loads(lines[1])
            row["layer"] = "ground_truth"
            lines[1] = json.dumps(row)
            shard.write_text("\n".join(lines) + "\n")

        _root, plan, shard, before, _backup = self._plan_then_disturb(tmp_path, edit_layer)
        assert len(plan["refused_shards"]) == 1, (
            "a line differing outside the claim_id preimage was accepted — the "
            "guard is comparing ids, not entries"
        )
        assert shard.read_bytes() == before

    def test_a_refused_shard_gets_NO_backup(self, tmp_path):
        """Validating after the copy would leave a stray backup asserting a
        rewrite that never happened."""

        def drop_first(shard):
            # The line BEFORE the target. Dropping the trailing line shifts
            # nothing and is correctly NOT refused — see
            # test_a_removal_AFTER_the_target_does_not_refuse below.
            lines = [x for x in shard.read_text().splitlines() if x.strip()]
            shard.write_text("\n".join(lines[1:]) + "\n")

        root, _plan, _shard, _before, backup = self._plan_then_disturb(tmp_path, drop_first)
        assert not list(backup.rglob("*.jsonl")), "a refused shard was backed up"
        assert not (root / "chronicle" / "claim_aliases.jsonl").exists(), (
            "an alias row was written for a rewrite that never happened"
        )

    def test_a_refused_shard_is_named_in_the_report(self, tmp_path, capsys):
        def drop_first(shard):
            # The line BEFORE the target. Dropping the trailing line shifts
            # nothing and is correctly NOT refused — see
            # test_a_removal_AFTER_the_target_does_not_refuse below.
            lines = [x for x in shard.read_text().splitlines() if x.strip()]
            shard.write_text("\n".join(lines[1:]) + "\n")

        _root, plan, _shard, _before, _backup = self._plan_then_disturb(tmp_path, drop_first)
        plan["_applied"] = True
        backfill.print_report(plan, verbose=False)
        out = capsys.readouterr().out
        assert "SHARDS REFUSED" in out
        assert "session_x.jsonl" in out

    # ── THE INVERSE ────────────────────────────────────────────────────────
    def test_a_removal_AFTER_the_target_does_not_refuse(self, tmp_path):
        """The guard is about the PLANNED LINES, not about the file being
        byte-identical. A change past the last planned line shifts nothing, and
        refusing it would make the guard fire on movement it does not care
        about. (This is also why the first draft of the removal test above
        passed while asserting refusal: it dropped the TRAILING line.)"""

        def drop_last(shard):
            lines = [x for x in shard.read_text().splitlines() if x.strip()]
            shard.write_text("\n".join(lines[:-1]) + "\n")

        _root, plan, shard, before, _backup = self._plan_then_disturb(tmp_path, drop_last)
        assert plan["refused_shards"] == []
        assert shard.read_bytes() != before
        rows = [json.loads(x) for x in shard.read_text().splitlines() if x.strip()]
        assert rows[1]["timestamp"] == FILED

    def test_an_UNDISTURBED_shard_is_rewritten_normally(self, tmp_path):
        """A gate that refuses everything is not a gate."""
        root, plan, shard, before, backup = self._plan_then_disturb(tmp_path, lambda _s: None)
        assert plan["refused_shards"] == []
        assert shard.read_bytes() != before
        rows = [json.loads(x) for x in shard.read_text().splitlines() if x.strip()]
        assert rows[1]["timestamp"] == FILED
        assert rows[0]["content"] == "before" and rows[2]["content"] == "after"
        assert (root / "chronicle" / "claim_aliases.jsonl").exists()
        assert list(backup.rglob("*.jsonl")), "an applied shard was not backed up"

    def test_a_second_shard_still_applies_when_the_first_is_refused(self, tmp_path):
        """Refusal is scoped to the shard that moved, not to the whole run."""
        root = _store(tmp_path)
        _proposal(root, "p1", content="c1", domain="dom")
        _proposal(root, "p2", content="c2", domain="other")
        _entry(root, content="c1", domain="dom")
        _entry(root, content="pad", domain="dom")
        _entry(root, content="c2", domain="other")
        plan = backfill.build_plan(root)
        assert len(plan["matched"]) == 2
        bad = root / "chronicle" / "insights" / "dom" / "session_x.jsonl"
        bad.write_text(json.dumps({"timestamp": COMMITTED, "content": "swapped"}) + "\n")
        untouched = bad.read_bytes()
        backfill.apply_plan(root, plan)
        assert len(plan["refused_shards"]) == 1
        assert bad.read_bytes() == untouched
        good = root / "chronicle" / "insights" / "other" / "session_x.jsonl"
        assert json.loads(good.read_text().splitlines()[0])["timestamp"] == FILED


class TestApplyRefusesWhileAServerIsLive:
    """The script rewrites shards by line position with no cross-process lock
    (chronicle_write_lock is an in-process RLock; the real fix is the unmerged
    hardening/cross-process-flock branch). A running bridge or SSE daemon
    appending mid-rewrite loses its write to the whole-file write_text, with no
    error on either side."""

    @staticmethod
    def _listener():
        import socket

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        return s, s.getsockname()[1]

    @staticmethod
    def _seed(tmp_path):
        root = _store(tmp_path)
        _proposal(root, "p1", content="c1", domain="dom")
        shard = _entry(root, content="c1", domain="dom")
        return root, shard

    def test_apply_is_refused_and_writes_nothing(self, tmp_path, capsys):
        root, shard = self._seed(tmp_path)
        before = shard.read_bytes()
        sock, port = self._listener()
        try:
            rc = backfill.main(["--root", str(root), "--apply"], live_ports=(port,))
        finally:
            sock.close()
        assert rc == 3
        assert shard.read_bytes() == before
        assert not (root / "backups").exists()
        assert not (root / "chronicle" / "claim_aliases.jsonl").exists()
        err = capsys.readouterr().err
        assert f"127.0.0.1:{port}" in err
        assert "com.templetwo.sovereign-sse" in err
        assert "com.templetwo.sovereign-bridge" in err

    def test_the_DRY_RUN_is_never_gated(self, tmp_path, capsys):
        """The report a human needs in order to decide must always be
        available, live server or not."""
        root, _shard = self._seed(tmp_path)
        sock, port = self._listener()
        try:
            rc = backfill.main(["--root", str(root)], live_ports=(port,))
        finally:
            sock.close()
        assert rc == 0
        assert "DRY RUN" in capsys.readouterr().out

    # ── THE INVERSE ────────────────────────────────────────────────────────
    def test_apply_PROCEEDS_when_no_port_answers(self, tmp_path):
        """A gate that refuses unconditionally is not a gate."""
        root, shard = self._seed(tmp_path)
        sock, port = self._listener()
        sock.close()  # now nothing is listening on `port`
        before = shard.read_bytes()
        rc = backfill.main(["--root", str(root), "--apply"], live_ports=(port,))
        assert rc == 0
        assert shard.read_bytes() != before

    def test_the_probe_itself_can_tell_the_difference(self, tmp_path):
        """Positive control on answering_ports, independent of main()."""
        sock, port = self._listener()
        try:
            assert backfill.answering_ports((port,)) == [port]
        finally:
            sock.close()
        assert backfill.answering_ports((port,)) == []

    def test_the_default_ports_are_the_live_stack_ones(self):
        assert backfill.LIVE_PORTS == (3434, 8100)


class TestARefusedShardIsARefusedRUN:
    """GUARD 2 WAS FAIL-CLOSED ON THE WRITE AND FAIL-OPEN ON THE EXIT CODE.

    `main` returned 0 after `apply_plan` whether one shard was refused or all
    of them were, so a caller scripting `--apply && ...` read a fully refused
    migration as a completed one — SOP #1's own "exit code 0 is not ran",
    inside the script written against SOP #2. Nothing was ever corrupted (a
    refused shard is genuinely untouched, and `print_report` does print SHARDS
    REFUSED); the hole was that the only signal was prose.

    The vocabulary already existed three lines of control flow away: 2 = no
    store, 3 = a live server refused the whole run. This adds 4 = applied, at
    least one shard refused.

    HOW THIS IS EXERCISED THROUGH `main`, WHICH IS THE POINT. Every other
    guard-2 test calls `apply_plan` directly, which is exactly why nothing
    caught the exit code. `main` plans and applies in one call, so a shard
    cannot be disturbed in between — the plan is built here, the shard is
    disturbed, and `build_plan` is then monkeypatched to hand `main` that
    stale plan. `live_ports=()` keeps guard 1 from firing first and masking
    the assertion with a 3.
    """

    @staticmethod
    def _stale_plan(tmp_path, monkeypatch, *, extra_shard=False):
        root = _store(tmp_path)
        _proposal(root, "p1", content="c1", domain="dom")
        _entry(root, content="before", domain="dom")
        _entry(root, content="c1", domain="dom")
        if extra_shard:
            # A SECOND, UNDISTURBED shard, so the run is PARTIAL: one shard is
            # rewritten and one is refused. 4 must not require total refusal.
            _proposal(root, "p2", content="c2", domain="other")
            _entry(root, content="c2", domain="other")
        plan = backfill.build_plan(root)
        assert len(plan["matched"]) == (2 if extra_shard else 1)
        shard = root / "chronicle" / "insights" / "dom" / "session_x.jsonl"
        body = shard.read_text()
        shard.write_text(json.dumps({"timestamp": COMMITTED, "content": "new"}) + "\n" + body)
        monkeypatch.setattr(backfill, "build_plan", lambda *a, **k: plan)
        return root, shard, plan

    def test_a_totally_refused_apply_exits_4(self, tmp_path, monkeypatch, capsys):
        root, shard, _plan = self._stale_plan(tmp_path, monkeypatch)
        before = shard.read_bytes()
        rc = backfill.main(["--root", str(root), "--apply"], live_ports=())
        assert rc == 4, "a fully refused migration reported success on its exit status"
        assert shard.read_bytes() == before
        assert "SHARDS REFUSED" in capsys.readouterr().out

    def test_a_PARTIALLY_refused_apply_also_exits_4(self, tmp_path, monkeypatch):
        """ "Some of it landed" is not "it landed"."""
        root, shard, _plan = self._stale_plan(tmp_path, monkeypatch, extra_shard=True)
        before = shard.read_bytes()
        rc = backfill.main(["--root", str(root), "--apply"], live_ports=())
        assert rc == 4
        assert shard.read_bytes() == before
        other = root / "chronicle" / "insights" / "other" / "session_x.jsonl"
        assert backfill.TIMESTAMP_SOURCE in other.read_text(), (
            "the undisturbed shard should still have been rewritten — 4 reports a "
            "refusal, it does not abort the run"
        )

    # ── THE INVERSE ────────────────────────────────────────────────────────
    def test_an_apply_that_refuses_NOTHING_still_exits_0(self, tmp_path):
        """A code that is always 4 says nothing. Same store, undisturbed."""
        root = _store(tmp_path)
        _proposal(root, "p1", content="c1", domain="dom")
        shard = _entry(root, content="c1", domain="dom")
        before = shard.read_bytes()
        rc = backfill.main(["--root", str(root), "--apply"], live_ports=())
        assert rc == 0
        assert shard.read_bytes() != before

    def test_the_json_surface_carries_the_refusal_too(self, tmp_path, monkeypatch, capsys):
        """--json bypasses print_report, so the prose signal is gone there.
        The exit code is then the ONLY signal, plus refused_shards in the
        payload — assert both, since a JSON caller is the scripted one."""
        root, _shard, _plan = self._stale_plan(tmp_path, monkeypatch)
        rc = backfill.main(["--root", str(root), "--apply", "--json"], live_ports=())
        assert rc == 4
        payload = json.loads(capsys.readouterr().out.split("\n", 1)[1])
        assert len(payload["refused_shards"]) == 1
