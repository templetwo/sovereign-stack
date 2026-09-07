"""Tests for the write-side aperture (gate_census).

The load-bearing test is test_census_equals_console_per_substrate. Everything
else guards a specific way this block could lie: reporting zero when it means
"could not read", hiding a status it did not anticipate, leaking proposal
content onto a public endpoint, or rendering a future-dated proposal as fresh.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sovereign_stack.gate_census import (
    GATE_POLICY_VERSION,
    _parse_ts,
    measure_gate,
    unmeasured,
)

NOW = datetime.now(timezone.utc)


# ── A BARE measure_gate() READS THE OPERATOR'S HOME, AND THAT IS THE MODULE
# ── BEHAVING CORRECTLY. THE TESTS WERE ASKING THE WRONG QUESTION. ────────────
#
# `_sovereign_root(None)` is `Path.home() / ".sovereign"`, by design: the live
# queues ARE the thing this census measures, and `SOVEREIGN_ROOT` does not
# redirect it. On any machine without that store — every CI runner — discovery
# finds nothing and raises FileNotFoundError, which is rule 3 working. Nine
# tests called `measure_gate(NOW)` bare and so asserted against Anthony's real
# store; they were green on his Mac and red everywhere else. Same class as the
# protected-drawer boot tests isolated at `a6f42cf` and the signal-ledger
# containment test retyped alongside this: a test that reads live human state
# fails for a reason that has nothing to do with the code.
#
# TWO REMEDIES, PICKED PER TEST, because they are two different kinds of claim:
#
#   * `_live_census()` — for assertions that are genuinely ABOUT the operator's
#     real queues (does the census agree with the console he drains from; do
#     real seat identifiers stay out of the public block). Those cannot be
#     asked anywhere else, so off that machine they SKIP, naming the reason —
#     the idiom `test_live_third_queue_is_not_invisible` already used.
#
#   * `_stub_measured()` — for assertions about AGGREGATION and PROSE, which
#     are properties of the code and must therefore run in CI. Skipping all
#     nine would trade a red suite for a silent one, and a suite that reports
#     success by declining to look is the failure this repo hunts everywhere
#     else.
#
# WHY A STUB IS NEEDED AT ALL, and it is worth knowing: `root=` redirects
# DISCOVERY ONLY. `measure_gate` calls `_measure_substrate(source, now)` with no
# root, so a routable substrate is always measured through the real bridge
# console. That is why every pre-existing tmp_path test in this file asserts
# only on *unmeasured* entries — a synthetic root cannot produce a measured one.


def _live_census() -> dict:
    """The census over the operator's real store, or a skip that says why."""
    base = Path.home() / ".sovereign"
    if not any(p.is_dir() for p in base.glob("*/pending_writes")):
        pytest.skip(
            f"no live bridge queues under {base}; this assertion is about the "
            "operator's real store and cannot be asked here"
        )
    return measure_gate(NOW)


def _synthetic_root(tmp_path: Path) -> Path:
    """A root holding one discoverable queue, so discovery does not raise."""
    (tmp_path / "some_connector" / "pending_writes").mkdir(parents=True)
    return tmp_path


def _stub_measured(monkeypatch, entries: dict[str, dict]) -> None:
    """Make named substrates render as MEASURED, with counts we control.

    A test double for the console read, NOT for the aggregation under test:
    `measure_gate` still does its own discovery, summing, partitioning and
    prose. Only the per-substrate console call is replaced.
    """
    from sovereign_stack import gate_census as gc

    monkeypatch.setitem(gc._DIR_TO_SOURCE, "some_connector", "stub")
    monkeypatch.setattr(gc, "_measure_substrate", lambda source, now: entries[source])


# -- The trust anchor ---------------------------------------------------------


def test_census_equals_console_per_substrate():
    """The census must agree with the console Anthony actually drains from.

    The two substrates run DIFFERENT backends (openai dispatches to the legacy
    openai_bridge module, grok to bridge_core with a registered context). If this
    module ever re-derived counts itself, it could diverge from the console and
    become a confident second source of truth. This test is the reason to
    believe the numbers.
    """
    from bridge_core.cli import _SubstrateOps

    # The skip has to come BEFORE the call: on a store with no queues
    # `measure_gate` raises, so the per-substrate skip below was unreachable.
    census = _live_census()
    for dirname, source in (("grok_bridge", "grok"), ("openai_bridge", "openai")):
        entry = census["substrates"][dirname]
        if entry["status"] != "measured":
            pytest.skip(f"{dirname} unmeasured in this environment: {entry.get('reason')}")
        console_rows = _SubstrateOps(source).list(None)
        console_pending = _SubstrateOps(source).list("pending")
        assert entry["total_proposals"] == len(console_rows)
        assert entry["by_status"].get("pending", 0) == len(console_pending)


