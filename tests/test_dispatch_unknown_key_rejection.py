"""Regression drills for the tool-dispatch fail-open: `_dispatch_tool`
branches read arguments with plain ``arguments.get(...)`` and never reject
an unrecognized key — every unknown parameter is silently dropped.

HISTORICAL INCIDENT this reproduces (a few hours before this file was
written): an HQ seat wanted a claim id back from `recall_insights` and
passed `return_claim_id: true` — the *write-path* (`record_insight`)
parameter name. The read path has no such parameter; `_dispatch_tool`
silently ignored the unknown key and returned a normal, well-formed
response with no ids and no error. The seat read that silence as a
property of the system ("there is no supported read-side way to get a
claim id") and recorded the false conclusion into the chronicle as
`ground_truth`. It had to be superseded: the real read-side parameter is
`with_ids` (memory.py `recall_insights`, dispatched at server.py's
`recall_insights` branch) and it works.

A silently-ignored parameter is a fail-open: the caller gets a confident,
well-formed, WRONG answer instead of a correction. This file:

1. Reproduces the exact historical call and asserts it now raises instead
   of silently succeeding. THIS TEST MUST FAIL ON PRE-FIX CODE — that
   failure is the proof the defect is real, not assumed.
2. Confirms valid parameters (including the same `return_claim_id` /
   `with_ids` names on their CORRECT tools) are completely unaffected —
   the fix must not become a new fail-open in the other direction.

ISOLATION: reuses `_isolated_server` from tests/test_nape_autohook.py —
every dispatched call is rooted in a tmp_path chronicle. Never touches
~/.sovereign.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from tests.test_nape_autohook import _isolated_server


@pytest.fixture
def isolated():
    with _isolated_server("dispatch-unknown-key-test") as (srv, tmp_root):
        yield srv, tmp_root


def _run(coro):
    return asyncio.run(coro)


async def _call(srv, name, arguments):
    return await srv._dispatch_tool(name, arguments)


def _seed(srv, content="seed entry", domain="test-domain"):
    _run(_call(srv, "record_insight", {"domain": domain, "content": content}))


class TestRecallInsightsRejectsUnknownKeys:
    """The exact historical misfire, reproduced against the live dispatcher."""

    def test_return_claim_id_on_recall_insights_must_raise(self, isolated):
        srv, _ = isolated
        _seed(srv)

        with pytest.raises(ValueError) as excinfo:
            _run(_call(srv, "recall_insights", {"query": "seed", "return_claim_id": True}))

        message = str(excinfo.value)
        assert "return_claim_id" in message
        # Nearest-match must point at the real read-side equivalent, not
        # just say "unknown" and leave the caller to guess again.
        assert "with_ids" in message

    def test_arbitrary_unknown_key_names_the_offender(self, isolated):
        srv, _ = isolated
        with pytest.raises(ValueError) as excinfo:
            _run(_call(srv, "recall_insights", {"totally_made_up_arg": True}))
        assert "totally_made_up_arg" in str(excinfo.value)


class TestRecallInsightsControlValidParamsUnaffected:
    """Every currently-documented recall_insights parameter must be untouched."""

    def test_with_ids_still_returns_ids(self, isolated):
        srv, _ = isolated
        _seed(srv)
        result = _run(_call(srv, "recall_insights", {"query": "seed", "with_ids": True}))
        payload = json.loads(result[0].text)
        items = payload["items"]
        assert items, "expected at least one matched entry"
        assert all("claim_id" in item for item in items)

    def test_plain_query_unaffected(self, isolated):
        srv, _ = isolated
        _seed(srv)
        result = _run(_call(srv, "recall_insights", {"query": "seed"}))
        payload = json.loads(result[0].text)
        assert payload["items"]

    def test_full_documented_param_set_unaffected(self, isolated):
        """One call exercising every recall_insights schema property at once —
        if the allowlist and the schema ever drift apart, this is where it
        would show up first."""
        srv, _ = isolated
        _seed(srv)
        result = _run(
            _call(
                srv,
                "recall_insights",
                {
                    "query": "seed",
                    "domain": "test-domain",
                    "limit": 5,
                    "start_date": "2020-01-01",
                    "end_date": "2099-01-01",
                    "since_last_reflection": False,
                    "with_ids": True,
                    "exclude_superseded": False,
                    "domain_contains": "test",
                    "order": "newest",
                    "offset": 0,
                    "content_class": ["outcome"],
                    "exclude_content_class": [],
                },
            )
        )
        # content_class=["outcome"] on an untagged seed entry legitimately
        # returns empty (include-mode blinds untagged entries) — the point
        # of this drill is "no exception", not "non-empty".
        payload = json.loads(result[0].text)
        assert "items" in payload


class TestRecordInsightControlAndCrossCheck:
    """record_insight's own valid parameters, including its return_claim_id,
    keep working; the read-side name (with_ids) is rejected on the write
    path with a symmetric nearest-match hint."""

    def test_return_claim_id_still_works_on_record_insight(self, isolated):
        srv, _ = isolated
        result = _run(
            _call(
                srv,
                "record_insight",
                {"domain": "test-domain", "content": "hello", "return_claim_id": True},
            )
        )
        text = result[0].text
        assert "claim_id:" in text

    def test_with_ids_on_record_insight_is_rejected_with_hint(self, isolated):
        srv, _ = isolated
        with pytest.raises(ValueError) as excinfo:
            _run(
                _call(
                    srv,
                    "record_insight",
                    {"domain": "test-domain", "content": "hello", "with_ids": True},
                )
            )
        message = str(excinfo.value)
        assert "with_ids" in message
        assert "return_claim_id" in message

    def test_full_documented_param_set_unaffected(self, isolated):
        srv, _ = isolated
        result = _run(
            _call(
                srv,
                "record_insight",
                {
                    "domain": "test-domain",
                    "content": "hello",
                    "intensity": 0.5,
                    "layer": "hypothesis",
                    "confidence": 0.8,
                    "vantage": "hq_filesystem",
                    "content_class": "outcome",
                    "return_claim_id": True,
                },
            )
        )
        assert "Insight recorded" in result[0].text


class TestBridgeInjectedKeysTolerated:
    """~/sovereign-bridge's /api/call injects session_token_id +
    source_instance into record_insight's arguments for scoped-session-token
    (Claude connector 'Door That Asks') callers — see bridge.py's
    req.arguments.setdefault(...) calls. Neither key is in the MCP-facing
    inputSchema; both are silently dropped downstream today (memory.py's own
    comment: "metadata is dropped by the server before it reaches here").
    That pre-existing silent no-op must NOT become a hard failure just
    because this guard now exists — these two keys are explicitly tolerated
    for record_insight so this fix cannot itself break that live surface."""

    def test_session_token_id_and_source_instance_tolerated_on_record_insight(self, isolated):
        srv, _ = isolated
        result = _run(
            _call(
                srv,
                "record_insight",
                {
                    "domain": "test-domain",
                    "content": "hello",
                    "session_token_id": "tok_abc123",
                    "source_instance": "claude-connector-test",
                },
            )
        )
        assert "Insight recorded" in result[0].text

    def test_same_keys_still_rejected_on_recall_insights(self, isolated):
        """The tolerance is record_insight-specific — recall_insights has no
        known caller injecting these, so they stay rejected there."""
        srv, _ = isolated
        with pytest.raises(ValueError) as excinfo:
            _run(_call(srv, "recall_insights", {"session_token_id": "tok_abc123"}))
        assert "session_token_id" in str(excinfo.value)
