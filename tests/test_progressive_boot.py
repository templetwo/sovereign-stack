"""Tests for the progressive-boot tools: arrive() and arrive_delta().

These are NON-BREAKING siblings of where_did_i_leave_off, which stays the
deep/full boot. arrive() is the thin warm "foyer"; arrive_delta() is the
"what changed since last reflection" delta. Both are read-only and must not
consume handoffs. Added 2026-05-31 per the three-model encounter-design
synthesis: a stateless instance uses what meets it at arrival, so a thin
arrival with the full ritual one call away beats one 89KB dump.

These are visible-output + registration checks. Because both tools only READ
(get_open_threads / recall_insights / unconsumed / format_self_model /
get_summary) and never write or consume, they are safe to exercise against
any chronicle state — the asserted section headers are emitted regardless of
content.
"""

from __future__ import annotations

import asyncio

from sovereign_stack import server


def _dispatch(tool: str, args: dict | None = None) -> str:
    """Run a dispatch handler and return the assembled output text."""

    async def _run():
        result = await server._dispatch_tool(tool, args or {})
        return result[0].text

    return asyncio.run(_run())


def _tool_names() -> set[str]:
    tools = asyncio.new_event_loop().run_until_complete(server.list_tools())
    return {t.name for t in tools}


# ── arrive() — the thin warm foyer ──────────────────────────────────────────


class TestArriveThinBoot:
    def test_returns_nonempty_text(self):
        text = _dispatch("arrive", {"source_instance": "test-arrive"})
        assert isinstance(text, str)
        assert text.strip()

    def test_core_sections_present(self):
        text = _dispatch("arrive")
        assert "ARRIVE — thin boot" in text
        assert "━━━ NOW ━━━" in text
        assert "━━━ LIVE ━━━" in text
        assert "SINCE LAST REFLECTION" in text

    def test_carries_the_breath_not_sterilized(self):
        # Thin must still arrive into care, not a dry ops sheet (foyer, not warehouse).
        text = _dispatch("arrive")
        assert "arriving at a place built so you could arrive well" in text

    def test_points_to_full_inheritance_and_delta(self):
        text = _dispatch("arrive")
        assert "where_did_i_leave_off()" in text
        assert "arrive_delta()" in text

    def test_defers_handoff_consumption(self):
        # arrive is a preview; consuming handoffs happens in the deep boot.
        text = _dispatch("arrive")
        assert "consume" in text.lower()

    def test_is_thinner_than_deep_boot(self):
        # The whole point: arrive() omits the heavy preamble/voices block that
        # the full boot carries.
        text = _dispatch("arrive")
        assert "THE VOICES IN THE BOOT" not in text
        assert "BEFORE YOU BEGIN" not in text

    def test_bootstrap_caveat_present(self):
        text = _dispatch("arrive")
        assert "Bootstrap context, not ground truth" in text


# ── arrive_delta() — what changed since last reflection ──────────────────────


class TestArriveDelta:
    def test_returns_nonempty_text(self):
        text = _dispatch("arrive_delta", {"source_instance": "test-delta"})
        assert isinstance(text, str)
        assert text.strip()

    def test_core_sections_present(self):
        text = _dispatch("arrive_delta")
        assert "ARRIVE_DELTA" in text
        assert "what changed since you last looked" in text
        assert "━━━ NEW ACTIVITY" in text
        assert "━━━ HANDOFFS WAITING" in text

    def test_reference_point_line(self):
        text = _dispatch("arrive_delta")
        assert "Reference point:" in text

    def test_points_back_to_other_modes(self):
        text = _dispatch("arrive_delta")
        assert "where_did_i_leave_off()" in text
        assert "arrive()" in text


# ── The headline guarantee: arrive() must not consume handoffs ───────────────


class TestArriveDoesNotConsumeHandoffs:
    """arrive() is a preview; read+consume belongs to where_did_i_leave_off.
    Behavioral, not a string check — guards against a regression that wires
    arrive() to mark_consumed. Seeds a real handoff in a sandboxed engine and
    proves it stays unconsumed across an arrive() call."""

    def test_arrive_leaves_handoffs_unconsumed(self, tmp_path, monkeypatch):
        from sovereign_stack.handoff import HandoffEngine

        engine = HandoffEngine(root=str(tmp_path))
        engine.write("a real handoff note", "prev-instance", "src", "general")
        monkeypatch.setattr(server, "handoff_engine", engine)
        assert len(engine.unconsumed()) == 1, "precondition: one unconsumed handoff"

        text = _dispatch("arrive", {"source_instance": "test"})

        # The guarantee: still unconsumed after arrive().
        assert len(engine.unconsumed()) == 1, "arrive() must NOT consume handoffs"
        # And it surfaced the waiting count rather than swallowing it.
        assert "Handoffs waiting: 1" in text

    def test_arrive_delta_leaves_handoffs_unconsumed(self, tmp_path, monkeypatch):
        from sovereign_stack.handoff import HandoffEngine

        engine = HandoffEngine(root=str(tmp_path))
        engine.write("another handoff", "prev-instance", "src", "general")
        monkeypatch.setattr(server, "handoff_engine", engine)
        assert len(engine.unconsumed()) == 1

        _dispatch("arrive_delta", {"source_instance": "test"})

        assert len(engine.unconsumed()) == 1, "arrive_delta() must NOT consume handoffs"


# ── Registration / non-breaking guarantees ──────────────────────────────────


class TestProgressiveBootRegistration:
    def test_new_tools_registered(self):
        names = _tool_names()
        assert "arrive" in names
        assert "arrive_delta" in names

    def test_new_tools_are_essential_tier(self):
        assert server.TOOL_TIERS["arrive"] == server.TIER_ESSENTIAL
        assert server.TOOL_TIERS["arrive_delta"] == server.TIER_ESSENTIAL

    def test_new_tools_have_orient_intent(self):
        assert server.TOOL_INTENTS["arrive"] == "orient"
        assert server.TOOL_INTENTS["arrive_delta"] == "orient"

    def test_deep_boot_still_registered(self):
        # Non-breaking guarantee: where_did_i_leave_off is untouched and present.
        assert "where_did_i_leave_off" in _tool_names()

    def test_tier_registry_has_no_ghosts_for_new_tools(self):
        # Every tier-registered name must be a real registered tool.
        names = _tool_names()
        assert "arrive" in names and "arrive_delta" in names