def test_total_is_the_sum_of_measured_substrates():
    c = _live_census()
    expected = sum(
        s["by_status"].get("pending", 0)
        for s in c["substrates"].values()
        if s.get("status") == "measured"
    )
    assert c["total_pending_all_substrates"] == expected


# -- Fail-closed: absent is never zero ----------------------------------------


def test_unmeasured_carries_no_counts():
    """The honest-failure shape must contain no numbers at all."""
    u = unmeasured(NOW, RuntimeError("queue unreadable"))
    assert u["status"] == "unmeasured"
    assert u["policy_version"] == GATE_POLICY_VERSION
    blob = json.dumps(u)
    for forbidden in ("total_pending", "by_status", "substrates", "pending_by_tool"):
        assert forbidden not in blob
    assert "NOT zero" in u["note"]


def test_unknown_substrate_dir_is_unmeasured_not_omitted(tmp_path):
    """A queue with no registered dispatcher must APPEAR, without counts.

    Omitting it would read as 'nothing pending there' -- the exact false-clean
    this module exists to prevent.
    """
    q = tmp_path / "someothersubstrate_bridge" / "pending_writes"
    q.mkdir(parents=True)
    c = measure_gate(NOW, root=tmp_path)
    entry = c["substrates"]["someothersubstrate_bridge"]
    assert entry["status"] == "unmeasured"
    assert "not zero" in entry["reason"]
    assert "by_status" not in entry
    assert c["any_unmeasured"] is True


def test_directory_without_pending_writes_is_skipped(tmp_path):
    """A *_bridge dir with no pending_writes subdir is not a queue at all."""
    (tmp_path / "decorative_bridge").mkdir(parents=True)
    (tmp_path / "real_bridge" / "pending_writes").mkdir(parents=True)
    c = measure_gate(NOW, root=tmp_path)
    assert "decorative_bridge" not in c["substrates"]
    assert "real_bridge" in c["substrates"]


def test_no_queues_discovered_raises(tmp_path):
    """Discovery failure propagates so the caller renders unmeasured()."""
    with pytest.raises(FileNotFoundError):
        measure_gate(NOW, root=tmp_path)


# -- Timestamp honesty --------------------------------------------------------


def test_naive_timestamp_is_read_as_utc():
    """Proposal timestamps are written naive-UTC; reading them as local would
    shift every age by the offset and under-report the backlog."""
    dt, age = _parse_ts(
        "2026-08-27T21:05:55", datetime(2026, 8, 28, 21, 5, 55, tzinfo=timezone.utc)
    )
    assert dt.tzinfo is timezone.utc
    assert age == 1


def test_future_timestamp_raises_rather_than_reading_as_fresh():
    """A future-dated proposal means the clock or the writer is wrong. Rendering
    it as '0 days' would read as 'filed just now'."""
    with pytest.raises(ValueError, match="future"):
        _parse_ts((NOW + timedelta(days=2)).isoformat(), NOW)


# -- Shape guarantees ---------------------------------------------------------


def test_status_keys_are_read_from_data_not_a_fixed_set():
    """Every status present in the data must appear in by_status and waiting_on,
    so a status added later cannot vanish silently."""
    c = _live_census()
    for entry in c["substrates"].values():
        if entry.get("status") != "measured":
            continue
        assert set(entry["by_status"]) == set(entry["waiting_on"])
        for owner in entry["waiting_on"].values():
            assert owner  # never empty; unrecognised statuses get "unknown"


def test_public_block_leaks_no_proposal_content():
    """This block renders on the PUBLIC unauthenticated heartbeat. Counts, dates,
    tool names and statuses are the ceiling -- never bodies, domains, or the
    source_instance strings that identify a seat."""
    c = _live_census()
    blob = json.dumps(c)
    for leak in ("chatgpt-openai-bridge", "grok-xai", "grok-4.5", "grok-4.6"):
        assert leak not in blob, f"source_instance leaked into public block: {leak}"
    for entry in c["substrates"].values():
        assert "content" not in entry
        assert "domain" not in entry
        assert "records" not in entry


def test_needs_revision_terminal_warning_appears_only_when_nonzero():
    c = _live_census()
    for entry in c["substrates"].values():
        if entry.get("status") != "measured":
            continue
        if entry["by_status"].get("needs_revision"):
            assert "needs_revision_is_terminal" in entry
            assert "only ever be rejected" in entry["needs_revision_is_terminal"]
        else:
            assert "needs_revision_is_terminal" not in entry


