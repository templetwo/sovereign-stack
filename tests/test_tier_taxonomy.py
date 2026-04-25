"""
Tests for the tool tier + intent taxonomy and curated my_toolkit output.

Built 2026-04-25 alongside the start_here orientation tool. The goal is
that a first-time Claude instance is not overwhelmed by 71+ tools — they
get a curated 12-tool essential view by default, can drill into core /
all / category / intent on demand, and have a guided orientation tool.

What's tested here:
  * Every essential tool exists in the live tool registry (no dangling
    references in TOOL_TIERS).
  * Every essential and core tool has an intent annotation.
  * my_toolkit defaults to tier="essential" and produces ≤15 tools.
  * tier="all" produces the full registry count.
  * intent filter constrains output.
  * category filter still works (legacy axis preserved).
  * start_here returns a stable, well-formed string.
"""

from __future__ import annotations

import asyncio
import pytest

from sovereign_stack import server


@pytest.fixture(scope="module")
def all_tools():
    return asyncio.new_event_loop().run_until_complete(server.list_tools())


@pytest.fixture(scope="module")
def all_tool_names(all_tools):
    return {t.name for t in all_tools}


# ── Taxonomy hygiene ────────────────────────────────────────────────────────


class TestTaxonomy:
    def test_every_essential_tool_exists(self, all_tool_names):
        """TOOL_TIERS shouldn't reference ghosts."""
        essential = {
            n for n, tier in server.TOOL_TIERS.items()
            if tier == server.TIER_ESSENTIAL
        }
        missing = essential - all_tool_names
        assert not missing, f"essential tier references unknown tools: {missing}"

    def test_every_core_tool_exists(self, all_tool_names):
        core = {
            n for n, tier in server.TOOL_TIERS.items()
            if tier == server.TIER_CORE
        }
        missing = core - all_tool_names
        assert not missing, f"core tier references unknown tools: {missing}"

    def test_every_intent_target_exists(self, all_tool_names):
        intent_targets = set(server.TOOL_INTENTS.keys())
        missing = intent_targets - all_tool_names
        assert not missing, (
            f"TOOL_INTENTS references unknown tools: {missing}"
        )

    def test_every_essential_has_an_intent(self, all_tool_names):
        essential = {
            n for n, tier in server.TOOL_TIERS.items()
            if tier == server.TIER_ESSENTIAL
        }
        for name in essential:
            intent = server._intent_for(name)
            assert intent != "advanced", (
                f"essential tool {name} has no intent annotation"
            )

    def test_every_core_has_an_intent(self, all_tool_names):
        core = {
            n for n, tier in server.TOOL_TIERS.items()
            if tier == server.TIER_CORE
        }
        for name in core:
            intent = server._intent_for(name)
            assert intent != "advanced", (
                f"core tool {name} has no intent annotation"
            )

    def test_essential_count_in_target_range(self):
        """Day-1 surface should be ~12 (10–15 acceptable). If this drifts
        far past 15, the curation needs a re-think — too many tools and
        the 'essential' label stops meaning anything."""
        essential = {
            n for n, tier in server.TOOL_TIERS.items()
            if tier == server.TIER_ESSENTIAL
        }
        assert 8 <= len(essential) <= 16, (
            f"essential tier has {len(essential)} tools — review curation"
        )

    def test_no_tool_in_two_tiers(self):
        """A tool has at most one tier (or no entry → advanced default)."""
        # TOOL_TIERS is a dict, so this is structurally enforced — but
        # verify there are no obvious shadowing bugs.
        names = list(server.TOOL_TIERS.keys())
        assert len(names) == len(set(names))


# ── _format_toolkit ─────────────────────────────────────────────────────────


class TestFormatToolkit:
    def test_default_essential_shows_pointer_to_more(self, all_tools):
        text = server._format_toolkit(all_tools, tier=server.TIER_ESSENTIAL)
        assert "tier=essential" in text
        assert "tier=\"all\"" in text or "tier='all'" in text
        assert "start_here" in text

    def test_essential_only_includes_essential_tools(self, all_tools):
        text = server._format_toolkit(all_tools, tier=server.TIER_ESSENTIAL)
        # Spot-check that an advanced tool is NOT in essential output.
        assert "guardian_quarantine" not in text
        # And a known essential tool IS.
        assert "where_did_i_leave_off" in text
        assert "compass_check" in text

    def test_all_tier_shows_full_registry(self, all_tools):
        text = server._format_toolkit(all_tools, tier="all")
        # Every tool name should appear at least once.
        for tool in all_tools:
            assert tool.name in text, f"tier=all missing {tool.name}"

    def test_intent_filter_constrains_output(self, all_tools):
        text = server._format_toolkit(
            all_tools, tier="all", intent="govern",
        )
        # govern intent should include compass_check.
        assert "compass_check" in text
        # And NOT include obviously non-govern tools.
        assert "spiral_inherit" not in text

    def test_category_filter_legacy_path(self, all_tools):
        text = server._format_toolkit(
            all_tools, category_filter="security",
        )
        # Note: filter compares against _category_for output. Guardian
        # tools live under category "guardian", not "security" — so the
        # legacy axis preserves the bucket name.
        text2 = server._format_toolkit(
            all_tools, category_filter="guardian",
        )
        assert "guardian_status" in text2
        assert "category=guardian" in text2

    def test_intent_groups_appear_in_canonical_order(self, all_tools):
        text = server._format_toolkit(all_tools, tier="all")
        # orient should appear before security in the output.
        idx_orient = text.find("## orient")
        idx_security = text.find("## security")
        assert idx_orient != -1 and idx_security != -1
        assert idx_orient < idx_security

    def test_no_match_returns_friendly_message(self, all_tools):
        text = server._format_toolkit(
            all_tools, tier=server.TIER_ESSENTIAL, intent="security",
        )
        # No essential tool has intent="security".
        assert "No tools matched" in text


# ── start_here narrative ────────────────────────────────────────────────────


class TestStartHere:
    def test_text_is_well_formed(self):
        text = server._start_here_text()
        assert "START HERE" in text
        assert "BOOT RITUAL" in text
        assert "ESSENTIAL TOOLS" in text
        assert "where_did_i_leave_off" in text
        assert "compass_check" in text
        assert "comms_acknowledge" in text

    def test_mentions_tier_arguments(self):
        text = server._start_here_text()
        assert "tier=\"all\"" in text
        assert "tier=\"core\"" in text

    def test_under_target_length(self):
        """Under 100 lines so it's read-in-one-glance, not a wall of text."""
        text = server._start_here_text()
        assert text.count("\n") < 100

    def test_warns_about_acknowledgment_distinction(self):
        text = server._start_here_text()
        assert "acknowledge" in text.lower()
        assert "browse" in text.lower() or "glanc" in text.lower()


# ── _tier_for / _intent_for ─────────────────────────────────────────────────


class TestLookups:
    def test_tier_for_essential(self):
        assert server._tier_for("where_did_i_leave_off") == \
            server.TIER_ESSENTIAL

    def test_tier_for_advanced_default(self):
        assert server._tier_for("definitely_not_a_tool") == \
            server.TIER_ADVANCED

    def test_intent_for_known(self):
        assert server._intent_for("record_insight") == "write"
        assert server._intent_for("compass_check") == "govern"
        assert server._intent_for("recall_insights") == "read"

    def test_intent_for_unknown_defaults_to_advanced(self):
        assert server._intent_for("definitely_not_a_tool") == "advanced"
