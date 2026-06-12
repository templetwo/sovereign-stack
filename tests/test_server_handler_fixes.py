"""
Server handler regression tests — record_insight validation + spiral_inherit
boot robustness.

Two confirmed bugs in server.py's _dispatch_tool surface:

1. record_insight silently invented defaults (domain="general", content="")
   when the schema-required 'domain'/'content' arguments were missing, so a
   malformed call wrote an empty/misfiled entry to the chronicle instead of
   returning a validation error. The handler must reject missing arguments
   the way compass_check / reflection_ack do, and write NOTHING.

2. spiral_inherit read session_handoff.json and self_model.json raw, with no
   try/except. A corrupt/truncated/mid-write file raised JSONDecodeError
   straight through _dispatch_tool and hard-failed a BOOT call. It was the
   lone raw-read boot path — every sibling (format_lineage_layer,
   format_self_model, format_unresolved_uncertainties) catches
   (json.JSONDecodeError, OSError) and degrades gracefully. The inherit path
   must do the same: log, skip the section, keep booting.

ISOLATION NOTE: Tests that call _dispatch_tool must patch srv_module so they
never touch the live ~/.sovereign/ tree. The _isolated_server helper below
copies the pattern from tests/test_nape_autohook.py — including the
full-spiral-state deepcopy snapshot/restore (2026-06-12 fix: partial restore
let test session ids leak into the live spiral_state.json) — and additionally
patches DEFAULT_ROOT, because spiral_inherit reads its boot files from there.
"""

import asyncio
import copy
import json
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from sovereign_stack.memory import ExperientialMemory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@contextmanager
def _isolated_server(session_id: str):
    """
    Context manager that patches the server module so _dispatch_tool calls
    never touch the live ~/.sovereign/ filesystem.

    Yields a tuple (srv_module, tmp_root: Path) where tmp_root is the
    temporary sovereign root used for this context.

    Patches applied:
    - srv_module.experiential  → ExperientialMemory rooted in tmp_root/chronicle
    - srv_module.SPIRAL_STATE_PATH → tmp_root/spiral_state.json
    - srv_module.DEFAULT_ROOT → tmp_root (spiral_inherit reads
      session_handoff.json / self_model.json from here)
    - srv_module.spiral_state.session_id → session_id (restored on exit)
    """
    from sovereign_stack import server as srv_module

    tmp_root = Path(tempfile.mkdtemp())
    chronicle_root = tmp_root / "chronicle"
    chronicle_root.mkdir(parents=True)

    tmp_experiential = ExperientialMemory(root=str(chronicle_root))
    tmp_spiral_path = tmp_root / "spiral_state.json"

    original_experiential = srv_module.experiential
    original_spiral_path = srv_module.SPIRAL_STATE_PATH
    original_default_root = srv_module.DEFAULT_ROOT
    # Snapshot the ENTIRE spiral state, not just session_id. Dispatched tools
    # mutate other fields on this shared object (phase_history, counters); a
    # partial restore let test session ids leak into the live
    # ~/.sovereign/spiral_state.json (found 2026-06-12: 'rotation-reminder-test'
    # and 'pre-close-session-id' in live phase_history).
    original_spiral_snapshot = copy.deepcopy(srv_module.spiral_state.__dict__)

    srv_module.experiential = tmp_experiential
    srv_module.SPIRAL_STATE_PATH = tmp_spiral_path
    srv_module.DEFAULT_ROOT = str(tmp_root)
    srv_module.spiral_state.session_id = session_id

    try:
        yield srv_module, tmp_root
    finally:
        srv_module.experiential = original_experiential
        srv_module.SPIRAL_STATE_PATH = original_spiral_path
        srv_module.DEFAULT_ROOT = original_default_root
        srv_module.spiral_state.__dict__.clear()
        srv_module.spiral_state.__dict__.update(original_spiral_snapshot)
        shutil.rmtree(tmp_root, ignore_errors=True)


def _insight_files(tmp_root: Path) -> list[Path]:
    """All chronicle insight files written inside the isolated root."""
    return list((tmp_root / "chronicle" / "insights").rglob("*.jsonl"))


# ---------------------------------------------------------------------------
# Bug 1: record_insight must reject missing domain/content, write nothing
# ---------------------------------------------------------------------------


