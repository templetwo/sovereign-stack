"""
Drain provenance passthrough — the bridge_core commit path must forward the
PROPOSAL's substrate identity to the Stack, never the drain operator's.

Live specimen this closes (2026-08-03T00:36Z): HQ live-committed six Grok
proposals via `cli --source=grok`. The proposal files carried
source_instance='grok-4.5-mesh-3of3-20260802' and a session_id, but
commit_pending_write forwarded only proposal.arguments — so the two handoff
commits landed in the Stack as source_instance='unknown' under the HQ seat's
live spiral session, and end_bridge_session executed close_session against
the HQ session (advancing its phase) with no trace of the proposer.

Every test here is unit-level on the built REST request body:
  - httpx.post is monkeypatched to a capture stub — the live bridge is NEVER
    called (the ctx URL is unroutable on purpose, belt and suspenders);
  - all queue/audit state lives under tmp_path — nothing touches ~/.sovereign.

Prove-can-fail, both directions:
  - forward direction: on the unfixed tree the source_instance assertions in
    the wire-body tests fail (the body has no source_instance at all);
  - reverse direction: the stale-args test fails if the drain ever lets a
    non-envelope identity (e.g. the operator's) through, and the
    record_insight test fails if injection is ever applied blindly to a
    target whose handler silently drops the kwarg.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from bridge_core.context import BridgeContext
from bridge_core.pending_writes import (
    approve_pending_write,
    commit_pending_write,
    create_pending_write,
)
from bridge_core.rings import (
    CANONICAL_COMMIT_TARGETS,
    CANONICAL_RING_1,
    CANONICAL_RING_2,
)

# The proposer, as the live specimen named itself. The drain operator (HQ's
# terminal session) deliberately has NO representation in this file — no
# value of the operator's may ever appear in a built request body.
PROPOSER = "grok-4.5-mesh-3of3-20260802"
PROPOSER_SESSION = "proposer-session-d34db33f"


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    """Substrate context fully scoped under tmp_path. Never ~/.sovereign."""
    monkeypatch.setenv("TEST_DRAIN_TOKEN", "test-token-not-real")
    return BridgeContext(
        substrate="grok-xai",
        pending_writes_dir=tmp_path / "pending_writes",
        audit_dir=tmp_path / "audit",
        sessions_dir=tmp_path / "sessions",
        ring_1_tools=CANONICAL_RING_1,
        ring_2_tools=CANONICAL_RING_2,
        commit_targets=dict(CANONICAL_COMMIT_TARGETS),
        # Unroutable on purpose: if the capture stub is ever bypassed, the
        # commit errors instead of reaching a real bridge.
        bridge_rest_url="http://127.0.0.1:1",
        bridge_rest_token_env="TEST_DRAIN_TOKEN",
    )


@pytest.fixture
def captured_posts(monkeypatch):
    """Capture httpx.post request bodies; no real request ever leaves."""
    calls: list[dict] = []

    class _FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True, "result": "stubbed"}

    def _fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers})
        return _FakeResponse()

    import bridge_core.pending_writes as pw

    monkeypatch.setattr(pw.httpx, "post", _fake_post)
    return calls


def _drain(ctx, tool, args, *, live=True):
    """Propose → approve → commit, exactly the cli --source=grok drain shape."""
    proposal = create_pending_write(
        ctx,
        tool,
        args,
        source_instance=PROPOSER,
        session_id=PROPOSER_SESSION,
    )
    approve_pending_write(ctx, proposal.proposal_id, approved_by="Anthony")
    return commit_pending_write(ctx, proposal.proposal_id, live=live)


# ── Forward direction: the proposer's identity travels ───────────────────────


def test_handoff_commit_body_carries_proposer_source_instance(ctx, captured_posts):
    _drain(ctx, "handoff", {"note": "mesh lane 3 report landed", "thread": "mesh-20260802"})

    assert len(captured_posts) == 1
    body = captured_posts[0]["json"]
    assert body["tool"] == "handoff"
    # THE fix: without provenance passthrough the Stack handler defaults to
    # "unknown" and the proposer vanishes from the record.
    assert body["arguments"]["source_instance"] == PROPOSER
    # Payload untouched around the injection.
    assert body["arguments"]["note"] == "mesh lane 3 report landed"
    assert body["arguments"]["thread"] == "mesh-20260802"
    # No invented session kwarg: handoff's inputSchema has no session
    # parameter, and the server drops unknown named args SILENTLY — injecting
    # one would be the same fail-open shape this fix closes. The proposer's
    # session identity lives in the proposal envelope + audit trail.
    assert "session_id" not in body["arguments"]
    assert "source_session_id" not in body["arguments"]


def test_end_bridge_session_commit_body_carries_proposer_source_instance(ctx, captured_posts):
    _drain(ctx, "end_bridge_session", {"what_i_learned": "ring 2 drain shape holds"})

    assert len(captured_posts) == 1
    body = captured_posts[0]["json"]
    # end_bridge_session commits as the Stack's close_session.
    assert body["tool"] == "close_session"
    assert body["arguments"]["source_instance"] == PROPOSER
    assert body["arguments"]["what_i_learned"] == "ring 2 drain shape holds"
    assert "session_id" not in body["arguments"]
    assert "source_session_id" not in body["arguments"]


# ── Reverse direction: nothing but the envelope's identity may travel ─────────


def test_envelope_wins_over_stale_source_instance_in_arguments(ctx, captured_posts):
    """A source_instance smuggled inside the args dict never reaches the wire.

    pop_bridge_metadata strips source_instance from args on the SSE/text-relay
    paths, but a proposal file is reviewable state — if one arrives with a
    foreign identity in its arguments (operator's, stale, crafted), the
    envelope's identity-gate value must overwrite it.
    """
    _drain(
        ctx,
        "handoff",
        {
            "note": "identity precedence check",
            "thread": "mesh-20260802",
            "source_instance": "some-other-seat-not-the-proposer",
        },
    )

    body = captured_posts[0]["json"]
    assert body["arguments"]["source_instance"] == PROPOSER
    assert "some-other-seat-not-the-proposer" not in str(body)


def test_record_insight_commit_body_is_not_injected(ctx, captured_posts):
    """record_insight is DELIBERATELY outside the passthrough set.

    Its Stack handler reads only schema-named args and silently drops the
    rest (documented on branch fix/tool-dispatch-unknown-key-rejection), so
    an injected source_instance kwarg would vanish without error. The origin
    carrier there is line-one self-naming in the content body — which this
    test's payload demonstrates and asserts.
    """
    content = f"{PROPOSER}: line-one self-naming is the origin carrier here"
    _drain(
        ctx,
        "propose_insight",
        {"domain": "mesh-20260802", "content": content, "layer": "reflection"},
    )

    body = captured_posts[0]["json"]
    assert body["tool"] == "record_insight"
    # No silently-dropped kwargs, ever.
    assert "source_instance" not in body["arguments"]
    assert "session_id" not in body["arguments"]
    # Layer translation still applies (bridge "reflection" → Stack "hypothesis").
    assert body["arguments"]["layer"] == "hypothesis"
    # The convention the non-injection relies on, held visibly.
    assert body["arguments"]["content"].startswith(PROPOSER)


# ── Review surface: the dry run shows the wire truth ─────────────────────────


def test_dry_run_review_surface_matches_wire(ctx, captured_posts):
    """live=False must preview the body a live commit would send.

    A review surface that differs from the wire is how 'unknown' authorship
    shipped — Anthony approved bodies that looked complete and the wire sent
    something else.
    """
    proposal = _drain(
        ctx,
        "handoff",
        {"note": "dry run truth check", "thread": "mesh-20260802"},
        live=False,
    )

    assert captured_posts == []  # dry run: nothing on the wire
    result = proposal.commit_result
    assert result["live"] is False
    assert result["would_call"] == "handoff"
    assert result["with_arguments"]["source_instance"] == PROPOSER


# ── Schema tripwire: the passthrough set stays true to server.py ─────────────


def test_passthrough_targets_match_server_input_schemas():
    """Every passthrough target must declare source_instance in server.py,
    and record_insight must NOT — if the Stack schema ever changes either
    way, this fails and forces the set to be re-derived, not assumed.

    Parses the schema blocks structurally (python, not grep) from THIS
    tree's server.py.
    """
    # Imported here, not at module top: on the unfixed tree this name does
    # not exist, and the failure should attribute to this test.
    from bridge_core.pending_writes import PROVENANCE_PASSTHROUGH_TARGETS

    server_py = Path(__file__).resolve().parent.parent / "src" / "sovereign_stack" / "server.py"
    text = server_py.read_text()

    def schema_block(tool_name: str) -> str:
        marker = f'name="{tool_name}"'
        start = text.index(marker)
        nxt = text.find('name="', start + len(marker))
        return text[start : nxt if nxt != -1 else len(text)]

    assert {"handoff", "close_session"} == PROVENANCE_PASSTHROUGH_TARGETS
    for target in sorted(PROVENANCE_PASSTHROUGH_TARGETS):
        assert '"source_instance"' in schema_block(target), (
            f"{target} no longer declares source_instance in server.py — "
            "the passthrough set is stale"
        )
    assert '"source_instance"' not in schema_block("record_insight"), (
        "record_insight now declares source_instance in server.py — "
        "add it to PROVENANCE_PASSTHROUGH_TARGETS and drop the line-one "
        "self-naming carve-out"
    )
