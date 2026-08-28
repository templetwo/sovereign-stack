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

    # One unread reflector marginalia.
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


def run_door(root: Path, name: str, arguments: dict) -> str:
    """Run one door via the real dispatcher against the fixture root."""
    from sovereign_stack.server import _dispatch_tool

    async def _run():
        with _patched_singletons(root):
            result = await _dispatch_tool(name, arguments)
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
