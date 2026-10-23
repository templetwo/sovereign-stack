"""Unknown arguments are refused on write tools and named on every other tool.

Design D7, thread 0804046c. Every dispatch branch reads its arguments with
``arguments.get(...)``, so a key the tool does not declare used to vanish: the
call ran and answered as if the key had never been sent. `_reject_unknown_params`
closed that for two tools (record_insight, recall_insights); about fifty others
still dropped silently. The guard now runs once, at dispatch entry, keyed on the
tool's intent in ``TOOL_INTENTS``:

  * write, govern and handoff tools REFUSE: ``ValueError("unknown argument(s):
    <names>; accepted: <names>")`` before the body runs, so nothing is written and
    the spiral does not move. The SDK marks that ``isError=True``, which the
    bridge reports as ``{"ok": false, "error": <that text>}``.
  * every other published tool RUNS without the unknown keys and names them in its
    envelope: ``warnings: ["unknown argument(s) ignored: ..."]``, as a first key of
    a JSON object or a first line of text, inside ``content[0]`` because that is the
    only item the bridge reads.

Review Focus 5 is pinned here too: the Ring-2 drains forward a proposal's arguments
verbatim, so an unknown key in a proposal must end in ``commit_failed`` with the
refusal recorded, never in ``committed``. Those tests drive the real stack handler;
only the HTTP hop to ~/sovereign-bridge is replaced, by a stand-in that maps the
result exactly as ``bridge.call_mcp_tool`` does.

ISOLATION: the dispatch-level tests replace ``_dispatch_registered_tool`` with a
sentinel, so no tool body runs; the real-call tests run inside ``_isolated_server``
(tests/test_nape_autohook.py) with Nape on its own tmp root. Nothing here touches
~/.sovereign.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from mcp.types import TextContent

from sovereign_stack import server as srv
from tests.test_unknown_tool_fail_closed import _call_through_framework, _sandbox

# The brief's set, spelled here rather than read from the server, so narrowing the
# server's set is a failure of this file rather than a quiet change of its scope.
REFUSING_INTENTS = frozenset({"write", "govern", "handoff"})

_PUBLISHED = {tool.name: tool for tool in asyncio.run(srv.list_tools())}
REFUSED = sorted(n for n in _PUBLISHED if srv._intent_for(n) in REFUSING_INTENTS)
WARNED = sorted(n for n in _PUBLISHED if srv._intent_for(n) not in REFUSING_INTENTS)


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
        assert {"recall_insights", "where_did_i_leave_off", "heartbeat"} <= set(WARNED)

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


@pytest.fixture
def drain(tmp_path, tmp_sovereign_root, monkeypatch):
    """A bridge_core context on tmp dirs whose live commit reaches the REAL stack.

    ``drain.ctx`` is the context; ``drain.stack_root`` is the stack's tmp root.
    The commit's POST goes to the real MCP handler inside a sandbox, and its
    result is mapped the way the bridge maps it.
    """
    import bridge_core.pending_writes as pw
    from bridge_core import BridgeContext
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

    class _Drain:
        pass

    d = _Drain()
    d.ctx = ctx
    d.posted = []

    with _sandbox("d7-drain") as (s, stack_root):
        d.stack_root = stack_root

        def _post(url, json=None, headers=None, timeout=None):  # httpx.post's keyword names
            d.posted.append(json)
            result = _run(_call_through_framework(s, json["tool"], json["arguments"]))
            return _Resp(_bridge_envelope(result))

        monkeypatch.setattr(pw.httpx, "post", _post)
        yield d


def _drain_one(d, ring2_tool: str, args: dict):
    import bridge_core.pending_writes as pw

    p = pw.create_pending_write(d.ctx, ring2_tool, dict(args), source_instance="d7-drain-seat")
    pw.approve_pending_write(d.ctx, p.proposal_id, approved_by="Anthony")
    return pw.commit_pending_write(d.ctx, p.proposal_id, live=True)


_RF5_CASES = [
    # (Ring-2 tool, commit target, proposal arguments); handoff and
    # record_open_thread dropped the key in silence before D7, and record_insight
    # refused it in the old wording.
    ("record_open_thread", "record_open_thread", {"question": "does D7 hold?", "domain": "d7"}),
    ("handoff", "handoff", {"note": "a D7 drain probe", "thread": "d7"}),
    ("propose_insight", "record_insight", {"content": "a D7 probe", "domain": "d7"}),
]


class TestTheDrainRecordsTheRefusal:
    @pytest.mark.parametrize(("ring2_tool", "target", "args"), _RF5_CASES)
    def test_an_unknown_key_in_a_proposal_ends_commit_failed(self, drain, ring2_tool, target, args):
        before = _tree(drain.stack_root)
        committed = _drain_one(drain, ring2_tool, {**args, "foo": 1})

        assert drain.posted[-1]["tool"] == target
        assert "foo" in drain.posted[-1]["arguments"], "the drain no longer forwards verbatim"
        assert committed.status == "commit_failed", f"{target}: {committed.commit_result}"
        assert committed.commit_result["error"].startswith(
            f"unknown argument(s): foo; accepted: {_accepted_text(target)}"
        )
        assert _tree(drain.stack_root) == before, "a refused commit wrote to the stack"

    @pytest.mark.parametrize(("ring2_tool", "target", "args"), _RF5_CASES)
    def test_the_same_proposal_without_it_commits(self, drain, ring2_tool, target, args):
        """Positive control: the route lands a real write, so commit_failed above is
        the stack's refusal and not the stand-in failing every call."""
        before = _tree(drain.stack_root)
        committed = _drain_one(drain, ring2_tool, args)
        assert committed.status == "committed", committed.commit_result
        assert _tree(drain.stack_root) != before

    def test_situation_on_a_learning_proposal_still_commits(self, drain):
        """`situation` is REQUIRED by all three bridges' propose_learning schemas and
        both drains forward it; record_learning does not declare it. The survey for
        D7 found it on every queued learning proposal, so it is tolerated by name
        (_ADDITIONAL_ALLOWED_PARAMS) rather than turning every drain into
        commit_failed. Its text is still dropped by the body, as before."""
        before = _tree(drain.stack_root)
        committed = _drain_one(
            drain,
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
