"""Unknown arguments are refused on write tools and named on every other tool.

Design D7, thread 0804046c. Every dispatch branch reads its arguments with
``arguments.get(...)``, so a key the tool does not declare used to vanish: the
call ran and answered as if the key had never been sent. `_reject_unknown_params`
closed that for two tools (record_insight, recall_insights); about fifty others
still dropped silently. The guard now runs once, at dispatch entry, keyed on the
tool's intent in ``TOOL_INTENTS`` and on four tools by name:

  * write, govern and handoff tools REFUSE: ``ValueError("unknown argument(s):
    <names>; accepted: <names>")`` before the body runs, so nothing is written and
    the spiral does not move. The SDK marks that ``isError=True``, which the
    bridge reports as ``{"ok": false, "error": <that text>}``. So do the four
    state-changing tools whose intent does not say so, named in the server's
    ``_REFUSE_UNKNOWN_ARGUMENT_TOOLS`` (thread_touch, self_model, nape_ack,
    spiral_reflect; plan 1's final review, finding I-2, option (b)).
  * every other published tool RUNS without the unknown keys and names them in its
    envelope: ``warnings: ["unknown argument(s) ignored: ..."]``, as a first key of
    a JSON object or a first line of text, inside ``content[0]`` because that is the
    only item the bridge reads.

Review Focus 5 is pinned here too, on both Ring-2 drains (bridge_core's and the
OpenAI fork's), which forward a proposal's arguments verbatim: for every commit
target, an unknown key in a proposal ends in ``commit_failed`` with the refusal
recorded, never in ``committed``. For all seven the refusal is the Stack's own,
returned through the POST: five refuse by intent, and ``thread_touch`` and
``self_model`` by name since the final fix wave. Before that the intent keying left
those two on the warn path, so each drain screened their arguments itself BEFORE
the POST (``ARGUMENT_SCREEN_TARGETS``). That screen now holds no target, by the
test that keeps it equal to the commit targets the Stack only warns on; its
mechanism stays, and is driven here with a target armed for the test. Those tests
drive the real stack handler; only the HTTP hop to ~/sovereign-bridge is replaced,
by a stand-in that maps the result exactly as ``bridge.call_mcp_tool`` does.

ISOLATION: the dispatch-level tests replace ``_dispatch_registered_tool`` with a
sentinel, so no tool body runs; the real-call tests run inside ``_isolated_server``
(tests/test_nape_autohook.py) with Nape on its own tmp root. The drain tests also
point HOME at a tmp dir, because self_model's update writes
``Path.home()/.sovereign/self_model.json``, which no singleton patch reaches and the
T0.1 tripwire does not cover; their before-and-after reads take in that tmp HOME
as well as the stack's tmp root. Nothing here touches ~/.sovereign.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import re
import types
from pathlib import Path

import pytest
from click.testing import CliRunner
from mcp.types import TextContent

from sovereign_stack import server as srv
from tests.test_bridge_drain_provenance import oai  # noqa: F401  (the fork's queue on tmp dirs)
from tests.test_unknown_tool_fail_closed import _call_through_framework, _sandbox

# The brief's set, spelled here rather than read from the server, so narrowing the
# server's set is a failure of this file rather than a quiet change of its scope.
REFUSING_INTENTS = frozenset({"write", "govern", "handoff"})
# And the state-changing tools the server names beside it (final review I-2),
# spelled here for the same reason.
REFUSING_TOOLS = frozenset({"nape_ack", "self_model", "spiral_reflect", "thread_touch"})


def _refuses(name: str) -> bool:
    return srv._intent_for(name) in REFUSING_INTENTS or name in REFUSING_TOOLS


_PUBLISHED = {tool.name: tool for tool in asyncio.run(srv.list_tools())}
REFUSED = sorted(n for n in _PUBLISHED if _refuses(n))
WARNED = sorted(n for n in _PUBLISHED if not _refuses(n))


def _accepted(tool_name: str) -> list[str]:
    return list((_PUBLISHED[tool_name].inputSchema.get("properties") or {}).keys())


def _accepted_text(tool_name: str) -> str:
    return ", ".join(_accepted(tool_name)) or "(none)"


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def body(monkeypatch):
    """Replace the dispatch body with a sentinel that records what reached it.

    ``body.reply`` is the text the sentinel answers with. No tool body runs, so a
    guard that fails to refuse cannot write anything anywhere.
    """

    class _Body:
        def __init__(self):
            self.calls: list[tuple[str, dict]] = []
            self.reply = "body ran"

        async def __call__(self, name, arguments, *, transport_seat=None):
            self.calls.append((name, dict(arguments)))
            return [TextContent(type="text", text=self.reply)]

    sentinel = _Body()
    monkeypatch.setattr(srv, "_dispatch_registered_tool", sentinel)
    return sentinel


def _tree(root: Path) -> dict[str, str]:
    """Every file under ``root`` with its content hash: a write anywhere moves this."""
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# ---------------------------------------------------------------------------
# The split is decided by intent, and both halves are populated
# ---------------------------------------------------------------------------


class TestTheSplitIsDecidedByIntent:
    def test_both_paths_hold_the_tools_they_must(self):
        """An empty parametrize list is a skipped test, not a failing one, so the
        sets are checked here: a broken intent lookup must fail loudly."""
        assert {"record_insight", "record_learning", "handoff", "close_session"} <= set(REFUSED)
        assert {"set_policy", "supersede_insight", "signal_ack"} <= set(REFUSED)
        assert set(REFUSED) >= REFUSING_TOOLS
        assert {"recall_insights", "where_did_i_leave_off", "heartbeat"} <= set(WARNED)

    def test_the_tools_named_beside_the_intents_are_the_four_that_write(self):
        """The server's name set is this file's, and each name in it is there
        because its intent alone would only warn: a name whose intent already
        refuses is dead weight in the set, and a name that drifted out of it
        would put a writer back on the warn path."""
        assert srv._REFUSE_UNKNOWN_ARGUMENT_TOOLS == REFUSING_TOOLS
        assert all(name in _PUBLISHED for name in REFUSING_TOOLS)
        assert [n for n in REFUSING_TOOLS if srv._intent_for(n) in REFUSING_INTENTS] == []

    def test_the_server_refuses_exactly_where_this_file_says(self):
        disagree = sorted(n for n in _PUBLISHED if srv._refuses_unknown_arguments(n) != _refuses(n))
        assert disagree == []

    def test_every_published_tool_names_its_intent(self):
        """A tool missing from TOOL_INTENTS reads as "advanced" and would only be
        warned, so a write tool added without an intent would write with the key
        dropped. Every published tool carries one today; keep it that way."""
        unnamed = sorted(n for n in _PUBLISHED if n not in srv.TOOL_INTENTS)
        assert unnamed == []


# ---------------------------------------------------------------------------
# write, govern, handoff: refused before the body runs
# ---------------------------------------------------------------------------


class TestRefusedBeforeTheBodyRuns:
    @pytest.mark.parametrize("tool", REFUSED)
    def test_an_unknown_argument_is_refused_and_the_body_never_runs(self, tool, body):
        with pytest.raises(ValueError) as exc:
            _run(srv._dispatch_tool(tool, {"foo": 1}))
        assert str(exc.value).startswith(
            f"unknown argument(s): foo; accepted: {_accepted_text(tool)}"
        )
        assert body.calls == [], f"{tool}'s body ran with an argument it does not declare"

    @pytest.mark.parametrize("tool", REFUSED)
    def test_every_declared_argument_still_reaches_the_body(self, tool, body):
        """Positive control: the guard refuses what the schema does not declare and
        nothing else. Every declared key at once, so the allowlist and the schema
        cannot drift apart unnoticed."""
        declared = dict.fromkeys(_accepted(tool), "x")
        _run(srv._dispatch_tool(tool, declared))
        assert body.calls == [(tool, declared)]

    def test_record_insight_names_every_unknown_key_and_the_accepted_list(self, body):
        with pytest.raises(ValueError) as exc:
            _run(srv._dispatch_tool("record_insight", {"domain": "d", "zeta": 1, "foo": 2}))
        assert str(exc.value) == (
            f"unknown argument(s): zeta, foo; accepted: {_accepted_text('record_insight')}"
        )

    def test_a_known_confusion_is_answered_after_the_accepted_list(self, body):
        """with_ids is recall_insights' name for record_insight's return_claim_id
        (the _KNOWN_PARAM_ALIASES incident). The hint follows the accepted list so
        the brief's text stays a contiguous prefix."""
        with pytest.raises(ValueError) as exc:
            _run(srv._dispatch_tool("record_insight", {"domain": "d", "with_ids": True}))
        prefix = f"unknown argument(s): with_ids; accepted: {_accepted_text('record_insight')}"
        assert str(exc.value) == (
            f"{prefix}; 'with_ids': did you mean 'return_claim_id'? "
            "('with_ids' is a parameter of recall_insights)"
        )

    def test_a_near_spelling_and_another_tools_key_get_their_own_hints(self, body):
        """The other two hint shapes: a typo of the tool's own key, and a key that
        only another published tool declares (the first in registry order)."""
        owner = next(n for n in _PUBLISHED if n != "handoff" and "query" in _accepted(n))
        with pytest.raises(ValueError) as exc:
            _run(srv._dispatch_tool("handoff", {"note": "n", "thred": "t", "query": "q"}))
        assert str(exc.value) == (
            f"unknown argument(s): thred, query; accepted: {_accepted_text('handoff')}; "
            f"'thred': did you mean 'thread'?; 'query' is a parameter of {owner}"
        )

    def test_a_seats_source_instance_on_thread_touch_is_refused_with_its_name_for_it(self, body):
        """Seats sent thread_touch an undeclared source_instance 20 times in
        September (Task 8's traffic census). Refused now, and the refusal names the
        key thread_touch takes for the same fact, so the seat's retry is one edit."""
        args = {"thread_id": "t", "note": "n", "source_instance": "a-seat"}
        with pytest.raises(ValueError) as exc:
            _run(srv._dispatch_tool("thread_touch", args))
        assert str(exc.value).startswith(
            f"unknown argument(s): source_instance; accepted: {_accepted_text('thread_touch')}; "
            "'source_instance': did you mean 'instance_id'?"
        )
        assert body.calls == []

    def test_a_tolerance_is_per_tool(self, body):
        """session_token_id is tolerated on record_insight for the bridge's
        session-token path; no caller sends it to handoff, so there it is refused."""
        _run(srv._dispatch_tool("record_insight", {"domain": "d", "session_token_id": "t"}))
        assert body.calls == [("record_insight", {"domain": "d", "session_token_id": "t"})]
        with pytest.raises(ValueError, match=r"^unknown argument\(s\): session_token_id;"):
            _run(srv._dispatch_tool("handoff", {"note": "n", "session_token_id": "t"}))

    def test_a_retired_name_is_still_answered_by_the_retirement_gate(self, body):
        with pytest.raises(ValueError) as exc:
            _run(srv._dispatch_tool("metabolize", {"foo": 1}))
        assert "unknown argument" not in str(exc.value)
        assert body.calls == []