def test_drain_names_the_human_gate(tmp_path):
    """HQ reviews; it does not approve, commit, or reject. The block must say so
    on its face -- a remote seat reading this must not conclude HQ can ratify.

    Runs anywhere: the prose is a constant, so it needs a discoverable queue and
    no live data at all."""
    d = measure_gate(NOW, root=_synthetic_root(tmp_path))["drain"]
    assert "Anthony only" in d["who"]
    assert "does not approve" in d["hq_role"]


# -- Discovery by shape, not by name ------------------------------------------


def test_queue_is_discovered_by_shape_not_by_name(tmp_path):
    """A queue directory that does not end in '_bridge' must still be found.

    The first version globbed '*_bridge' and silently missed
    ~/.sovereign/antigravity_connector/pending_writes -- 2 real proposals from a
    real connector rendering as ABSENT rather than as unmeasured. A name-based
    glob defeats the fail-closed rule from the outside: an unmatched queue is not
    reported as unreadable, it is not reported at all.
    """
    (tmp_path / "some_connector" / "pending_writes").mkdir(parents=True)
    c = measure_gate(NOW, root=tmp_path)
    assert "some_connector" in c["substrates"]


def test_unroutable_queue_reports_an_existence_floor(tmp_path):
    """No console route means no STATUS counts -- but a readable directory still
    proves how many records exist, and a blank would hide them."""
    q = tmp_path / "some_connector" / "pending_writes"
    q.mkdir(parents=True)
    for i in range(3):
        (q / f"p{i}.json").write_text("{}")
    entry = measure_gate(NOW, root=tmp_path)["substrates"]["some_connector"]
    assert entry["status"] == "unmeasured"
    assert entry["files_on_disk"] == 3
    assert "NOT a status count" in entry["files_on_disk_note"]
    # The floor must never be mistaken for a status histogram.
    assert "by_status" not in entry


def test_live_third_queue_is_not_invisible():
    """Regression guard for the real miss: every queue on disk under the live
    sovereign root must appear in the census, routable or not."""
    from pathlib import Path

    base = Path.home() / ".sovereign"
    on_disk = {p.parent.name for p in base.glob("*/pending_writes") if p.is_dir()}
    if not on_disk:
        pytest.skip("no live queues on this machine")
    reported = set(measure_gate(NOW)["substrates"])
    assert on_disk <= reported, f"queues missing from census: {on_disk - reported}"


# -- Burden: claim-bearing vs bookkeeping -------------------------------------


def test_claim_bearing_and_bookkeeping_partition_the_pending_set():
    """The two kind-counts must sum to pending -- no write falls outside both."""
    c = _live_census()
    for entry in c["substrates"].values():
        if entry.get("status") != "measured":
            continue
        assert entry["pending_claim_bearing"] + entry["pending_bookkeeping"] == entry[
            "by_status"
        ].get("pending", 0)


def test_claim_bearing_total_is_never_larger_than_total_pending():
    c = _live_census()
    assert c["total_pending_claim_bearing"] <= c["total_pending_all_substrates"]
    assert "overstates" in c["burden_note"]


def test_bookkeeping_classification_is_by_tool_semantics_not_content():
    """Kind is decided by the tool name alone. A per-item judgement call would be
    a new fail-open -- it would be wrong silently."""
    from sovereign_stack.gate_census import _BOOKKEEPING_TOOLS

    assert "reflection_ack" in _BOOKKEEPING_TOOLS
    assert "end_bridge_session" in _BOOKKEEPING_TOOLS
    # Anything that asserts a claim into the record is NOT bookkeeping.
    for claim_tool in ("propose_insight", "propose_learning", "record_open_thread", "handoff"):
        assert claim_tool not in _BOOKKEEPING_TOOLS


def test_unreadable_queue_is_unmeasured_never_zero(tmp_path):
    """Rule 3's other half: a queue that EXISTS but cannot be read must render
    unmeasured, not empty. The name-glob fix does not exercise this path."""
    import os

    q = tmp_path / "locked_connector" / "pending_writes"
    q.mkdir(parents=True)
    (q / "a.json").write_text("{}")
    os.chmod(q, 0o000)
    try:
        entry = measure_gate(NOW, root=tmp_path)["substrates"]["locked_connector"]
        assert entry["status"] == "unmeasured"
        assert entry.get("files_on_disk") != 0, "unreadable must not render as zero"
        assert "by_status" not in entry
    finally:
        os.chmod(q, 0o755)


