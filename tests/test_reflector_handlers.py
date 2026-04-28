"""
End-to-end tests for the three reflector MCP handlers added 2026-04-26:

  * recall_reflections — list reflections with filters
  * reflection_ack — confirm/engage/discard a reflection
  * synthesize_now — manual trigger of the synthesis daemon

These tests exercise the dispatch layer (_dispatch_tool) on top of the
helpers in reflections.py and daemons/synthesis_daemon.py — handler-level
coverage that catches arg-coercion and JSON-serialization bugs the unit
tests for the helpers would not.

For synthesize_now we patch SynthesisDaemon.run() to return a stub
RunResult so we don't actually fire the local model in CI — the goal is
to exercise the handler's wrapping logic, not to integration-test the
LLM round-trip (covered manually at the fireside).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest


def _dispatch(name: str, arguments: dict) -> str:
    """Run an MCP tool dispatch and return the assembled text result."""
    from sovereign_stack.server import _dispatch_tool

    async def _run():
        result = await _dispatch_tool(name, arguments)
        return result[0].text

    return asyncio.run(_run())


def _seed_reflection(
    reflections_dir: Path,
    *,
    rid: str,
    timestamp: datetime | None = None,
    model: str = "test-model",
    observation: str = "test obs",
    ack_status: str = "unread",
) -> None:
    """Helper: write one reflection record into the fake reflections dir."""
    ts = timestamp or datetime.now(timezone.utc)
    file_name = ts.strftime("%Y-%m-%d") + ".jsonl"
    path = reflections_dir / file_name
    record = {
        "id": rid,
        "timestamp": ts.isoformat(),
        "model": model,
        "prompt_version": "v-test",
        "run_id": "test-run",
        "observation": observation,
        "entries_referenced": [],
        "connection_type": "convergence",
        "confidence": "medium",
        "ack_status": ack_status,
    }
    with path.open("a") as fh:
        fh.write(json.dumps(record) + "\n")


# ── recall_reflections ──────────────────────────────────────────────────────


class TestRecallReflectionsHandler:
    def test_lists_reflections(self, tmp_path: Path):
        d = tmp_path / "reflections"
        d.mkdir()
        _seed_reflection(d, rid="r1", observation="first observation")
        _seed_reflection(d, rid="r2", observation="second observation")

        with patch("sovereign_stack.reflections.REFLECTIONS_DIR", d):
            text = _dispatch(
                "recall_reflections", {"limit": 5, "ack_status": "unread"}
            )

        data = json.loads(text)
        assert data["count"] == 2
        ids = {r["id"] for r in data["reflections"]}
        assert ids == {"r1", "r2"}

    def test_ack_status_filter(self, tmp_path: Path):
        d = tmp_path / "reflections"
        d.mkdir()
        _seed_reflection(d, rid="u1", ack_status="unread")
        _seed_reflection(d, rid="c1", ack_status="confirm")

        with patch("sovereign_stack.reflections.REFLECTIONS_DIR", d):
            text = _dispatch(
                "recall_reflections", {"limit": 10, "ack_status": "confirm"}
            )
        data = json.loads(text)
        assert data["count"] == 1
        assert data["reflections"][0]["id"] == "c1"

    def test_model_filter(self, tmp_path: Path):
        d = tmp_path / "reflections"
        d.mkdir()
        _seed_reflection(d, rid="m1", model="ministral-3:14b")
        _seed_reflection(d, rid="g1", model="glm-4.7-flash")

        with patch("sovereign_stack.reflections.REFLECTIONS_DIR", d):
            text = _dispatch(
                "recall_reflections",
                {"limit": 10, "ack_status": "all", "model": "glm-4.7-flash"},
            )
        data = json.loads(text)
        assert {r["id"] for r in data["reflections"]} == {"g1"}

    def test_invalid_ack_status_returns_error_text(self, tmp_path: Path):
        d = tmp_path / "reflections"
        d.mkdir()
        with patch("sovereign_stack.reflections.REFLECTIONS_DIR", d):
            text = _dispatch(
                "recall_reflections", {"ack_status": "not-a-status"}
            )
        # The handler catches ValueError and surfaces a readable message,
        # not a stack trace.
        assert "recall_reflections error" in text


# ── reflection_ack ──────────────────────────────────────────────────────────


class TestReflectionAckHandler:
    def test_confirm_flow(self, tmp_path: Path):
        d = tmp_path / "reflections"
        d.mkdir()
        _seed_reflection(d, rid="r1")

        with patch("sovereign_stack.reflections.REFLECTIONS_DIR", d):
            text = _dispatch(
                "reflection_ack",
                {
                    "reflection_id": "r1",
                    "action": "confirm",
                    "note": "good catch",
                    "by": "test-instance",
                },
            )

        data = json.loads(text)
        assert data["ok"] is True
        assert data["reflection"]["ack_status"] == "confirm"
        assert data["reflection"]["ack_note"] == "good catch"
        assert data["reflection"]["ack_by"] == "test-instance"

    def test_discard_flow(self, tmp_path: Path):
        d = tmp_path / "reflections"
        d.mkdir()
        _seed_reflection(d, rid="r2")

        with patch("sovereign_stack.reflections.REFLECTIONS_DIR", d):
            _dispatch(
                "reflection_ack",
                {"reflection_id": "r2", "action": "discard"},
            )
            # Re-fetch — should be discarded.
            text = _dispatch(
                "recall_reflections",
                {"limit": 10, "ack_status": "discard"},
            )
        data = json.loads(text)
        assert data["count"] == 1
        assert data["reflections"][0]["id"] == "r2"

    def test_missing_id_returns_error(self, tmp_path: Path):
        d = tmp_path / "reflections"
        d.mkdir()
        with patch("sovereign_stack.reflections.REFLECTIONS_DIR", d):
            text = _dispatch(
                "reflection_ack",
                {"reflection_id": "nonexistent", "action": "confirm"},
            )
        # Handler catches KeyError and returns a readable error string.
        assert "reflection_ack error" in text

    def test_empty_args_rejected(self, tmp_path: Path):
        d = tmp_path / "reflections"
        d.mkdir()
        with patch("sovereign_stack.reflections.REFLECTIONS_DIR", d):
            text = _dispatch(
                "reflection_ack",
                {"reflection_id": "", "action": ""},
            )
        # Empty required args produce an explicit error message.
        assert "non-empty" in text

    def test_invalid_action_rejected(self, tmp_path: Path):
        d = tmp_path / "reflections"
        d.mkdir()
        _seed_reflection(d, rid="r3")
        with patch("sovereign_stack.reflections.REFLECTIONS_DIR", d):
            text = _dispatch(
                "reflection_ack",
                {"reflection_id": "r3", "action": "promote"},
            )
        # 'promote' is not in ACK_ACTIONS — ValueError surfaced.
        assert "reflection_ack error" in text


# ── synthesize_now ──────────────────────────────────────────────────────────


class _StubRunResult:
    """Minimal stub matching the RunResult shape that the handler
    consumes. We don't import the real RunResult because the handler
    only reads attribute names — duck-typing keeps this test
    isolated from changes to that dataclass."""

    def __init__(
        self,
        *,
        outcome: str,
        run_id: str = "stub-run",
        model: str = "stub-model",
        elapsed_seconds: float = 0.0,
        reflections_written: int = 0,
        reflections_path: str | None = None,
        details: str = "",
        raw_model_output: str = "",
    ):
        self.outcome = outcome
        self.run_id = run_id
        self.model = model
        self.elapsed_seconds = elapsed_seconds
        self.reflections_written = reflections_written
        self.reflections_path = reflections_path
        self.details = details
        self.raw_model_output = raw_model_output


class TestSynthesizeNowHandler:
    """We don't fire the actual local model in CI. We patch
    SynthesisDaemon.run() to return a stub, and verify the handler
    serializes the result correctly + reads back the new reflections
    that were just written."""

    def test_handler_serializes_run_result(self, tmp_path: Path):
        # Run the daemon stub — return a 'wrote' outcome with no actual
        # reflections file, just verify the handler's payload shape.
        from sovereign_stack.daemons import synthesis_daemon as sd

        with patch.object(
            sd.SynthesisDaemon,
            "run",
            return_value=_StubRunResult(
                outcome="wrote",
                run_id="stub-1",
                reflections_written=0,
                reflections_path=None,
                elapsed_seconds=0.5,
                details="stub run",
            ),
        ):
            text = _dispatch("synthesize_now", {"recent_hours": 12})

        data = json.loads(text)
        assert data["outcome"] == "wrote"
        assert data["run_id"] == "stub-1"
        assert data["elapsed_seconds"] == 0.5
        assert data["reflections_written"] == 0
        # When reflections_path is None, reflections list is empty.
        assert data["reflections"] == []

    def test_handler_reads_back_reflections_from_path(self, tmp_path: Path):
        # Seed a reflections file matching what the daemon would write,
        # then have the daemon stub return its path. The handler should
        # read the file back and surface the new reflections inline.
        d = tmp_path / "reflections"
        d.mkdir()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = d / f"{today}.jsonl"

        # Two records with the run_id we'll claim, one from another run.
        records = [
            {
                "id": "reflection_other-run_1",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "model": "m",
                "prompt_version": "v",
                "run_id": "other-run",  # not the one we report
                "observation": "older obs",
                "entries_referenced": [],
                "connection_type": "other",
                "confidence": "low",
                "ack_status": "unread",
            },
            {
                "id": "reflection_target-run_1",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "model": "m",
                "prompt_version": "v",
                "run_id": "target-run",
                "observation": "fresh obs from this run",
                "entries_referenced": [],
                "connection_type": "convergence",
                "confidence": "medium",
                "ack_status": "unread",
            },
        ]
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n")

        from sovereign_stack.daemons import synthesis_daemon as sd

        with patch.object(
            sd.SynthesisDaemon,
            "run",
            return_value=_StubRunResult(
                outcome="wrote",
                run_id="target-run",
                reflections_written=1,
                reflections_path=str(path),
                elapsed_seconds=1.2,
                details="wrote 1",
            ),
        ):
            text = _dispatch("synthesize_now", {})

        data = json.loads(text)
        assert data["outcome"] == "wrote"
        # Handler filters by run_id — only target-run's reflection surfaces.
        assert len(data["reflections"]) == 1
        assert data["reflections"][0]["run_id"] == "target-run"
        assert data["reflections"][0]["observation"] == "fresh obs from this run"

    def test_handler_handles_failure_outcomes(self, tmp_path: Path):
        # Daemon failed — handler returns the failure outcome cleanly,
        # not a crash. This is the offline / Ollama-down path.
        from sovereign_stack.daemons import synthesis_daemon as sd

        with patch.object(
            sd.SynthesisDaemon,
            "run",
            return_value=_StubRunResult(
                outcome="model_failed",
                details="ollama timed out after 180s",
                run_id="failed-run",
                elapsed_seconds=180.0,
            ),
        ):
            text = _dispatch("synthesize_now", {})
        data = json.loads(text)
        assert data["outcome"] == "model_failed"
        assert "timed out" in data["details"]
        assert data["reflections"] == []

    def test_handler_passes_args_to_daemon(self):
        # The handler should forward model/recent_hours/max_entries/focus
        # to SynthesisDaemon.__init__. Capture the kwargs the handler
        # passes to verify the wiring.
        captured: dict = {}

        from sovereign_stack.daemons import synthesis_daemon as sd

        def _capture_init(self, **kwargs):
            captured.update(kwargs)
            # Set the minimum attrs the handler expects on the daemon
            # (it doesn't actually call .run() here because we patch that).
            for k, v in kwargs.items():
                setattr(self, k, v)
            # Default model attr if not in kwargs (the dataclass default).
            if "model" not in kwargs:
                self.model = sd.DEFAULT_MODEL

        with patch.object(sd.SynthesisDaemon, "__init__", _capture_init), \
             patch.object(
                 sd.SynthesisDaemon,
                 "run",
                 return_value=_StubRunResult(outcome="no_entries", run_id="x"),
             ):
            _dispatch(
                "synthesize_now",
                {
                    "model": "qwen3.6:27b",
                    "recent_hours": 48,
                    "max_entries": 12,
                    "focus": "register-drift",
                },
            )

        assert captured["model"] == "qwen3.6:27b"
        assert captured["recent_hours"] == 48
        assert captured["max_entries"] == 12
        assert captured["focus"] == "register-drift"
