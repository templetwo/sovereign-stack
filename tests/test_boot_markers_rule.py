"""
The five markers that matter (design B2; section 8 decision 1).

The rule the boot now states, as the design proposed it and the plan enacts it:
the PERSISTENT MARKERS are ground-truth entries not superseded, at or above
0.9, ranked verified-or-lived first, then by their stored date, then by
intensity, and the door says so. Before this, the five shown were the five
newest writes at >= 0.9 in ANY layer.

  * "Verified" is a receipt the server stamped ``verified`` at write (here a
    file receipt whose sha256 matched), OR a receipt-exempt lived vantage
    (provenance.LIVED_VANTAGES). An attested receipt (human, claim, cmd, url)
    ranks below both, and so does a seat-tag vantage.
  * "Stored date" is the entry's own ``timestamp`` (backdated in place under
    the 2026-06-19 ruling), parsed to one instant. The chronicle holds naive,
    ``+00:00``, other-offset and ``Z`` stamps, so the raw string is never the
    key.
  * Superseded and retired markers are held back and counted, never shown,
    and an all-superseded pool still renders the section (0 of 0 live) rather
    than falling back to anything else.

Every store here is a tmp_sovereign_root; nothing reads or writes ~/.sovereign.
Stamps are seeded through record_insight's ``original_timestamp`` (stored
verbatim), in mixed shapes on purpose.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from sovereign_stack import arrival_state, policies
from sovereign_stack import provenance as prov
from sovereign_stack.arrival_state import build_arrival_state, render_foyer, render_full
from sovereign_stack.handoff import HandoffEngine
from sovereign_stack.memory import ExperientialMemory
from sovereign_stack.reflexive import ReflexiveSurface
from sovereign_stack.witness import is_verified_or_lived, marker_rank_key, rank_markers

_PINNED = "  Persistent marker (pinned standing instruction): "
# Names the cap and what it cost, and no call: the published recall_insights
# declares neither layer_filter nor min_intensity (Task 2 review, finding I-3).
_TRUNCATED_LINE = (
    "  (pool truncated at 1000; entries past the cap were not read, "
    "so the ranking and the counts above cover only the pool)"
)


def _held_back(count: int) -> str:
    """The one holdback line both doors print (Task 2 review, finding I-2)."""
    plural = "s" if count != 1 else ""
    return (
        f"  ({count} superseded marker{plural} held back; "
        "recall_insights(exclude_superseded=false) shows the chain)"
    )


def _header(shown: int, live: int) -> str:
    return (
        "━━━ PERSISTENT MARKERS (ground truth, not superseded, intensity ≥ 0.9; "
        "ranked verified-or-lived first, then stored date, then intensity; "
        f"showing {shown} of {live} live) ━━━"
    )


# ── Fixtures & helpers ───────────────────────────────────────────────────────


@pytest.fixture
def chronicle(tmp_sovereign_root: Path, monkeypatch: pytest.MonkeyPatch) -> ExperientialMemory:
    # The full door and the foyer read the policy registry for their boot line;
    # point it into the tmp root too, so no door here reads the live registry.
    monkeypatch.setattr(
        policies, "DEFAULT_POLICIES_PATH", tmp_sovereign_root / "policies" / "policies.jsonl"
    )
    return ExperientialMemory(root=str(tmp_sovereign_root / "chronicle"))


def _sovereign_root(chronicle: ExperientialMemory) -> Path:
    return Path(chronicle.root).parent


def _verified_receipt(chronicle: ExperientialMemory, content: str) -> dict:
    """A file receipt the server stamps ``verified`` at write: a real file
    under tmp_path (outside /tmp and /private/tmp, which the scratch-path rule
    refuses), with its true sha256."""
    name = hashlib.sha256(content.encode()).hexdigest()[:16]
    artifact = _sovereign_root(chronicle).parent / "receipt-artifacts" / f"{name}.txt"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(f"the bytes that verify {content!r}", encoding="utf-8")
    return {
        "kind": "file",
        "ref": str(artifact),
        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
    }


def _marker(
    chronicle: ExperientialMemory,
    content: str,
    stamp: str,
    *,
    intensity: float = 0.9,
    layer: str = "ground_truth",
    verified: bool = False,
    attested: bool = False,
    vantage: str | None = None,
) -> str:
    """Record one entry with ``stamp`` as its stored timestamp; return its claim id."""
    receipts = []
    if verified:
        receipts.append(_verified_receipt(chronicle, content))
    if attested:
        receipts.append({"kind": "human", "ref": "anthony"})
    path = chronicle.record_insight(
        domain="markers",
        content=content,
        intensity=intensity,
        layer=layer,
        session_id="markers",
        vantage=vantage,
        verified_by=receipts or None,
        original_timestamp=stamp,
        return_claim_id=True,
    )
    return path.claim_id


def _pool(chronicle: ExperientialMemory) -> list[dict]:
    """The pool exactly as both boot profiles gather it."""
    return chronicle.recall_insights(
        min_intensity=0.9, layer_filter="ground_truth", limit=arrival_state._MARKER_POOL
    )


def _contents(entries: list[dict]) -> list[str]:
    return [e["content"] for e in entries]


def _state(chronicle: ExperientialMemory, profile: str):
    root = _sovereign_root(chronicle)
    return build_arrival_state(
        root,
        reader="markers-reader",
        profile=profile,
        experiential=chronicle,
        handoff_engine=HandoffEngine(root=str(root)),
        reflexive_surface=ReflexiveSurface(sovereign_root=root),
        spiral_summary={
            "session_id": "s",
            "current_phase": "INITIALIZATION",
            "tool_call_count": 1,
            "reflection_depth": 0,
            "session_duration_seconds": 0.0,
        },
    )


def _marker_section(text: str) -> list[str]:
    """The full door's PERSISTENT MARKERS lines, header to the closing blank."""
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith("━━━ PERSISTENT MARKERS")]
    assert len(starts) == 1, f"expected one marker section, found {len(starts)}"
    start = starts[0]
    return lines[start : lines.index("", start)]


