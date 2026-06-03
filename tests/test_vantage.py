"""Tests for source-vantage metadata on record_insight.

Each insight can carry the seat/vantage it was made from (hq_filesystem,
bridge_runtime, web_connector, local_jetson, claude_sandbox, ...) so a future
reader knows how to weight the claim — the write-path-divergence lesson: a
runtime seat and a filesystem seat see different truths. Non-breaking:
vantage is optional; insights without it are unchanged. Added 2026-06-02.
"""

from __future__ import annotations

import asyncio

from sovereign_stack import server
from sovereign_stack.memory import ExperientialMemory


def _em(tmp_path) -> ExperientialMemory:
    return ExperientialMemory(root=str(tmp_path))


class TestVantageStoreAndRecall:
    def test_vantage_stored_and_recalled(self, tmp_path):
        em = _em(tmp_path)
        em.record_insight(
            domain="test-vantage",
            content="a claim from the HQ filesystem seat",
            vantage="hq_filesystem",
            session_id="s1",
        )
        got = em.recall_insights(domain="test-vantage")
        assert got, "insight should be recallable"
        assert any(r.get("vantage") == "hq_filesystem" for r in got)

    def test_vantage_omitted_keeps_record_clean(self, tmp_path):
        em = _em(tmp_path)
        em.record_insight(
            domain="test-vantage",
            content="a claim with no seat tagged",
            session_id="s1",
        )
        got = em.recall_insights(domain="test-vantage")
        assert got
        # No vantage key should be written when none is supplied.
        assert all("vantage" not in r for r in got)

    def test_both_coexist(self, tmp_path):
        em = _em(tmp_path)
        em.record_insight(domain="d", content="with seat", vantage="bridge_runtime", session_id="s")
        em.record_insight(domain="d", content="without seat", session_id="s")
        got = em.recall_insights(domain="d")
        with_seat = [r for r in got if "with seat" in r.get("content", "")]
        without = [r for r in got if "without seat" in r.get("content", "")]
        assert with_seat and with_seat[0].get("vantage") == "bridge_runtime"
        assert without and "vantage" not in without[0]


class TestVantageSchema:
    def test_record_insight_schema_has_vantage(self):
        tools = asyncio.new_event_loop().run_until_complete(server.list_tools())
        ri = next(t for t in tools if t.name == "record_insight")
        props = ri.inputSchema["properties"]
        assert "vantage" in props
        # vantage stays optional — not in required.
        assert "vantage" not in ri.inputSchema.get("required", [])

    def test_vantage_not_required(self):
        # Non-breaking guarantee: domain + content remain the only required fields.
        tools = asyncio.new_event_loop().run_until_complete(server.list_tools())
        ri = next(t for t in tools if t.name == "record_insight")
        assert set(ri.inputSchema["required"]) == {"domain", "content"}
