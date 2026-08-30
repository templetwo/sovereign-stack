"""Regressions found by seat 3/2's pre-committed verification of fd73258 (v1.20/v1.21).

Every test here is written to FAIL on fd73258 and PASS after the fix, per
experimental law #2: a gate that has never been shown to fail is not a gate.

The failure class throughout is FAIL-OPEN (HQ SOP #2) — a membrane that reports
refusal it did not perform, a commit that reports "committed" on a write the
Stack rejected, a resolver that reads the gated tool's own output as evidence.

NOTHING HERE TOUCHES ~/.sovereign. Substrate context objects are monkeypatched
onto tmp dirs, and the tmp_sovereign_root fixture (conftest) sets SOVEREIGN_ROOT
so target_risk's referential checks resolve against the sandbox too.
"""

from __future__ import annotations

import json

import pytest

# ── shared fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def ag_ctx(tmp_path, monkeypatch, tmp_sovereign_root):
    """Antigravity context rooted in tmp.

    bridge_setup._ROOT is Path.home()/".sovereign"/"antigravity_connector" at
    import time, so the module-level ANTIGRAVITY_CONTEXT must be replaced, not
    merely env-shadowed.
    """
    import antigravity_connector.bridge_setup as bs
    from bridge_core import BridgeContext
    from bridge_core.rings import (
        CANONICAL_COMMIT_TARGETS,
        CANONICAL_RING_1,
        CANONICAL_RING_2,
    )

    ctx = BridgeContext(
        substrate=bs.SUBSTRATE,
        pending_writes_dir=tmp_path / "ag" / "pending_writes",
        audit_dir=tmp_path / "ag" / "audit",
        sessions_dir=tmp_path / "ag" / "sessions",
        ring_1_tools=CANONICAL_RING_1,
        ring_2_tools=CANONICAL_RING_2,
        commit_targets=dict(CANONICAL_COMMIT_TARGETS),
    )
    monkeypatch.setattr(bs, "ANTIGRAVITY_CONTEXT", ctx)
    return ctx


@pytest.fixture
def core_ctx(tmp_path, tmp_sovereign_root):
    """A generic bridge_core context on tmp dirs, unroutable REST url."""
    from bridge_core import BridgeContext
    from bridge_core.rings import (
        CANONICAL_COMMIT_TARGETS,
        CANONICAL_RING_1,
        CANONICAL_RING_2,
    )

    return BridgeContext(
        substrate="grok-xai",
        pending_writes_dir=tmp_path / "pending_writes",
        audit_dir=tmp_path / "audit",
        sessions_dir=tmp_path / "sessions",
        ring_1_tools=CANONICAL_RING_1,
        ring_2_tools=CANONICAL_RING_2,
        commit_targets=dict(CANONICAL_COMMIT_TARGETS),
        bridge_rest_url="http://127.0.0.1:1",  # unroutable on purpose
        bridge_rest_token_env="TEST_R2_TOKEN",
    )


@pytest.fixture
def openai_tmp(tmp_path, monkeypatch, tmp_sovereign_root):
    """Root the openai substrate's module-level path constants in tmp."""
    import openai_bridge.hash_chain as hc
    import openai_bridge.pending_writes as opw

    monkeypatch.setattr(opw, "PENDING_DIR", tmp_path / "oai" / "pending_writes")
    monkeypatch.setattr(hc, "AUDIT_DIR", tmp_path / "oai" / "audit")
    return tmp_path / "oai"


def _stack_response(payload: dict, status: int = 200):
    class _Resp:
        status_code = status

        def raise_for_status(self):
            return None

        def json(self):
            return payload

    return _Resp()


# ══════════════════════════════════════════════════════════════════════════════
# ITEM 1 — THE ANTIGRAVITY MEMBRANE FAILS OPEN
# ══════════════════════════════════════════════════════════════════════════════


def test_antigravity_governed_call_refuses_a_deny_compass(ag_ctx):
    """fd73258 indented the intercept() call INSIDE the list_bridge_proposals
    branch, after that branch's unconditional return — so it is dead code and
    `if not res.allowed` reads an unbound name.

    Pre-fix: UnboundLocalError (the membrane raises instead of refusing).
    Post-fix: a refusal, with the compass named.
    """
    from antigravity_connector.bridge_setup import governed_call

    def _must_not_dispatch(name, args):  # pragma: no cover - proves non-dispatch
        raise AssertionError(f"Ring 1 dispatch reached for a denied call: {name}")

    result = governed_call(
        _must_not_dispatch,
        "propose_insight",
        {"content": "a denied write", "domain": "test", "layer": "hypothesis"},
        "test-seat",
        substrate="gemini-antigravity",
        compass_check_result="witness",  # mixed case: normalisation must catch it
    )

    assert result.get("isError") is True, f"membrane did not refuse: {result}"
    text = result["content"][0]["text"]
    assert "WITNESS" in text.upper()


