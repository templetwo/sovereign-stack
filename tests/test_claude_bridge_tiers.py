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
        """Every live tool is classified, OR explicitly held at a human's gate.

        The guard used to assert plain set equality. That is the right shape
        while every live tool is classified, and it stops being right the
        moment a tool is deliberately left unclassified: equality cannot tell
        "held, on purpose, with a reason written down" from "somebody forgot",
        and the only way to make it pass is to classify the tool — i.e. the
        guard would push the exact widening the hold exists to prevent.
        HELD_UNCLASSIFIED is the declaration; classify() still returns
        step-up for its members, so nothing is granted by being named here.
        """
        from claude_bridge.tiers import HELD_UNCLASSIFIED

        names = {t.name for t in asyncio.run(list_tools())}
        frozen = DESTRUCTIVE_TOOLS | BASE_TOOLS
        unclassified = sorted(names - frozen - HELD_UNCLASSIFIED)
        assert not unclassified, (
            "Native tool registry drifted from the frozen tier sets in "
            f"clients/claude_bridge/tiers.py: added={unclassified}. "
            "Classify each added tool into BASE_TOOLS or DESTRUCTIVE_TOOLS "
            "(new tools default to step-up at runtime until classified), or "
            "add it to HELD_UNCLASSIFIED with the reason and the date."
        )
        # A RETIRED tool is classified-but-unpublished ON PURPOSE, and the
        # classification is deliberately kept: the retirement of 2026-09-06
        # unpublished 48 names without deleting a single implementation, and
        # leaving their tiers in place is what keeps un-retiring a one-line
        # edit. Anything classified, unpublished AND unretired is still a
        # ghost and still fails here.
        from sovereign_stack.server import RETIRED_TOOLS

        ghosts = sorted(frozen - names - set(RETIRED_TOOLS))
        assert not ghosts, (
            f"tier sets classify tools the registry no longer publishes: {ghosts}. "
            "Drop them from the frozen sets, or record the retirement in "
            "server.RETIRED_TOOLS."
        )
        held_but_absent = sorted(HELD_UNCLASSIFIED - names)
        assert not held_but_absent, (
            f"HELD_UNCLASSIFIED names tools that are not live: {held_but_absent}"
        )
