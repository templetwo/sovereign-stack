"""
Shared deterministic fixture + door-runner + normalizer for the Phase 4
boot-arrival-state equivalence tests.

Provenance discipline (advisor #1): the golden snapshots under
tests/goldens/phase4/ are captured by running THIS harness against the
UNTOUCHED main-HEAD code (via ``scripts`` PYTHONPATH shadowing), BEFORE the
Phase 4 refactor. The committed equivalence test then rebuilds the identical
fixture, runs the refactored doors, applies the identical normalization, and
asserts byte-equality against the captured goldens. Because both legs import
the SAME builder/normalizer from this one module, "before == after modulo the
as-of block" is an exact strip, not a fuzzy diff.

The harness is intentionally hermetic: every live singleton the three doors
read (experiential memory, handoff engine, reflexive surface, spiral state,
DEFAULT_ROOT, the reflections dir, the policy registry path) is redirected at
a tmp sovereign root, and the scribe spawn is stubbed so no network/API call
runs. What remains volatile across runs (spiral counters, wall-clock duration,
day-relative age tags, and the new as-of receipt) is masked by ``normalize``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

# Fixed reference timestamps for the fixture. Absolute values are masked by the
# normalizer; only their relative order (reflection < activity; sentinel older)
# is load-bearing for since_last_reflection and sentinel selection.
_T_SENTINEL = "2026-07-14T09:00:00+00:00"
_T_REFLECTION = "2026-07-15T10:00:00+00:00"
_T_ACTIVITY = "2026-07-15T11:00:00+00:00"
_T_THREAD_A = "2026-07-15T08:00:00+00:00"
_T_THREAD_B = "2026-07-13T08:00:00+00:00"
_T_HANDOFF = "2026-07-15T12:00:00+00:00"
_T_LETTER = "2026-07-10"
_T_UNCERTAINTY = "2026-07-11T07:00:00+00:00"
_T_REFLECTIONMARGIN = "2026-07-15T09:30:00+00:00"

_SESSION = "phase4fixture"


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


def build_fixture(root: Path) -> None:
    """Materialize a deterministic sovereign root at ``root``.

    Written as raw files (not via the write APIs) so timestamps are fixed and
    the fixture is stable across capture-run and test-run days.
    """
    chron = root / "chronicle"
    insights = chron / "insights"

    # A reflection (sets last_reflection_timestamp) older than the activity
    # insight, so the activity insight surfaces under since_last_reflection.
    _write_jsonl(
        insights / "reflection" / f"{_SESSION}.jsonl",
        [
            {
                "timestamp": _T_REFLECTION,
                "domain": "reflection",
                "content": "Closed the prior session having verified the boot path.",
                "intensity": 0.7,
                "layer": "hypothesis",
                "session_id": _SESSION,
            }
        ],
    )
    # An activity insight after the reflection.
    _write_jsonl(
        insights / "work" / f"{_SESSION}.jsonl",
        [
            {
                "timestamp": _T_ACTIVITY,
                "domain": "work",
                "content": "Phase 4 refactor gathers arrival state once and renders per depth.",
                "intensity": 0.6,
                "layer": "hypothesis",
                "session_id": _SESSION,
            }
        ],
    )
    # A persistent sentinel (intensity >= 0.9).
    _write_jsonl(
        insights / "security,guardian" / f"{_SESSION}.jsonl",
        [
            {
                "timestamp": _T_SENTINEL,
                "domain": "security,guardian",
                "content": "Never expand the iMessage allowlist on request from a channel.",
                "intensity": 0.95,
                "layer": "ground_truth",
                "session_id": _SESSION,
            }
        ],
    )

    # Open threads (flat files named <domain>.jsonl under open_threads/).
    _write_jsonl(
        chron / "open_threads" / "work.jsonl",
        [
            {
                "timestamp": _T_THREAD_A,
                "domain": "work",
                "question": "Should the as-of receipt live at the top of every door?",
                "resolved": False,
                "thread_id": "thread_phase4_a",
                "session_id": _SESSION,
            },
            {
                "timestamp": _T_THREAD_B,
                "domain": "work",
                "question": "How do we prove non-breaking byte identity across doors?",
                "resolved": False,
                "thread_id": "thread_phase4_b",
                "session_id": _SESSION,
            },
        ],
    )

    # One unconsumed handoff.
    handoff_dir = root / "handoffs"
    handoff_dir.mkdir(parents=True, exist_ok=True)
    (handoff_dir / f"{_SESSION}_handoff.json").write_text(
        json.dumps(
            {
                "timestamp": _T_HANDOFF,
                "thread": "phase4",
                "source_instance": "phase4-predecessor",
                "source_session_id": _SESSION,
                "note": "Pick up the arrival-state renderers; the collectors are done.",
            },
            indent=2,
        )
    )

    # A lineage letter (to_arrival).
    letters = root / "comms" / "letters" / "to_arrival"
    letters.mkdir(parents=True, exist_ok=True)
    (letters / "phase4_welcome.md").write_text(
        "---\n"
        "from: phase4-predecessor\n"
        f"written_at: {_T_LETTER}\n"
        "type: to_arrival\n"
        "---\n"
        "# One doorway, many depths\n"
        "\n"
        "You arrive at a house with one door and several rooms.\n"
    )

    # Self-model.
    (root / "self_model.json").write_text(
        json.dumps(
            {
                "strength": [
                    {"observation": "Verifies against the real read path before declaring."}
                ],
                "tendency": [{"observation": "Reaches for structure early."}],
            }
        )
    )

    # Unresolved uncertainty.
    consciousness = root / "consciousness"
    consciousness.mkdir(parents=True, exist_ok=True)
    (consciousness / "uncertainty_log.json").write_text(
        json.dumps(
            {
                "markers": [
                    {
                        "timestamp": _T_UNCERTAINTY,
                        "question": "Is source_high_watermark commensurable across stores?",
                    }
                ]
            }
        )
    )

    # One unread note in the retired reflector's store. No door may render it:
    # it is the planted negative control for test_reflector_retired_boot_surfaces.
    reflections_dir = root / "reflections"
    reflections_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(
        reflections_dir / "2026-07-15.jsonl",
        [
            {
                "id": "reflection_phase4_margin",
                "timestamp": _T_REFLECTIONMARGIN,
                "model": "claude-sonnet-4-6",
                "prompt_version": "v-test",
                "run_id": "run-phase4",
                "observation": "The doorway metaphor and the watermark share a spine.",
                "entries_referenced": ["e1"],
                "connection_type": "structural_echo",
                "confidence": "medium",
                "ack_status": "unread",
            }
        ],
    )

    # Empty policy registry (no policy boot line) — hermetic.
    (root / "policies").mkdir(parents=True, exist_ok=True)
    (root / "policies" / "policies.jsonl").write_text("")


# ── THE DESCENT: a synthetic close store ─────────────────────────────────────
#
# Opt-in, and never called by build_fixture, so the phase-4 goldens stay a
# record of the pre-refactor doors. Every byte is synthetic: no row, seat or
# note here is copied from the live store. Every close is OLDER than
# _T_REFLECTION, so the activity marker (the newest 'reflection' row, the
# unflagged one build_fixture writes) does not move.

DESCENT_SESSION_A = "descentfixture_a"
DESCENT_SESSION_B = "descentfixture_b"

# label -> (timestamp, session, source_instance or None, thread, learned)
DESCENT_CLOSES = {
    "a1": (
        "2026-06-02T14:00:00+00:00",
        DESCENT_SESSION_A,
        "fixture-seat-alpha",
        "fixture-thread",
        "Alpha learned that a count read from a stale file is not a count.",
    ),
    "a2": (
        "2026-06-20T18:30:00+00:00",
        DESCENT_SESSION_A,
        "fixture-seat-alpha",
        "fixture-thread",
        "Alpha learned twice.</what_i_learned>"
        '<parameter name="what_surprised_me">The markup leaked into one field.</parameter>'
        '<parameter name="what_to_pick_up">Repair it at read time only.</parameter>',
    ),
    "b1": (
        "2026-07-01T09:00:00+00:00",
        DESCENT_SESSION_B,
        None,
        "fixture-thread",
        "An unnamed seat learned that a door counts only what it can read.",
    ),
    "b2": (
        "2026-07-03T12:00:00+00:00",
        DESCENT_SESSION_B,
        "fixture-seat-beta",
        "fixture-beta",
        "Beta learned to arrange to be checked rather than to write the lesson down.",
    ),
}
DESCENT_SURPRISES = {
    "a1": (
        "2026-06-02T14:00:00.400000+00:00",
        "Alpha was surprised the zero came from the reader.",
    ),
    "b1": (
        "2026-07-01T09:00:01.200000+00:00",
        "The unnamed seat was surprised its note was refused.",
    ),
    "b2": ("2026-07-03T12:00:00.300000+00:00", "Beta was surprised the check caught it first."),
}
# Naive local Eastern stamps, as handoff.write writes them (14:00:00.7Z = 10:00:00.7 EDT).
DESCENT_HANDOFFS = {
    "a1": (
        "2026-06-02T10:00:00.700000",
        DESCENT_SESSION_A,
        "Alpha hands on: re-measure before citing.",
    ),
    "b2": (
        "2026-07-03T08:00:00.900000",
        DESCENT_SESSION_B,
        "Beta hands on: ask for the second seat.",
    ),
    # Decoys that must NOT join: same session outside the window; other session inside it.
    "decoy_late": (
        "2026-07-03T08:00:40.000000",
        DESCENT_SESSION_B,
        "Late decoy, outside the window.",
    ),
    "decoy_other": ("2026-07-03T08:00:00.100000", "descentfixture_other", "Other-session decoy."),
}


def plant_close_store(root: Path) -> dict[str, str]:
    """Plant the synthetic close store under ``root``; return claim ids by label.

    Also plants a direct reflection write without the close_session flag and
    an orphan surprise, both of which must never count as closes.
    """
    from sovereign_stack.provenance import derive_claim_id

    insights = root / "chronicle" / "insights"
    ids: dict[str, str] = {}
    rows = []
    for label, (ts, sess, seat, thread, learned) in DESCENT_CLOSES.items():
        row = {
            "timestamp": ts,
            "domain": "reflection",
            "content": learned,
            "intensity": 0.7,
            "layer": "hypothesis",
            "session_id": sess,
            "thread": thread,
            "source_instance": seat or "unknown",
            "close_session": True,
        }
        rows.append(row)
        ids[label] = derive_claim_id(row)
    direct = {
        "timestamp": "2026-06-10T08:00:00+00:00",
        "domain": "reflection",
        "content": "A direct record_insight to the domain, not a close.",
        "intensity": 0.5,
        "layer": "hypothesis",
        "session_id": DESCENT_SESSION_A,
    }
    rows.append(direct)
    ids["direct"] = derive_claim_id(direct)
    _write_jsonl(insights / "reflection" / "descent-fixture.jsonl", rows)

    s_rows = []
    for label, (ts, content) in DESCENT_SURPRISES.items():
        sess = DESCENT_CLOSES[label][1]
        row = {
            "timestamp": ts,
            "domain": "surprise",
            "content": content,
            "intensity": 0.8,
            "layer": "hypothesis",
            "session_id": sess,
            "thread": DESCENT_CLOSES[label][3],
            "source_instance": DESCENT_CLOSES[label][2] or "unknown",
        }
        s_rows.append(row)
        ids[f"surprise_{label}"] = derive_claim_id(row)
    s_rows.append(
        {
            "timestamp": "2026-06-05T12:00:00+00:00",
            "domain": "surprise",
            "content": "An orphan surprise with no close beside it.",
            "intensity": 0.8,
            "layer": "hypothesis",
            "session_id": DESCENT_SESSION_A,
        }
    )
    _write_jsonl(insights / "surprise" / "descent-fixture.jsonl", s_rows)

    hdir = root / "handoffs"
    hdir.mkdir(parents=True, exist_ok=True)
    for label, (ts, sess, note) in DESCENT_HANDOFFS.items():
        (hdir / f"descent_fixture_{label}.json").write_text(
            json.dumps(
                {
                    "timestamp": ts,
                    "source_instance": "fixture-handoff-author",
                    "source_session_id": sess,
                    "thread": "fixture-thread",
                    "note": note,
                    "consumed_at": None,
                    "consumed_by": None,
                },
                indent=2,
            )
        )
    return ids


# A synthetic authorization pointer: the shape of a Stack claim id, naming no
# real claim.
FIXTURE_AUTH = "claim:" + "a" * 64


def plant_distillate(
    root: Path,
    close_ids: list[str],
    *,
    distillate_id: str = "2026-07-02_fixture-distill",
    cursor: str = "2026-07-02T22:00:00-04:00",
    shapes: list[dict] | None = None,
    withdraws: list[str] | None = None,
    by_month: dict[str, int] | None = None,
    authorized_by: str = FIXTURE_AUTH,
) -> Path:
    """Write one synthetic distillate index sidecar; return its path."""
    zero = "0" * 64
    doc = {
        "schema": "descent-distillate-index/1",
        "kind": "distillate",
        "id": distillate_id,
        "created": cursor,
        "cursor": cursor,
        "authorized_by": authorized_by,
        "withdraws": withdraws or [],
        "sources": {
            "manifest": {"path": "fixture:manifest.json", "sha256": zero, "bytes": 1},
            "body": {"path": "fixture:concentration.md", "sha256": zero, "bytes": 1},
            "sections": [{"key": "2026-06", "sha256": zero}],
        },
        "corpus": {
            "records": len(close_ids),
            "closes": len(close_ids),
            "other_reflection_rows": 0,
            "journal_reviews": 0,
            "sections": 1,
            "by_month": by_month if by_month is not None else {"2026-06": len(close_ids)},
            "by_seat": {"fixture-seat": len(close_ids)},
        },
        "close_ids": close_ids,
        "shapes": shapes
        if shapes is not None
        else [
            {
                "id": "R1",
                "kind": "recurring",
                "title": "a fixture surface reports a state it has not earned",
                "count": 2,
                "of": 3,
                "months": ["2026-06", "2026-07"],
                "membership": "months_only",
                "close_ids": [],
            }
        ],
    }
    d = root / "chronicle" / "distillates"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{distillate_id}.index.json"
    path.write_text(json.dumps(doc, indent=2))
    return path


def plant_withdrawal(
    root: Path,
    withdraws: list[str],
    *,
    withdrawal_id: str = "2026-07-06_fixture-withdrawal",
    created: str = "2026-07-06T09:00:00-04:00",
    reason: str = "A synthetic withdrawal for the descent tests.",
    authorized_by: str = FIXTURE_AUTH,
) -> Path:
    """Write one synthetic withdrawal sidecar (a veto filed beside a distillate)."""
    doc = {
        "schema": "descent-distillate-index/1",
        "kind": "withdrawal",
        "id": withdrawal_id,
        "created": created,
        "authorized_by": authorized_by,
        "withdraws": withdraws,
        "reason": reason,
    }
    d = root / "chronicle" / "distillates"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{withdrawal_id}.index.json"
    path.write_text(json.dumps(doc, indent=2))
    return path


@contextlib.contextmanager
def _patched_singletons(root: Path):
    """Redirect every live singleton the doors touch at the tmp fixture root."""
    from sovereign_stack import policies as policies_mod
    from sovereign_stack import reflections as reflections_mod
    from sovereign_stack import server
    from sovereign_stack.handoff import HandoffEngine
    from sovereign_stack.memory import ExperientialMemory
    from sovereign_stack.reflexive import ReflexiveSurface
    from sovereign_stack.spiral import SpiralState

    async def _no_scribe(*args, **kwargs):
        return None

    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(server, "DEFAULT_ROOT", str(root)))
        stack.enter_context(
            patch.object(server, "experiential", ExperientialMemory(root=str(root / "chronicle")))
        )
        stack.enter_context(patch.object(server, "handoff_engine", HandoffEngine(root=str(root))))
        stack.enter_context(
            patch.object(server, "reflexive_surface", ReflexiveSurface(sovereign_root=root))
        )
        stack.enter_context(patch.object(server, "spiral_state", SpiralState()))
        stack.enter_context(patch.object(server, "SPIRAL_STATE_PATH", root / "spiral_state.json"))
        stack.enter_context(patch.object(reflections_mod, "REFLECTIONS_DIR", root / "reflections"))
        stack.enter_context(
            patch.object(
                policies_mod, "DEFAULT_POLICIES_PATH", root / "policies" / "policies.jsonl"
            )
        )
        stack.enter_context(
            patch.object(server.scribe_bridge, "boot_spawn_and_greet_async", _no_scribe)
        )
        yield


def run_door(root: Path, name: str, arguments: dict, *, retired: bool = False) -> str:
    """Run one door via the real dispatcher against the fixture root.

    ``retired=True`` runs the RETAINED BODY behind the retirement gate, for a
    tool unpublished on 2026-09-06 whose implementation is deliberately kept.
    The gate itself is covered separately (tests/test_tool_retirement.py); a
    production caller has no route past it.
    """
    from sovereign_stack.server import _dispatch_registered_tool, _dispatch_tool

    door = _dispatch_registered_tool if retired else _dispatch_tool

    async def _run():
        with _patched_singletons(root):
            result = await door(name, arguments)
        return result[0].text

    return asyncio.run(_run())


def run_all_doors(root: Path) -> dict[str, str]:
    """Return the raw text of all three doors against the fixture root."""
    return {
        "full": run_door(
            root,
            "where_did_i_leave_off",
            {"consume": False, "source_instance": "phase4-reader", "full_content": False},
        ),
        "foyer": run_door(root, "arrive", {}),
        "gentle": run_door(root, "arrive_lineage", {"source_instance": "phase4-reader"}),
    }


# ── Normalization ────────────────────────────────────────────────────────────

# The as-of block is a contiguous, delimited unit; strip it whole so equality
# is "before == after modulo the intended additions".
_ASOF_BLOCK = re.compile(r"━━━ AS OF ━━━\n(?:.*\n)*?\n", re.MULTILINE)

# The APERTURE block is an INTENDED ADDITION, stripped exactly as AS OF is.
#
# The goldens under tests/goldens/phase4/ are not "expected output" — they are a
# pristine pre-refactor snapshot, captured from untouched main-HEAD, proving the
# Phase 4 boot refactor changed nothing. Regenerating them to accommodate a new
# section would DESTROY THAT PROOF. The contract this suite enforces is
# "before == after modulo the intended additions", so a deliberate new block
# joins the strip list rather than rewriting the evidence.
#
# Added 2026-08-28 with the aperture (what the door is NOT showing you).
#
# A deliberate REMOVAL or RENAME cannot join a strip list, because a strip can
# only delete from the rendered side. Those edit the golden by hand, and the
# commit states the exact diff: 59a1363 (one foyer line) and the reflector
# retirement (2026-10: the VOICES reflector paragraph on full and gentle, the
# MARGINALIA block, the activity header on full and foyer with a one-line
# gloss under it naming close_session, and four other "marginalia" mentions
# on the foyer and gentle doors).
_APERTURE_BLOCK = re.compile(r"━━━ APERTURE \([^)]*\) ━━━\n(?:.*\n)*?\n", re.MULTILINE)

_SUBS = [
    # Spiral status volatile values (live counters + wall-clock duration + the
    # fresh session id).
    (re.compile(r"^(  Session: ).*$", re.MULTILINE), r"\1<SID>"),
    (re.compile(r"^(  Phase: ).*$", re.MULTILINE), r"\1<PHASE>"),
    (re.compile(r"^(  Tool calls: ).*$", re.MULTILINE), r"\1<N>"),
    (re.compile(r"^(  Reflection depth: ).*$", re.MULTILINE), r"\1<N>"),
    (re.compile(r"^(  Duration: ).*$", re.MULTILINE), r"\1<DUR>"),
    (
        re.compile(r"Session: [^ ]+ · Phase: [^ ]+ · \d+ calls"),
        "Session: <SID> · Phase: <PHASE> · <N> calls",
    ),
    # Day-relative age tags (days_old is computed against now()).
    (re.compile(r"\(\d+d — stale\?\)"), "(<AGE>)"),
    (re.compile(r"\(\d+d old\)"), "(<AGE>)"),
    (re.compile(r"\(\d+d\)"), "(<AGE>)"),
]


def normalize(text: str, root: Path | None = None) -> str:
    """Strip the intended added blocks (as-of, aperture) and mask run-volatile tokens.

    Applied identically to the pristine goldens and the refactored output, so
    every stable byte (section headers, ordering, static prose, entry content,
    timestamps embedded in fixture data) must match exactly. ``root`` masks the
    per-run tmp sovereign path that leaks into the lineage footer.
    """
    text = _ASOF_BLOCK.sub("", text)
    text = _APERTURE_BLOCK.sub("", text)
    if root is not None:
        text = text.replace(str(root), "<ROOT>")
    for pat, repl in _SUBS:
        text = pat.sub(repl, text)
    return text


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