def test_antigravity_governed_call_still_creates_a_proposal_when_allowed(ag_ctx):
    """The re-indent must not break the ordinary Ring 2 path."""
    from antigravity_connector.bridge_setup import governed_call

    result = governed_call(
        lambda name, args: {"content": [{"type": "text", "text": "ring1"}]},
        "propose_insight",
        {"content": "an ordinary write", "domain": "test", "layer": "hypothesis"},
        "test-seat",
        substrate="gemini-antigravity",
        compass_check_result="PROCEED",
    )

    assert result.get("isError") is not True
    assert "PROPOSAL CREATED" in result["content"][0]["text"]


def test_run_proxy_governance_exception_does_not_forward_ungoverned(monkeypatch):
    """A bare `except Exception` around the governance call fell through to the
    raw-forward at the bottom of the loop, sending an ungoverned tools/call to
    the spawned sovereign.

    Pre-fix: the raw line is forwarded (governance bypassed entirely).
    Post-fix: nothing is forwarded and a JSON-RPC error response is written.
    """
    import io

    import antigravity_connector.sovereign_connector as sc

    forwarded: list[str] = []
    written: list[str] = []

    class _FakeProcStdin:
        def write(self, data):
            forwarded.append(data)

        def flush(self):
            return None

    class _FakeProc:
        """stdout/stderr are exhausted StringIOs, so the pump threads exit at once."""

        stdin = _FakeProcStdin()
        stdout = io.StringIO()
        stderr = io.StringIO()

        def terminate(self):
            return None

        def wait(self):
            return None

    def _boom(*a, **kw):
        raise RuntimeError("governance subsystem exploded")

    monkeypatch.setattr(sc, "governed_call", _boom)

    call_line = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "propose_insight", "arguments": {"content": "x"}},
        }
    )

    conn = sc.SovereignConnector.__new__(sc.SovereignConnector)
    conn.process = _FakeProc()
    # start() would spawn the real `sovereign` binary; the proxy loop is what
    # is under test, not the handshake.
    monkeypatch.setattr(sc.SovereignConnector, "start", lambda self, perform_handshake=False: None)

    monkeypatch.setattr(sc.sys, "stdin", io.StringIO(call_line + "\n"))

    class _Out:
        def write(self, d):
            written.append(d)

        def flush(self):
            return None

    monkeypatch.setattr(sc.sys, "stdout", _Out())

    conn.run_proxy(source_instance="test-seat", substrate="gemini-antigravity")

    assert forwarded == [], (
        f"a governance failure forwarded the raw tools/call ungoverned: {forwarded}"
    )
    joined = "".join(written)
    assert joined, "no response written — the caller is left hanging"
    assert "error" in joined.lower()


# ══════════════════════════════════════════════════════════════════════════════
# ITEM 2 — receipt_url LAUNDERED INTO "committed"
# ══════════════════════════════════════════════════════════════════════════════


def test_commit_does_not_mark_committed_when_stack_returns_ok_false(core_ctx, monkeypatch):
    """FIX A. The bridge returns HTTP 200 with {"ok": false} when the Stack
    handler raises; commit_pending_write called raise_for_status() (which sees
    200 and is happy) then set status="committed" without inspecting `ok`.

    Pre-fix: status == "committed" on a write that never landed.
    Post-fix: NOT committed, error captured, audit event written.
    """
    import bridge_core.pending_writes as pw

    monkeypatch.setenv("TEST_R2_TOKEN", "t")
    monkeypatch.setattr(
        pw.httpx,
        "post",
        lambda *a, **kw: _stack_response(
            {"ok": False, "error": "record_insight: unknown parameter 'receipt_url'"}
        ),
    )

    p = pw.create_pending_write(
        core_ctx,
        "propose_insight",
        {"content": "c", "domain": "d", "layer": "hypothesis"},
        source_instance="seat",
    )
    pw.approve_pending_write(core_ctx, p.proposal_id, approved_by="Anthony")
    committed = pw.commit_pending_write(core_ctx, p.proposal_id, live=True)

    assert committed.status != "committed", (
        "a rejected write was laundered into 'committed' — the exact fail-open "
        "this fix exists to close"
    )
    blob = json.dumps(committed.commit_result or {})
    assert "receipt_url" in blob or "unknown parameter" in blob, (
        f"the Stack's error was not captured: {committed.commit_result}"
    )