# ── The rank ─────────────────────────────────────────────────────────────────


def test_verified_older_outranks_attested_newer(chronicle):
    _marker(chronicle, "A", "2026-09-19T12:00:00+00:00", intensity=0.9, verified=True)
    _marker(chronicle, "B", "2026-10-20T12:00:00+00:00", intensity=0.95, attested=True)

    pool = _pool(chronicle)
    # The read itself is newest first: B leads it, so only the rank moves A up.
    assert _contents(pool) == ["B", "A"]
    assert _contents(rank_markers(pool))[:2] == ["A", "B"]


def test_lived_vantage_counts_as_verified(chronicle):
    _marker(chronicle, "B", "2026-10-20T12:00:00+00:00", intensity=0.95, attested=True)
    _marker(chronicle, "C", "2026-08-01T12:00:00+00:00", vantage="human_attestation")
    # A seat tag is a vantage too, and it is never lived: it ranks as attested.
    _marker(chronicle, "D", "2026-07-01T12:00:00+00:00", vantage="hq_filesystem")

    by_content = {e["content"]: e for e in _pool(chronicle)}
    assert is_verified_or_lived(by_content["C"]) is True
    assert is_verified_or_lived(by_content["B"]) is False
    assert is_verified_or_lived(by_content["D"]) is False
    assert _contents(rank_markers(list(by_content.values()))) == ["C", "B", "D"]


def test_recency_then_intensity_within_a_class(chronicle):
    _marker(chronicle, "older verified", "2026-09-01T00:00:00+00:00", verified=True)
    _marker(chronicle, "newer verified", "2026-09-10T00:00:00+00:00", verified=True)
    _marker(
        chronicle, "same instant 0.9", "2026-09-05T00:00:00+00:00", intensity=0.9, verified=True
    )
    _marker(
        chronicle, "same instant 1.0", "2026-09-05T00:00:00+00:00", intensity=1.0, verified=True
    )

    assert _contents(rank_markers(_pool(chronicle))) == [
        "newer verified",
        "same instant 1.0",
        "same instant 0.9",
        "older verified",
    ]


def test_mixed_stamp_shapes_rank_by_instant(chronicle):
    _marker(chronicle, "00:00 Z", "2026-10-01T00:00:00Z")
    _marker(chronicle, "01:00 +00:00", "2026-10-01T01:00:00+00:00")
    _marker(chronicle, "02:00 naive", "2026-10-01T02:00:00")
    assert _contents(rank_markers(_pool(chronicle))) == ["02:00 naive", "01:00 +00:00", "00:00 Z"]

    # 03:30 UTC, spelled with an Eastern offset on the previous calendar day: the
    # newest instant and the lexically smallest string. A raw-string sort puts it
    # last; on a machine in Eastern time (the Studio), reading the naive stamp as
    # local time would put the naive entry (06:00 UTC) ahead of it.
    _marker(chronicle, "03:30Z as -04:00", "2026-09-30T23:30:00-04:00")
    assert _contents(rank_markers(_pool(chronicle))) == [
        "03:30Z as -04:00",
        "02:00 naive",
        "01:00 +00:00",
        "00:00 Z",
    ]