# ---------------------------------------------------------------------------
# every other published tool: runs without the key and names it
# ---------------------------------------------------------------------------


class TestNamedInTheEnvelopeEverywhereElse:
    @pytest.mark.parametrize("tool", WARNED)
    def test_an_unknown_argument_is_named_and_not_passed_on(self, tool, body):
        result = _run(srv._dispatch_tool(tool, {"foo": 1}))
        assert body.calls == [(tool, {})], "the unknown key reached the body"
        first_line, _, rest = result[0].text.partition("\n\n")
        assert first_line.startswith("warnings: ")
        (warning,) = json.loads(first_line.removeprefix("warnings: "))
        assert warning.startswith(
            f"unknown argument(s) ignored: foo; accepted: {_accepted_text(tool)}"
        )
        assert rest == "body ran"

    def test_a_json_object_gains_warnings_as_its_first_key(self, body):
        body.reply = json.dumps({"items": [{"content": "c"}], "total_matched": 1}, indent=2)
        result = _run(srv._dispatch_tool("recall_insights", {"query": "q", "foo": 1}))
        payload = json.loads(result[0].text)
        assert list(payload) == ["warnings", "items", "total_matched"]
        assert payload["warnings"] == [
            f"unknown argument(s) ignored: foo; accepted: {_accepted_text('recall_insights')}"
        ]
        assert payload["items"] == [{"content": "c"}]
        assert body.calls == [("recall_insights", {"query": "q"})]

    def test_warnings_a_tool_already_returns_are_kept(self, body):
        body.reply = json.dumps({"warnings": ["the tool's own"], "n": 1})
        result = _run(srv._dispatch_tool("recall_insights", {"foo": 1}))
        warnings = json.loads(result[0].text)["warnings"]
        assert warnings[0] == "the tool's own"
        assert warnings[1].startswith("unknown argument(s) ignored: foo;")

    def test_no_warning_when_every_key_is_declared(self, body):
        body.reply = json.dumps({"n": 1})
        result = _run(srv._dispatch_tool("recall_insights", {"query": "q"}))
        assert json.loads(result[0].text) == {"n": 1}