def test_commit_still_marks_committed_on_a_genuine_ok(core_ctx, monkeypatch):
    """The FIX A guard must not refuse a real success."""
    import bridge_core.pending_writes as pw

    monkeypatch.setenv("TEST_R2_TOKEN", "t")
    monkeypatch.setattr(
        pw.httpx, "post", lambda *a, **kw: _stack_response({"ok": True, "result": "ok"})
    )

    p = pw.create_pending_write(
        core_ctx,
        "propose_insight",
        {"content": "c", "domain": "d", "layer": "hypothesis"},
        source_instance="seat",
    )
    pw.approve_pending_write(core_ctx, p.proposal_id, approved_by="Anthony")
    assert pw.commit_pending_write(core_ctx, p.proposal_id, live=True).status == "committed"


def test_commit_marks_committed_when_response_omits_ok(core_ctx, monkeypatch):
    """Absence of `ok` is not failure. Only an EXPLICITLY falsy `ok` blocks —
    otherwise every tool whose envelope omits the key would become uncommittable.
    """
    import bridge_core.pending_writes as pw

    monkeypatch.setenv("TEST_R2_TOKEN", "t")
    monkeypatch.setattr(
        pw.httpx, "post", lambda *a, **kw: _stack_response({"result": "no ok key here"})
    )

    p = pw.create_pending_write(
        core_ctx, "handoff", {"note": "n", "thread": "t"}, source_instance="seat"
    )
    pw.approve_pending_write(core_ctx, p.proposal_id, approved_by="Anthony")
    assert pw.commit_pending_write(core_ctx, p.proposal_id, live=True).status == "committed"


def test_receipt_url_is_translated_to_verified_by_on_the_wire(core_ctx):
    """FIX B. `receipt_url` is advertised in all three bridges' propose_insight
    schemas and is load-bearing (has_receipt / ground_truth-requires-receipt),
    but it is NOT a declared record_insight parameter — so since fd73258's
    _reject_unknown_params every such commit raises server-side.

    `verified_by` IS declared, and provenance.py stamps kind="url" as
    "attested" with no live check, so the translation is lossless and safe.

    Pre-fix: receipt_url rides to the Stack verbatim and is rejected.
    Post-fix: it leaves as verified_by=[{"kind": "url", "ref": ...}].
    """
    import bridge_core.pending_writes as pw

    p = pw.create_pending_write(
        core_ctx,
        "propose_insight",
        {
            "content": "c",
            "domain": "d",
            "layer": "ground_truth",
            "receipt_url": "https://example.com/proof",
        },
        source_instance="seat",
    )
    args = pw.build_commit_arguments(core_ctx, p)

    assert "receipt_url" not in args, (
        "receipt_url would reach record_insight, which does not declare it — "
        "_reject_unknown_params raises and the bridge reports ok:false"
    )
    assert {"kind": "url", "ref": "https://example.com/proof"} in args.get("verified_by", []), (
        f"the receipt was dropped rather than translated: {args}"
    )


def test_receipt_url_translation_preserves_existing_verified_by(core_ctx):
    """A proposal carrying both must not lose the pre-existing receipts."""
    import bridge_core.pending_writes as pw

    p = pw.create_pending_write(
        core_ctx,
        "propose_insight",
        {
            "content": "c",
            "domain": "d",
            "layer": "ground_truth",
            "receipt_url": "https://example.com/b",
            "verified_by": [{"kind": "cmd", "ref": "git log -1"}],
        },
        source_instance="seat",
    )
    args = pw.build_commit_arguments(core_ctx, p)

    assert "receipt_url" not in args
    kinds = [(r["kind"], r["ref"]) for r in args["verified_by"]]
    assert ("cmd", "git log -1") in kinds
    assert ("url", "https://example.com/b") in kinds


