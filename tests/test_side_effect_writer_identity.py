"""Side-effect writers name their caller (design D2, thread a85ec5e6).

``resolve_thread_by_id`` writes a ground_truth insight AS A SIDE EFFECT: the
stack makes that ``record_insight`` call, not the caller, so the seat the
socket stamped on the request never reached the row. On 2026-09-06 eight
resolutions Grok made as ``grok-build-studio`` landed with the SSE server's
spiral ``session_id`` and no ``source_instance``, and the record could not say
which seat resolved them.

THE TRAP EVERY TEST HERE IS BUILT AROUND. ``_dispatch_tool`` fills
``dispatch_context.CALLER_SEAT`` with the SERVER'S OWN identity,
``seat:<spiral session>``, whenever no transport set one, because the signal
ledger needs a closer. That fill names the server, not a caller. A writer that
stamped whatever ``CALLER_SEAT`` held would write ``seat:spiral_...`` onto every
native resolution and override every argument. So every call below goes
through ``_dispatch_tool``, the door that has the fill, with a spiral session
the fill accepts. A placeholder session such as ``test`` would switch the fill
off and let that naive writer pass these tests.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from sovereign_stack import server
from sovereign_stack.dispatch_context import reset_caller_seat, set_caller_seat
from sovereign_stack.handoff import HandoffEngine
from sovereign_stack.memory import ExperientialMemory
from sovereign_stack.spiral import SpiralState

DOMAIN = "d2-side-effect-writers"
SPIRAL_SESSION = "spiral_20261022_120000"
RESOLUTION = "answered by the caller who asked"


@pytest.fixture
def mem(tmp_sovereign_root, monkeypatch):
    memory = ExperientialMemory(root=str(tmp_sovereign_root / "chronicle"))
    monkeypatch.setattr(server, "experiential", memory)
    monkeypatch.setattr(server, "handoff_engine", HandoffEngine(root=str(tmp_sovereign_root)))
    monkeypatch.setattr(server, "spiral_state", SpiralState(session_id=SPIRAL_SESSION))
    # The precondition the whole file rests on: the server's own fill is LIVE.
    assert server._signal_actor() == f"seat:{SPIRAL_SESSION}"
    return memory


def _rows(paths) -> list[dict]:
    rows = []
    for path in sorted(paths):
        rows += [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return rows


def _insights(memory, domain) -> list[dict]:
    return _rows((memory.insights_dir / domain).rglob("*.jsonl"))


def _dispatch(name: str, arguments: dict) -> str:
    return asyncio.run(server._dispatch_tool(name, arguments))[0].text


def _resolve(memory, **extra) -> tuple[str, dict]:
    memory.record_open_thread("which seat resolved this?", domain=DOMAIN)
    (thread,) = _rows([memory.threads_dir / f"{DOMAIN}.jsonl"])
    thread_id = thread["thread_id"]
    text = _dispatch(
        "resolve_thread_by_id", {"thread_id": thread_id, "resolution": RESOLUTION, **extra}
    )
    # Line one is unchanged: the Nape daemon and every reader key on it.
    assert text.startswith(f"Thread {thread_id} resolved → ground_truth insight: "), text
    (row,) = [r for r in _insights(memory, DOMAIN) if r.get("resolved_thread_id") == thread_id]
    return text, row


def _with_seat(seat: str, call):
    token = set_caller_seat(seat)
    try:
        return call()
    finally:
        reset_caller_seat(token)


def test_a_seat_on_the_dispatch_context_is_stamped_on_the_resolution(mem):
    """The brief's first case: CALLER_SEAT holds "hq-claude-studio" when the
    call arrives, and the insight the stack writes carries it."""
    text, row = _with_seat("hq-claude-studio", lambda: _resolve(mem))
    assert row["source_instance"] == "hq-claude-studio"
    assert "attribution: seat" in text


def test_a_resolve_over_the_seat_header_is_stamped_with_the_bare_seat(mem):
    """Thread a85ec5e6's own acceptance test, through the real header path.

    The transport binds ``seat:grok-build-studio``; the row stores
    ``grok-build-studio``, the spelling the seat socket already signs on the
    same seat's direct writes (the bridge's ``seat_identity.sign_arguments``),
    so one seat keeps one spelling across its resolutions and its insights.
    """
    from sovereign_stack import sse_server

    scope = {
        "type": "http",
        "path": "/sse",
        "method": "GET",
        "headers": [(b"x-sovereign-seat", b"grok-build-studio")],
        "query_string": b"",
        "client": ("127.0.0.1", 12345),
    }
    with sse_server.caller_seat_for_session(scope) as seat:
        assert seat == "seat:grok-build-studio"
        text, row = _resolve(mem)
    assert row["source_instance"] == "grok-build-studio"
    assert "attribution: seat" in text


def test_without_a_seat_the_argument_is_the_fallback(mem):
    """The brief's second case. The server's own ``seat:<spiral session>`` fill
    is set during this call and must not win: it names the server, the one
    identity thread a85ec5e6 says the record already had (as session_id)."""
    text, row = _resolve(mem, source_instance="codex-astra-studio")
    assert row["source_instance"] == "codex-astra-studio"
    assert "attribution: argument" in text


def test_with_neither_the_insight_lands_unattributed_and_the_result_says_none(mem):
    """The brief's third case. The insight still lands (refusing it is D1, plan
    2), the key is ABSENT rather than null or the server's fill, and the result
    says so."""
    text, row = _resolve(mem)
    assert "source_instance" not in row
    assert not any("seat:" in str(value) for value in row.values())
    assert row["session_id"] == SPIRAL_SESSION  # stored exactly as before
    assert "attribution: none" in text


def test_the_seat_wins_over_a_differing_argument_and_the_result_names_it(mem):
    """An overridden argument is reported, not dropped in silence: from the
    caller's side an ignored argument looks exactly like an honoured one."""
    text, row = _with_seat(
        "hq-claude-studio", lambda: _resolve(mem, source_instance="someone-else")
    )
    assert row["source_instance"] == "hq-claude-studio"
    assert "attribution: seat" in text
    assert "'someone-else'" in text and "not used" in text