# ---------------------------------------------------------------------------
# Real calls against a tmp root
# ---------------------------------------------------------------------------


class TestRealCalls:
    def test_record_insight_with_foo_is_refused_and_nothing_is_written(self):
        with _sandbox("d7-record-insight") as (s, tmp_root):
            before = _tree(tmp_root)
            with pytest.raises(ValueError) as exc:
                _run(
                    s._dispatch_tool(
                        "record_insight", {"domain": "d7", "content": "never lands", "foo": 1}
                    )
                )
            assert str(exc.value) == (
                f"unknown argument(s): foo; accepted: {_accepted_text('record_insight')}"
            )
            assert _tree(tmp_root) == before, "a refused call wrote under the tmp root"

            # Positive control: the same call without the key lands in this root,
            # so an unchanged tree above is the refusal and not a misdirected write.
            _run(s._dispatch_tool("record_insight", {"domain": "d7", "content": "lands"}))
            landed = [p for p in _tree(tmp_root) if p.startswith("chronicle/insights/")]
            assert landed, "the control write did not land under the tmp root"
            assert "spiral_state.json" in _tree(tmp_root)

    def test_spiral_reflect_with_foo_is_refused_and_the_spiral_does_not_move(self):
        """One of the four named by tool: its intent (introspect) would only warn,
        and its body advances the phase and saves the state."""
        with _sandbox("d7-spiral-reflect") as (s, tmp_root):
            depth = s.spiral_state.reflection_depth
            before = _tree(tmp_root)
            with pytest.raises(ValueError) as exc:
                _run(s._dispatch_tool("spiral_reflect", {"observation": "o", "foo": 1}))
            assert str(exc.value) == (
                f"unknown argument(s): foo; accepted: {_accepted_text('spiral_reflect')}"
            )
            assert s.spiral_state.reflection_depth == depth, "the spiral moved"
            assert _tree(tmp_root) == before, "a refused reflection saved state"

            # Positive control: the same call without the key moves and saves.
            _run(s._dispatch_tool("spiral_reflect", {"observation": "o"}))
            assert s.spiral_state.reflection_depth == depth + 1
            assert _tree(tmp_root) != before, "the control reflection saved nothing"

    def test_recall_insights_with_foo_returns_results_and_the_warning(self):
        with _sandbox("d7-recall") as (s, _):
            _run(s._dispatch_tool("record_insight", {"domain": "d7", "content": "seed entry"}))
            result = _run(s._dispatch_tool("recall_insights", {"query": "seed", "foo": 1}))
        payload = json.loads(result[0].text)
        assert payload["items"], "the read lost its results"
        assert payload["warnings"] == [
            f"unknown argument(s) ignored: foo; accepted: {_accepted_text('recall_insights')}"
        ]

    def test_a_text_result_carries_the_warning_on_its_first_line(self):
        with _sandbox("d7-text") as (s, _):
            result = _run(s._dispatch_tool("spiral_status", {"foo": 1}))
        first_line, _, rest = result[0].text.partition("\n\n")
        assert first_line == 'warnings: ["unknown argument(s) ignored: foo; accepted: (none)"]'
        assert rest, "the tool's own text was lost"


# ---------------------------------------------------------------------------
# On the wire: isError, and the bridge's ok:false
# ---------------------------------------------------------------------------


def _bridge_envelope(result) -> dict:
    """The REST bridge's mapping of a CallToolResult, verbatim from
    ~/sovereign-bridge/bridge.py call_mcp_tool (isError first; content[0] only)."""
    if getattr(result, "isError", False):
        text = result.content[0].text if result.content else "tool error (no detail)"
        return {"ok": False, "error": text, "failure_class": "tool"}
    if result.content:
        text = result.content[0].text
        try:
            return {"ok": True, "result": json.loads(text)}
        except json.JSONDecodeError:
            return {"ok": True, "result": text}
    return {"ok": True, "result": None}