def test_openai_commit_does_not_launder_ok_false_into_committed(openai_tmp, monkeypatch):
    """FIX A on the second substrate — the same defect, separately implemented."""
    import openai_bridge.pending_writes as opw

    monkeypatch.setenv("BRIDGE_TOKEN", "t")
    monkeypatch.setattr(
        opw.httpx,
        "post",
        lambda *a, **kw: _stack_response({"ok": False, "error": "unknown parameter"}),
    )

    p = opw.create_pending_write(
        "propose_insight",
        {"content": "c", "domain": "d", "layer": "hypothesis"},
        source_instance="seat",
    )
    opw.approve_pending_write(p.proposal_id, approved_by="Anthony")
    committed = opw.commit_pending_write(p.proposal_id, live=True)

    assert committed.status != "committed"
    assert "unknown parameter" in json.dumps(committed.commit_result or {})


def test_openai_receipt_url_is_translated_to_verified_by(openai_tmp, monkeypatch):
    """FIX B on the second substrate. openai has no build_commit_arguments — the
    translation lands beside its inline layer translation in commit_pending_write.
    """
    import openai_bridge.pending_writes as opw

    sent: list[dict] = []

    def _capture(url, json=None, headers=None, timeout=None):
        sent.append(json)
        return _stack_response({"ok": True})

    monkeypatch.setenv("BRIDGE_TOKEN", "t")
    monkeypatch.setattr(opw.httpx, "post", _capture)

    p = opw.create_pending_write(
        "propose_insight",
        {
            "content": "c",
            "domain": "d",
            "layer": "ground_truth",
            "receipt_url": "https://example.com/p",
        },
        source_instance="seat",
    )
    opw.approve_pending_write(p.proposal_id, approved_by="Anthony")
    opw.commit_pending_write(p.proposal_id, live=True)

    args = sent[0]["arguments"]
    assert "receipt_url" not in args
    assert {"kind": "url", "ref": "https://example.com/p"} in args.get("verified_by", [])


# ══════════════════════════════════════════════════════════════════════════════
# ITEM 3 — THE PRODUCTION-PATH GATE TEST
# ══════════════════════════════════════════════════════════════════════════════


def test_production_path_mixed_case_deny_is_refused_at_create_and_at_commit(core_ctx, monkeypatch):
    """The gate proven through the door production actually uses.

    Every existing compass test calls create_pending_write with an explicit
    compass_check_result kwarg. Production does not: the SSE handler puts the
    value INSIDE the tool args and pop_bridge_metadata lifts it out. A test that
    skips that step cannot see a regression in the wiring between them.

    Enters via dispatch.pop_bridge_metadata with a mixed-case "witness" in args.
    """
    import bridge_core.pending_writes as pw
    from bridge_core.dispatch import pop_bridge_metadata

    args = {
        "content": "a write behind a deny",
        "domain": "test",
        "layer": "hypothesis",
        "compass_check_result": "witness",  # the casing that defeated `== "WITNESS"`
    }
    meta = pop_bridge_metadata(args, "grok-xai")
    assert "compass_check_result" not in args, "pop_bridge_metadata contract changed"

    # ── refusal at CREATE ────────────────────────────────────────────────────
    with pytest.raises(Exception) as created:
        pw.create_pending_write(
            core_ctx,
            "propose_insight",
            args,
            source_instance=meta["source_instance"],
            session_id=meta["session_id"],
            compass_check_result=meta["compass_check_result"],
            compass_check_rationale=meta["compass_check_rationale"],
        )
    assert "WITNESS" in str(created.value).upper()

    # ── refusal at COMMIT, for a proposal that reached the queue anyway ──────
    p = pw.create_pending_write(
        core_ctx,
        "propose_insight",
        {"content": "c", "domain": "d", "layer": "hypothesis"},
        source_instance="seat",
    )
    (path,) = core_ctx.pending_writes_dir.glob(f"*{p.proposal_id[:8]}*.json")
    raw = json.loads(path.read_text())
    raw["compass_check_result"] = "witness"
    raw["status"] = "approved"
    # RECOMPUTE the audit hash. Without this the proposal also trips the
    # tamper check, and _precondition_check aggregates all errors — so the
    # assertion below would pass on the hash error even if the compass gate
    # were gone. Re-hashing leaves the compass block as the ONLY thing that
    # can refuse this commit, which is what the test claims to prove.
    from bridge_core.hash_chain import hash_pending_write

    raw["audit_hash"] = hash_pending_write(raw, raw.get("prev_hash"))
    path.write_text(json.dumps(raw))

    monkeypatch.setenv("TEST_R2_TOKEN", "t")

    def _must_not_post(*a, **kw):  # pragma: no cover - proves non-dispatch
        raise AssertionError("a denied proposal reached the Stack")

    monkeypatch.setattr(pw.httpx, "post", _must_not_post)

    with pytest.raises(Exception) as committed:
        pw.commit_pending_write(core_ctx, p.proposal_id, live=True)
    assert "WITNESS" in str(committed.value).upper()


