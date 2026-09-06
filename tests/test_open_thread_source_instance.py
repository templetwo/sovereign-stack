"""record_open_thread must carry its author.

Added 2026-09-05 with the bridge's seat-identity path: a seated Studio terminal
signs every write with its seat id, and record_open_thread had no field to sign
into — a thread recorded who ASKED nothing at all.

The regression these guard against is specific and this house has already had it
once, on record_insight: the schema declared source_instance, the dispatch never
forwarded it, and every caller following the documented convention got ok:true
while attributing nothing (fixed 2026-08-28). Declaring a property is not
wiring it, so the wire is what is tested here — schema, dispatch, and storage,
each separately.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from sovereign_stack import server
from sovereign_stack.memory import ExperientialMemory


@pytest.fixture
def mem(tmp_path):
    return ExperientialMemory(root=str(tmp_path))


def _threads(mem, domain):
    path = mem.threads_dir / f"{domain}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_storage_stamps_source_instance(mem):
    mem.record_open_thread(
        "does the seat sign its questions?",
        context="seat identity",
        domain="seat-signing-test",
        source_instance="grok-build-studio",
    )
    (row,) = _threads(mem, "seat-signing-test")
    assert row["source_instance"] == "grok-build-studio"


def test_absent_source_instance_omits_the_key_entirely(mem):
    """Not None, not "" — ABSENT. Every thread ever written lacks this key, and
    a reader distinguishing 'unattributed' from 'attributed to nothing' must
    keep being able to."""
    mem.record_open_thread("who wrote this?", domain="seat-signing-test")
    (row,) = _threads(mem, "seat-signing-test")
    assert "source_instance" not in row


def test_bundled_question_stamps_every_atomic_thread(mem):
    """A bundle auto-splits into atomic threads. One seat asked all of them, so
    each split must carry the author — otherwise splitting silently drops
    attribution for every item but (at best) the first."""
    mem.record_open_thread(
        "(1) does splitting keep the author? (2) does it keep it on item two?",
        domain="seat-signing-test",
        source_instance="hq-claude-studio",
    )
    rows = _threads(mem, "seat-signing-test")
    assert len(rows) > 1, "fixture no longer exercises the split path"
    assert all(r["source_instance"] == "hq-claude-studio" for r in rows)


def test_schema_declares_source_instance():
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    props = tools["record_open_thread"].inputSchema["properties"]
    assert "source_instance" in props
    assert props["source_instance"]["type"] == "string"


def test_dispatch_forwards_source_instance(monkeypatch, tmp_path):
    """THE fail-open this file exists for: a declared property that the
    dispatch never passes on. Asserted on the argument the storage layer
    actually received, not on the tool's ok-shaped response."""
    seen = {}

    def fake(question, context, domain, session_id, **kwargs):
        seen["kwargs"] = kwargs
        return str(tmp_path / f"{domain}.jsonl")

    monkeypatch.setattr(server.experiential, "record_open_thread", fake)
    asyncio.run(
        server._dispatch_tool(
            "record_open_thread",
            {"question": "q", "source_instance": "codex-astra-studio"},
        )
    )
    assert seen["kwargs"]["source_instance"] == "codex-astra-studio"
