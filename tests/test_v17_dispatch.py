"""Dispatch-level smoke for the six v1.7.0 tools (Receipts & Seasons).

Module-level handler tests can't catch wiring bugs in server.py's dispatch
(found live 2026-06-12: the policy dispatch dropped the registry argument
and every current_policies call failed with a TypeError). These tests run
each new tool through the REAL _dispatch_tool path, hermetically.
"""

import json

import pytest

from sovereign_stack import policies as policies_mod
from tests.test_nape_autohook import _isolated_server


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Isolated server + tmp policy registry (PolicyRegistry() in dispatch
    resolves its default path through DEFAULT_POLICIES_PATH)."""
    monkeypatch.setattr(
        policies_mod, "DEFAULT_POLICIES_PATH", tmp_path / "policies" / "policies.jsonl"
    )
    with _isolated_server("v17-dispatch-test") as (srv, tmp_root):
        yield srv, tmp_root


async def _call(srv, name, arguments):
    result = await srv._dispatch_tool(name, arguments)
    assert result and result[0].type == "text"
    return result[0].text


def _run(coro):
    import asyncio

    return asyncio.run(coro)


class TestV17Dispatch:
    def test_current_policies_dispatches(self, isolated):
        srv, _ = isolated
        text = _run(_call(srv, "current_policies", {}))
        # Empty-registry honesty line, not a TypeError.
        assert "no policies registered yet" in text.lower()

    def test_set_policy_requires_set_by(self, isolated):
        srv, _ = isolated
        text = _run(_call(srv, "set_policy", {"statement": "test", "domain": "test"}))
        assert "set_by" in text

    def test_season_review_dispatches_read_only(self, isolated):
        srv, tmp_root = isolated
        text = _run(_call(srv, "season_review", {}))
        assert "SEASON REVIEW" in text

    def test_link_threads_rejects_unknown_ids(self, isolated):
        srv, _ = isolated
        text = _run(
            _call(
                srv,
                "link_threads",
                {"thread_ids": ["thread_00000000_000000_00000000"], "label": "x"},
            )
        )
        assert (
            "rejected" in text.lower() or "not found" in text.lower() or "unknown" in text.lower()
        )

    def test_inspect_claim_unknown_id(self, isolated):
        srv, _ = isolated
        text = _run(_call(srv, "inspect_claim", {"claim_id": "f" * 64}))
        payload = json.loads(text)
        assert payload["found"] is False

    def test_supersede_insight_rejects_unknown(self, isolated):
        srv, _ = isolated
        text = _run(
            _call(
                srv,
                "supersede_insight",
                {"predecessor_id": "a" * 64, "successor_id": "b" * 64},
            )
        )
        assert (
            "rejected" in text.lower() or "not found" in text.lower() or "no entry" in text.lower()
        )