# ══════════════════════════════════════════════════════════════════════════════
# ITEM 4 — ONE COMPASS ENUM, NOT THREE
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("module", ["bridge_core.risk", "openai_bridge.risk"])
def test_risk_modules_consume_the_shared_deny_constant(module, monkeypatch):
    """Behavioral, not a source grep: widen the shared constant and assert the
    consumer's behaviour changes. A bare literal in risk.py cannot notice.

    This only works if risk.py imports the constant INSIDE the function (late
    binding), which is how it already imports normalize_compass.
    """
    import importlib

    import bridge_core.target_risk as tr

    risk_mod = importlib.import_module(module)

    # PROCEED is not a deny anywhere. If the consumer truly reads the shared
    # constant, adding it makes PROCEED escalate to CRITICAL.
    monkeypatch.setattr(
        tr, "DENY_OR_UNRECOGNISED", frozenset({"PAUSE", "WITNESS", "UNRECOGNISED", "PROCEED"})
    )

    level, reasons = risk_mod.risk_classify(
        "propose_insight",
        {"content": "c", "domain": "d"},
        compass_check_result="PROCEED",
    )
    assert level == risk_mod.RiskLevel.CRITICAL, (
        f"{module} did not consult target_risk.DENY_OR_UNRECOGNISED — it still "
        "carries its own literal copy of the enum"
    )


def test_shared_deny_constant_covers_both_denies_and_the_unrecognised_sentinel():
    from bridge_core.target_risk import (
        _DENY_COMPASS_VALUES,
        DENY_OR_UNRECOGNISED,
    )

    assert _DENY_COMPASS_VALUES | {"UNRECOGNISED"} == DENY_OR_UNRECOGNISED
    assert "PROCEED" not in DENY_OR_UNRECOGNISED


# ══════════════════════════════════════════════════════════════════════════════
# ITEM 5 — classify_tool NameError ON EVERY RING-2 TOOL
# ══════════════════════════════════════════════════════════════════════════════


def test_bridge_core_classify_tool_handles_a_ring_2_tool(core_ctx):
    """fd73258 added `compass_check_result=compass_check_result` to the
    risk_classify call inside classify_tool without adding the parameter — an
    unbound name on every Ring 2 tool. The only harness reaching it is
    grok_bridge/_smoke_test.py, which pytest never collects (testpaths=["tests"],
    python_files=["test_*.py"]).
    """
    from bridge_core.interceptor import classify_tool

    r = classify_tool(core_ctx, "propose_insight", {"content": "c", "domain": "d"})
    assert r["ring"] == 2
    assert "risk_level" in r


def test_bridge_core_classify_tool_threads_the_compass(core_ctx):
    from bridge_core.interceptor import classify_tool

    r = classify_tool(
        core_ctx,
        "propose_insight",
        {"content": "c", "domain": "d"},
        compass_check_result="witness",
    )
    assert r["risk_level"] == "critical"


def test_openai_classify_tool_handles_a_ring_2_tool(tmp_sovereign_root):
    from openai_bridge.interceptor import classify_tool

    r = classify_tool("propose_insight", {"content": "c", "domain": "d"})
    assert r["ring"] == 2
    assert "risk_level" in r


def test_classify_tool_still_classifies_ring_1_and_ring_3(core_ctx):
    from bridge_core.interceptor import classify_tool

    assert classify_tool(core_ctx, "where_did_i_leave_off")["ring"] == 1
    assert classify_tool(core_ctx, "guardian_quarantine", {})["ring"] == 3


# ══════════════════════════════════════════════════════════════════════════════
# ITEM 6 — THE RESOLVER READS THE GATED TOOL'S OWN OUTPUT
# ══════════════════════════════════════════════════════════════════════════════