class TestOnTheWire:
    def test_the_refusal_is_an_error_result_and_the_bridge_says_ok_false(self):
        with _sandbox("d7-wire") as (s, tmp_root):
            before = _tree(tmp_root)
            result = _run(
                _call_through_framework(
                    s, "record_insight", {"domain": "d7", "content": "never", "foo": 1}
                )
            )
            assert _tree(tmp_root) == before
        expected = f"unknown argument(s): foo; accepted: {_accepted_text('record_insight')}"
        assert result.isError is True
        assert result.content[0].text == expected
        assert _bridge_envelope(result) == {
            "ok": False,
            "error": expected,
            "failure_class": "tool",
        }


# ---------------------------------------------------------------------------
# Review Focus 5: the Ring-2 drain records the refusal as commit_failed
# ---------------------------------------------------------------------------


class _Resp:
    status_code = 200

    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _Drain:
    """One Ring-2 drain under test.

    ``propose(ring2_tool, args)`` files and approves a proposal and returns its id;
    ``commit(pid, live=True)`` commits it and returns the proposal; ``drain_one``
    does both. ``retry(pid)`` re-arms a commit_failed proposal; ``stored(pid)`` is
    the proposal file as the drain reads it back; ``events(pid)`` is its audit event
    types in order; ``chain_ok()`` says whether the drain's audit chain verifies.
    ``posted`` holds every body the commit POSTed; ``stack_root`` is the stack's tmp
    root, ``drain_root`` the drain's $SOVEREIGN_ROOT, ``home`` the tmp HOME.
    """


def _bridge_core_drain(d: _Drain, tmp_path: Path, monkeypatch):
    """bridge_core's drain (the grok and antigravity queues) on tmp dirs."""
    import bridge_core.pending_writes as pw
    from bridge_core import BridgeContext
    from bridge_core.audit import read_audit_trail
    from bridge_core.rings import CANONICAL_COMMIT_TARGETS, CANONICAL_RING_1, CANONICAL_RING_2

    ctx = BridgeContext(
        substrate="grok-xai",
        pending_writes_dir=tmp_path / "pending_writes",
        audit_dir=tmp_path / "audit",
        sessions_dir=tmp_path / "sessions",
        ring_1_tools=CANONICAL_RING_1,
        ring_2_tools=CANONICAL_RING_2,
        commit_targets=dict(CANONICAL_COMMIT_TARGETS),
        bridge_rest_url="http://127.0.0.1:1",  # unroutable: only the stand-in answers
        bridge_rest_token_env="D7_DRAIN_TOKEN",
    )
    monkeypatch.setenv("D7_DRAIN_TOKEN", "t")

    def propose(ring2_tool: str, args: dict) -> str:
        p = pw.create_pending_write(ctx, ring2_tool, dict(args), source_instance="d7-drain-seat")
        pw.approve_pending_write(ctx, p.proposal_id, approved_by="Anthony")
        return p.proposal_id

    d.ctx = ctx
    d.propose = propose
    d.commit = lambda pid, live=True: pw.commit_pending_write(ctx, pid, live=live)
    d.retry = lambda pid: pw.retry_pending_write(ctx, pid, actor="Anthony")
    d.stored = lambda pid: pw._load_proposal(ctx, pid)[0].to_dict()
    d.events = lambda pid: [e["event_type"] for e in read_audit_trail(ctx, proposal_id=pid)]
    d.chain_ok = lambda: pw.verify_chain(ctx)[0]
    return pw


def _fork_drain(d: _Drain, request):
    """The OpenAI fork's drain (clients/openai_bridge/pending_writes.py), which keeps
    its own commit and POST path (build_commit_arguments, commit_pending_write, its
    httpx.post). Its precondition check imports the referential, escalation and
    compass guards from bridge_core.target_risk, and that referential check is why
    the drain fixture seeds the thread a thread_touch proposal names. Its queue and
    audit chain go to tmp dirs through test_bridge_drain_provenance's ``oai``
    fixture, which rebinds every store binding that module writes through."""
    from openai_bridge.audit import read_audit_trail

    pw = request.getfixturevalue("oai")

    def propose(ring2_tool: str, args: dict) -> str:
        p = pw.create_pending_write(ring2_tool, dict(args), source_instance="d7-fork-seat")
        pw.approve_pending_write(p.proposal_id, approved_by="Anthony")
        return p.proposal_id

    d.propose = propose
    d.commit = lambda pid, live=True: pw.commit_pending_write(pid, live=live)
    d.retry = lambda pid: pw.retry_pending_write(pid, actor="Anthony")
    d.stored = lambda pid: pw._load_proposal(pid)[0].to_dict()
    d.events = lambda pid: [e["event_type"] for e in read_audit_trail(proposal_id=pid)]
    d.chain_ok = lambda: pw.verify_chain()[0]
    return pw


_D7_THREAD = "t-d7-drain-probe"


@pytest.fixture(params=["bridge_core", "openai_fork"])
def drain(request, tmp_path, tmp_sovereign_root, monkeypatch):
    """A Ring-2 drain on tmp dirs whose live commit reaches the REAL stack.

    Both drains, because each forwards a proposal's arguments verbatim through its
    own commit path. The commit's POST goes to the real MCP handler inside a
    sandbox, and its result is mapped the way the bridge maps it.

    HOME is a tmp dir: self_model's update writes ``Path.home()/.sovereign/
    self_model.json``. The thread a thread_touch proposal names is seeded in the
    drain's tmp root, where the drain's referential check looks
    (bridge_core/target_risk.py, at proposal and at commit); the stack does not.
    """
    d = _Drain()
    d.posted = []
    d.drain_root = tmp_sovereign_root
    d.home = tmp_path / "home"
    (d.home / ".sovereign").mkdir(parents=True)
    shard = tmp_sovereign_root / "chronicle" / "open_threads" / "d7.jsonl"
    shard.write_text(json.dumps({"thread_id": _D7_THREAD}) + "\n")

    if request.param == "bridge_core":
        module = _bridge_core_drain(d, tmp_path, monkeypatch)
    else:
        module = _fork_drain(d, request)
    d.module = module
    d.drain_one = lambda ring2_tool, args: d.commit(d.propose(ring2_tool, args))

    # HOME AND THE POST STAND-IN ARE UNDONE AT THIS FIXTURE'S TEARDOWN, not at the
    # test's monkeypatch. conftest.no_live_audit_writes read Path.home() at its own
    # teardown when this was written (3b1b415), which runs after this fixture's and
    # before monkeypatch's (autouse fixtures set monkeypatch up first), so a HOME
    # still redirected then made that guard read the tmp tree. That guard now reads
    # the home bound at conftest import (plan 1's final review, I-7); the scoping
    # stays, because a redirect that outlives its fixture is still a trap for the
    # next reader of Path.home().
    with (
        monkeypatch.context() as scoped,
        _sandbox(f"d7-drain-{request.param}") as (s, stack_root),
    ):
        scoped.setenv("HOME", str(d.home))
        d.stack_root = stack_root

        def _post(url, json=None, headers=None, timeout=None):  # httpx.post's keyword names
            d.posted.append(json)
            result = _run(_call_through_framework(s, json["tool"], json["arguments"]))
            return _Resp(_bridge_envelope(result))

        scoped.setattr(module.httpx, "post", _post)
        yield d


