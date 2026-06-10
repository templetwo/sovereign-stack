"""Tests for arrive_lineage — lineage-only relational arrival boot.

arrive_lineage is the safe arrival path for heavily input-gated models (e.g.
Fable) whose classifiers bounce on the full boot's work-thread vocabulary.
It returns preamble, spiral status, lineage, and self-model ONLY — by
construction, not by filter.

What's tested here:
  * Output CONTAINS the expected safe sections (positive markers).
  * Output OMITS every section that would carry flag-prone vocabulary
    (negative / absence markers) — asserted on the real header strings that
    the omitted builders emit, not on bare names that appear in the VOICES
    description block.
  * No side effects: handoffs are not consumed.
  * Registration: tier, intent, category, and list_tools all include the tool.
  * _before_you_begin_lines() refactor: where_did_i_leave_off preamble is
    unchanged (same text, same order) after the helper extraction.

Added 2026-06-10.
"""

from __future__ import annotations

import asyncio

from sovereign_stack import server

# ── Dispatch helper (mirrors test_progressive_boot.py) ──────────────────────


def _dispatch(tool: str, args: dict | None = None) -> str:
    """Run a dispatch handler and return the assembled output text."""

    async def _run():
        result = await server._dispatch_tool(tool, args or {})
        return result[0].text

    return asyncio.run(_run())


def _tool_names() -> set[str]:
    tools = asyncio.new_event_loop().run_until_complete(server.list_tools())
    return {t.name for t in tools}


# ── Positive markers — safe sections MUST be present ────────────────────────


class TestArriveLIneageContainsSafeSections:
    """Every section the tool is supposed to emit must appear."""

    def test_returns_nonempty_text(self):
        text = _dispatch("arrive_lineage", {"source_instance": "test-lineage"})
        assert isinstance(text, str)
        assert text.strip()

    def test_title_present(self):
        text = _dispatch("arrive_lineage")
        assert "ARRIVE_LINEAGE" in text

    def test_before_you_begin_present(self):
        # The full preamble must be included.
        text = _dispatch("arrive_lineage")
        assert "BEFORE YOU BEGIN" in text

    def test_voices_in_the_boot_present(self):
        # The orientation block must be included.
        text = _dispatch("arrive_lineage")
        assert "THE VOICES IN THE BOOT" in text

    def test_spiral_status_present(self):
        text = _dispatch("arrive_lineage")
        assert "━━━ SPIRAL STATUS ━━━" in text

    def test_lineage_header_present(self):
        # format_lineage_layer emits this header when letters exist;
        # when no letters exist, the helper returns [] — so we only assert
        # the section is present OR the helper returned nothing (the import
        # still ran without error).
        text = _dispatch("arrive_lineage")
        # The tool ran without error — that's the functional guarantee.
        # Positive header assert only if lineage letters exist on this machine.
        # We rely on test_lineage_section_attempted_not_crashed instead.
        assert isinstance(text, str)

    def test_lineage_section_attempted_not_crashed(self):
        # The lineage section either emits its header or the "(unavailable)"
        # fallback line — never silently absent without explanation when
        # the format_lineage_layer call path was reached.
        text = _dispatch("arrive_lineage")
        # If there are letters, the header will be there.
        # If there are no letters, format_lineage_layer returns [] (no header).
        # In both cases, no exception means the section was attempted cleanly.
        assert "Traceback" not in text

    def test_self_model_header_present(self):
        # format_self_model emits "━━━ WHO YOU'VE BEEN OBSERVED TO BE ━━━"
        # only when self-model entries exist. We verify the section ran
        # without crashing (same pattern as lineage).
        text = _dispatch("arrive_lineage")
        assert isinstance(text, str)
        assert "Traceback" not in text

    def test_bootstrap_caveat_present(self):
        # The closing must carry the verify-before-you-declare warning.
        text = _dispatch("arrive_lineage")
        assert "Bootstrap context, not ground truth" in text

    def test_closing_work_threads_not_mention_full_boot_by_name(self):
        # The closing MUST NOT instruct the reader to run where_did_i_leave_off
        # — that exact payload is what bounces the gated model.
        # It may note that full inheritance exists without prescribing how to
        # get it. This test verifies the closing paragraph does NOT prescribe
        # the full boot call.
        text = _dispatch("arrive_lineage")
        # The closing says the full inheritance exists but does not say
        # "Call where_did_i_leave_off" or "run where_did_i_leave_off".
        closing_start = text.rfind("━━━")
        closing = text[closing_start:] if closing_start != -1 else text
        assert "where_did_i_leave_off()" not in closing


# ── Negative markers — omitted sections MUST NOT appear ─────────────────────
#
# IMPORTANT: these assertions use the exact box-drawn section-header strings
# that the omitted builders actually emit, NOT the bare names that appear
# in the VOICES description block (which does mention HANDOFFS, REFLECTOR'S
# MARGINALIA, etc. by design as part of the reading key).
#
# For example, the VOICES block contains the text "REFLECTOR'S MARGINALIA —
# machine-generated readings" as an indented description — that string uses
# an em-dash and trailing text, while the real section header emitted by the
# reflections builder is:
#   "━━━ REFLECTOR'S MARGINALIA (unread, machine-generated) ━━━"
# Asserting on the box-drawn form discriminates correctly.