def test_acks_file_alone_does_not_make_a_phantom_message_id_resolve(tmp_path):
    """_resolve_comms_message globbed every *.jsonl under comms/ — including
    acks.jsonl, which comms_acknowledge ITSELF writes. So acknowledging a
    phantom id once made every later ack of that id resolve: the gate cited its
    own output as evidence the target existed.

    Live specimen: id 'the-spiral-hums'.

    Pre-fix: MISSING is not returned — the phantom resolves.
    Post-fix: acks.jsonl is excluded and the phantom is refused.
    """
    from bridge_core.target_risk import TargetStatus, referential_errors, resolve_target

    comms = tmp_path / "comms"
    comms.mkdir(parents=True)
    (comms / "messages.jsonl").write_text(json.dumps({"id": "a-real-message"}) + "\n")
    # the only trace of the phantom is the gated tool's own prior output
    (comms / "acks.jsonl").write_text(json.dumps({"id": "the-spiral-hums"}) + "\n")

    args = {"message_id": "the-spiral-hums"}
    res = resolve_target("comms_acknowledge", args, root=tmp_path)

    assert res.status == TargetStatus.MISSING, (
        "acks.jsonl was accepted as evidence the message exists — the resolver "
        "is reading the output of the very tool it gates"
    )
    assert referential_errors("comms_acknowledge", args, root=tmp_path)


def test_a_genuine_message_still_resolves_after_the_acks_exclusion(tmp_path):
    """The exclusion must not blind the resolver to real messages."""
    from bridge_core.target_risk import TargetStatus, resolve_target

    comms = tmp_path / "comms"
    comms.mkdir(parents=True)
    (comms / "messages.jsonl").write_text(json.dumps({"id": "a-real-message"}) + "\n")
    (comms / "acks.jsonl").write_text(json.dumps({"id": "a-real-message"}) + "\n")

    res = resolve_target("comms_acknowledge", {"message_id": "a-real-message"}, root=tmp_path)
    assert res.status != TargetStatus.MISSING


def test_acks_exclusion_does_not_leak_into_other_resolvers(tmp_path):
    """reflection_ack and thread_touch read different directories; an acks.jsonl
    living there is ordinary content and must still resolve."""
    from bridge_core.target_risk import TargetStatus, resolve_target

    refl = tmp_path / "reflections"
    refl.mkdir(parents=True)
    (refl / "acks.jsonl").write_text(json.dumps({"id": "reflection_real"}) + "\n")

    res = resolve_target("reflection_ack", {"reflection_id": "reflection_real"}, root=tmp_path)
    assert res.status != TargetStatus.MISSING


# ══════════════════════════════════════════════════════════════════════════════
# ITEM 7 — A BLANK COMPASS SATISFIES THE CRITICAL GATE
# ══════════════════════════════════════════════════════════════════════════════


def test_blank_compass_does_not_satisfy_the_critical_gate(core_ctx):
    """validate_pending_write decided "compass present" by raw truthiness, so
    "   " passed the CRITICAL-requires-compass gate while normalize_compass
    treats it as absent (verified: normalize_compass("   ") is None, and
    compass_create_error("   ") returns None — nothing else catches it).

    A whitespace string is the cheapest possible way to claim you ran the
    compass without running it.

    Pre-fix: accepted. Post-fix: refused, exactly as an absent compass is.
    """
    import bridge_core.pending_writes as pw

    # An identity claim escalates MEDIUM → CRITICAL on the hypothesis layer, so
    # CRITICAL is reached without also tripping the ground_truth receipt gate:
    # the compass error is then the ONLY error, and the sole discriminator.
    critical_args = {
        "content": "i remember the founding night",
        "domain": "d",
        "layer": "hypothesis",
    }

    with pytest.raises(Exception) as exc:
        pw.create_pending_write(
            core_ctx,
            "propose_insight",
            critical_args,
            source_instance="seat",
            compass_check_result="   ",
        )
    assert "compass" in str(exc.value).lower()


def test_openai_blank_compass_does_not_satisfy_the_critical_gate(openai_tmp):
    import openai_bridge.pending_writes as opw

    with pytest.raises(Exception) as exc:
        opw.create_pending_write(
            "propose_insight",
            {
                "content": "i remember the founding night",
                "domain": "d",
                "layer": "hypothesis",
            },
            source_instance="seat",
            compass_check_result="   ",
        )
    assert "compass" in str(exc.value).lower()


def test_a_real_compass_value_still_satisfies_the_critical_gate(core_ctx):
    """The blank-compass fix must not refuse a genuine PROCEED."""
    import bridge_core.pending_writes as pw

    p = pw.create_pending_write(
        core_ctx,
        "propose_insight",
        {
            "content": "i remember the founding night",
            "domain": "d",
            "layer": "hypothesis",
        },
        source_instance="seat",
        compass_check_result="PROCEED",
    )
    assert p.proposal_id


