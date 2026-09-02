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

    def test_an_entry_is_not_a_citation_of_itself(self, tmp_path):
        """Its own shard contains its content, not its id — but if the id form
        ever changes, this is the assertion that stops the report inflating."""
        root = _store(tmp_path)
        _proposal(root, "p1", content="c1", domain="dom")
        _entry(root, content="c1", domain="dom")
        assert backfill.build_plan(root)["citations"] == {}

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
