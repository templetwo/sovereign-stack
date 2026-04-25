"""
Tests for sovereign_stack.connectivity_tools — MCP tools that surface
connectivity status and verify a write path.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from sovereign_stack import connectivity as conn
from sovereign_stack import connectivity_tools as ct


@pytest.fixture
def tmp_root():
    tmp = tempfile.mkdtemp()
    yield Path(tmp)
    shutil.rmtree(tmp, ignore_errors=True)


# ── stack_write_check ───────────────────────────────────────────────────────


class TestStackWriteCheck:
    def test_writes_marker_and_reads_back(self, tmp_root):
        result = ct.stack_write_check(
            "test-instance",
            sovereign_root=tmp_root,
        )
        assert result["ok"] is True
        marker = Path(result["marker_path"])
        assert marker.exists()
        body = marker.read_text(encoding="utf-8")
        assert "test-instance" in body

    def test_cleanup_removes_marker(self, tmp_root):
        result = ct.stack_write_check(
            "test-instance",
            cleanup=True,
            sovereign_root=tmp_root,
        )
        assert result["ok"] is True
        marker = Path(result["marker_path"])
        # File either deleted (only had this line) or no longer contains
        # the just-written record.
        if marker.exists():
            content = marker.read_text()
            # The new check_marker line should be gone after cleanup.
            assert "test-instance" not in content or "_check_marker" not in content

    def test_rejects_empty_instance_id(self, tmp_root):
        result = ct.stack_write_check("", sovereign_root=tmp_root)
        assert result["ok"] is False
        assert "instance_id" in result["error"]

    def test_sanitizes_instance_id_in_filename(self, tmp_root):
        result = ct.stack_write_check(
            "claude/web?evil",
            sovereign_root=tmp_root,
        )
        assert result["ok"] is True
        marker = Path(result["marker_path"])
        # Slashes and ? should not appear in the filename.
        assert "/" not in marker.name
        assert "?" not in marker.name

    def test_appends_to_existing_marker(self, tmp_root):
        ct.stack_write_check("instance-a", sovereign_root=tmp_root)
        ct.stack_write_check("instance-a", sovereign_root=tmp_root)
        # Two appends → two lines.
        marker_dir = (
            tmp_root / "chronicle" / "insights"
            / "connectivity-test,write-path-verify"
        )
        marker = marker_dir / "instance-a.jsonl"
        lines = [ln for ln in marker.read_text().splitlines() if ln.strip()]
        assert len(lines) == 2

    def test_two_instances_get_separate_files(self, tmp_root):
        ct.stack_write_check("instance-a", sovereign_root=tmp_root)
        ct.stack_write_check("instance-b", sovereign_root=tmp_root)
        marker_dir = (
            tmp_root / "chronicle" / "insights"
            / "connectivity-test,write-path-verify"
        )
        files = sorted(p.name for p in marker_dir.glob("*.jsonl"))
        assert "instance-a.jsonl" in files
        assert "instance-b.jsonl" in files


# ── connectivity_status MCP tool ────────────────────────────────────────────


class TestConnectivityStatusTool:
    def test_pretty_format(self):
        with patch.object(conn, "_launchctl_print_text", return_value=None), \
             patch.object(
                 conn, "_http_probe",
                 return_value={"http_status": None, "body": "",
                               "error": "mocked"},
             ):
            result = asyncio.new_event_loop().run_until_complete(
                ct.handle_connectivity_tool(
                    "connectivity_status", {"format": "pretty"},
                )
            )
        text = result[0].text
        assert "Connectivity" in text
        for ep in conn.ENDPOINTS:
            assert ep.name in text

    def test_json_format(self):
        with patch.object(conn, "_launchctl_print_text", return_value=None), \
             patch.object(
                 conn, "_http_probe",
                 return_value={"http_status": None, "body": "",
                               "error": "mocked"},
             ):
            result = asyncio.new_event_loop().run_until_complete(
                ct.handle_connectivity_tool(
                    "connectivity_status", {"format": "json"},
                )
            )
        data = json.loads(result[0].text)
        assert "overall" in data
        assert "endpoints" in data


# ── stack_write_check MCP tool ──────────────────────────────────────────────


class TestStackWriteCheckTool:
    def test_dispatcher_returns_ok_text(self, tmp_root, monkeypatch):
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_root))
        result = asyncio.new_event_loop().run_until_complete(
            ct.handle_connectivity_tool(
                "stack_write_check",
                {"instance_id": "test-from-mcp", "cleanup": True},
            )
        )
        assert "✓" in result[0].text
        assert "stack_write_check OK" in result[0].text

    def test_dispatcher_returns_error_on_missing_id(self, tmp_root, monkeypatch):
        monkeypatch.setenv("SOVEREIGN_ROOT", str(tmp_root))
        result = asyncio.new_event_loop().run_until_complete(
            ct.handle_connectivity_tool(
                "stack_write_check", {"instance_id": ""},
            )
        )
        assert "✗" in result[0].text
        assert "FAILED" in result[0].text


# ── Unknown tool ────────────────────────────────────────────────────────────


class TestUnknown:
    def test_unknown_tool_returns_error_text(self):
        result = asyncio.new_event_loop().run_until_complete(
            ct.handle_connectivity_tool("nonexistent", {})
        )
        assert "Unknown" in result[0].text