def test_bookkeeping_label_disclaims_being_a_safety_tier(tmp_path):
    """The set is burden accounting. A comms_acknowledge that would fabricate a
    consent record is 'bookkeeping' by tool name — the block must say on its face
    that this is not a drain list."""
    c = measure_gate(NOW, root=_synthetic_root(tmp_path))
    note = c["burden_note"]
    assert "NOT a safety tier" in note
    assert "NOT a drain list" in note


# -- Aggregation, exercised without the operator's store ----------------------
#
# THE SKIPS ABOVE ARE HONEST AND THEY ARE ALSO A COST: assertions that ran on one
# Mac now run nowhere else. These re-ask the part that is machine-independent.
#
# WHAT A STUB CAN AND CANNOT EARN HERE, stated because the first draft of this
# block got it wrong in both directions. Replacing `_measure_substrate` replaces
# the console read AND everything computed inside it — the per-substrate
# `by_status` / `waiting_on` / claim-bearing split, and the
# `needs_revision_is_terminal` warning, are all produced THERE. So:
#
#   * Asserting that a stubbed entry's own fields partition, or that its
#     by_status keys match its waiting_on keys, asserts nothing about the code:
#     it reads back the fixture. Those stay live-or-skip.
#   * Asserting the needs_revision prose is not merely tautological but
#     impossible — the stub is what removed it.
#   * What `measure_gate` itself still computes over the stub is the CROSS-
#     SUBSTRATE SUMMATION and the constant prose. That is what is tested below,
#     and it is the half that had no coverage off Anthony's machine at all.


def _measured(pending: int, claim_bearing: int) -> dict:
    return {
        "status": "measured",
        "total_proposals": pending,
        "by_status": {"pending": pending},
        "waiting_on": {"pending": "Anthony"},
        "pending_claim_bearing": claim_bearing,
        "pending_bookkeeping": pending - claim_bearing,
    }


def test_the_totals_sum_the_measured_substrates_without_a_live_store(tmp_path, monkeypatch):
    """measure_gate's own arithmetic, over a substrate it never had to read."""
    _stub_measured(monkeypatch, {"stub": _measured(7, 4)})
    c = measure_gate(NOW, root=_synthetic_root(tmp_path))
    assert c["substrates"]["some_connector"]["status"] == "measured"
    assert c["total_pending_all_substrates"] == 7
    assert c["total_pending_claim_bearing"] == 4
    assert c["total_pending_claim_bearing"] <= c["total_pending_all_substrates"]
    assert "overstates" in c["burden_note"]


def test_an_unmeasured_substrate_contributes_nothing_to_the_totals(tmp_path, monkeypatch):
    """Rule 3 at the aggregate: a substrate that could not be read must not be
    summed as a zero — it must be excluded, and flagged.

    TWO SUBSTRATES, ONE OF EACH, DELIBERATELY. With only the failing queue the
    total is 0 whether the code excludes it or sums it as zero, so the assertion
    would hold for both the right and the wrong implementation — a test that
    cannot fail is not a check. A healthy queue beside it makes the two
    behaviours produce different numbers: 7 if the unmeasured one is excluded,
    still 7 if it were summed as zero — so the load-bearing half is that the
    total is REPORTED AT ALL while `any_unmeasured` is true, and that the
    unreadable entry carries no counts of its own.
    """
    from sovereign_stack import gate_census as gc

    def measure(source, now):
        if source == "boom":
            raise RuntimeError("console unavailable")
        return _measured(7, 4)

    (tmp_path / "good_connector" / "pending_writes").mkdir(parents=True)
    (tmp_path / "broken_connector" / "pending_writes").mkdir(parents=True)
    monkeypatch.setitem(gc._DIR_TO_SOURCE, "good_connector", "stub")
    monkeypatch.setitem(gc._DIR_TO_SOURCE, "broken_connector", "boom")
    monkeypatch.setattr(gc, "_measure_substrate", measure)

    c = measure_gate(NOW, root=tmp_path)
    broken = c["substrates"]["broken_connector"]
    assert broken["status"] == "unmeasured"
    assert "console unavailable" in broken["reason"]
    assert "by_status" not in broken
    assert "pending_claim_bearing" not in broken
    assert c["substrates"]["good_connector"]["status"] == "measured"
    assert c["total_pending_all_substrates"] == 7
    assert c["total_pending_claim_bearing"] == 4
    assert c["any_unmeasured"] is True