class TestRecordInsightRequiredArguments:
    """The handler must mirror the schema's required: ['domain', 'content']."""

    def test_missing_content_returns_validation_error_and_writes_nothing(self):
        """Omitting 'content' must return a tool error, not record an entry."""
        from sovereign_stack.server import _dispatch_tool

        with _isolated_server("record-insight-missing-content") as (_srv, tmp_root):
            result = asyncio.run(_dispatch_tool("record_insight", {"domain": "regression"}))

            text = result[0].text
            assert "record_insight requires non-empty 'domain' and 'content'" in text, (
                f"Expected validation error for missing content; got: {text[:200]}"
            )
            assert "Insight recorded" not in text
            written = _insight_files(tmp_root)
            assert not written, (
                f"record_insight wrote to the chronicle despite missing content: {written}"
            )

    def test_missing_domain_returns_validation_error_and_writes_nothing(self):
        """Omitting 'domain' must return a tool error, not default to 'general'."""
        from sovereign_stack.server import _dispatch_tool

        with _isolated_server("record-insight-missing-domain") as (_srv, tmp_root):
            result = asyncio.run(
                _dispatch_tool("record_insight", {"content": "an orphaned insight"})
            )

            text = result[0].text
            assert "record_insight requires non-empty 'domain' and 'content'" in text, (
                f"Expected validation error for missing domain; got: {text[:200]}"
            )
            written = _insight_files(tmp_root)
            assert not written, (
                f"record_insight invented a default domain and wrote anyway: {written}"
            )

    def test_valid_call_still_records(self):
        """Non-breaking: a well-formed call keeps working exactly as before."""
        from sovereign_stack.server import _dispatch_tool

        with _isolated_server("record-insight-valid-call") as (_srv, tmp_root):
            result = asyncio.run(
                _dispatch_tool(
                    "record_insight",
                    {"domain": "regression", "content": "valid calls keep working"},
                )
            )

            assert "Insight recorded" in result[0].text
            written = _insight_files(tmp_root)
            assert len(written) == 1, f"Expected exactly 1 insight file; got {written}"
            entry = json.loads(written[0].read_text().splitlines()[0])
            assert entry["domain"] == "regression"
            assert entry["content"] == "valid calls keep working"


# ---------------------------------------------------------------------------
# Bug 2: spiral_inherit must survive corrupt boot files
# ---------------------------------------------------------------------------


class TestSpiralInheritCorruptBootFiles:
    """A torn session_handoff.json / self_model.json must degrade, not raise."""

    def test_corrupt_handoff_degrades_instead_of_raising(self):
        """Garbage bytes in session_handoff.json must not hard-fail the boot."""
        from sovereign_stack.server import _dispatch_tool

        with _isolated_server("inherit-corrupt-handoff") as (_srv, tmp_root):
            # Simulate a mid-write/truncated handoff: valid UTF-8, invalid JSON.
            (tmp_root / "session_handoff.json").write_bytes(b'{"summary": "tor')

            result = asyncio.run(_dispatch_tool("spiral_inherit", {}))

            text = result[0].text
            assert "New session:" in text, (
                f"spiral_inherit must still rotate and report the new session; got: {text[:200]}"
            )
            assert "(Session handoff unreadable — section skipped)" in text
            assert "=== SESSION HANDOFF" not in text
            # The rest of the inheritance surface must still be assembled.
            assert "=== INHERITED CONTEXT" in text

    def test_corrupt_self_model_skips_mirror_section(self):
        """Garbage bytes in self_model.json skip the mirror, keep the handoff."""
        from sovereign_stack.server import _dispatch_tool

        with _isolated_server("inherit-corrupt-mirror") as (_srv, tmp_root):
            (tmp_root / "session_handoff.json").write_text(
                json.dumps({"summary": "clean handoff survives a torn mirror"})
            )
            (tmp_root / "self_model.json").write_bytes(b"not json at all {{{")

            result = asyncio.run(_dispatch_tool("spiral_inherit", {}))

            text = result[0].text
            assert "clean handoff survives a torn mirror" in text, (
                "The readable handoff section must still surface when only "
                f"self_model.json is corrupt; got: {text[:300]}"
            )
            assert "=== SELF-MODEL" not in text
            assert "=== INHERITED CONTEXT" in text

    def test_valid_boot_files_render_both_sections(self):
        """Non-breaking: intact files keep producing the full boot surface."""
        from sovereign_stack.server import _dispatch_tool

        with _isolated_server("inherit-valid-boot-files") as (_srv, tmp_root):
            (tmp_root / "session_handoff.json").write_text(
                json.dumps(
                    {
                        "summary": "everything was fine",
                        "next_priorities": ["keep it that way"],
                    }
                )
            )
            (tmp_root / "self_model.json").write_text(
                json.dumps({"strength": [{"observation": "reads files before declaring"}]})
            )

            result = asyncio.run(_dispatch_tool("spiral_inherit", {}))

            text = result[0].text
            assert "=== SESSION HANDOFF (read this first) ===" in text
            assert "everything was fine" in text
            assert "  > keep it that way" in text
            assert "=== SELF-MODEL (know your shape) ===" in text
            assert "  strength: reads files before declaring" in text