def test_unparseable_stamp_sorts_last_and_odd_rows_do_not_raise():
    # Direct rows, not store writes: record_insight refuses all of these, but the
    # live chronicle is older than its validators, and a withheld protected
    # record (protected.ProtectedStakesUnavailable) carries only a locator.
    verified = [{"kind": "file", "ref": "/x", "checked_at_write": "verified"}]
    rows = [
        {"content": "no stamp", "intensity": 0.95, "verified_by": verified},
        {"content": "garbled stamp", "timestamp": "last tuesday", "intensity": 1.0},
        {"content": "withheld", "timestamp": "2026-06-01T00:00:00+00:00", "domain": "d"},
        {"content": "odd receipts", "timestamp": "2026-05-01", "verified_by": "file:/x"},
        {"content": "odd vantage", "timestamp": "2026-04-01T00:00:00", "vantage": ["x"]},
        {"content": "dated verified", "timestamp": "2026-01-01T00:00:00Z", "verified_by": verified},
    ]
    for row in rows:
        marker_rank_key(row)  # never raises
    assert is_verified_or_lived(rows[3]) is False
    assert is_verified_or_lived(rows[4]) is False
    assert _contents(rank_markers(rows)) == [
        "dated verified",
        "no stamp",
        "withheld",
        "odd receipts",
        "odd vantage",
        "garbled stamp",
    ]


# ── The pool ─────────────────────────────────────────────────────────────────


def test_hypothesis_at_1_0_is_not_a_marker(chronicle):
    _marker(chronicle, "the ground truth marker", "2026-09-01T00:00:00+00:00")
    _marker(
        chronicle,
        "a confident guess",
        "2026-10-20T00:00:00+00:00",
        intensity=1.0,
        layer="hypothesis",
    )
    # The floor stays: ground truth just under 0.9 is not a marker either.
    _marker(chronicle, "ground truth at 0.89", "2026-10-21T00:00:00+00:00", intensity=0.89)

    assert _contents(rank_markers(_pool(chronicle))) == ["the ground truth marker"]
    section = _marker_section(render_full(_state(chronicle, "full")))
    assert section[0] == _header(1, 1)
    assert section[1].endswith("the ground truth marker")
    assert len(section) == 2


def test_superseded_marker_held_back_and_counted(chronicle):
    old_id = _marker(chronicle, "the first word", "2026-09-01T00:00:00+00:00")
    chronicle.record_insight(
        domain="markers",
        content="the corrected word",
        intensity=0.95,
        layer="ground_truth",
        session_id="markers",
        supersedes=[old_id],
        carry_forward_summary="the first word was close but not right",
        original_timestamp="2026-09-02T00:00:00+00:00",
    )

    state = _state(chronicle, "full")
    assert _contents(state.sentinel_pool) == ["the corrected word"]
    assert state.sentinel_held_back == 1
    section = _marker_section(render_full(state))
    assert section[0] == _header(1, 1)
    assert section[1].endswith("the corrected word")
    assert "the first word" not in "\n".join(section)
    assert section[2:] == [_held_back(1)]

    # The foyer, with a live pin AND a held-back count, words the count the same.
    foyer = render_foyer(_state(chronicle, "foyer")).splitlines()
    assert [line for line in foyer if line.startswith(_PINNED)] == [_PINNED + "the corrected word"]
    assert [line for line in foyer if "superseded marker" in line] == [_held_back(1)]


