"""
The boot doors surface a protected record only as its threshold.

Standing law: protected records are never decoupled from their stakes and are
surfaced only as thresholds. The protected-source layer's preview rule
(spec section 5.4, e133ede) says how a surface honors it: one that previews or
truncates can never carry the full stakes, so it WITHHOLDS the record's content
to protected.PROTECTED_PREVIEW_NOTICE. The Ground and The Descent already do.

Every insight-content row the full door and the foyer render is such a surface,
and none of them renders the stakes, so a coupled body on any of them travels
decoupled at every depth, full_content included:

  * a PERSISTENT MARKERS line (120 characters, or the whole body);
  * the foyer's pinned marker (the whole body);
  * an activity row (120, or whole) and the foyer's "Latest:" line (200);
  * a related-insight row under CONTEXTUAL RESONANCE (120, or whole).

The read chokepoint (memory.finalize_read, stage B) marks every protected entry
``_protected``, coupled or withheld; arrival_state passes each one through
protected.withhold_preview at the gather. The filter itself (protected.py,
finalize_read) is untouched, and the locators survive, so the rank and the
counts do not move.

The verifier is the one the bypass-path tests use, protected.audit_decoupling on
the REAL rendered text, plus the stricter audit_threshold (a boot row is a
threshold surface). The undesignated control proves each door's read reaches the
record, so every absence below is measured against a body that would otherwise
render. Every store is a tmp_sovereign_root; nothing reads or writes ~/.sovereign.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sovereign_stack import arrival_state, policies
from sovereign_stack.arrival_state import build_arrival_state, render_foyer, render_full
from sovereign_stack.handoff import HandoffEngine
from sovereign_stack.memory import ExperientialMemory
from sovereign_stack.protected import (
    PROTECTED_PREVIEW_NOTICE,
    ProtectedStakesUnavailable,
    audit_decoupling,
    audit_threshold,
    designate_protected,
)
from sovereign_stack.reflexive import ReflexiveSurface

NOTICE = PROTECTED_PREVIEW_NOTICE
PROTECTED_CONTENT = "zz the lived marker body that the boot may surface only as its threshold"
STAKES_PROSE = "zz a lived weight the human carries, held coupled to the words it belongs to"
SUBJECT, EMOTION = "zzpreviewsubj", "zzpreviewemo"
ORDINARY = "an ordinary standing marker that renders as it always has"
PROTECTED_STAMP = "2026-10-20T12:00:00+00:00"
ORDINARY_STAMP = "2026-10-01T12:00:00+00:00"
_PINNED = "  Persistent marker (pinned standing instruction): "

STAKES = pytest.mark.parametrize(
    "break_stakes", (False, True), ids=("stakes-verified", "stakes-missing")
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


def _seed(chronicle: ExperientialMemory, *, designate: bool, break_stakes: bool = False) -> None:
    """An ordinary marker, and a newer lived marker that is designated protected.

    The lived one ranks first (verified-or-lived, newest), so it is the foyer's
    pin, the first marker line and the newest activity row. The ordinary one is
    unreceipted and older, so it ranks second even when the stakes are missing
    (the withheld sentinel keeps only its locator and ranks as attested).
    """
    chronicle.record_insight(
        domain="markers",
        content=ORDINARY,
        intensity=0.9,
        layer="ground_truth",
        session_id="previews",
        original_timestamp=ORDINARY_STAMP,
    )
    path = chronicle.record_insight(
        domain="personal",
        content=PROTECTED_CONTENT,
        intensity=0.95,
        layer="ground_truth",
        session_id="previews",
        vantage="human_attestation",
        original_timestamp=PROTECTED_STAMP,
        return_claim_id=True,
    )
    if not designate:
        return
    archive = chronicle.archive_exchange(
        content=STAKES_PROSE,
        source="human-relay",
        descriptor="stakes",
        vector_id="boot_preview_stakes",
    )
    designate_protected(
        claim_ref=path.claim_id,
        stakes_archive_id=archive["archive_id"],
        designated_by="Anthony",
        chronicle_root=str(chronicle.root),
        subject=SUBJECT,
        emotion=EMOTION,
    )
    if break_stakes:
        index = chronicle._read_archive_index()
        blob = Path(next(r for r in index if r["archive_id"] == archive["archive_id"])["path"])
        blob.unlink()


def _state(chronicle: ExperientialMemory, profile: str, *, domain_tags: list[str] | None = None):
    root = Path(chronicle.root).parent
    return build_arrival_state(
        root,
        reader="previews-reader",
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
        domain_tags=domain_tags,
    )


def _block(text: str, starts: tuple[str, ...]) -> list[str]:
    """The lines from the one line starting with ``starts`` to the next blank."""
    lines = text.splitlines()
    hits = [i for i, line in enumerate(lines) if line.startswith(starts)]
    assert len(hits) == 1, f"expected one block starting {starts}, found {len(hits)}"
    return lines[hits[0] : lines.index("", hits[0])]


def _markers(text: str) -> list[str]:
    return _block(text, ("━━━ PERSISTENT MARKERS",))


def _activity(text: str) -> list[str]:
    return _block(text, ("━━━ RECENT ACTIVITY", "━━━ ACTIVITY SINCE"))


def _related(text: str) -> list[str]:
    return _block(text, ("  Related insights (",))


def _assert_threshold_only(text: str, chronicle: ExperientialMemory) -> None:
    # The audits first: they are the verifier, run on the string a model reads.
    assert audit_decoupling(text, chronicle.root) == []
    assert audit_threshold(text, chronicle.root) == []
    for secret in (PROTECTED_CONTENT, STAKES_PROSE, SUBJECT, EMOTION):
        assert secret not in text


# ── The control: without a designation every surface reaches the body ──────


def test_undesignated_body_renders_on_every_surface(chronicle):
    _seed(chronicle, designate=False)
    full = render_full(_state(chronicle, "full", domain_tags=["personal"]))
    assert _markers(full)[1].endswith(PROTECTED_CONTENT)
    assert _activity(full)[1].endswith(PROTECTED_CONTENT)
    assert _related(full)[1].endswith(PROTECTED_CONTENT)
    foyer = render_foyer(_state(chronicle, "foyer")).splitlines()
    assert _PINNED + PROTECTED_CONTENT in foyer
    assert any(line.endswith("Latest: " + PROTECTED_CONTENT) for line in foyer)


# ── The doors, designated ────────────────────────────────────────────────────


@STAKES
def test_protected_marker_ranks_but_renders_only_as_its_threshold(chronicle, break_stakes):
    _seed(chronicle, designate=True, break_stakes=break_stakes)
    state = _state(chronicle, "full")

    for text in (
        render_full(state),
        render_full(state, full_content=True),
        render_full(state, compact=True),
    ):
        _assert_threshold_only(text, chronicle)
        section = _markers(text)
        assert section[1] == f"  [2026-10-20] [personal] {NOTICE}"
        assert section[2].endswith(ORDINARY)
        activity = _activity(text)
        assert activity[1].startswith("  [2026-10-20T12:00:00] [personal]")
        assert activity[1].endswith(NOTICE)
        assert activity[2].endswith(ORDINARY)

    # The rank and the locator survive the withhold: still first, same stamp.
    top = state.sentinel_pool[0]
    assert top["content"] == NOTICE
    assert top["timestamp"] == PROTECTED_STAMP
    assert top["domain"] == "personal"
    assert state.sentinel_pool[1]["content"] == ORDINARY


@STAKES
def test_foyer_pins_and_reports_the_protected_record_as_its_threshold(chronicle, break_stakes):
    _seed(chronicle, designate=True, break_stakes=break_stakes)
    text = render_foyer(_state(chronicle, "foyer"))
    _assert_threshold_only(text, chronicle)
    lines = text.splitlines()
    assert [line for line in lines if line.startswith(_PINNED)] == [_PINNED + NOTICE]
    assert [line for line in lines if "Latest: " in line][0].endswith("Latest: " + NOTICE)


@STAKES
def test_related_insight_is_withheld_under_contextual_resonance(chronicle, break_stakes):
    _seed(chronicle, designate=True, break_stakes=break_stakes)
    state = _state(chronicle, "full", domain_tags=["personal"])
    for text in (render_full(state), render_full(state, full_content=True)):
        _assert_threshold_only(text, chronicle)
        related = _related(text)
        assert related[0] == "  Related insights (1):"
        assert related[1].endswith(NOTICE)


def test_withhold_covers_both_outcomes_and_passes_the_rest():
    coupled = {
        "timestamp": "2026-10-20T12:00:00+00:00",
        "domain": "personal",
        "content": "a coupled body",
        "intensity": 0.95,
        "vantage": "human_attestation",
        "_protected": True,
        "_stakes": "its stakes",
        "_stakes_verdict": "verified",
    }
    sentinel = ProtectedStakesUnavailable.from_entry(
        {"timestamp": "2026-10-19T00:00:00+00:00", "domain": "personal"}, "0" * 64, "missing"
    )
    plain = {"timestamp": "2026-10-18T00:00:00+00:00", "domain": "ops", "content": "plain"}

    out = arrival_state._withhold_protected([coupled, sentinel, plain])
    assert [e["content"] for e in out] == [NOTICE, NOTICE, "plain"]
    # The locators the rank, the counts and the aperture read survive.
    assert (out[0]["timestamp"], out[0]["vantage"], out[0]["intensity"]) == (
        "2026-10-20T12:00:00+00:00",
        "human_attestation",
        0.95,
    )
    assert out[1]["timestamp"] == "2026-10-19T00:00:00+00:00"
    assert out[2] is plain
    assert coupled["content"] == "a coupled body"  # the input is never mutated
    assert arrival_state._withhold_protected([]) == []
    assert arrival_state._withhold_protected(None) is None