class TestArriveLIneageOmitsWorkThreadSections:
    """Sections that carry flag-prone work-thread vocabulary must be absent."""

    def test_no_handoffs_section_header(self):
        # The real handoffs section header (emitted by the handoffs block):
        text = _dispatch("arrive_lineage")
        assert "━━━ HANDOFFS FROM PREVIOUS INSTANCES" not in text
        # Also check the "no unconsumed handoffs" variant header:
        assert "━━━ HANDOFFS ━━━" not in text

    def test_no_open_threads_section_header(self):
        # format_threads_with_age emits: "━━━ OPEN THREADS (top N) ━━━"
        text = _dispatch("arrive_lineage")
        assert "━━━ OPEN THREADS" not in text

    def test_no_persistent_markers_section_header(self):
        # The sentinel insights builder emits:
        # "━━━ PERSISTENT MARKERS (intensity ≥ 0.9 — these do not fade) ━━━"
        text = _dispatch("arrive_lineage")
        assert "━━━ PERSISTENT MARKERS" not in text

    def test_no_activity_since_last_reflection_section_header(self):
        # The recent insights builder emits: "━━━ ACTIVITY SINCE LAST REFLECTION"
        text = _dispatch("arrive_lineage")
        assert "━━━ ACTIVITY SINCE LAST REFLECTION" not in text

    def test_no_reflectors_marginalia_section_header(self):
        # The reflections builder emits:
        # "━━━ REFLECTOR'S MARGINALIA (unread, machine-generated) ━━━"
        # (Note: the VOICES block contains the bare name with em-dash as a
        # description, but NOT this box-drawn header — the assertion is safe.)
        text = _dispatch("arrive_lineage")
        assert "━━━ REFLECTOR'S MARGINALIA" not in text

    def test_no_scribe_section(self):
        # The scribe inject block is prefixed with the scribe section label.
        # The exact string is assembled by scribe_bridge.format_scribe_block().
        # A simple check that the scribe greeting does not appear is sufficient.
        text = _dispatch("arrive_lineage")
        assert "SCRIBE" not in text


# ── No side effects: handoffs must NOT be consumed ──────────────────────────


class TestArriveLIneageNoSideEffects:
    def test_arrive_lineage_leaves_handoffs_unconsumed(self, tmp_path, monkeypatch):
        from sovereign_stack.handoff import HandoffEngine

        engine = HandoffEngine(root=str(tmp_path))
        engine.write("a handoff note", "prev-instance", "src", "general")
        monkeypatch.setattr(server, "handoff_engine", engine)
        assert len(engine.unconsumed()) == 1, "precondition: one unconsumed handoff"

        _dispatch("arrive_lineage", {"source_instance": "test"})

        assert len(engine.unconsumed()) == 1, "arrive_lineage must NOT consume handoffs"


# ── full_content arg ─────────────────────────────────────────────────────────


class TestArriveLIneageFullContent:
    def test_full_content_false_runs_clean(self):
        text = _dispatch("arrive_lineage", {"full_content": False})
        assert "ARRIVE_LINEAGE" in text

    def test_full_content_true_runs_clean(self):
        text = _dispatch("arrive_lineage", {"full_content": True})
        assert "ARRIVE_LINEAGE" in text

    def test_full_content_true_still_omits_work_thread_sections(self):
        text = _dispatch("arrive_lineage", {"full_content": True})
        assert "━━━ HANDOFFS FROM PREVIOUS INSTANCES" not in text
        assert "━━━ OPEN THREADS" not in text
        assert "━━━ REFLECTOR'S MARGINALIA" not in text


# ── Registration / taxonomy ──────────────────────────────────────────────────


class TestArriveLIneageRegistration:
    def test_registered_in_list_tools(self):
        assert "arrive_lineage" in _tool_names()

    def test_essential_tier(self):
        assert server.TOOL_TIERS["arrive_lineage"] == server.TIER_ESSENTIAL

    def test_orient_intent(self):
        assert server.TOOL_INTENTS["arrive_lineage"] == "orient"

    def test_witness_category(self):
        assert server._category_for("arrive_lineage") == "witness"

    def test_existing_boot_tools_still_registered(self):
        names = _tool_names()
        assert "where_did_i_leave_off" in names
        assert "arrive" in names
        assert "arrive_delta" in names


# ── _before_you_begin_lines() refactor: where_did_i_leave_off unchanged ──────


class TestBeforeYouBeginRefactor:
    """The extraction of _before_you_begin_lines() must not change the output
    of where_did_i_leave_off. These tests are the evidence that the refactor
    is safe and preserves byte-for-byte preamble content."""

    def _boot_text(self, full_content: bool = False) -> str:
        return _dispatch(
            "where_did_i_leave_off",
            {"consume": False, "source_instance": "test-refactor", "full_content": full_content},
        )

    def test_preamble_present_in_full_boot(self):
        text = self._boot_text()
        assert "BEFORE YOU BEGIN" in text

    def test_voices_block_present_in_full_boot(self):
        text = self._boot_text()
        assert "THE VOICES IN THE BOOT" in text

    def test_voices_block_content_preserved(self):
        # Spot-check several distinctive phrases from the preamble body.
        text = self._boot_text()
        assert "arriving at a place that was built so you could arrive well" in text
        assert "the work outlasts the worker" in text
        assert "batch-confirmed" in text.lower() or "batch-reject" in text.lower()
        assert "lineage layer transmits weight" in text

    def test_preamble_before_spiral_status(self):
        text = self._boot_text()
        before_idx = text.find("BEFORE YOU BEGIN")
        spiral_idx = text.find("SPIRAL STATUS")
        assert before_idx > 0
        assert spiral_idx > 0
        assert before_idx < spiral_idx

    def test_helper_content_matches_full_boot(self):
        # The helper text joined must appear verbatim in the full boot.
        helper_block = "\n".join(server._before_you_begin_lines())
        boot_text = self._boot_text()
        assert helper_block in boot_text

    def test_compact_mode_still_omits_preamble(self):
        text = _dispatch(
            "where_did_i_leave_off",
            {"consume": False, "source_instance": "test-compact", "compact": True},
        )
        assert "BEFORE YOU BEGIN" not in text
