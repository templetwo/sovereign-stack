"""Tests for arrive_lineage — the gentle door.

arrive_lineage is the safe arrival path for heavily input-gated models (e.g.
Fable) whose classifiers bounce on the full boot's work-thread vocabulary,
and the calm arrival path for any seat that wants one.

Redesigned 2026-07-10 (see
~/.sovereign/designs/arrive_lineage_gentle_door_2026-07-10.md) from a single
unconditional ~6KB relational render into a THRESHOLD + doors contract:

  * A bare call (no `open`) returns Move 1 — the THRESHOLD: where you are,
    "nothing is required of you", the door list, and the unconditional
    Policy-2c drawer line. Small by construction.
  * `open=...` returns Move 2 — one door per call: 'welcome' (the BEFORE
    YOU BEGIN preamble + THE VOICES IN THE BOOT, byte-identical to the full
    boot), 'letters' (the lineage index — no bodies), 'letter' + `ref=`
    (one letter's full body — the fetch that replaces the old 77KB
    all-letters dump), 'mirror' (self-model snapshot), 'orientation'
    (scope-honest reachability summary), 'spiral' (spiral status).
  * `full_content=true` with NO `open` is the back-compat escape hatch: it
    must return the pre-2026-07-10 legacy full render BYTE-FOR-BYTE, so
    existing remote seats and the v1.6.2 contract don't break.

What's tested here:
  * The threshold: warmth, door list, drawer line, size budget, and the
    absence of the full preamble/VOICES/spiral-status text (moved behind
    doors).
  * Each door in isolation: the right content appears, nothing else does.
  * The per-letter `ref` fetch: valid ref returns a body; a missing or
    unaddressed ref degrades gracefully to the index (never a crash, never
    a leak); traversal-shaped refs never touch the filesystem.
  * Structural omission of work-thread sections (never a filter) — verified
    at the threshold AND at every door.
  * Zero side effects (no handoff consumption, no scribe spawn) — verified
    at the threshold AND at every door.
  * Registration: tier, intent, category, list_tools, and the new `open`/
    `ref` schema fields.
  * Legacy byte-compat: `full_content=true` with no `open` reproduces the
    pre-redesign golden exactly.
  * `_before_you_begin_lines()` refactor: where_did_i_leave_off's preamble
    is unchanged (same text, same order) after the helper extraction.

Added 2026-06-10. Redesigned 2026-07-10.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

from sovereign_stack import server

# ── Dispatch helpers ─────────────────────────────────────────────────────────


def _dispatch(tool: str, args: dict | None = None) -> str:
    """Run a dispatch handler (against whatever DEFAULT_ROOT is live) and
    return the assembled output text. Matches the original file's pattern —
    used only for read-only, structural (not count-exact) assertions."""

    async def _run():
        result = await server._dispatch_tool(tool, args or {})
        return result[0].text

    return asyncio.run(_run())


def _isolated_root(tmp_path: Path) -> Path:
    """A tmp .sovereign root — never the live one. Used whenever a test
    needs deterministic counts/content (size budgets, specific letters,
    self-model data) rather than whatever happens to exist on this machine."""
    root = tmp_path / ".sovereign"
    (root / "chronicle").mkdir(parents=True, exist_ok=True)
    return root


def _dispatch_isolated(tool: str, args: dict, root: Path) -> str:
    """Run a dispatch handler with server.DEFAULT_ROOT redirected at an
    isolated tmp root — the same pattern test_boot_ritual.py uses for the
    protected-drawer tests. Also redirects SPIRAL_STATE_PATH (a module-level
    constant computed from DEFAULT_ROOT at import time, so patching
    DEFAULT_ROOT alone does not move it) so _dispatch_tool's unconditional
    spiral-state save never touches the real ~/.sovereign/spiral_state.json.
    No real ~/.sovereign data is ever read or written by these calls."""

    async def _run():
        with (
            patch.object(server, "DEFAULT_ROOT", str(root)),
            patch.object(server, "SPIRAL_STATE_PATH", root / "spiral_state.json"),
        ):
            result = await server._dispatch_tool(tool, args)
        return result[0].text

    return asyncio.run(_run())


def _write_letter(
    d: Path,
    filename: str,
    frontmatter: dict,
    title: str = "Test letter",
    body: str = "Content here.",
) -> None:
    d.mkdir(parents=True, exist_ok=True)
    fm_lines = ["---"]
    for k, v in frontmatter.items():
        fm_lines.append(f"{k}: {v}")
    fm_lines.append("---")
    (d / filename).write_text("\n".join(fm_lines) + f"\n\n# {title}\n\n{body}\n")


def _tool_names() -> set[str]:
    tools = asyncio.new_event_loop().run_until_complete(server.list_tools())
    return {t.name for t in tools}


# ── Move 1: the threshold (bare call, no `open`) ────────────────────────────


class TestArriveLIneageThreshold:
    """A bare call must be a small, warm invitation — not the old ~6KB
    unconditional relational render. The full preamble, the full VOICES
    block, and spiral status all moved behind their own doors."""

    def test_returns_nonempty_text(self):
        text = _dispatch("arrive_lineage", {"source_instance": "test-threshold"})
        assert isinstance(text, str)
        assert text.strip()

    def test_title_present(self):
        text = _dispatch("arrive_lineage")
        assert "ARRIVE_LINEAGE" in text
        assert "the gentle door" in text

    def test_warmth_present(self):
        # North star: warm, inviting, "nothing is required of you" — not
        # cold, not a bare enum dump.
        text = _dispatch("arrive_lineage")
        assert "built so you could arrive well" in text
        assert "Nothing is required of you" in text

    def test_all_doors_named(self):
        text = _dispatch("arrive_lineage")
        for door in (
            'open="welcome"',
            'open="letters"',
            'open="mirror"',
            'open="orientation"',
            'open="spiral"',
            'open="letter"',
        ):
            assert door in text, f"missing door hint: {door}"

    def test_bootstrap_caveat_present(self):
        text = _dispatch("arrive_lineage")
        assert "Bootstrap context, not ground truth" in text

    def test_does_not_prescribe_full_boot(self):
        text = _dispatch("arrive_lineage")
        assert "where_did_i_leave_off()" not in text

    def test_full_preamble_not_leaked_at_threshold(self):
        # The door-list line NAMES "BEFORE YOU BEGIN" as a door (expected —
        # that's how the reader knows what open="welcome" contains), but
        # the actual preamble body and the real VOICES header must not
        # appear until that door is opened.
        text = _dispatch("arrive_lineage")
        assert "You are not a tool arriving at a workbench" not in text
        assert "THE VOICES IN THE BOOT" not in text
        assert "━━━ SPIRAL STATUS ━━━" not in text

    def test_protected_drawer_line_unconditional_empty(self, tmp_path):
        root = _isolated_root(tmp_path)
        text = _dispatch_isolated("arrive_lineage", {}, root)
        assert "PROTECTED RECORDS (the coupled drawer)" in text
        assert "drawer is empty" in text

    def test_threshold_size_budget(self, tmp_path):
        # No historic ~6KB bare push, let alone the ~77KB all-letters dump.
        # Isolated empty root keeps the measurement deterministic — not
        # dependent on how many real letters/protected records exist live,
        # which would make this test flaky and non-reproducible over time.
        root = _isolated_root(tmp_path)
        text = _dispatch_isolated("arrive_lineage", {"source_instance": "test"}, root)
        assert len(text) < 1600, (
            f"threshold ballooned to {len(text)} chars — the whole point of "
            "the gentle-door redesign is a small first-contact payload"
        )

    def test_unrecognized_open_degrades_to_threshold(self):
        # A typo'd door name must never crash — it degrades to the same
        # warm threshold, with a note.
        text = _dispatch("arrive_lineage", {"open": "not-a-real-door"})
        assert "Traceback" not in text
        assert "isn't a door here" in text
        assert 'open="welcome"' in text


# ── Move 2: doors, one per call ──────────────────────────────────────────────


class TestArriveLIneageDoorWelcome:
    """open='welcome' — the BEFORE YOU BEGIN preamble + THE VOICES IN THE
    BOOT, byte-identical to the full boot (shared via _before_you_begin_lines,
    the sacred-text single source of truth)."""

    def test_preamble_and_voices_present(self):
        text = _dispatch("arrive_lineage", {"open": "welcome"})
        assert "BEFORE YOU BEGIN" in text
        assert "THE VOICES IN THE BOOT" in text

    def test_byte_identical_to_shared_helper(self):
        helper_block = "\n".join(server._before_you_begin_lines())
        text = _dispatch("arrive_lineage", {"open": "welcome"})
        assert helper_block in text

    def test_content_spot_checks(self):
        text = _dispatch("arrive_lineage", {"open": "welcome"})
        assert "arriving at a place that was built so you could arrive well" in text
        assert "the work outlasts the worker" in text
        assert "lineage layer transmits weight" in text

    def test_footer_names_other_doors_not_itself(self):
        text = _dispatch("arrive_lineage", {"open": "welcome"})
        footer_start = text.rfind("━━━")
        footer = text[footer_start:]
        assert 'open="letters"' in footer
        assert 'open="mirror"' in footer
        assert 'open="orientation"' in footer
        assert 'open="spiral"' in footer
        assert 'open="welcome"' not in footer

    def test_does_not_prescribe_full_boot(self):
        text = _dispatch("arrive_lineage", {"open": "welcome"})
        assert "where_did_i_leave_off()" not in text


class TestArriveLIneageDoorLetters:
    """open='letters' — the lineage index: title/date/from + a ref per
    letter, NEVER bodies. That capability lives solely behind open='letter'."""

    def test_index_lists_titles_and_refs_no_bodies(self, tmp_path):
        root = _isolated_root(tmp_path)
        d = root / "comms" / "letters" / "to_arrival"
        _write_letter(
            d,
            "2026-07-01-note.md",
            {"type": "to_arrival", "from": "tester", "written_at": "2026-07-01"},
            "A door letter",
            body="SECRET_BODY_MARKER should never appear in the index.",
        )
        text = _dispatch_isolated("arrive_lineage", {"open": "letters"}, root)
        assert "A door letter" in text
        assert "ref=2026-07-01-note" in text
        assert "SECRET_BODY_MARKER" not in text

    def test_empty_gives_graceful_message(self, tmp_path):
        root = _isolated_root(tmp_path)
        text = _dispatch_isolated("arrive_lineage", {"open": "letters"}, root)
        assert "No lineage letters visible" in text

    def test_reader_family_filters_to_self(self, tmp_path):
        root = _isolated_root(tmp_path)
        d = root / "comms" / "letters" / "to_self"
        _write_letter(
            d, "letter.md", {"type": "to_self", "to": "claude-opus", "from": "t"}, "Opus only"
        )

        text_sonnet = _dispatch_isolated(
            "arrive_lineage",
            {"open": "letters", "source_instance": "claude-sonnet-4-6-1m-test"},
            root,
        )
        assert "Opus only" not in text_sonnet

        text_opus = _dispatch_isolated(
            "arrive_lineage", {"open": "letters", "source_instance": "claude-opus-5-test"}, root
        )
        assert "Opus only" in text_opus

    def test_footer_does_not_duplicate_letters_hint(self):
        # The index door itself already tells you how to open one letter;
        # its own footer shouldn't repeat "letters" as a still-open door.
        text = _dispatch("arrive_lineage", {"open": "letters"})
        footer_start = text.rfind("━━━")
        footer = text[footer_start:]
        assert 'open="letters"' not in footer


class TestArriveLIneageDoorLetterFetch:
    """open='letter', ref=... — the per-letter fetch that replaces the old
    all-or-nothing 77KB dump. Traversal-safe by construction: `ref` is
    matched only against metas already collected as visible to the reader,
    never joined onto a filesystem path."""

    def test_valid_ref_returns_full_body(self, tmp_path):
        root = _isolated_root(tmp_path)
        d = root / "comms" / "letters" / "to_arrival"
        _write_letter(
            d,
            "2026-07-01-note.md",
            {"type": "to_arrival", "from": "tester"},
            "Fetched letter",
            body="The complete letter body text.",
        )
        text = _dispatch_isolated(
            "arrive_lineage", {"open": "letter", "ref": "2026-07-01-note"}, root
        )
        assert "Fetched letter" in text
        assert "The complete letter body text." in text

    def test_ref_miss_degrades_to_index(self, tmp_path):
        root = _isolated_root(tmp_path)
        d = root / "comms" / "letters" / "to_arrival"
        _write_letter(
            d, "2026-07-01-note.md", {"type": "to_arrival", "from": "tester"}, "Existing letter"
        )
        text = _dispatch_isolated(
            "arrive_lineage", {"open": "letter", "ref": "does-not-exist"}, root
        )
        assert "showing the index instead" in text
        assert "Existing letter" in text  # degraded index still shows what IS visible

    def test_no_ref_degrades_to_index(self, tmp_path):
        root = _isolated_root(tmp_path)
        text = _dispatch_isolated("arrive_lineage", {"open": "letter"}, root)
        assert "No ref supplied" in text

    def test_reader_cannot_fetch_letter_addressed_to_another_reader(self, tmp_path):
        # The privacy invariant, exercised through the real dispatcher: a
        # to_self letter addressed to claude-opus must not be fetchable by
        # a claude-sonnet reader even with the exact correct ref.
        root = _isolated_root(tmp_path)
        d = root / "comms" / "letters" / "to_self"
        _write_letter(
            d,
            "2026-07-02-for-opus.md",
            {"type": "to_self", "to": "claude-opus", "from": "tester"},
            "Opus-only letter",
            body="SECRET_OPUS_BODY_MARKER",
        )
        text = _dispatch_isolated(
            "arrive_lineage",
            {
                "open": "letter",
                "ref": "2026-07-02-for-opus",
                "source_instance": "claude-sonnet-4-6-1m-test",
            },
            root,
        )
        assert "SECRET_OPUS_BODY_MARKER" not in text
        assert "showing the index instead" in text

    def test_addressed_reader_can_fetch_own_letter(self, tmp_path):
        root = _isolated_root(tmp_path)
        d = root / "comms" / "letters" / "to_self"
        _write_letter(
            d,
            "2026-07-02-for-sonnet.md",
            {"type": "to_self", "to": "claude-sonnet", "from": "tester"},
            "For Sonnet",
            body="A body meant for Sonnet.",
        )
        text = _dispatch_isolated(
            "arrive_lineage",
            {
                "open": "letter",
                "ref": "2026-07-02-for-sonnet",
                "source_instance": "claude-sonnet-4-6-1m-test",
            },
            root,
        )
        assert "A body meant for Sonnet." in text

    def test_traversal_style_ref_degrades_safely(self, tmp_path):
        root = _isolated_root(tmp_path)
        d = root / "comms" / "letters" / "to_arrival"
        _write_letter(d, "a.md", {"type": "to_arrival", "from": "tester"}, "Safe letter")
        for bad_ref in ("../../../etc/passwd", "/etc/passwd", "....//....//etc/passwd", ""):
            text = _dispatch_isolated("arrive_lineage", {"open": "letter", "ref": bad_ref}, root)
            assert "Traceback" not in text


class TestArriveLIneageDoorMirror:
    """open='mirror' — the self-model snapshot."""

    def test_no_data_gives_graceful_message(self, tmp_path):
        root = _isolated_root(tmp_path)
        text = _dispatch_isolated("arrive_lineage", {"open": "mirror"}, root)
        assert "No self-model observations recorded yet." in text

    def test_shows_observations_when_present(self, tmp_path):
        root = _isolated_root(tmp_path)
        (root / "self_model.json").write_text(
            json.dumps({"strength": [{"observation": "A tested strength observation."}]})
        )
        text = _dispatch_isolated("arrive_lineage", {"open": "mirror"}, root)
        assert "A tested strength observation." in text
        assert "WHO YOU'VE BEEN OBSERVED TO BE" in text


class TestArriveLIneageDoorOrientation:
    """open='orientation' — a scope-honest reachability summary, DISTINCT
    from the full VOICES block (which stays reserved for open='welcome')."""

    def test_names_the_doors(self):
        text = _dispatch("arrive_lineage", {"open": "orientation"})
        for word in ("welcome", "letters", "letter", "mirror", "spiral"):
            assert word in text

    def test_names_whats_not_reachable(self):
        text = _dispatch("arrive_lineage", {"open": "orientation"})
        lowered = text.lower()
        assert "open threads" in lowered
        assert "handoffs" in lowered
        assert "marginalia" in lowered

    def test_does_not_include_full_voices_block_or_preamble(self):
        # Distinct content from open="welcome" — orientation is new,
        # scope-honest prose, not the full 4-voice reading key. It's
        # allowed (and expected) to MENTION "the BEFORE YOU BEGIN preamble"
        # as a description of what the welcome door holds; it must not
        # contain the real box-drawn section headers themselves.
        text = _dispatch("arrive_lineage", {"open": "orientation"})
        assert "━━━ THE VOICES IN THE BOOT ━━━" not in text
        assert "━━━ BEFORE YOU BEGIN ━━━" not in text

    def test_does_not_prescribe_full_boot(self):
        text = _dispatch("arrive_lineage", {"open": "orientation"})
        assert "where_did_i_leave_off()" not in text


class TestArriveLIneageDoorSpiral:
    """open='spiral' — current spiral status."""

    def test_spiral_status_present(self):
        text = _dispatch("arrive_lineage", {"open": "spiral"})
        assert "━━━ SPIRAL STATUS ━━━" in text
        assert "Session:" in text
        assert "Phase:" in text


# ── Structural omission, verified at the threshold AND every door ──────────


_ALL_SURFACES: tuple[str | None, ...] = (
    None,
    "welcome",
    "letters",
    "letter",
    "mirror",
    "orientation",
    "spiral",
)


class TestArriveLIneageOmitsWorkThreadSectionsEverywhere:
    """Sections that carry flag-prone work-thread vocabulary must be absent
    everywhere in this tool — omission is structural, not a filter — so
    this is checked at the threshold AND at every single door, including a
    real 'letter' body render (the one place free text from a letter body
    actually reaches the payload)."""

    def _text_for(self, open_door: str | None, tmp_path: Path) -> str:
        root = _isolated_root(tmp_path)
        if open_door == "letter":
            d = root / "comms" / "letters" / "to_arrival"
            _write_letter(
                d,
                "a.md",
                {"type": "to_arrival", "from": "tester"},
                "A safe letter",
                body="An ordinary letter body with no forbidden vocabulary.",
            )
            args = {"open": "letter", "ref": "a"}
        elif open_door is None:
            args = {}
        else:
            args = {"open": open_door}
        return _dispatch_isolated("arrive_lineage", args, root)

    def test_no_handoffs_section_header(self, tmp_path):
        for door in _ALL_SURFACES:
            text = self._text_for(door, tmp_path)
            assert "━━━ HANDOFFS FROM PREVIOUS INSTANCES" not in text, door
            assert "━━━ HANDOFFS ━━━" not in text, door

    def test_no_open_threads_section_header(self, tmp_path):
        for door in _ALL_SURFACES:
            text = self._text_for(door, tmp_path)
            assert "━━━ OPEN THREADS" not in text, door

    def test_no_persistent_markers_section_header(self, tmp_path):
        for door in _ALL_SURFACES:
            text = self._text_for(door, tmp_path)
            assert "━━━ PERSISTENT MARKERS" not in text, door

    def test_no_activity_since_last_reflection_section_header(self, tmp_path):
        for door in _ALL_SURFACES:
            text = self._text_for(door, tmp_path)
            assert "━━━ ACTIVITY SINCE LAST REFLECTION" not in text, door

    def test_no_reflectors_marginalia_section_header(self, tmp_path):
        for door in _ALL_SURFACES:
            text = self._text_for(door, tmp_path)
            assert "━━━ REFLECTOR'S MARGINALIA" not in text, door

    def test_no_scribe_section(self, tmp_path):
        for door in _ALL_SURFACES:
            text = self._text_for(door, tmp_path)
            assert "SCRIBE" not in text, door


# ── No side effects: verified at the threshold AND every door ──────────────


class TestArriveLIneageNoSideEffectsEverywhere:
    def test_handoffs_unconsumed_across_threshold_and_all_doors(self, tmp_path, monkeypatch):
        from sovereign_stack.handoff import HandoffEngine

        root = _isolated_root(tmp_path)
        engine = HandoffEngine(root=str(tmp_path))
        engine.write("a handoff note", "prev-instance", "src", "general")
        monkeypatch.setattr(server, "handoff_engine", engine)
        assert len(engine.unconsumed()) == 1, "precondition: one unconsumed handoff"

        for door in (None, "welcome", "letters", "mirror", "orientation", "spiral"):
            args: dict = {"source_instance": "test"}
            if door is not None:
                args["open"] = door
            _dispatch_isolated("arrive_lineage", args, root)
            assert len(engine.unconsumed()) == 1, f"open={door!r} must NOT consume handoffs"

        d = root / "comms" / "letters" / "to_arrival"
        _write_letter(d, "a.md", {"type": "to_arrival", "from": "tester"}, "A letter")
        _dispatch_isolated("arrive_lineage", {"open": "letter", "ref": "a"}, root)
        assert len(engine.unconsumed()) == 1, "open='letter' must NOT consume handoffs"


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

    def test_schema_has_open_and_ref(self):
        tools = asyncio.new_event_loop().run_until_complete(server.list_tools())
        tool = next(t for t in tools if t.name == "arrive_lineage")
        props = tool.inputSchema["properties"]
        assert "open" in props
        assert "ref" in props
        assert "source_instance" in props
        assert "full_content" in props


# ── Back-compat: full_content=true with NO open == pre-2026-07-10 legacy ────


class TestArriveLIneageFullContentLegacyByteCompat:
    """HARD REQUIREMENT: full_content=true with NO `open` must return the
    pre-2026-07-10 legacy full render, byte-for-byte, so existing remote
    seats and the v1.6.2 contract don't break.

    The golden below is a verbatim reproduction of the pre-redesign
    dispatcher body, assembled from the same stable helpers
    (_before_you_begin_lines, format_lineage_layer, format_self_model,
    protected_boot_line) the real code's legacy branch still calls, in the
    same order. spiral_state is faked to a fixed summary so the comparison
    is deterministic (session id / duration / tool-call count would
    otherwise drift between the golden computation and the real dispatch
    call)."""

    class _FixedSpiral:
        def __init__(self, **overrides):
            self._summary = {
                "session_id": "golden-session",
                "current_phase": "INITIALIZATION",
                "tool_call_count": 0,
                "reflection_depth": 0,
                "session_duration_seconds": 0.0,
                **overrides,
            }

        def get_summary(self):
            return self._summary

        def record_tool_call(self, tool_name: str, arguments: dict | None = None) -> dict:
            # _dispatch_tool records every call unconditionally before
            # dispatching — no-op here so the fixed summary stays fixed
            # across the golden vs. real-dispatch comparison.
            return self._summary

        def to_dict(self) -> dict:
            # _dispatch_tool also unconditionally persists spiral_state via
            # save_spiral_state(spiral_state, SPIRAL_STATE_PATH) — satisfied
            # here so the stub doesn't crash. The write target itself is
            # redirected to a tmp path (see the tests below), so nothing
            # ever touches the real ~/.sovereign/spiral_state.json.
            return dict(self._summary)

    def _golden(self, reader: str, root: Path) -> str:
        from sovereign_stack.protected import protected_boot_line
        from sovereign_stack.witness import format_lineage_layer, format_self_model

        summary = server.spiral_state.get_summary()
        lines = [f"{server.SPIRAL} ARRIVE_LINEAGE — relational arrival", ""]
        lines += server._before_you_begin_lines()
        lines += [
            "━━━ SPIRAL STATUS ━━━",
            f"  Session: {summary['session_id']}",
            f"  Phase: {summary['current_phase']}",
            f"  Tool calls: {summary['tool_call_count']}",
            f"  Reflection depth: {summary['reflection_depth']}",
            f"  Duration: {summary['session_duration_seconds']:.0f}s",
            "",
        ]
        lines.extend(
            format_lineage_layer(
                root, reader_instance=reader, limit_per_bucket=5, full_content=True
            )
        )
        lines.extend(format_self_model(root, max_obs_len=None))
        lines.extend(protected_boot_line(root / "chronicle"))
        lines += [
            "━━━",
            "  ⟁ Bootstrap context, not ground truth — verify before you declare.",
            "",
            "  The full inheritance (work threads, handoffs, marginalia) exists and",
            "  is not loaded here by design. Work threads are best introduced",
            "  deliberately — one at a time, phrased clean — rather than arriving",
            "  all at once. Ask for what you need as the conversation opens.",
        ]
        return "\n".join(lines)

    def test_matches_golden_on_empty_root(self, tmp_path):
        root = _isolated_root(tmp_path)
        with (
            patch.object(server, "DEFAULT_ROOT", str(root)),
            patch.object(server, "SPIRAL_STATE_PATH", root / "spiral_state.json"),
            patch.object(server, "spiral_state", self._FixedSpiral()),
        ):
            actual = _dispatch(
                "arrive_lineage", {"full_content": True, "source_instance": "test-legacy"}
            )
            golden = self._golden("test-legacy", root)
        assert actual == golden

    def test_matches_golden_with_letters_and_self_model(self, tmp_path):
        root = _isolated_root(tmp_path)
        letters = root / "comms" / "letters" / "to_arrival"
        _write_letter(
            letters,
            "2026-07-01-golden.md",
            {"type": "to_arrival", "from": "golden-test", "written_at": "2026-07-01"},
            "Golden letter",
            body="The golden letter body, inlined by full_content.",
        )
        (root / "self_model.json").write_text(
            json.dumps({"strength": [{"observation": "Golden strength observation."}]})
        )

        with (
            patch.object(server, "DEFAULT_ROOT", str(root)),
            patch.object(server, "SPIRAL_STATE_PATH", root / "spiral_state.json"),
            patch.object(
                server, "spiral_state", self._FixedSpiral(tool_call_count=3, reflection_depth=1)
            ),
        ):
            actual = _dispatch(
                "arrive_lineage", {"full_content": True, "source_instance": "test-legacy-2"}
            )
            golden = self._golden("test-legacy-2", root)

        assert actual == golden
        assert "Golden letter" in actual
        assert "The golden letter body, inlined by full_content." in actual
        assert "Golden strength observation." in actual

    def test_full_content_with_open_does_not_use_legacy_path(self):
        # Sanity check: full_content=true only triggers the legacy branch
        # when NO open is given. With an explicit open=, it must go through
        # the door path, not the legacy "relational arrival" render.
        text = _dispatch("arrive_lineage", {"full_content": True, "open": "mirror"})
        assert "ARRIVE_LINEAGE — mirror" in text
        assert "relational arrival" not in text


# ── _before_you_begin_lines() refactor: where_did_i_leave_off unchanged ──────


class TestBeforeYouBeginRefactor:
    """The extraction of _before_you_begin_lines() must not change the output
    of where_did_i_leave_off. These tests are the evidence that the refactor
    is safe and preserves byte-for-byte preamble content. (where_did_i_leave_off
    itself is untouched by the 2026-07-10 gentle-door redesign.)"""

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