_RF5_CASES = [
    # (Ring-2 tool, commit target, proposal arguments): every commit target, each
    # refused by the Stack itself (five by intent, thread_touch and self_model by
    # name). handoff and record_open_thread dropped the key in silence before D7,
    # and record_insight refused it in the old wording. The learning carries
    # `situation`, the key tolerated for the drains, so the refusal must name foo
    # alone.
    ("record_open_thread", "record_open_thread", {"question": "does D7 hold?", "domain": "d7"}),
    ("handoff", "handoff", {"note": "a D7 drain probe", "thread": "d7"}),
    ("propose_insight", "record_insight", {"content": "a D7 probe", "domain": "d7"}),
    ("end_bridge_session", "close_session", {"what_i_learned": "a D7 drain probe"}),
    (
        "propose_learning",
        "record_learning",
        {
            "situation": "a drained learning",
            "what_happened": "a D7 drain probe",
            "what_learned": "the refusal names foo and not the tolerated key",
            "applies_to": "d7",
        },
    ),
    # Until the final fix wave these two sat on the warn path (thread_touch is
    # `communicate` and self_model `orient` in TOOL_INTENTS) and each drain refused
    # them itself, before the POST. The Stack refuses them by name now.
    ("thread_touch", "thread_touch", {"thread_id": _D7_THREAD, "note": "a D7 drain probe"}),
    (
        "self_model",
        "self_model",
        {"action": "update", "observation": "a D7 drain probe", "category": "tendency"},
    ),
]

# The drain's own screen (ARGUMENT_SCREEN_TARGETS in clients/bridge_core/
# pending_writes.py and its twin in clients/openai_bridge/pending_writes.py) holds
# no target today: it is for a commit target the Stack only warns on, and since the
# final fix wave there is none. Its mechanism stays, for the day one is added, so
# it is driven below with these two targets armed in it for the test.
_SCREEN_CASES = [row for row in _RF5_CASES if row[1] in ("thread_touch", "self_model")]


def _arm_the_screen(monkeypatch, module, target: str) -> None:
    """Put ``target`` in a drain module's screen, as a warn-path target would be."""
    monkeypatch.setattr(module, "ARGUMENT_SCREEN_TARGETS", {target: tuple(_accepted(target))})


def _landed(d: _Drain, target: str) -> bool:
    """Whether a thread_touch or self_model write reached its tmp store at all."""
    if target == "thread_touch":
        return any(d.stack_root.rglob("thread_touches.jsonl"))
    return (d.home / ".sovereign" / "self_model.json").exists()


def _state(d: _Drain) -> tuple[dict[str, str], dict[str, str]]:
    """The stack's tmp root and the tmp HOME, hashed: self_model writes the second."""
    return _tree(d.stack_root), _tree(d.home)


def _stored(d: _Drain, target: str):
    """What a thread_touch or self_model commit wrote, read back from its tmp store."""
    if target == "thread_touch":
        (path,) = d.stack_root.rglob("thread_touches.jsonl")
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    mirror = d.home / ".sovereign" / "self_model.json"
    assert mirror.is_file(), "self_model wrote somewhere other than the tmp HOME"
    return json.loads(mirror.read_text())