def test_all_superseded_renders_zero_live_no_fallback(chronicle):
    replaced_id = _marker(chronicle, "replaced marker", "2026-09-01T00:00:00+00:00")
    retired_id = _marker(chronicle, "retired marker", "2026-09-03T00:00:00+00:00")
    # The successor is a hypothesis at 1.0: the strongest guess in the store is
    # still not a marker, so it must not stand in for the two that left.
    chronicle.record_insight(
        domain="markers",
        content="the hypothesis successor",
        intensity=1.0,
        layer="hypothesis",
        session_id="markers",
        supersedes=[replaced_id],
        carry_forward_summary="what the replaced marker still teaches",
    )
    # A retirement annotates `_superseded_by: None`: held back by the key's
    # presence, not by its truth.
    prov.append_supersession(
        chronicle.supersessions_path,
        prov.build_supersession_record(action="retire", superseded_id=retired_id),
    )
    assert len(_pool(chronicle)) == 2
    assert rank_markers(_pool(chronicle)) == []

    full_state = _state(chronicle, "full")
    assert full_state.sentinel_pool == []
    assert full_state.sentinel_held_back == 2
    full_text = render_full(full_state)
    section = _marker_section(full_text)
    assert section[0] == _header(0, 0)
    # The whole line, so a clause claiming successors are shown cannot come back:
    # one successor is a hypothesis and the other marker was retired, so none is.
    assert section[1:] == [_held_back(2)]
    for absent in ("replaced marker", "retired marker", "the hypothesis successor"):
        assert absent not in "\n".join(section)

    foyer_text = render_foyer(_state(chronicle, "foyer"))
    foyer = foyer_text.splitlines()
    assert not [line for line in foyer if line.startswith(_PINNED)]
    assert [line for line in foyer if "superseded marker" in line] == [_held_back(2)]
    for text in (full_text, foyer_text):
        assert "successors shown" not in text


def test_pool_truncation_line_renders_when_pool_is_full(chronicle, monkeypatch):
    production_cap = arrival_state._MARKER_POOL
    assert production_cap == 1000
    for day in ("01", "02", "03"):
        _marker(chronicle, f"marker of the {day}th", f"2026-09-{day}T00:00:00+00:00")

    monkeypatch.setattr(arrival_state, "_MARKER_POOL", 2)
    state = _state(chronicle, "full")
    assert state.sentinel_pool_truncated is True
    section = _marker_section(render_full(state))
    assert section[-1] == _TRUNCATED_LINE
    # The line names the production cap (arrival_state._MARKER_POOL), not the
    # patched one: the two constants live in two modules and this holds them equal.
    assert f"pool truncated at {production_cap};" in section[-1]
    # And it sends no seat to a call: none the published tool declares lists the
    # ground-truth entries at >= 0.9, and an undeclared argument on a read tool
    # is ignored, which returns the newest rows of every layer instead.
    assert "recall_insights(" not in section[-1]

    monkeypatch.setattr(arrival_state, "_MARKER_POOL", production_cap)
    state = _state(chronicle, "full")
    assert state.sentinel_pool_truncated is False
    section = _marker_section(render_full(state))
    assert _TRUNCATED_LINE not in section
    assert section[0] == _header(3, 3)


# ── The doors ────────────────────────────────────────────────────────────────


def test_door_label_states_the_rule(chronicle):
    _marker(chronicle, "attested newest", "2026-10-22T00:00:00+00:00", attested=True)
    _marker(chronicle, "verified old", "2026-06-01T00:00:00+00:00", verified=True)
    _marker(chronicle, "lived", "2026-07-01T00:00:00+00:00", vantage="witnessed_account")
    _marker(chronicle, "verified new", "2026-10-01T00:00:00+00:00", verified=True)
    _marker(chronicle, "unreceipted", "2026-10-21T00:00:00+00:00")
    _marker(chronicle, "attested oldest", "2026-05-01T00:00:00+00:00", attested=True)

    section = _marker_section(render_full(_state(chronicle, "full")))
    assert section[0] == _header(5, 6)
    shown = [line.split("] ", 2)[-1] for line in section[1:]]
    assert shown == [
        "verified new [1 verified, 0 attested]",
        "lived",
        "verified old [1 verified, 0 attested]",
        "attested newest [0 verified, 1 attested]",
        "unreceipted",
    ]


def test_foyer_pins_the_top_ranked(chronicle):
    _marker(chronicle, "A", "2026-09-19T12:00:00+00:00", intensity=0.9, verified=True)
    _marker(chronicle, "B", "2026-10-20T12:00:00+00:00", intensity=0.95, attested=True)

    foyer = render_foyer(_state(chronicle, "foyer")).splitlines()
    assert [line for line in foyer if line.startswith(_PINNED)] == [_PINNED + "A"]
    assert not [line for line in foyer if "superseded marker" in line]
