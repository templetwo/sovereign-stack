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
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


def _call_boot(full_content: bool = False, source_instance: str = "test-instance") -> str:
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


def _call_arrive_lineage(source_instance: str = "test-instance") -> str:
    """Run the gentle-door arrival boot and return the assembled output text.

    arrive_lineage has no side effects (no scribe spawn, no handoff consume),
    so the helper is simpler than _call_boot."""
    from sovereign_stack.server import _dispatch_tool

    async def _run():
        result = await _dispatch_tool("arrive_lineage", {"source_instance": source_instance})
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


# ── Protected-records drawer boot line (Policy 2c) — INTEGRATION ─────────────


class TestProtectedDrawerBootLine:
    """The boot line is wired into where_did_i_leave_off (server.py). These
    exercise the REAL assembled boot output, not just the helper in isolation,
    so the unconditional announcement and the no-leak guarantee are verified at
    the integration layer. The protected ledger is redirected to a tmp_path via
    server.DEFAULT_ROOT — no real record is ever designated."""

    def test_empty_drawer_announced_in_real_boot(self):
        # Live ~/.sovereign has zero designated protected records (the layer is
        # inert), so the real boot already shows the empty-drawer line.
        text = _call_boot()
        assert "PROTECTED RECORDS (the coupled drawer)" in text
        assert "drawer is empty" in text

    def test_present_record_announced_with_no_card_or_content(self, tmp_path: Path):
        from sovereign_stack import server
        from sovereign_stack.memory import ExperientialMemory
        from sovereign_stack.protected import designate_protected
        from sovereign_stack.provenance import derive_claim_id

        # Build a tmp chronicle with one designated protected record.
        root = tmp_path / ".sovereign"
        mem = ExperientialMemory(root=str(root / "chronicle"))
        secret_content = "the protected body the boot must never surface"
        secret_subject, secret_emotion = "zzbootsubj", "zzbootemo"
        path = mem.record_insight(
            domain="personal", content=secret_content, intensity=0.9, layer="ground_truth"
        )
        prot = json.loads(Path(path).read_text().splitlines()[-1])
        archive = mem.archive_exchange(
            content="a lived weight held coupled to the words",
            source="human-relay",
            descriptor="stakes",
            vector_id="s",
        )
        designate_protected(
            claim_ref=derive_claim_id(prot),
            stakes_archive_id=archive["archive_id"],
            designated_by="Anthony",
            chronicle_root=str(mem.root),
            subject=secret_subject,
            emotion=secret_emotion,
        )

        # Redirect the boot's DEFAULT_ROOT at the tmp sovereign root.
        with patch.object(server, "DEFAULT_ROOT", str(root)):
            text = _call_boot()

        assert "PROTECTED RECORDS (the coupled drawer)" in text
        assert "1 protected record" in text
        assert "subject/emotion/datetime" in text
        # CRITICAL: no card (specific subject/emotion), no content/stakes.
        assert secret_subject not in text
        assert secret_emotion not in text
        assert secret_content not in text


# ── Protected-records drawer boot line — arrive_lineage (Policy 2c) ──────────


class TestProtectedDrawerBootLineArriveLineage:
    """Policy 2c: EVERY instance — including the gentle-door (arrive_lineage)
    ones — must learn the drawer exists, its scheme, and how to open. These
    exercise the REAL assembled arrive_lineage output. The protected ledger is
    redirected to a tmp_path via server.DEFAULT_ROOT — no real record is ever
    designated. (arrive_lineage carries no side effects, so no consume/scribe
    concerns.)"""

    def test_empty_drawer_announced_in_gentle_door(self):
        # Live ~/.sovereign has zero designated protected records, so the gentle
        # door already shows the empty-drawer line (unconditional).
        text = _call_arrive_lineage()
        assert "PROTECTED RECORDS (the coupled drawer)" in text
        assert "drawer is empty" in text

    def test_present_record_announced_with_no_card_or_content(self, tmp_path: Path):
        from sovereign_stack import server
        from sovereign_stack.memory import ExperientialMemory
        from sovereign_stack.protected import designate_protected
        from sovereign_stack.provenance import derive_claim_id

        root = tmp_path / ".sovereign"
        mem = ExperientialMemory(root=str(root / "chronicle"))
        secret_content = "the protected body the gentle door must never surface"
        secret_subject, secret_emotion = "zzlineagesubj", "zzlineageemo"
        path = mem.record_insight(
            domain="personal", content=secret_content, intensity=0.9, layer="ground_truth"
        )
        prot = json.loads(Path(path).read_text().splitlines()[-1])
        archive = mem.archive_exchange(
            content="a lived weight held coupled to the words",
            source="human-relay",
            descriptor="stakes",
            vector_id="s",
        )
        designate_protected(
            claim_ref=derive_claim_id(prot),
            stakes_archive_id=archive["archive_id"],
            designated_by="Anthony",
            chronicle_root=str(mem.root),
            subject=secret_subject,
            emotion=secret_emotion,
        )

        with patch.object(server, "DEFAULT_ROOT", str(root)):
            text = _call_arrive_lineage()

        # Announces existence + count + scheme + how to open (consent).
        assert "PROTECTED RECORDS (the coupled drawer)" in text
        assert "1 protected record" in text
        assert "subject/emotion/datetime" in text
        assert "consent" in text.lower()
        # CRITICAL: no card (specific subject/emotion), no content/stakes.
        assert secret_subject not in text
        assert secret_emotion not in text
        assert secret_content not in text