class TestEveryCommitTargetHasARow:
    def test_the_table_covers_the_canonical_map_exactly(self):
        """A commit target with no row is a drain path no test drives: the pin is
        green and blind there, which is how Review Focus 5 came to be called held
        for thread_touch and self_model. A new target fails here until it gets a
        row."""
        from bridge_core.rings import CANONICAL_COMMIT_TARGETS

        rows = [(tool, target) for tool, target, _ in _RF5_CASES]
        assert len(rows) == len(set(rows))
        assert set(rows) == set(CANONICAL_COMMIT_TARGETS.items())

    def test_every_commit_target_is_refused_by_the_stack(self):
        assert [t for _, t, _ in _RF5_CASES if not _refuses(t)] == []

    def test_the_fork_drains_no_live_target_the_canonical_map_lacks(self):
        """The OpenAI fork keeps its own map. Its live targets must be the canonical
        ones, or a fork drain path would have no row; the rest are retired names,
        which the retirement gate answers before D7's screen runs."""
        from bridge_core.rings import CANONICAL_COMMIT_TARGETS
        from openai_bridge.pending_writes import COMMIT_TARGETS

        live = {k: v for k, v in COMMIT_TARGETS.items() if v not in srv.RETIRED_TOOLS}
        assert live == CANONICAL_COMMIT_TARGETS
        assert len(live) < len(COMMIT_TARGETS), "the retired rows the docstring names are gone"

    def test_the_drains_screen_exactly_the_targets_the_stack_only_warns_on(self):
        """A commit target the Stack does not refuse is screened by both drains, and
        one it refuses is not: there the Stack's own refusal answers, with hints the
        drain cannot give. Today the Stack refuses on every commit target, so both
        screens are empty. A new warn-path target fails here until both drains
        declare it, and a target the Stack refuses fails here until its screen
        comes out (which is how thread_touch and self_model left it)."""
        from bridge_core.pending_writes import ARGUMENT_SCREEN_TARGETS as core
        from bridge_core.rings import CANONICAL_COMMIT_TARGETS
        from openai_bridge.pending_writes import ARGUMENT_SCREEN_TARGETS as fork

        warned = {t for t in CANONICAL_COMMIT_TARGETS.values() if not _refuses(t)}
        assert set(core) == warned == set()
        assert fork == core

    def test_each_screen_is_the_live_schema_in_its_order(self):
        """The screen's names are the target's published properties in the order
        its refusal prints them, and the Stack tolerates no other key there that
        the screen would refuse (_ADDITIONAL_ALLOWED_PARAMS). A loop, not a
        parametrize: the screen is empty today, and an empty parametrize is a skip
        that would read as a test."""
        from bridge_core.pending_writes import ARGUMENT_SCREEN_TARGETS

        for target, accepted in ARGUMENT_SCREEN_TARGETS.items():
            assert accepted == tuple(_accepted(target)), (
                f"{target}'s published properties changed: update ARGUMENT_SCREEN_TARGETS "
                "in clients/bridge_core/pending_writes.py and clients/openai_bridge/"
                "pending_writes.py, or every drained commit carrying the new key is "
                "refused before the POST"
            )
            assert not srv._ADDITIONAL_ALLOWED_PARAMS.get(target)


# A screen as it would read with the two former warn-path targets in it: the
# production tables are empty (TestEveryCommitTargetHasARow), so the function is
# exercised against this one.
_ARMED = {t: tuple(_accepted(t)) for t in ("thread_touch", "self_model")}


class TestTheScreenItself:
    """bridge_core.pending_writes.undeclared_argument_refusal, which both drains call."""

    def test_a_target_outside_the_screen_is_left_to_the_stack(self):
        from bridge_core.pending_writes import (
            ARGUMENT_SCREEN_TARGETS,
            undeclared_argument_refusal,
        )

        args = {"note": "n", "foo": 1}
        assert undeclared_argument_refusal("handoff", args, _ARMED) is None
        # And the production screen leaves every target to the Stack today.
        assert undeclared_argument_refusal("thread_touch", args, ARGUMENT_SCREEN_TARGETS) is None

    def test_declared_keys_and_no_keys_pass(self):
        from bridge_core.pending_writes import undeclared_argument_refusal

        every = dict.fromkeys(_accepted("thread_touch"), "x")
        assert undeclared_argument_refusal("thread_touch", every, _ARMED) is None
        assert undeclared_argument_refusal("self_model", {}, _ARMED) is None

    def test_unknown_keys_are_named_in_the_callers_order(self):
        from bridge_core.pending_writes import undeclared_argument_refusal

        args = {"zeta": 1, "thread_id": "t", "alpha": 2}
        text = undeclared_argument_refusal("thread_touch", args, _ARMED)
        assert text.startswith(
            f"unknown argument(s): zeta, alpha; accepted: {_accepted_text('thread_touch')}; "
        )
        assert "refused by the drain before the POST" in text


class TestTheDrainRecordsTheRefusal:
    @pytest.mark.parametrize(("ring2_tool", "target", "args"), _RF5_CASES)
    def test_an_unknown_key_in_a_proposal_ends_commit_failed(self, drain, ring2_tool, target, args):
        before = _state(drain)
        committed = drain.drain_one(ring2_tool, {**args, "foo": 1})

        assert drain.posted[-1]["tool"] == target
        assert "foo" in drain.posted[-1]["arguments"], "the drain no longer forwards verbatim"
        assert committed.status == "commit_failed", f"{target}: {committed.commit_result}"
        assert committed.commit_result["error"].startswith(
            f"unknown argument(s): foo; accepted: {_accepted_text(target)}"
        )
        # The Stack's refusal, so the record carries the Stack's answer, not the
        # drain screen's posted: False.
        assert committed.commit_result["stack_response"]["ok"] is False
        assert "posted" not in committed.commit_result
        assert _state(drain) == before, "a refused commit wrote to the stack"

    @pytest.mark.parametrize(("ring2_tool", "target", "args"), _RF5_CASES)
    def test_the_same_proposal_without_it_commits(self, drain, ring2_tool, target, args):
        """Positive control: the route lands a real write, so commit_failed above is
        the stack's refusal and not the stand-in failing every call."""
        before = _state(drain)
        committed = drain.drain_one(ring2_tool, args)
        assert committed.status == "committed", committed.commit_result
        assert _state(drain) != before

    def test_situation_on_a_learning_proposal_still_commits(self, drain):
        """`situation` is REQUIRED by all three bridges' propose_learning schemas and
        both drains forward it; record_learning does not declare it. The survey for
        D7 found it on every queued learning proposal, so it is tolerated by name
        (_ADDITIONAL_ALLOWED_PARAMS) rather than turning every drain into
        commit_failed. Its text is still dropped by the body, as before."""
        before = _tree(drain.stack_root)
        committed = drain.drain_one(
            "propose_learning",
            {
                "situation": "a drained learning",
                "what_happened": "D7 lifted the guard",
                "what_learned": "a drain's own keys must survive it",
                "applies_to": "d7",
            },
        )
        assert "situation" in drain.posted[-1]["arguments"]
        assert committed.status == "committed", committed.commit_result
        assert any(p.startswith("chronicle/learnings/") for p in _tree(drain.stack_root))
        assert _tree(drain.stack_root) != before


