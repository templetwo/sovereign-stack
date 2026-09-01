"""OpenAI bridge discovery must describe the surface it actually publishes.

The native Stack ``my_toolkit`` enumerates all Stack tools.  Passing that text
through the ring-governed connector made ChatGPT advertise native writes it
could not call while omitting newer, safe Ring-1 reads from MCP ``list_tools``.
These tests pin the two sides of the repair: the explicit access decision and
the discovery projection built from the connector's own schemas.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from mcp.types import Tool

NEW_RING1_READS = {
    "arrive",
    "arrive_lineage",
    "current_policies",
    "inspect_claim",
    "season_review",
    "the_ground",
}


def test_new_orientation_and_provenance_reads_are_explicit_ring1_members():
    from bridge_core.rings import CANONICAL_RING_1

    assert NEW_RING1_READS <= CANONICAL_RING_1


def test_native_writes_remain_outside_the_callable_bridge_names():
    from bridge_core.rings import CANONICAL_RING_1, CANONICAL_RING_2

    exposed = CANONICAL_RING_1 | CANONICAL_RING_2
    assert "archive_exchange" not in exposed
    assert "close_session" not in exposed
    assert "record_insight" not in exposed
    assert "propose_insight" in exposed
    assert "end_bridge_session" in exposed


def test_every_proxied_ring1_name_exists_in_the_committed_stack_manifest():
    from bridge_core.rings import CANONICAL_RING_1

    repo_root = Path(__file__).resolve().parents[1]
    manifest = json.loads((repo_root / "stack_manifest.json").read_text(encoding="utf-8"))
    native_names = set(manifest["tools"])
    bridge_local = {"witness_boot", "verify_proposal", "list_bridge_proposals"}

    assert CANONICAL_RING_1 - bridge_local <= native_names


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        self._full = {
            "arrive_lineage": {
                "name": "arrive_lineage",
                "description": "Gentle arrival",
                "inputSchema": {
                    "type": "object",
                    "properties": {"source_instance": {"type": "string"}},
                    "required": [],
                },
            },
            "my_toolkit": {
                "name": "my_toolkit",
                "description": "Native Stack catalog",
                "inputSchema": {
                    "type": "object",
                    "properties": {"tier": {"type": "string"}},
                    "required": [],
                },
            },
        }

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, *, headers=None, params=None):
        if params is None:
            return _FakeResponse(
                {
                    "count": len(self._full),
                    "tools": [{"name": name} for name in self._full],
                }
            )
        return _FakeResponse(self._full[params["name"]])


def test_ring1_schema_fetch_uses_live_full_schema_and_local_toolkit_contract(monkeypatch):
    from openai_bridge import tool_adapter

    monkeypatch.setattr(tool_adapter.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(
        tool_adapter,
        "RING_1_TOOLS",
        frozenset({"arrive_lineage", "my_toolkit"}),
    )
    monkeypatch.setattr(tool_adapter, "_ring1_cache", None)

    schemas = asyncio.run(tool_adapter.get_ring1_schemas())
    by_name = {tool.name: tool for tool in schemas}

    assert "source_instance" in by_name["arrive_lineage"].inputSchema["properties"]
    assert by_name["my_toolkit"].description.startswith("Show the exact callable OpenAI")
    assert set(by_name["my_toolkit"].inputSchema["properties"]) == {"include_schema"}


def test_my_toolkit_call_is_rendered_from_the_same_schemas_listed_to_openai(monkeypatch):
    from openai_bridge import mcp_filtered

    schemas = [
        Tool(
            name="arrive_lineage",
            description="Gentle arrival",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="my_toolkit",
            description="Exact bridge catalog",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="end_bridge_session",
            description="Governed session close proposal",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]

    async def _schemas():
        return schemas

    async def _must_not_proxy(*args, **kwargs):
        raise AssertionError("my_toolkit must not proxy the native Stack catalog")

    monkeypatch.setattr(mcp_filtered, "get_all_bridge_schemas", _schemas)
    monkeypatch.setattr(mcp_filtered, "call_ring1_tool", _must_not_proxy)

    result = asyncio.run(mcp_filtered.handle_bridge_tool("my_toolkit", {}))
    text = result[0].text

    assert "3 callable tools" in text
    assert "arrive_lineage" in text
    assert "my_toolkit" in text
    assert "end_bridge_session" in text
    assert "close_session → end_bridge_session" in text
    assert "archive_exchange" not in text