def test_close_session_stamps_the_seat_on_every_record_it_writes(mem, tmp_sovereign_root):
    """close_session is the other handler that writes insights as a side
    effect. With a seat, the reflection, the surprise and the handoff all name
    it; before D2 the insights said "unknown" and the handoff was refused for
    want of an author the server already knew."""

    def close():
        return _dispatch(
            "close_session",
            {
                "what_i_learned": "the seat travels to the side effects",
                "what_surprised_me": "the server's own fill looked like a caller",
                "what_to_pick_up": "nothing; this close exists for a test",
            },
        )

    text = _with_seat("hq-claude-studio", close)
    assert "Instance: hq-claude-studio" in text
    (reflection,) = _insights(mem, "reflection")
    (surprise,) = _insights(mem, "surprise")
    assert reflection["source_instance"] == "hq-claude-studio"
    assert surprise["source_instance"] == "hq-claude-studio"
    (handoff,) = [
        json.loads(path.read_text()) for path in (tmp_sovereign_root / "handoffs").glob("*.json")
    ]
    assert handoff["source_instance"] == "hq-claude-studio"


def test_close_session_without_a_seat_keeps_its_historical_attribution(mem):
    """A pin, green before and after: with no seat, D2 changes nothing. The
    insight legs keep the historical "unknown" (de-defaulting them is D1, plan
    2), and the server's own fill does not leak in as an author."""
    _dispatch("close_session", {"what_i_learned": "an unnamed close"})
    (reflection,) = _insights(mem, "reflection")
    assert reflection["source_instance"] == "unknown"


def test_schema_declares_source_instance_as_the_optional_fallback():
    """Declared so the documented fallback survives the unknown-argument
    refusal on write tools (plan 1, Task 8); optional, and its description
    names the precedence."""
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    schema = tools["resolve_thread_by_id"].inputSchema
    prop = schema["properties"]["source_instance"]
    assert prop["type"] == "string"
    assert "source_instance" not in schema["required"]
    description = prop["description"].lower()
    assert "seat" in description and "fallback" in description
