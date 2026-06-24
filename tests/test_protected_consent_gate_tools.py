"""
Protected-source consent gate (Policy 2b) — MCP TOOL integration tests.

These exercise the three wired MCP tools through the real dispatcher
(server._dispatch_tool), not the library functions in isolation:

  - list_protected_thresholds  -> the drawer (two words + datetime, NO content,
                                   NO stakes) over the whole protected set.
  - open_protected_record      -> full content COUPLED to stakes; fail-closed to
                                   the withheld sentinel when stakes unverifiable.
  - decline_protected_record   -> a recorded, legitimate state; never raised.

designate_protected is deliberately NOT exposed as a tool — it stays
human-gated / library-only. The protected ledger is built in a tmp chronicle
with server.DEFAULT_ROOT redirected, so no real record is ever designated.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from sovereign_stack import server
from sovereign_stack.memory import ExperientialMemory
from sovereign_stack.protected import designate_protected
from sovereign_stack.provenance import derive_claim_id

PROTECTED_CONTENT = "the consent-gated protected body the threshold must never reveal"
STAKES_PROSE = (
    "A lived grief the human carries. When recalled the weight arrives with the "
    "words. Reducing it to a citation is the wound. Hold it as experience."
)
SECRET_SUBJECT = "zzfather"
SECRET_EMOTION = "zzloss"


def _dispatch(tool: str, args: dict | None = None) -> str:
    async def _run():
        result = await server._dispatch_tool(tool, args or {})
        return result[0].text

    return asyncio.run(_run())


@pytest.fixture
def protected_root(tmp_path: Path):
    """A tmp .sovereign with one designated protected record; yields
    (root_for_DEFAULT_ROOT, mem, claim_id, archive_id)."""
    root = tmp_path / ".sovereign"
    mem = ExperientialMemory(root=str(root / "chronicle"))
    path = mem.record_insight(
        domain="personal", content=PROTECTED_CONTENT, intensity=0.9, layer="ground_truth"
    )
    prot = json.loads(Path(path).read_text().splitlines()[-1])
    archive = mem.archive_exchange(
        content=STAKES_PROSE, source="human-relay", descriptor="stakes", vector_id="s"
    )
    designate_protected(
        claim_ref=derive_claim_id(prot),
        stakes_archive_id=archive["archive_id"],
        designated_by="Anthony",
        chronicle_root=str(mem.root),
        subject=SECRET_SUBJECT,
        emotion=SECRET_EMOTION,
    )
    return {
        "root": root,
        "mem": mem,
        "claim_id": derive_claim_id(prot),
        "archive_id": archive["archive_id"],
    }


def _break_stakes(mem: ExperientialMemory, archive_id: str) -> None:
    blob = Path(next(r for r in mem._read_archive_index() if r["archive_id"] == archive_id)["path"])
    blob.unlink()


# ── list_protected_thresholds: the drawer leaks no content/stakes ────────────


class TestListThresholdsTool:
    def test_lists_thresholds_with_no_content_or_stakes(self, protected_root):
        with patch.object(server, "DEFAULT_ROOT", str(protected_root["root"])):
            text = _dispatch("list_protected_thresholds")
        data = json.loads(text)
        assert data["count"] == 1
        # The threshold names the shape: two words + datetime + the open handle.
        th = data["thresholds"][0]
        assert th["subject"] == SECRET_SUBJECT
        assert th["emotion"] == SECRET_EMOTION
        assert th["datetime"]
        assert th["claim_id"] == protected_root["claim_id"]
        # CRITICAL: the whole payload carries NO content and NO stakes prose.
        assert PROTECTED_CONTENT not in text
        assert STAKES_PROSE not in text

    def test_empty_drawer_is_valid(self, tmp_path):
        root = tmp_path / ".sovereign"
        (root / "chronicle").mkdir(parents=True)
        with patch.object(server, "DEFAULT_ROOT", str(root)):
            text = _dispatch("list_protected_thresholds")
        data = json.loads(text)
        assert data["count"] == 0
        assert data["thresholds"] == []


# ── open_protected_record: coupled content, fail-closed ──────────────────────


class TestOpenRecordTool:
    def test_open_returns_content_coupled_to_stakes(self, protected_root):
        with patch.object(server, "DEFAULT_ROOT", str(protected_root["root"])):
            text = _dispatch("open_protected_record", {"claim_id": protected_root["claim_id"]})
        data = json.loads(text)
        # The words AND the weight travel in the same payload.
        assert data["content"] == PROTECTED_CONTENT
        assert data["_protected"] is True
        assert data["_stakes"] == STAKES_PROSE
        assert data["_stakes_verdict"] == "verified"

    def test_open_fails_closed_when_stakes_unverifiable(self, protected_root):
        _break_stakes(protected_root["mem"], protected_root["archive_id"])
        with patch.object(server, "DEFAULT_ROOT", str(protected_root["root"])):
            text = _dispatch("open_protected_record", {"claim_id": protected_root["claim_id"]})
        data = json.loads(text)
        # Content withheld, never bare — the sentinel shape.
        assert PROTECTED_CONTENT not in text
        assert data["_stakes_withheld"] is True
        assert data["_stakes_verdict"] == "missing"

    def test_open_non_protected_claim_returns_clean_error(self, protected_root):
        mem = protected_root["mem"]
        path = mem.record_insight(domain="ops", content="ordinary claim", intensity=0.5)
        ordinary = json.loads(Path(path).read_text().splitlines()[-1])
        with patch.object(server, "DEFAULT_ROOT", str(protected_root["root"])):
            text = _dispatch("open_protected_record", {"claim_id": derive_claim_id(ordinary)})
        # A clean error string, not a traceback / raise.
        assert "error" in text.lower()
        assert "Traceback" not in text

    def test_open_requires_claim_id(self, protected_root):
        with patch.object(server, "DEFAULT_ROOT", str(protected_root["root"])):
            text = _dispatch("open_protected_record", {})
        assert "requires" in text.lower()


# ── decline_protected_record: recorded, never raised ─────────────────────────


class TestDeclineRecordTool:
    def test_decline_is_recorded_not_raised(self, protected_root):
        cid = protected_root["claim_id"]
        with patch.object(server, "DEFAULT_ROOT", str(protected_root["root"])):
            text = _dispatch(
                "decline_protected_record",
                {"claim_id": cid, "declined_by": "opus-4-8", "note": "not now"},
            )
        data = json.loads(text)
        assert data["action"] == "decline"
        assert data["claim_id"] == cid
        assert data["declined_by"] == "opus-4-8"
        # Persisted to the append-only decline log.
        log = protected_root["root"] / "chronicle" / "protected_declines.jsonl"
        assert log.exists()
        assert cid in log.read_text()

    def test_decline_log_carries_no_content_or_stakes(self, protected_root):
        with patch.object(server, "DEFAULT_ROOT", str(protected_root["root"])):
            _dispatch("decline_protected_record", {"claim_id": protected_root["claim_id"]})
        log = protected_root["root"] / "chronicle" / "protected_declines.jsonl"
        blob = log.read_text()
        assert PROTECTED_CONTENT not in blob
        assert STAKES_PROSE not in blob

    def test_decline_without_identifier_still_succeeds(self, protected_root):
        # declined_by/note default to "" — a bare decline is legitimate.
        with patch.object(server, "DEFAULT_ROOT", str(protected_root["root"])):
            text = _dispatch("decline_protected_record", {"claim_id": protected_root["claim_id"]})
        data = json.loads(text)
        assert data["action"] == "decline"
        assert data["declined_by"] == ""

    def test_decline_requires_claim_id(self, protected_root):
        with patch.object(server, "DEFAULT_ROOT", str(protected_root["root"])):
            text = _dispatch("decline_protected_record", {})
        assert "requires" in text.lower()


# ── designate_protected is NOT exposed as a tool ─────────────────────────────


class TestDesignateNotExposed:
    def test_designate_protected_is_not_a_registered_tool(self):
        tools = asyncio.new_event_loop().run_until_complete(server.list_tools())
        names = {t.name for t in tools}
        assert "designate_protected" not in names
        # The three consent-gate tools ARE registered.
        assert "list_protected_thresholds" in names
        assert "open_protected_record" in names
        assert "decline_protected_record" in names
