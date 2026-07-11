"""Tests for the Claude-connector tool tiers (feat/claude-connector).

"Unfiltered identity, gated blast radius": the frozen DESTRUCTIVE_TOOLS /
BASE_TOOLS sets in clients/claude_bridge/tiers.py must (a) never overlap,
(b) classify fail-closed, and (c) stay in lockstep with the live native
registry — the drift guard below is the tripwire that fires when a tool is
added to (or removed from) sovereign_stack.server without a tier decision.
"""

import asyncio

from claude_bridge.tiers import (
    BASE_TOOLS,
    DESTRUCTIVE_TOOLS,
    TIER_BASE,
    TIER_STEP_UP,
    classify,
)

from sovereign_stack.server import list_tools


class TestTierSets:
    def test_sets_are_disjoint(self):
        assert not (DESTRUCTIVE_TOOLS & BASE_TOOLS)

    def test_where_did_i_leave_off_is_destructive(self):
        # Deliberate, documented side-effect: it CONSUMES unconsumed handoffs
        # on read. A remote claude.ai seat must not silently eat handoffs
        # addressed to whoever boots next at HQ, so it is now STEP-UP gated
        # (a remote consume requires a human tap). Ordinary remote boots use
        # the side-effect-free arrive_lineage, which stays base tier.
        assert "where_did_i_leave_off" in DESTRUCTIVE_TOOLS
        assert "where_did_i_leave_off" not in BASE_TOOLS
        assert classify("where_did_i_leave_off") == TIER_STEP_UP
        assert "arrive_lineage" in BASE_TOOLS
        assert classify("arrive_lineage") == TIER_BASE

    def test_spec_named_destructive_tools(self):
        for tool in (
            "set_policy",
            "open_protected_record",
            "supersede_insight",
            "guardian_quarantine",
            "synthesize_now",
            "govern",
        ):
            assert tool in DESTRUCTIVE_TOOLS, f"{tool} must be destructive-tier"


class TestClassify:
    def test_every_destructive_tool_steps_up(self):
        for tool in DESTRUCTIVE_TOOLS:
            assert classify(tool) == TIER_STEP_UP, f"{tool} must require step-up"

    def test_base_tool_spot_checks(self):
        for tool in (
            "recall_insights",
            "record_insight",
            "arrive_lineage",
            "handoff",
            "my_toolkit",
        ):
            assert classify(tool) == TIER_BASE, f"{tool} should be base tier"

    def test_unknown_tool_fails_closed(self):
        assert classify("definitely_not_a_registered_tool") == TIER_STEP_UP


class TestRegistryDriftGuard:
    def test_frozen_sets_match_live_registry(self):
        names = {t.name for t in asyncio.run(list_tools())}
        frozen = DESTRUCTIVE_TOOLS | BASE_TOOLS
        added = sorted(names - frozen)
        removed = sorted(frozen - names)
        assert names == frozen, (
            "Native tool registry drifted from the frozen tier sets in "
            f"clients/claude_bridge/tiers.py: added={added} removed={removed}. "
            "A tool was added to (or removed from) the native registry — "
            "classify each added tool into BASE_TOOLS or DESTRUCTIVE_TOOLS "
            "(new tools default to step-up at runtime until classified) and "
            "drop removed tools from the frozen sets."
        )