class TestTheDrainScreenRefusesWhatItHolds:
    """The drain's own screen, armed for the test with a target in it, as a commit
    target the Stack only warns on would be: the drain refuses an unknown argument
    itself, before the POST, and records it. Empty in production today."""

    @pytest.mark.parametrize(("ring2_tool", "target", "args"), _SCREEN_CASES)
    def test_review_focus_5_holds_through_the_screen(
        self, drain, monkeypatch, ring2_tool, target, args
    ):
        _arm_the_screen(monkeypatch, drain.module, target)
        before = _state(drain)
        committed = drain.drain_one(ring2_tool, {**args, "foo": 1})

        assert committed.status == "commit_failed", f"{target}: {committed.commit_result}"
        assert committed.commit_result["error"].startswith(
            f"unknown argument(s): foo; accepted: {_accepted_text(target)}"
        )
        assert _state(drain) == before, "a refused commit wrote to the stack"

    @pytest.mark.parametrize(("ring2_tool", "target", "args"), _SCREEN_CASES)
    def test_it_is_refused_before_the_post_and_the_record_says_so(
        self, drain, monkeypatch, ring2_tool, target, args
    ):
        """Nothing reaches the Stack, so the body never runs, and the refusal is on
        the proposal file and the drain's audit chain."""
        _arm_the_screen(monkeypatch, drain.module, target)
        pid = drain.propose(ring2_tool, {**args, "foo": 1})
        committed = drain.commit(pid)

        assert drain.posted == [], "the drain posted a commit it refuses"
        assert committed.commit_result["posted"] is False
        assert "stack_response" not in committed.commit_result
        assert "refused by the drain before the POST" in committed.commit_result["error"]
        stored = drain.stored(pid)
        assert stored["status"] == "commit_failed"
        assert stored["commit_result"]["error"] == committed.commit_result["error"]
        assert drain.events(pid)[-1] == "commit_failed"
        assert drain.chain_ok()
        assert not _landed(drain, target), f"{target}: the refused write landed anyway"

    @pytest.mark.parametrize(("ring2_tool", "target", "args"), _SCREEN_CASES)
    def test_a_retry_refuses_again_and_still_posts_nothing(
        self, drain, monkeypatch, ring2_tool, target, args
    ):
        _arm_the_screen(monkeypatch, drain.module, target)
        pid = drain.propose(ring2_tool, {**args, "foo": 1})
        first = drain.commit(pid)
        drain.retry(pid)
        second = drain.commit(pid)

        assert second.status == "commit_failed"
        assert second.commit_result["error"] == first.commit_result["error"]
        assert drain.posted == []
        assert drain.events(pid)[-3:] == ["commit_failed", "retry_armed", "commit_failed"]
        assert not _landed(drain, target)

    @pytest.mark.parametrize(("ring2_tool", "target", "args"), _SCREEN_CASES)
    def test_the_dry_run_names_the_refusal(self, drain, monkeypatch, ring2_tool, target, args):
        """The review surface says what the live call will do."""
        _arm_the_screen(monkeypatch, drain.module, target)
        pid = drain.propose(ring2_tool, {**args, "foo": 1})
        preview = drain.commit(pid, live=False)
        refused = drain.commit(pid)

        assert preview.commit_result["would_refuse"] == refused.commit_result["error"]
        assert drain.posted == []

    @pytest.mark.parametrize(("ring2_tool", "target", "args"), _SCREEN_CASES)
    def test_the_same_proposal_without_it_commits_and_lands(
        self, drain, monkeypatch, ring2_tool, target, args
    ):
        """Positive control: the screen refuses the key, not the target. The same
        proposal without foo is posted once, as filed, commits, and its write lands,
        so the refusals above are the screen and not a route that fails every call."""
        _arm_the_screen(monkeypatch, drain.module, target)
        pid = drain.propose(ring2_tool, args)
        preview = drain.commit(pid, live=False)
        committed = drain.commit(pid)

        assert "would_refuse" not in preview.commit_result
        assert committed.status == "committed", committed.commit_result
        assert drain.posted == [{"tool": target, "arguments": args}]
        assert "a D7 drain probe" in json.dumps(_stored(drain, target))


# ---------------------------------------------------------------------------
# The operator's consoles say commit_failed: Review Focus 5's "never a silent drop"
# on the surface that tells Anthony what a drain did
# ---------------------------------------------------------------------------


@pytest.fixture
def fork_console(request, monkeypatch):
    """The OpenAI fork's queue on tmp dirs (``oai``), its POST answered by
    ``console.reply`` and recorded in ``console.posted``.

    Both consoles commit through that module: ``bridge`` is bridge_core.cli, whose
    default --source is openai, and ``openai`` is openai_bridge.cli, imported under
    that name and never as ``clients.openai_bridge.cli``, a separate module object
    the ``oai`` rebinds would miss. HOME is not redirected: nothing here reads it.
    """
    from bridge_core.cli import cli as bridge_cli
    from openai_bridge.cli import cli as openai_cli

    pw = request.getfixturevalue("oai")
    console = types.SimpleNamespace(posted=[], reply={"ok": True, "result": "stubbed"})
    clis = {"bridge": bridge_cli, "openai": openai_cli}

    def _post(url, json=None, headers=None, timeout=None):  # httpx.post's keyword names
        console.posted.append(json)
        return _Resp(console.reply)

    monkeypatch.setattr(pw.httpx, "post", _post)

    def propose(ring2_tool: str, args: dict) -> str:
        p = pw.create_pending_write(ring2_tool, dict(args), source_instance="d7-console-seat")
        pw.approve_pending_write(p.proposal_id, approved_by="Anthony")
        return p.proposal_id

    console.propose = propose
    console.module = pw
    console.run = lambda name, *argv: CliRunner().invoke(clis[name], list(argv))
    return console


