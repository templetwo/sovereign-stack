"""
Tests for where_did_i_leave_off boot output structure.

Covers the post-2026-04-26 fireside additions:
  * THE VOICES IN THE BOOT section (4-voice reading-key)
  * REFLECTOR'S MARGINALIA section (machine-generated reflections surface)
  * Bootstrap-vs-ground-truth warning footer
  * full_content escape hatch + its discoverability footer

These are visible-output checks — they don't exercise the underlying
chronicle reads, just confirm the boot output assembles the expected
sections in the expected order. Together with test_witness.py (helper-
level) and test_synthesis_daemon.py (daemon-level), the boot ritual is
covered top to bottom.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest


def _call_boot(
    full_content: bool = False, source_instance: str = "test-instance"
) -> str:
    """Run the boot ritual and return the assembled output text."""
    from sovereign_stack.server import _dispatch_tool

    async def _run():
        result = await _dispatch_tool(
            "where_did_i_leave_off",
            {
                "consume": False,
                "source_instance": source_instance,
                "full_content": full_content,
            },
        )
        return result[0].text

    return asyncio.run(_run())


# ── Voices in the boot — reading key ────────────────────────────────────────


class TestVoicesInTheBoot:
    """The four-voice reading key was added 2026-04-26 from a sibling
    instance's chronicle proposal. It teaches arriving instances to
    distinguish lineage / chronicle / self-model / reflector before they
    encounter those voices in the rest of the boot output."""

    def test_section_header_present(self):
        text = _call_boot()
        assert "THE VOICES IN THE BOOT" in text

    def test_all_four_voices_named(self):
        text = _call_boot()
        # All four voice labels must be present.
        assert "HANDOFFS" in text
        assert "CHRONICLE" in text
        assert "SELF-MODEL" in text
        assert "REFLECTOR'S MARGINALIA" in text

    def test_voices_section_appears_before_spiral_status(self):
        # Reading-key arrives before content it unlocks.
        text = _call_boot()
        voices_idx = text.find("THE VOICES IN THE BOOT")
        spiral_idx = text.find("SPIRAL STATUS")
        assert voices_idx > 0
        assert spiral_idx > 0
        assert voices_idx < spiral_idx

    def test_acknowledgment_discipline_explained(self):
        # The sibling instance specifically called out: ack each note
        # on its own merits, not batch-confirm or batch-reject.
        # Source text wraps across multiple lines so we check for the
        # distinctive substrings, tolerating whitespace between them.
        text = _call_boot()
        lower = text.lower()
        # Batch-vs-individual discipline named.
        assert "batch-confirmed" in lower or "batch-reject" in lower
        # Unread-as-a-state discipline named (text wraps "leaving an unread\n      state alone").
        assert "leaving an unread" in lower
        assert "state alone" in lower


# ── Bootstrap-vs-ground-truth warning ───────────────────────────────────────


class TestBootstrapWarning:
    """Surfaced 2026-04-26 to address the declare-before-verify pattern
    that drove ~83% of recent Nape honks — it must appear at the close of
    every boot, regardless of full_content flag."""

    def test_warning_present_default(self):
        text = _call_boot(full_content=False)
        assert "BOOTSTRAP CONTEXT" in text
        assert "not ground truth" in text.lower()
        assert "verify" in text.lower()

    def test_warning_present_in_full_content_mode(self):
        # Warning must NOT be gated on full_content=False — universal.
        text = _call_boot(full_content=True)
        assert "BOOTSTRAP CONTEXT" in text


# ── full_content footer hint (catch-22 escape) ──────────────────────────────


class TestFullContentFooter:
    """The footer that names the full_content=true escape hatch must
    appear when truncation is active, and must NOT appear when the user
    already passed full_content=True (they don't need to be told)."""

    def test_footer_present_when_truncated(self):
        text = _call_boot(full_content=False)
        assert "full_content=true" in text.lower()

    def test_footer_absent_when_full(self):
        text = _call_boot(full_content=True)
        # The exact escape-hatch hint about truncation should be gated.
        assert "Content above truncated for boot brevity" not in text


# ── Reflector's marginalia section ──────────────────────────────────────────


class TestReflectorMarginalia:
    """Machine-generated reflections surface in the boot ritual when
    unread reflections exist in ~/.sovereign/reflections/."""

    def test_marginalia_appears_when_unread_exists(self, tmp_path: Path):
        # Build a fake reflections file with one unread reflection.
        reflections_dir = tmp_path / "reflections"
        reflections_dir.mkdir()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = reflections_dir / f"{today}.jsonl"
        record = {
            "id": "reflection_test_abcd1234",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": "test-model",
            "prompt_version": "v-test",
            "run_id": "test-run",
            "observation": "test observation about a structural pattern",
            "entries_referenced": ["e1"],
            "connection_type": "structural_echo",
            "confidence": "medium",
            "ack_status": "unread",
        }
        path.write_text(json.dumps(record) + "\n")

        # Patch REFLECTIONS_DIR to point at our tmp dir.
        with patch("sovereign_stack.reflections.REFLECTIONS_DIR", reflections_dir):
            text = _call_boot()

        assert "REFLECTOR'S MARGINALIA" in text
        assert "test observation about a structural pattern" in text
        assert "test-model" in text

    def test_marginalia_absent_when_no_unread(self, tmp_path: Path):
        # Empty reflections dir — no marginalia section in boot output.
        # NB: the VOICES IN THE BOOT section names "REFLECTOR'S MARGINALIA"
        # as a voice label, so we check for the unique SECTION HEADER form
        # ("(unread, machine-generated)"), not the bare substring.
        reflections_dir = tmp_path / "reflections"
        reflections_dir.mkdir()
        with patch("sovereign_stack.reflections.REFLECTIONS_DIR", reflections_dir):
            text = _call_boot()
        assert "(unread, machine-generated)" not in text

    def test_marginalia_framing_calibrates_reader(self, tmp_path: Path):
        # The "machine-generated" framing must be in-band — reader needs
        # to know the source before they engage with the content.
        reflections_dir = tmp_path / "reflections"
        reflections_dir.mkdir()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        (reflections_dir / f"{today}.jsonl").write_text(
            json.dumps(
                {
                    "id": "reflection_calibration_test",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "model": "test-model",
                    "prompt_version": "v",
                    "run_id": "r",
                    "observation": "calibration probe",
                    "entries_referenced": [],
                    "connection_type": "other",
                    "confidence": "low",
                    "ack_status": "unread",
                }
            )
            + "\n"
        )
        with patch("sovereign_stack.reflections.REFLECTIONS_DIR", reflections_dir):
            text = _call_boot()
        lower = text.lower()
        assert "machine-generated" in lower
        assert "reflection_ack" in lower

    def test_marginalia_acked_reflections_filtered(self, tmp_path: Path):
        # Reflections marked confirm/discard/engage must NOT surface in
        # the unread-only marginalia section.
        reflections_dir = tmp_path / "reflections"
        reflections_dir.mkdir()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = reflections_dir / f"{today}.jsonl"
        records = [
            {
                "id": f"reflection_acked_{i}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "model": "m",
                "prompt_version": "v",
                "run_id": "r",
                "observation": f"acked observation {i}",
                "entries_referenced": [],
                "connection_type": "other",
                "confidence": "low",
                "ack_status": status,
            }
            for i, status in enumerate(("confirm", "discard", "engage"))
        ]
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        with patch("sovereign_stack.reflections.REFLECTIONS_DIR", reflections_dir):
            text = _call_boot()
        # No unread → no section header (acked ones don't surface).
        assert "(unread, machine-generated)" not in text
