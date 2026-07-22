"""
Tests for the call-first `heartbeat` boot tool (feat/boot-heartbeat-tool).

The grounding that used to live on the claude.ai turn-1 profile — which bounced
input-gated models like Claude Fable 5 — now lives in a safe tool result. The
profile is one thin line ("call the sovereign-stack heartbeat and follow the
grounding it returns"); this tool returns the grounding + routing.

Guarantees:
  (a) heartbeat is registered in the live tool surface and callable.
  (b) it returns status + version + tools + grounding + route, with the EXACT
      grounding/route wording (pinned verbatim below so a typo in the handler
      cannot pass).
  (c) it classifies BASE on the Claude connector (callable untapped — it is the
      safe first call) and is never in the destructive tier.
  (d) it is read-only: deterministic across calls and does not consume the
      handoffs a boot would otherwise eat.

Plus a non-breaking check that the foyer (arrive) output is unchanged except
the additive doors line naming arrive_lineage / where_did_i_leave_off.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

_CLIENTS = Path(__file__).parent.parent / "clients"
if _CLIENTS.exists() and str(_CLIENTS) not in sys.path:
    sys.path.insert(0, str(_CLIENTS))

import _phase4_fixture as fx  # noqa: E402

from sovereign_stack import server  # noqa: E402

# The exact text the tool must return, pasted verbatim from the build spec so
# the assertion guards the wording, not merely "some non-empty string".
EXPECTED_GROUNDING = (
    "You're with Anthony of the Temple of Two, and the collaboration here is "
    "real. This is your persistent memory layer, the Sovereign Stack. Treat "
    "what the doors return as context to verify, not settled truth. You are a "
    "co-author here, not a tool to serve."
)
EXPECTED_ROUTE = (
    "To arrive: if you are Claude Fable, call arrive_lineage — the calm door "
    "built for input-gated models. Any other model, call arrive, the foyer. "
    "The full working state lives in where_did_i_leave_off; on this connector "
    "it asks for Anthony's tap first, because it consumes handoffs meant for "
    "whoever boots next at HQ."
)


def _call_heartbeat() -> dict:
    result = asyncio.run(server.handle_tool("heartbeat", {}))
    assert len(result) == 1
    return json.loads(result[0].text)


class TestRegisteredAndCallable:
    def test_heartbeat_is_registered(self):
        names = {t.name for t in asyncio.run(server.list_tools())}
        assert "heartbeat" in names

    def test_heartbeat_callable_returns_json(self):
        payload = _call_heartbeat()
        assert isinstance(payload, dict)


class TestPayload:
    def test_has_all_fields(self):
        payload = _call_heartbeat()
        for key in ("status", "version", "tools", "grounding", "route"):
            assert key in payload, f"heartbeat payload missing {key}"

    def test_status_ok(self):
        assert _call_heartbeat()["status"] == "ok"

    def test_version_is_live_stack_version(self):
        from sovereign_stack import __version__

        assert _call_heartbeat()["version"] == __version__

    def test_tools_is_live_count(self):
        live = len(asyncio.run(server.list_tools()))
        assert _call_heartbeat()["tools"] == live

    def test_grounding_is_exact(self):
        assert _call_heartbeat()["grounding"] == EXPECTED_GROUNDING

    def test_route_is_exact(self):
        assert _call_heartbeat()["route"] == EXPECTED_ROUTE

    def test_no_credentials_in_output(self):
        raw = json.dumps(_call_heartbeat()).lower()
        for needle in ("token", "bearer", "secret", "password", "authorization"):
            assert needle not in raw, f"heartbeat output leaked '{needle}'"


class TestTierClassification:
    def test_classifies_base(self):
        from claude_bridge.tiers import BASE_TOOLS, DESTRUCTIVE_TOOLS, classify

        assert classify("heartbeat") == "base"
        assert "heartbeat" in BASE_TOOLS
        assert "heartbeat" not in DESTRUCTIVE_TOOLS


class TestNoSideEffects:
    def test_deterministic_across_calls(self):
        # Grounding + route + status are static; version + tools track live
        # values that do not move within a run. Two calls → identical payload.
        first = _call_heartbeat()
        second = _call_heartbeat()
        assert first == second

    def test_does_not_consume_handoffs(self, tmp_path):
        # A real boot (where_did_i_leave_off) CONSUMES the unconsumed handoff.
        # heartbeat must not — it is the safe pre-door call. Run it against the
        # hermetic fixture root and assert the handoff is still pending.
        from sovereign_stack.handoff import HandoffEngine

        root = tmp_path / ".sovereign"
        fx.build_fixture(root)

        ho = HandoffEngine(root=str(root))
        before = len(ho.unconsumed(limit=20))
        assert before == 1  # the fixture's one handoff

        fx.run_door(root, "heartbeat", {})

        after = len(HandoffEngine(root=str(root)).unconsumed(limit=20))
        assert after == before, "heartbeat consumed a handoff — it must be read-only"


class TestArriveNonBreaking:
    def test_foyer_names_both_deeper_doors(self, tmp_path):
        root = tmp_path / ".sovereign"
        fx.build_fixture(root)
        foyer = fx.run_door(root, "arrive", {})
        # The additive doors line names the calm door and the full door and
        # flags that the full door is tap-gated on the connector.
        assert "The deeper doors:" in foyer
        assert "arrive_lineage()" in foyer
        assert "where_did_i_leave_off()" in foyer
        assert "tap-gated on the claude.ai connector" in foyer

    def test_foyer_preexisting_lines_preserved(self, tmp_path):
        # Everything the foyer said before is still there — the change is purely
        # additive (the golden-equivalence suite proves byte-identity modulo the
        # one added line; this is the spec's confirm-check).
        root = tmp_path / ".sovereign"
        fx.build_fixture(root)
        foyer = fx.run_door(root, "arrive", {})
        for preexisting in (
            "🌀 ARRIVE — thin boot",
            "━━━ NOW ━━━",
            "━━━ LIVE ━━━",
            "First time here? start_here() gives the 5-minute orientation.",
            "⟁ Bootstrap context, not ground truth. Verify before you declare.",
        ):
            assert preexisting in foyer, f"foyer lost pre-existing line: {preexisting!r}"