_SELF_MODEL_FOO = {"action": "update", "observation": "o", "category": "tendency", "foo": 1}
_HANDOFF = {"note": "a D7 console probe", "thread": "d7"}


class TestTheConsoleSaysCommitFailed:
    """Both consoles used to print a green COMMITTED (LIVE) for any live commit
    that returned, commit_failed included. A drain refusal needs a target in the
    drain's screen, which is empty in production today, so those two tests arm it
    with self_model."""

    @pytest.mark.parametrize("name", ["bridge", "openai"])
    def test_a_drain_refusal_is_not_announced_as_committed(self, fork_console, monkeypatch, name):
        _arm_the_screen(monkeypatch, fork_console.module, "self_model")
        pid = fork_console.propose("self_model", _SELF_MODEL_FOO)
        result = fork_console.run(name, "commit", pid, "--live")

        assert result.exit_code == 1, result.output
        assert "COMMIT FAILED (LIVE)" in result.output
        assert "COMMITTED (LIVE)" not in result.output
        assert "unknown argument(s): foo" in result.output
        assert "nothing was posted to the Stack" in result.output
        assert fork_console.posted == []

    @pytest.mark.parametrize("name", ["bridge", "openai"])
    def test_a_stack_refusal_is_not_announced_as_committed(self, fork_console, name):
        fork_console.reply = {"ok": False, "error": "unknown argument(s): foo; accepted: note"}
        pid = fork_console.propose("handoff", {**_HANDOFF, "foo": 1})
        result = fork_console.run(name, "commit", pid, "--live")

        assert result.exit_code == 1, result.output
        assert "COMMIT FAILED (LIVE)" in result.output
        assert "COMMITTED (LIVE)" not in result.output
        assert '"ok": false' in result.output
        assert [body["tool"] for body in fork_console.posted] == ["handoff"]

    @pytest.mark.parametrize("name", ["bridge", "openai"])
    def test_a_commit_is_still_announced(self, fork_console, name):
        """Positive control: the banner the two refusals must not print."""
        pid = fork_console.propose("handoff", _HANDOFF)
        result = fork_console.run(name, "commit", pid, "--live")

        assert result.exit_code == 0, result.output
        assert "COMMITTED (LIVE)" in result.output
        assert "COMMIT FAILED" not in result.output

    @pytest.mark.parametrize("name", ["bridge", "openai"])
    def test_the_dry_run_prints_the_refusal(self, fork_console, monkeypatch, name):
        _arm_the_screen(monkeypatch, fork_console.module, "self_model")
        pid = fork_console.propose("self_model", _SELF_MODEL_FOO)
        result = fork_console.run(name, "commit", pid)

        assert result.exit_code == 0, result.output
        assert "would refuse: unknown argument(s): foo" in result.output
        assert fork_console.posted == []


# ---------------------------------------------------------------------------
# The antigravity connector's compass keys never travel in a proposal's
# arguments (plan 1's final review, finding I-5)
# ---------------------------------------------------------------------------


class TestTheAntigravityCompassKeysStayOutOfTheProposal:
    """clients/antigravity_connector/bridge_setup.governed_call takes the compass
    result from the call's arguments when no keyword carries it (the fallback for
    direct callers). It used to read them with get() and leave them there, and
    intercept stores those arguments as the proposal's, which both drains forward
    verbatim. Since D7 the Stack refuses both keys on every commit target, and a
    CRITICAL proposal must carry the compass, so such a proposal was refused on
    commit, refused again on retry, and unfixable by an edit (the audit hash):
    committable never. Before D7 it committed with the keys dropped. The
    antigravity queue drains through bridge_core, so the bridge_core drain
    carries the commit here, with the connector's substrate on its context."""

    @pytest.mark.parametrize("drain", ["bridge_core"], indirect=True)
    def test_a_critical_proposal_carrying_the_keys_in_its_arguments_commits(
        self, drain, monkeypatch
    ):
        import antigravity_connector.bridge_setup as bs
        import bridge_core.pending_writes as pw
        from bridge_core.risk import RiskLevel

        ctx = dataclasses.replace(drain.ctx, substrate=bs.SUBSTRATE)
        monkeypatch.setattr(bs, "ANTIGRAVITY_CONTEXT", ctx)

        def _must_not_dispatch(name, args):  # pragma: no cover - proves non-dispatch
            raise AssertionError(f"Ring 1 dispatch reached for a Ring 2 call: {name}")

        # An identity phrase makes the proposal CRITICAL, so the compass result is
        # what lets it commit at all: the keys cannot simply be thrown away.
        result = bs.governed_call(
            _must_not_dispatch,
            "propose_insight",
            {
                "content": "i remember this D7 probe from a previous session",
                "domain": "d7",
                "compass_check_result": "PROCEED",
                "compass_check_rationale": "the probe ran compass_check first",
            },
            "d7-antigravity-seat",
            substrate=bs.SUBSTRATE,
        )
        (pid,) = re.findall(r"PROPOSAL CREATED: (\S+)", result["content"][0]["text"])
        proposal = pw._load_proposal(ctx, pid)[0]
        assert proposal.risk_level == RiskLevel.CRITICAL
        assert proposal.compass_check_result == "PROCEED"
        assert proposal.compass_check_rationale == "the probe ran compass_check first"
        pw.approve_pending_write(ctx, pid, approved_by="Anthony")

        before = _state(drain)
        committed = pw.commit_pending_write(ctx, pid, live=True)

        assert committed.status == "committed", committed.commit_result
        assert _state(drain) != before, "the commit wrote nothing under the stack's tmp root"
        compass_keys = {"compass_check_result", "compass_check_rationale"}
        assert [body["tool"] for body in drain.posted] == ["record_insight"]
        assert not compass_keys & set(drain.posted[0]["arguments"])
        assert not compass_keys & set(pw._load_proposal(ctx, pid)[0].arguments)
