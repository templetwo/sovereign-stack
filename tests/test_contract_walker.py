"""
Contract-test walker — schema defaults vs handler behavior.

THE GAP THIS CLOSES: the recall_reflections schema promised
ack_status default='unread' while the handler did arguments.get("ack_status")
with no default, so omitting the argument returned EVERY reflection
(20c00ce regression). Nothing enforced schema/handler agreement at the
contract layer, so the mismatch shipped.

THE ENFORCEMENT: for every tool registered in sovereign_stack.server
(the list_tools schema list), and for every schema property carrying a
'default', this walker asserts that omitting the argument behaves
identically to passing the default explicitly. Both invocations run in
their own fully hermetic environment (fresh temp sovereign root, every
module-level singleton patched, Path.home() redirected) seeded with
identical fixture state, and the serialized tool result payloads are
compared after normalizing nondeterminism (timestamps, uuids, hashes,
temp paths, floats).

Tools that cannot run hermetically (network probes, the scribe spawn,
subprocess calls) live in the explicit SKIP dict below with a one-line
reason each — an honest skip list beats a flaky suite. An accounting
test asserts every (tool, param, default) triple is either walked or
skipped, so a new tool with defaults cannot silently dodge the walker.

The known-fixed case is also pinned explicitly: recall_reflections with
no args must equal ack_status='unread' and must EXCLUDE acked records
(the seeded reflections make the filter observable — see
TestRecallReflectionsDefaultPinned).

ISOLATION NOTE: extends the _isolated_server pattern from
tests/test_nape_autohook.py — nothing here touches ~/.sovereign.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import pytest

from sovereign_stack import comms as comms_module
from sovereign_stack import consciousness_tools as consciousness_tools_module
from sovereign_stack import metabolism as metabolism_module
from sovereign_stack import reflections as reflections_module
from sovereign_stack import server as srv
from sovereign_stack.coherence import AGENT_MEMORY_SCHEMA, Coherence
from sovereign_stack.consciousness import MetaCognition
from sovereign_stack.handoff import HandoffEngine
from sovereign_stack.memory import ExperientialMemory
from sovereign_stack.nape_daemon import NapeDaemon
from sovereign_stack.reflexive import PerTurnPriors, ReflexiveSurface
from sovereign_stack.spiral import SpiralState

SESSION_ID = "contract-walker-session"

# ---------------------------------------------------------------------------
# Skip list — tools whose handlers cannot run hermetically. One line each.
# Adding a tool here requires a reason; the accounting tests reject stale or
# reasonless entries. An honest skip list beats a flaky suite.
# ---------------------------------------------------------------------------

SKIP: dict[str, str] = {
    "where_did_i_leave_off": "spawns the per-boot Haiku scribe and consumes handoffs/lineage",
    "synthesize_now": "SynthesisDaemon.run() invokes a local LLM (network/subprocess)",
    "guardian_scan": "shells out to lsof; the live listener set varies between calls",
    "guardian_report": "shells out to lsof for live listener counts",
    "guardian_baseline": "gathers live ports/processes/network via subprocess",
    "connectivity_status": "urllib probes against live service endpoints",
    "stack_write_check": "live write-path probe across running services",
}


# ---------------------------------------------------------------------------
# Triple collection — read the registered schemas, not a parallel list.
# ---------------------------------------------------------------------------


def _collect_triples() -> list[tuple[str, str, object]]:
    """Every (tool_name, param_name, default) from the live tool registry."""
    tools = asyncio.run(srv.list_tools())
    triples: list[tuple[str, str, object]] = []
    for tool in tools:
        props = (tool.inputSchema or {}).get("properties", {})
        for pname in sorted(props):
            spec = props[pname]
            if isinstance(spec, dict) and "default" in spec:
                triples.append((tool.name, pname, spec["default"]))
    return triples


ALL_TRIPLES = _collect_triples()
RUNNABLE_TRIPLES = [t for t in ALL_TRIPLES if t[0] not in SKIP]
SKIPPED_TRIPLES = [t for t in ALL_TRIPLES if t[0] in SKIP]

CASES = [
    pytest.param(
        tool,
        param,
        default,
        id=f"{tool}.{param}",
        marks=[pytest.mark.skip(reason=SKIP[tool])] if tool in SKIP else [],
    )
    for tool, param, default in ALL_TRIPLES
]


# ---------------------------------------------------------------------------
# Minimal required arguments per tool (values independent of run state).
# Factories receive the env tmp_root for args that need a real path.
# ---------------------------------------------------------------------------


def _scan_target_args(tmp_root: Path) -> dict:
    target = tmp_root / "scan_target"
    target.mkdir(exist_ok=True)
    (target / "probe.txt").write_text("contract walker probe\n")
    return {"path": str(target)}


REQUIRED_ARGS: dict = {
    "route": lambda root: {"packet": {}},
    "scan_thresholds": _scan_target_args,
    "govern": lambda root: {"target": "contract-walker-target"},
    "compass_check": lambda root: {"action": "read the chronicle"},
    "record_insight": lambda root: {
        "domain": "contract-walker",
        "content": "default-equivalence probe",
    },
    "record_learning": lambda root: {
        "what_happened": "walker probed a default",
        "what_learned": "omission must equal explicit default",
    },
    "record_open_thread": lambda root: {"question": "does omitting a default equal passing it?"},
    "handoff": lambda root: {"note": "contract walker handoff probe"},
    "close_session": lambda root: {"what_i_learned": "contract walker close probe"},
    "comms_unread_bodies": lambda root: {"instance_id": "contract-walker"},
    "comms_acknowledge": lambda root: {
        "message_id": "msg-0001",
        "instance_id": "contract-walker",
    },
    "nape_observe": lambda root: {
        "tool_name": "probe_tool",
        "result": "ok",
        "session_id": SESSION_ID,
    },
    "reflexive_surface": lambda root: {"domain_tags": ["contract-walker"]},
    "agent_reflect": lambda root: {
        "observation": "walker probe observation",
        "pattern_type": "curiosity",
    },
    "resolve_uncertainty": lambda root: {
        "marker_id": "nonexistent-marker",
        "resolution": "walker probe resolution",
    },
    "context_retrieve": lambda root: {"current_focus": "contract walker default probe"},
    "watch_resample": lambda root: {"watch_id": "nonexistent-watch"},
    # decline_protected_record short-circuits on an empty claim_id before its
    # defaulted params (declined_by / note) are reached, so without a real
    # claim_id the walker would pass vacuously. Supply one so the default path
    # actually runs (decline_record never validates the claim_id shape and never
    # raises — it writes + returns, exercising the defaults).
    "decline_protected_record": lambda root: {"claim_id": "contract-walker-claim"},
}


def _args_for(tool: str, tmp_root: Path) -> dict:
    factory = REQUIRED_ARGS.get(tool)
    return dict(factory(tmp_root)) if factory else {}


# ---------------------------------------------------------------------------
# Hermetic environment — every singleton the dispatcher reaches is patched
# to a fresh temp root, then seeded with identical fixture state.
# ---------------------------------------------------------------------------


def _seed(tmp_root: Path, experiential: ExperientialMemory) -> None:
    """Identical fixture state for every run, so defaults are observable.

    Without seeded state most filters are invisible (everything returns
    empty either way). The reflections seed in particular is what makes
    the recall_reflections ack_status default falsifiable: 2 unread + 1
    confirmed means 'no filter applied' returns a different payload than
    'unread'. The insight/thread counts sit past the common limit
    defaults (recall_insights=10, context_retrieve=5, get_open_threads=10,
    triage_threads=15) so a handler falling back to a wrong limit constant
    is observable too.
    """
    for i in range(12):
        experiential.record_insight(
            "contract-walker",
            f"seed insight {i:02d}: walker fixture",
            0.6,
            SESSION_ID,
            layer="hypothesis",
        )
    for i in range(16):
        experiential.record_open_thread(
            f"seed question {i:02d}: do defaults hold?",
            "seed context",
            "contract-walker",
            SESSION_ID,
        )

    def _refl(rid: str, obs: str, ack_status: str) -> dict:
        rec = {
            "id": rid,
            "timestamp": "2026-06-01T01:00:00+00:00",
            "model": "seed-model",
            "prompt_version": "v1",
            "run_id": "seed-run",
            "observation": obs,
            "entries_referenced": [],
            "connection_type": "other",
            "confidence": "low",
            "ack_status": ack_status,
        }
        if ack_status != "unread":
            rec["ack_note"] = "seed ack"
            rec["ack_timestamp"] = "2026-06-02T01:00:00+00:00"
            rec["ack_by"] = "seeder"
        return rec

    reflections = [
        _refl("refl-0001", "seed unread observation one", "unread"),
        _refl("refl-0002", "seed unread observation two", "unread"),
        _refl("refl-0003", "seed confirmed observation", "confirm"),
    ]
    (tmp_root / "reflections" / "2026-06-01.jsonl").write_text(
        "\n".join(json.dumps(r) for r in reflections) + "\n"
    )

    seed_message = {
        "id": "msg-0001",
        "from": "seeder",
        "instance_id": "seeder",
        "body": "seed message body",
        "ts": 1750000000.0,
        "timestamp": "2026-06-01T00:00:00+00:00",
        "read_by": [],
    }
    (tmp_root / "comms" / "general.jsonl").write_text(json.dumps(seed_message) + "\n")


@contextmanager
def _hermetic_env():
    """
    Extends the _isolated_server pattern from tests/test_nape_autohook.py:
    fresh temp sovereign root; every module-level singleton the dispatcher
    can reach is swapped for one rooted there; Path.home() and the
    SOVEREIGN_ROOT / GUARDIAN_ROOT env vars are redirected so call-time
    lookups land in the sandbox too. NOTHING touches ~/.sovereign.

    Yields tmp_root.
    """
    tmp_root = Path(tempfile.mkdtemp(prefix="contract-walker-"))
    fake_home = tmp_root / "home"
    (fake_home / ".sovereign").mkdir(parents=True)
    for sub in ("chronicle", "comms", "reflections", "guardian", "memory"):
        (tmp_root / sub).mkdir()

    tmp_experiential = ExperientialMemory(root=str(tmp_root / "chronicle"))
    tmp_surface = ReflexiveSurface(sovereign_root=tmp_root)

    saved = {
        "experiential": srv.experiential,
        "SPIRAL_STATE_PATH": srv.SPIRAL_STATE_PATH,
        "DEFAULT_ROOT": srv.DEFAULT_ROOT,
        "spiral_state": srv.spiral_state,
        "nape_daemon": srv.nape_daemon,
        "handoff_engine": srv.handoff_engine,
        "reflexive_surface": srv.reflexive_surface,
        "per_turn_priors": srv.per_turn_priors,
        "coherence": srv.coherence,
        "comms.COMMS_DIR": comms_module.COMMS_DIR,
        "reflections.REFLECTIONS_DIR": reflections_module.REFLECTIONS_DIR,
        "consciousness.meta": consciousness_tools_module.meta,
        "metabolism.SOVEREIGN_ROOT": metabolism_module.SOVEREIGN_ROOT,
        "metabolism.CHRONICLE_DIR": metabolism_module.CHRONICLE_DIR,
        "metabolism.METABOLISM_LOG": metabolism_module.METABOLISM_LOG,
    }
    saved_env = {k: os.environ.get(k) for k in ("SOVEREIGN_ROOT", "GUARDIAN_ROOT")}

    srv.experiential = tmp_experiential
    srv.SPIRAL_STATE_PATH = tmp_root / "spiral_state.json"
    srv.DEFAULT_ROOT = str(tmp_root)
    srv.spiral_state = SpiralState(session_id=SESSION_ID)
    srv.nape_daemon = NapeDaemon(root=str(tmp_root))
    srv.handoff_engine = HandoffEngine(root=str(tmp_root))
    srv.reflexive_surface = tmp_surface
    srv.per_turn_priors = PerTurnPriors(
        surface=tmp_surface,
        sovereign_root=tmp_root,
        uncertainty_fn=list,
        honks_fn=list,
    )
    srv.coherence = Coherence(AGENT_MEMORY_SCHEMA, root=str(tmp_root / "memory"))
    comms_module.COMMS_DIR = tmp_root / "comms"
    reflections_module.REFLECTIONS_DIR = tmp_root / "reflections"
    consciousness_tools_module.meta = MetaCognition(str(tmp_root / "consciousness"))
    metabolism_module.SOVEREIGN_ROOT = tmp_root
    metabolism_module.CHRONICLE_DIR = tmp_root / "chronicle"
    metabolism_module.METABOLISM_LOG = tmp_root / "metabolism_log.jsonl"
    os.environ["SOVEREIGN_ROOT"] = str(tmp_root)
    os.environ["GUARDIAN_ROOT"] = str(tmp_root / "guardian")

    home_patcher = mock.patch.object(Path, "home", classmethod(lambda cls: fake_home))
    home_patcher.start()

    try:
        _seed(tmp_root, tmp_experiential)
        yield tmp_root
    finally:
        home_patcher.stop()
        srv.experiential = saved["experiential"]
        srv.SPIRAL_STATE_PATH = saved["SPIRAL_STATE_PATH"]
        srv.DEFAULT_ROOT = saved["DEFAULT_ROOT"]
        srv.spiral_state = saved["spiral_state"]
        srv.nape_daemon = saved["nape_daemon"]
        srv.handoff_engine = saved["handoff_engine"]
        srv.reflexive_surface = saved["reflexive_surface"]
        srv.per_turn_priors = saved["per_turn_priors"]
        srv.coherence = saved["coherence"]
        comms_module.COMMS_DIR = saved["comms.COMMS_DIR"]
        reflections_module.REFLECTIONS_DIR = saved["reflections.REFLECTIONS_DIR"]
        consciousness_tools_module.meta = saved["consciousness.meta"]
        metabolism_module.SOVEREIGN_ROOT = saved["metabolism.SOVEREIGN_ROOT"]
        metabolism_module.CHRONICLE_DIR = saved["metabolism.CHRONICLE_DIR"]
        metabolism_module.METABOLISM_LOG = saved["metabolism.METABOLISM_LOG"]
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(tmp_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Payload serialization + nondeterminism normalization
# ---------------------------------------------------------------------------

_NORMALIZERS: list[tuple[re.Pattern, str]] = [
    # ISO-8601 timestamps (with optional fraction / offset) before bare dates.
    (
        re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"),
        "<TS>",
    ),
    # Compact stamps like 20260612_141500 (spiral session ids, handoff file
    # names with a trailing microseconds fragment, delib-...-HHMMSS ids).
    (re.compile(r"\d{8}[T_-]\d{6}(?:_\d{1,6})?"), "<TS_COMPACT>"),
    # Short hex entropy glued to a compact stamp (e.g. delib session ids).
    (re.compile(r"(?<=<TS_COMPACT>)[-_][0-9a-f]{6,10}\b"), "-<HEX>"),
    (
        re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"),
        "<UUID>",
    ),
    (re.compile(r"\b[0-9a-f]{12,64}\b"), "<HEX>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}"), "<DATE>"),
    # Floats last — wall-clock-derived scores/ages drift between the two runs.
    (re.compile(r"-?\d+\.\d+(?:[eE][+-]?\d+)?"), "<F>"),
]


def _serialize(result) -> str:
    parts = []
    for item in result or []:
        parts.append(
            json.dumps(
                {
                    "type": getattr(item, "type", None),
                    "text": getattr(item, "text", repr(item)),
                },
                sort_keys=True,
            )
        )
    return "\n".join(parts)


def _normalize(payload: str, tmp_root: Path) -> str:
    payload = payload.replace(str(tmp_root), "<ROOT>")
    for pattern, replacement in _NORMALIZERS:
        payload = pattern.sub(replacement, payload)
    return payload


def _run_case(tool: str, extra_args: dict | None = None) -> str:
    """Dispatch `tool` in a fresh hermetic env; return the normalized payload.

    Exceptions are part of the contract too: if omitting an argument raises
    where the explicit default does not (or vice versa, or a different
    error), the payloads differ and the walker fails.
    """
    with _hermetic_env() as tmp_root:
        args = _args_for(tool, tmp_root)
        if extra_args:
            args.update(extra_args)
        try:
            result = asyncio.run(srv._dispatch_tool(tool, args))
            payload = _serialize(result)
        except Exception as exc:  # noqa: BLE001 — error equality IS the contract check
            payload = f"EXCEPTION {type(exc).__name__}: {exc}"
        return _normalize(payload, tmp_root)


# ---------------------------------------------------------------------------
# THE WALKER — one parameterized case per (tool, param, default) triple
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("tool", "param", "default"), CASES)
def test_omitting_default_equals_passing_default(tool, param, default):
    """Omitting a schema-defaulted argument must equal passing it explicitly.

    This is the contract-layer 'unable to lie' enforcement: the schema is a
    promise about handler behavior, and the next time a handler and its
    schema disagree (the 20c00ce class of bug) this case fails loudly.
    """
    omitted = _run_case(tool)
    explicit = _run_case(tool, {param: default})
    assert omitted == explicit, (
        f"Schema/handler default mismatch on {tool}.{param}: the schema promises "
        f"default={default!r}, but omitting the argument produced a different result "
        f"than passing it explicitly.\n"
        f"--- omitted ---\n{omitted[:1500]}\n"
        f"--- explicit default ---\n{explicit[:1500]}"
    )


# ---------------------------------------------------------------------------
# Accounting — every triple is walked or honestly skipped; no stale skips.
# ---------------------------------------------------------------------------


class TestWalkerAccounting:
    def test_every_default_triple_is_walked_or_skipped(self):
        walked = {(t, p) for t, p, _ in RUNNABLE_TRIPLES}
        skipped = {(t, p) for t, p, _ in SKIPPED_TRIPLES}
        everything = {(t, p) for t, p, _ in ALL_TRIPLES}
        assert walked | skipped == everything
        assert not (walked & skipped)
        assert len(ALL_TRIPLES) > 0, "schema list yielded no defaulted params — walker is blind"

    def test_skip_list_entries_are_live_tools_with_defaults(self):
        """A skip entry for a tool that no longer exists (or no longer has
        defaults) is stale and must be removed."""
        tools_with_defaults = {t for t, _, _ in ALL_TRIPLES}
        for tool_name in SKIP:
            assert tool_name in tools_with_defaults, (
                f"Stale SKIP entry: '{tool_name}' is not a registered tool with "
                f"schema defaults. Remove it or fix the name."
            )

    def test_skip_reasons_are_nonempty(self):
        for tool_name, reason in SKIP.items():
            assert reason.strip(), f"SKIP['{tool_name}'] needs a one-line reason"

    def test_walked_vs_skipped_counts_are_visible(self):
        """Pin the shape of the walk so a silent coverage collapse is loud.

        If the registry grows, these numbers move — update them consciously
        alongside the new tool, the same way the skip list is curated.
        """
        assert len(ALL_TRIPLES) == len(RUNNABLE_TRIPLES) + len(SKIPPED_TRIPLES)
        assert len(RUNNABLE_TRIPLES) >= 50, (
            f"Only {len(RUNNABLE_TRIPLES)} (tool,param) cases run — the walker "
            f"lost coverage (expected at least 50)."
        )
        assert len(SKIPPED_TRIPLES) <= 15, (
            f"{len(SKIPPED_TRIPLES)} cases skipped — the skip list is growing; "
            f"check whether new entries truly cannot run hermetically."
        )


# ---------------------------------------------------------------------------
# Pinned regression: recall_reflections default ack_status='unread' (20c00ce)
# ---------------------------------------------------------------------------


class TestRecallReflectionsDefaultPinned:
    """The known-fixed case, pinned explicitly and falsifiably.

    The seed writes 2 unread + 1 confirmed reflection, so 'no filter
    applied' (the 20c00ce behavior: count=3) is observably different from
    the schema's promised default 'unread' (count=2).
    """

    def test_no_args_equals_explicit_unread(self):
        omitted = _run_case("recall_reflections")
        explicit = _run_case("recall_reflections", {"ack_status": "unread"})
        assert omitted == explicit, (
            "recall_reflections with no args must behave exactly like "
            "ack_status='unread' (the schema default). This is the 20c00ce regression."
        )

    def test_no_args_returns_only_unread(self):
        with _hermetic_env() as _tmp_root:
            result = asyncio.run(srv._dispatch_tool("recall_reflections", {}))
        data = json.loads(result[0].text)
        assert data["count"] == 2, (
            f"Expected the 2 seeded unread reflections, got count={data['count']} — "
            f"the ack_status='unread' default is not being applied (20c00ce regression)."
        )
        assert all(r["ack_status"] == "unread" for r in data["reflections"])

    def test_ack_status_all_returns_everything(self):
        """Proves the seed makes the filter observable: 'all' must differ
        from the default, so the walker case above cannot pass vacuously."""
        with _hermetic_env() as _tmp_root:
            result = asyncio.run(srv._dispatch_tool("recall_reflections", {"ack_status": "all"}))
        data = json.loads(result[0].text)
        assert data["count"] == 3
        assert _run_case("recall_reflections", {"ack_status": "all"}) != _run_case(
            "recall_reflections"
        )