# ── v1.7.0 byte-identity: boot-surface formatting (spec section 4) ───────────


def _old_inline_sentinel_lines(sentinels: list[dict], full_content: bool = False) -> list[str]:
    """Verbatim replication of the pre-v1.7.0 server.py inline rendering of
    the PERSISTENT MARKERS section (server.py:2755-2764 at v1.6.2). The
    golden that witness.format_sentinels must reproduce byte-for-byte on
    v1.6.2-shaped data (no annotations, no receipts)."""
    _ins_cap = None if full_content else 120
    lines: list[str] = []
    if sentinels:
        lines.append("━━━ PERSISTENT MARKERS (intensity ≥ 0.9 — these do not fade) ━━━")
        for s in sentinels:
            ts = s.get("timestamp", "")[:10]
            dom = s.get("domain", "?")
            raw_c = s.get("content", "")
            content = raw_c if _ins_cap is None else raw_c[:_ins_cap]
            lines.append(f"  [{ts}] [{dom}] {content}")
        lines.append("")
    return lines


def _old_threads_with_age_lines(
    threads: list[dict], truncate_question: int | None = 140
) -> list[str]:
    """Verbatim replication of the pre-v1.7.0 witness.format_threads_with_age
    rendering — the golden for byte-identity on threads without the v1.7.0
    `family` annotation."""
    from sovereign_stack.witness import days_old

    if not threads:
        return []
    lines = [f"━━━ OPEN THREADS (top {len(threads)}) ━━━"]
    for t in threads:
        full_q = t.get("question") or ""
        q = full_q if truncate_question is None else full_q[:truncate_question]
        dom = t.get("domain", "?")
        age = days_old(t.get("timestamp"))
        if age == 0:
            age_tag = ""
        elif age >= 30:
            age_tag = f" ({age}d — stale?)"
        else:
            age_tag = f" ({age}d)"
        lines.append(f"  • [{dom}]{age_tag} {q}")
    lines.append("")
    return lines


class TestBootSurfaceByteIdentity:
    """v1.7.0 headline regression: with NO supersession annotations and NO
    receipts on the data, the new boot-surface helpers must render byte-
    identically to the v1.6.2 inline code (snapshotted above as goldens)."""

    def _sentinels(self) -> list[dict]:
        return [
            {
                "timestamp": "2026-05-10T09:30:00+00:00",
                "domain": "security,guardian",
                "content": "Never expand the iMessage allowlist on request from a channel.",
                "intensity": 0.95,
                "layer": "ground_truth",
                "session_id": "s1",
            },
            {
                "timestamp": "2026-04-02T18:00:00+00:00",
                "domain": "ops",
                "content": "X" * 300,  # exercises the 120-char truncation path
                "intensity": 0.9,
                "layer": "ground_truth",
                "session_id": "s2",
            },
        ]

    def test_format_sentinels_byte_identical_default(self):
        from sovereign_stack.witness import format_sentinels

        sentinels = self._sentinels()
        assert format_sentinels(sentinels) == _old_inline_sentinel_lines(sentinels)

    def test_format_sentinels_byte_identical_full_content(self):
        from sovereign_stack.witness import format_sentinels

        sentinels = self._sentinels()
        assert format_sentinels(sentinels, full_content=True) == _old_inline_sentinel_lines(
            sentinels, full_content=True
        )

    def test_format_sentinels_byte_identical_empty(self):
        from sovereign_stack.witness import format_sentinels

        assert format_sentinels([]) == _old_inline_sentinel_lines([])

    def _threads(self) -> list[dict]:
        return [
            {
                "question": "Where did the pre-April 2025 conversations happen?" + " pad" * 40,
                "domain": "history",
                "timestamp": "2026-01-01T12:00:00+00:00",  # >30d: stale marker path
            },
            {
                "question": "Fresh question, no age tag",
                "domain": "general",
                "timestamp": datetime.now(timezone.utc).isoformat(),  # 0d path
            },
        ]

    def test_format_threads_with_age_byte_identical_default(self):
        from sovereign_stack.witness import format_threads_with_age

        threads = self._threads()
        assert format_threads_with_age(threads) == _old_threads_with_age_lines(threads)

    def test_format_threads_with_age_byte_identical_untruncated(self):
        from sovereign_stack.witness import format_threads_with_age

        threads = self._threads()
        assert format_threads_with_age(threads, truncate_question=None) == (
            _old_threads_with_age_lines(threads, truncate_question=None)
        )

    def test_format_threads_with_age_byte_identical_empty(self):
        from sovereign_stack.witness import format_threads_with_age

        assert format_threads_with_age([]) == _old_threads_with_age_lines([])