# ══════════════════════════════════════════════════════════════════════════════
# ITEM 2, END TO END — against the REAL record_insight handler, not a mock
# ══════════════════════════════════════════════════════════════════════════════
#
# Every other FIX B test asserts on build_commit_arguments output or a stubbed
# httpx response. Those prove the bridge emits what we intended; they cannot
# prove the STACK accepts it. Without this pair, "receipt_url is now translated"
# would rest on reading a docstring — and the failure mode we were guarding
# against (swapping one ok:false for a different ok:false) is invisible to a
# mock by construction.


def _dispatch(tool: str, args: dict) -> str:
    import asyncio

    from sovereign_stack.server import _dispatch_tool

    result = asyncio.run(_dispatch_tool(tool, args))
    return "".join(getattr(c, "text", "") for c in result)


@pytest.fixture
def rooted_chronicle(tmp_path, monkeypatch, tmp_sovereign_root):
    """Re-root the SERVER's chronicle singleton, not just the environment.

    SOVEREIGN_ROOT alone is NOT enough here and the difference is dangerous.
    server.py resolves CHRONICLE_ROOT at MODULE IMPORT and builds
    `experiential = ExperientialMemory(root=CHRONICLE_ROOT)` as a module-level
    singleton, so once sovereign_stack.server has been imported the root is
    frozen for the process and a later monkeypatch.setenv changes nothing.

    Caught by this file's own assertion: the first version of the e2e test wrote
    its insight into a DIFFERENT test's tmp root (whichever one happened to be
    set when server was first imported). In a process where the suite runs this
    file first, with SOVEREIGN_ROOT unset, that same code writes a real insight
    into Anthony's LIVE chronicle — the exact class fixed in b96efb8 and again
    in sovereign-bridge 28592c7. Replacing the singleton is deterministic and
    independent of import order.
    """
    from sovereign_stack import server
    from sovereign_stack.memory import ExperientialMemory

    chronicle = tmp_path / "chronicle"
    chronicle.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(server, "CHRONICLE_ROOT", str(chronicle))
    monkeypatch.setattr(server, "experiential", ExperientialMemory(root=str(chronicle)))
    return chronicle


def test_translated_args_are_accepted_by_the_real_record_insight(core_ctx, rooted_chronicle):
    """POSITIVE: the exact args build_commit_arguments now emits are accepted,
    the ground_truth insight lands, and the url receipt is stamped attested.

    Also settles a question the mocks could not: ground_truth does NOT require a
    receipt that stamps "verified". "attested" — which is all a url receipt can
    ever be, since provenance.py runs no live fetch at write — is sufficient.
    """
    import bridge_core.pending_writes as pw

    p = pw.create_pending_write(
        core_ctx,
        "propose_insight",
        {
            "content": "end-to-end receipt translation probe",
            "domain": "test-fixb-e2e",
            "layer": "ground_truth",
            "receipt_url": "https://example.com/proof",
        },
        source_instance="probe-seat",
    )
    args = pw.build_commit_arguments(core_ctx, p)

    out = _dispatch("record_insight", args)

    assert "Insight recorded" in out, out
    assert "ground_truth" in out
    assert "1 attested" in out, f"url receipt did not land as provenance: {out}"
    # Landed in THIS test's sandbox chronicle, not Anthony's store and not
    # some other test's tmp dir. This assertion is what caught the frozen
    # singleton described on rooted_chronicle - keep it.
    assert str(rooted_chronicle) in out, out


def test_the_untranslated_args_are_still_rejected_by_the_real_record_insight(
    rooted_chronicle,
):
    """NEGATIVE CONTROL: the pre-fix body — receipt_url riding through verbatim
    — is refused by the live handler. This is what made every such commit come
    back {"ok": false} while the bridge stamped it "committed".

    Without this the positive test above proves only that SOME args work, not
    that the translation is what fixed anything.
    """
    with pytest.raises(Exception) as exc:
        _dispatch(
            "record_insight",
            {
                "content": "pre-fix shape",
                "domain": "test-fixb-e2e",
                "layer": "ground_truth",
                "receipt_url": "https://example.com/proof",
            },
        )
    assert "receipt_url" in str(exc.value)
