"""
The canonical seat list and the committed spelling map (design B3/D1, decision 2).

THE GAP THIS CLOSES: the handoff signature ledger keys a reader's queue on the
raw signer string, and one seat signs under many spellings. Measured on
~/.sovereign/handoffs/signatures.jsonl 2026-10-22 (820 rows, 16 signers;
re-measured exactly 2026-10-23): HQ signed under seven spellings, so each
spelling read its own queue, and six signers were probe or audit strings that
name no seat. sovereign_stack/seats.py commits the seven canonical seat ids of
decision 2 (ruled "All as proposed", 2026-10-22) and the table that folds ten of
the sixteen onto three of them (HQ, Grok, Codex), and leaves the six probes, and
any spelling it does not know, under their own keys.

The first six tests are the brief's (plan 2 Task 1, Step 1) as written. The
last four pin the table's integrity: no row points outside the seat list, the
sixteen rows split exactly as measured (7 HQ, 2 Grok, 1 Codex, 6 probes),
every canonical id maps to itself, and every lookup strips the same way.

Nothing here reads the seat registry or the live ledger: the registry is
Anthony's switch and the ledger grows while tests run. HQ checks the table
against both outside the suite.
"""

from __future__ import annotations

from collections import Counter

from sovereign_stack.seats import (
    CANONICAL_SEATS,
    SPELLINGS,
    canonical_seat,
    is_probe_spelling,
    reader_key,
)

PROBE_SPELLINGS = {
    "HQ Mac Studio — claude-fable-5-1 (Claude Code) captest",
    "HQ-Audit-Subagent (claude-sonnet-5)",
    "audit-verify-sonnet5",
    "audit-phase3-independent-check",
    "HQ-acceptance-probe-20260831",
    "audit-check-sonnet5",
}


def test_exactly_seven_canonical_ids():
    assert set(CANONICAL_SEATS) == {
        "hq-claude-studio",
        "grok-build-studio",
        "codex-astra-studio",
        "openclaw-watcher-studio",
        "web-claude-ai",
        "edge-claude-jetson",
        "terminal-claude-macbook",
    }


def test_every_measured_spelling_has_a_row():
    assert len(SPELLINGS) == 16 and "HQ Mac Studio, claude-fable-5-1" in SPELLINGS


def test_hq_spellings_fold_to_one_key():
    for s in (
        "HQ Mac Studio, claude-fable-5-1",
        "HQ Mac Studio — claude-fable-5-1 (Claude Code)",
        "HQ Mac Studio — claude-fable-5-1",
        "hq-claude-studio",
    ):
        assert reader_key(s) == "hq-claude-studio"


def test_canonical_id_maps_to_itself():
    assert canonical_seat("grok-build-studio") == "grok-build-studio"


def test_probe_spellings_map_to_none_and_keep_their_key():
    assert canonical_seat("audit-check-sonnet5") is None and is_probe_spelling(
        "audit-check-sonnet5"
    )
    assert reader_key("audit-check-sonnet5") == "audit-check-sonnet5"


def test_unknown_spelling_keeps_its_own_key():
    assert (
        canonical_seat("a brand new seat") is None
        and reader_key(" a brand new seat ") == "a brand new seat"
    )


def test_no_spelling_points_outside_the_canonical_seats():
    for spelling, seat in SPELLINGS.items():
        assert seat is None or seat in CANONICAL_SEATS, spelling
        if spelling in CANONICAL_SEATS:
            assert seat == spelling, spelling


def test_the_sixteen_rows_split_as_measured():
    assert Counter(SPELLINGS.values()) == {
        "hq-claude-studio": 7,
        "grok-build-studio": 2,
        "codex-astra-studio": 1,
        None: 6,
    }
    assert {s for s, seat in SPELLINGS.items() if seat is None} == PROBE_SPELLINGS
    assert SPELLINGS["gpt-6-astra"] == "codex-astra-studio"
    assert SPELLINGS["Grok 4.6 Mac Studio Copilot seat"] == "grok-build-studio"


def test_every_canonical_id_maps_to_itself_with_a_full_record():
    for seat, record in CANONICAL_SEATS.items():
        assert set(record) == {"machine", "substrate", "kind", "source"}, seat
        assert canonical_seat(seat) == seat
        assert reader_key(seat) == seat
        assert not is_probe_spelling(seat)


def test_every_lookup_strips_the_same_way():
    assert canonical_seat("  gpt-6-astra\n") == "codex-astra-studio"
    assert reader_key("\tGrok 4.6 Mac Studio Copilot seat ") == "grok-build-studio"
    assert is_probe_spelling(" audit-check-sonnet5 ")
    assert not is_probe_spelling("HQ Mac Studio, claude-fable-5-1")
    assert not is_probe_spelling("a brand new seat")
