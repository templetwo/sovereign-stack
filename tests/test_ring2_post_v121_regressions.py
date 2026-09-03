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
from pathlib import Path

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


# ALL FIVE OF THESE MUST MOVE TOGETHER, and the reason is not obvious from any
# one file. openai_bridge addresses its stores through module-level constants
# rather than a context object, and audit.py does
#     from .hash_chain import AUDIT_DIR, AUDIT_LOG
# — a from-import copies the VALUE into audit's namespace at import time. Rebind
# hash_chain alone and append_audit_event keeps writing to the LIVE chain
# through audit.AUDIT_LOG, silently, with the suite still green.
#
# That is not hypothetical: the first version of this fixture rebound only
# PENDING_DIR and hash_chain.AUDIT_DIR and put 383 rows across 114 synthetic
# proposal ids into ~/.sovereign/openai_bridge/audit/audit.jsonl on 2026-08-30,
# terminating Anthony's real hash chain on a fake retry_armed event. The
# no_live_audit_writes tripwire in conftest.py now fails loudly on this class;
# this tuple is the fix. Mirrors ISOLATED_NAMES in
# clients/openai_bridge/_smoke_test.py and the oai fixture in
# tests/test_bridge_drain_provenance.py — both document the same trap.
_OPENAI_ISOLATED = (
    ("pending_writes", "PENDING_DIR", "pending_writes"),
    ("hash_chain", "AUDIT_DIR", "audit"),
    ("hash_chain", "AUDIT_LOG", "audit/audit.jsonl"),
    ("audit", "AUDIT_DIR", "audit"),
    ("audit", "AUDIT_LOG", "audit/audit.jsonl"),
)


@pytest.fixture
def openai_tmp(tmp_path, monkeypatch, tmp_sovereign_root):
    """Point every openai_bridge write path at tmp. Never ~/.sovereign.

    Rebinds on BOTH import identities of the package. The same source is
    reachable as `openai_bridge.x` and `clients.openai_bridge.x`, and Python
    caches those as SEPARATE module objects — patching one leaves the other
    aimed at the live store, so whichever identity the code under test happens
    to have imported decides whether the isolation holds.
    """
    import importlib
    import sys

    root = tmp_path / "oai"
    seen = 0
    for pkg in ("openai_bridge", "clients.openai_bridge"):
        for mod_name, attr, rel in _OPENAI_ISOLATED:
            full = f"{pkg}.{mod_name}"
            mod = sys.modules.get(full)
            if mod is None and pkg == "openai_bridge":
                mod = importlib.import_module(full)  # the identity our tests use
            if mod is None:
                continue  # that identity was never imported; nothing to redirect
            monkeypatch.setattr(mod, attr, root / rel)
            seen += 1

    assert seen >= len(_OPENAI_ISOLATED), (
        f"expected to rebind at least {len(_OPENAI_ISOLATED)} names, rebound {seen}"
    )

    # Unroutable on purpose: if a capture stub is ever bypassed, the commit
    # errors instead of reaching the real bridge on :8100.
    import openai_bridge.pending_writes as opw

    monkeypatch.setattr(opw, "_BRIDGE_URL", "http://127.0.0.1:1/api/call")
    return root


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


# ══════════════════════════════════════════════════════════════════════════════
# HQ FOLLOW-UP 1 — THE FAILED-COMMIT BRANCH MUST VERIFY THE CHAIN TOO
# ══════════════════════════════════════════════════════════════════════════════
#
# The commit_failed branch added above returned early, skipping the
# verify_chain(ctx) the success path runs. A failed commit still APPENDS an
# audit event, so it can still break the chain — and skipping verification on
# the failure path means a break goes undetected in exactly the run where
# something has already gone wrong.


def _fail_the_commit(monkeypatch, module):
    """Point the substrate's httpx at a Stack that rejects the write."""
    monkeypatch.setattr(
        module.httpx,
        "post",
        lambda *a, **kw: _stack_response({"ok": False, "error": "rejected for the test"}),
    )


def test_failed_commit_verifies_the_chain(core_ctx, monkeypatch):
    """Invocation: verify_chain runs on the failure path, not only on success."""
    import bridge_core.pending_writes as pw

    monkeypatch.setenv("TEST_R2_TOKEN", "t")
    _fail_the_commit(monkeypatch, pw)

    calls: list[str] = []
    real = pw.verify_chain
    monkeypatch.setattr(pw, "verify_chain", lambda ctx: (calls.append("called"), real(ctx))[1])

    p = pw.create_pending_write(
        core_ctx, "handoff", {"note": "n", "thread": "t"}, source_instance="seat"
    )
    pw.approve_pending_write(core_ctx, p.proposal_id, approved_by="Anthony")
    out = pw.commit_pending_write(core_ctx, p.proposal_id, live=True)

    assert out.status == "commit_failed"
    assert calls, "verify_chain was never called on the commit_failed branch"


def test_failed_commit_detects_a_broken_chain(core_ctx, monkeypatch):
    """Detection, not just invocation: corrupt a prior audit event, fail the
    commit, and assert the break is caught AND recorded as CHAIN_BROKEN.

    A spy proves the call happens; only this proves the call does anything.
    """
    import bridge_core.pending_writes as pw
    from bridge_core.audit import read_audit_trail

    monkeypatch.setenv("TEST_R2_TOKEN", "t")

    p = pw.create_pending_write(
        core_ctx, "handoff", {"note": "n", "thread": "t"}, source_instance="seat"
    )
    pw.approve_pending_write(core_ctx, p.proposal_id, approved_by="Anthony")

    # Tamper with an already-written audit entry so the chain no longer verifies.
    log = core_ctx.audit_log_path
    lines = log.read_text().splitlines()
    first = json.loads(lines[0])
    first["actor"] = "someone-who-was-never-here"
    lines[0] = json.dumps(first)
    log.write_text("\n".join(lines) + "\n")
    assert not pw.verify_chain(core_ctx)[0], "the corruption did not break the chain"

    _fail_the_commit(monkeypatch, pw)
    out = pw.commit_pending_write(core_ctx, p.proposal_id, live=True)

    assert out.status == "commit_failed"
    events = [e.get("event_type") or e.get("event") for e in read_audit_trail(core_ctx)]
    assert "chain_broken" in events, (
        f"a broken chain went unrecorded on the failed-commit path: {events}"
    )


def test_openai_failed_commit_verifies_the_chain(openai_tmp, monkeypatch):
    """The same guarantee on the second substrate."""
    import openai_bridge.pending_writes as opw

    monkeypatch.setenv("BRIDGE_TOKEN", "t")
    _fail_the_commit(monkeypatch, opw)

    calls: list[str] = []
    real = opw.verify_chain
    monkeypatch.setattr(opw, "verify_chain", lambda: (calls.append("called"), real())[1])

    p = opw.create_pending_write("handoff", {"note": "n", "thread": "t"}, source_instance="seat")
    opw.approve_pending_write(p.proposal_id, approved_by="Anthony")
    out = opw.commit_pending_write(p.proposal_id, live=True)

    assert out.status == "commit_failed"
    assert calls, "verify_chain was never called on the openai commit_failed branch"


# ══════════════════════════════════════════════════════════════════════════════
# HQ FOLLOW-UP 2 — `bridge retry <id>`: THE RECORDED WAY BACK FROM commit_failed
# ══════════════════════════════════════════════════════════════════════════════


def _fail_one(ctx, monkeypatch, module, tool="handoff", args=None):
    monkeypatch.setenv("TEST_R2_TOKEN", "t")
    _fail_the_commit(monkeypatch, module)
    p = module.create_pending_write(
        ctx, tool, args or {"note": "n", "thread": "t"}, source_instance="seat"
    )
    module.approve_pending_write(ctx, p.proposal_id, approved_by="Anthony")
    return module.commit_pending_write(ctx, p.proposal_id, live=True)


def test_hand_editing_status_is_the_hazard_retry_removes(core_ctx, monkeypatch):
    """THE JUSTIFICATION, stated as a test.

    The danger of the manual route is NOT that it breaks the audit hash — it
    does not. `status` and `commit_result` are both in _MUTABLE, so the tamper
    check normalises them away and a hand-edit passes every guard SILENTLY,
    leaving no audit event. A retried write becomes indistinguishable from one
    that was never rejected. That is what `bridge retry` exists to prevent.
    """
    import bridge_core.pending_writes as pw
    from bridge_core.audit import read_audit_trail

    failed = _fail_one(core_ctx, monkeypatch, pw)
    (path,) = core_ctx.pending_writes_dir.glob(f"*{failed.proposal_id[:8]}*.json")

    raw = json.loads(path.read_text())
    raw["status"] = "approved"
    raw["commit_result"] = None
    path.write_text(json.dumps(raw))

    reloaded, _ = pw._load_proposal(core_ctx, failed.proposal_id)
    errors = pw._precondition_check(core_ctx, reloaded)
    assert not any("audit_hash mismatch" in e for e in errors), (
        "expected the hand-edit to pass the tamper check — if this now fails, "
        "the hazard has changed shape and this rationale needs rewriting"
    )
    events = [e.get("event_type") or e.get("event") for e in read_audit_trail(core_ctx)]
    assert "retry_armed" not in events, "a hand-edit must leave no retry record"


def test_retry_moves_commit_failed_back_to_approved_with_an_audit_event(core_ctx, monkeypatch):
    import bridge_core.pending_writes as pw
    from bridge_core.audit import read_audit_trail

    failed = _fail_one(core_ctx, monkeypatch, pw)
    assert failed.status == "commit_failed"

    out = pw.retry_pending_write(core_ctx, failed.proposal_id, actor="HQ-review-seat")

    assert out.status == "approved"
    assert out.commit_result is None, "a stale failure must not sit beside 'approved'"

    trail = read_audit_trail(core_ctx, proposal_id=failed.proposal_id)
    armed = [e for e in trail if (e.get("event_type") or e.get("event")) == "retry_armed"]
    assert len(armed) == 1, f"no retry_armed event: {trail}"
    ev = armed[0]
    assert ev["actor"] == "HQ-review-seat"
    assert "rejected for the test" in json.dumps(ev.get("details", {})), (
        f"the prior error did not travel into the audit event: {ev}"
    )
    # The chain must still verify after the transition.
    assert pw.verify_chain(core_ctx)[0]


def test_retry_preserves_the_original_approver(core_ctx, monkeypatch):
    """reviewed_by is provenance — who re-armed it is a different fact and
    belongs on the audit event, not on top of the approver's name."""
    import bridge_core.pending_writes as pw

    failed = _fail_one(core_ctx, monkeypatch, pw)
    out = pw.retry_pending_write(core_ctx, failed.proposal_id, actor="HQ-review-seat")
    assert out.reviewed_by == "Anthony"


def test_a_retried_proposal_can_actually_commit(core_ctx, monkeypatch):
    """The point of the command: the retry path really is re-committable."""
    import bridge_core.pending_writes as pw

    failed = _fail_one(core_ctx, monkeypatch, pw)
    pw.retry_pending_write(core_ctx, failed.proposal_id, actor="HQ-review-seat")

    monkeypatch.setattr(
        pw.httpx, "post", lambda *a, **kw: _stack_response({"ok": True, "result": "ok"})
    )
    out = pw.commit_pending_write(core_ctx, failed.proposal_id, live=True)
    assert out.status == "committed"


@pytest.mark.parametrize("status", ["pending", "approved", "committed", "rejected"])
def test_retry_refuses_any_status_but_commit_failed(core_ctx, monkeypatch, status):
    import bridge_core.pending_writes as pw

    p = pw.create_pending_write(
        core_ctx, "handoff", {"note": "n", "thread": "t"}, source_instance="seat"
    )
    (path,) = core_ctx.pending_writes_dir.glob(f"*{p.proposal_id[:8]}*.json")
    raw = json.loads(path.read_text())
    raw["status"] = status
    path.write_text(json.dumps(raw))

    with pytest.raises(ValueError) as exc:
        pw.retry_pending_write(core_ctx, p.proposal_id, actor="HQ-review-seat")
    assert "commit_failed" in str(exc.value)


@pytest.mark.parametrize("actor", ["", "   ", None])
def test_retry_refuses_an_unnamed_actor(core_ctx, monkeypatch, actor):
    """No anonymous re-arming, and no inherited human name."""
    import bridge_core.pending_writes as pw

    failed = _fail_one(core_ctx, monkeypatch, pw)
    with pytest.raises(ValueError) as exc:
        pw.retry_pending_write(core_ctx, failed.proposal_id, actor=actor)
    assert "actor" in str(exc.value).lower()


def test_openai_retry_moves_commit_failed_back_to_approved(openai_tmp, monkeypatch):
    """The same command on the second substrate bridge_core/cli.py routes to."""
    import openai_bridge.pending_writes as opw
    from openai_bridge.audit import read_audit_trail

    monkeypatch.setenv("BRIDGE_TOKEN", "t")
    _fail_the_commit(monkeypatch, opw)
    p = opw.create_pending_write("handoff", {"note": "n", "thread": "t"}, source_instance="seat")
    opw.approve_pending_write(p.proposal_id, approved_by="Anthony")
    failed = opw.commit_pending_write(p.proposal_id, live=True)
    assert failed.status == "commit_failed"

    out = opw.retry_pending_write(failed.proposal_id, actor="HQ-review-seat")
    assert out.status == "approved"
    trail = read_audit_trail(proposal_id=failed.proposal_id)
    armed = [e for e in trail if (e.get("event_type") or e.get("event")) == "retry_armed"]
    assert len(armed) == 1 and armed[0]["actor"] == "HQ-review-seat"


def test_retry_cli_requires_by_and_never_defaults_to_a_human():
    """--by is REQUIRED. The reviewer-identity convention (ca11e4c) exists
    because an automated caller inheriting "Anthony" forges a human's approval.
    """
    from bridge_core.cli import cli
    from click.testing import CliRunner

    res = CliRunner().invoke(cli, ["--source=grok", "retry", "abc123"])
    assert res.exit_code != 0
    assert "Missing option" in res.output and "--by" in res.output

    help_out = CliRunner().invoke(cli, ["--source=grok", "retry", "--help"]).output
    assert "Anthony" not in help_out, "the retry help must not name a human default"


def test_retry_cli_is_wired_for_both_substrates():
    """bridge_core/cli.py routes --source=openai and --source=grok; the retry
    op must exist on both, or the command is half-built."""
    from bridge_core.cli import _SubstrateOps

    for source in ("openai", "grok"):
        assert callable(getattr(_SubstrateOps(source), "_retry", None)), source


# ══════════════════════════════════════════════════════════════════════════════
# THE TRIPWIRE'S OWN SELFTEST — a guard that has never failed is not a guard
# ══════════════════════════════════════════════════════════════════════════════
#
# conftest.no_live_audit_writes exists because this file put 383 rows into
# Anthony's live openai audit chain on 2026-08-30 while reporting green. A guard
# written in response to that must itself be shown to FAIL on the case it was
# built for (experimental law #2) — otherwise the next seat inherits a second
# reassuring green.
#
# Driven against a FAKE home. Proving the guard by letting something write to
# the real chain would be committing the original offence to test the alarm.


def _conftest_module():
    """The LIVE conftest module object pytest already loaded.

    Not `import conftest` (the tests dir is not on sys.path) and not a fresh
    importlib load either — a re-import would give a DIFFERENT module object,
    and the selftest would then exercise a copy while the real guard went
    unchecked. Found by the attribute it defines.
    """
    import sys

    for mod in list(sys.modules.values()):
        if getattr(mod, "__name__", "").endswith("conftest") and hasattr(mod, "_live_audit_sizes"):
            return mod
    raise AssertionError("could not locate the loaded conftest defining the tripwire")


def _run_guard(monkeypatch, fake_home, mutate):
    """Drive the real conftest fixture body around `mutate`, rooted at a fake
    home. Returns the AssertionError it raised, or None if it stayed silent."""
    conftest = _conftest_module()

    monkeypatch.setattr(conftest.Path, "home", classmethod(lambda cls: fake_home))
    try:
        gen = conftest.no_live_audit_writes.__wrapped__()
        next(gen)  # setup: snapshot sizes
        mutate()
        try:
            next(gen)
        except StopIteration:
            return None
        except AssertionError as exc:
            return exc
        return None
    finally:
        # UNDO IMMEDIATELY, do not wait for fixture teardown.
        #
        # The REAL no_live_audit_writes is autouse, so it is already wrapping
        # this very test — and it reads Path.home() at ITS teardown. If the
        # patch is still in place then, the real guard globs the FAKE home,
        # sees a file that did not exist at its own setup, and fails the test
        # for the write this test made on purpose. Whether that happens depends
        # on fixture teardown ORDER, which shifts when anyone adds another
        # autouse fixture: green on this branch, ERROR on the
        # feat/console-v2-reskin merge, same code. Undoing here removes the
        # ordering dependency instead of relying on the current ordering.
        monkeypatch.undo()


@pytest.fixture
def fake_home(tmp_path):
    log = tmp_path / ".sovereign" / "openai_bridge" / "audit" / "audit.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text('{"event_type": "approved"}\n')
    return tmp_path


def test_the_tripwire_catches_a_write_to_a_live_audit_log(monkeypatch, fake_home):
    """NEGATIVE: the exact 2026-08-30 shape — an audit log grows during a test."""
    log = fake_home / ".sovereign" / "openai_bridge" / "audit" / "audit.jsonl"

    exc = _run_guard(
        monkeypatch,
        fake_home,
        lambda: log.open("a").write('{"event_type": "retry_armed"}\n'),
    )

    assert exc is not None, "the tripwire did not fire on a live audit write"
    assert "LIVE AUDIT CHAIN" in str(exc)


def test_the_tripwire_stays_quiet_when_nothing_writes(monkeypatch, fake_home):
    """POSITIVE control: it must not fire on every test in the suite."""
    assert _run_guard(monkeypatch, fake_home, lambda: None) is None


def test_the_tripwire_skips_cleanly_without_a_sovereign_dir(monkeypatch, tmp_path):
    """CI and fresh clones have no ~/.sovereign — that is not a failure."""
    assert _run_guard(monkeypatch, tmp_path, lambda: None) is None


def test_the_openai_fixture_rebinds_every_write_path(openai_tmp):
    """The fix, asserted directly: all five names point into the sandbox.

    Checking behaviour (bytes on disk) is what the tripwire does; this checks
    the MECHANISM, so a future edit that drops one name fails here with a
    message naming the binding rather than only as a mysterious live write.
    """
    import openai_bridge.audit as oai_audit
    import openai_bridge.hash_chain as oai_hash
    import openai_bridge.pending_writes as oai_pw

    bindings = {
        "pending_writes.PENDING_DIR": oai_pw.PENDING_DIR,
        "hash_chain.AUDIT_DIR": oai_hash.AUDIT_DIR,
        "hash_chain.AUDIT_LOG": oai_hash.AUDIT_LOG,
        "audit.AUDIT_DIR": oai_audit.AUDIT_DIR,
        "audit.AUDIT_LOG": oai_audit.AUDIT_LOG,
    }
    live = Path.home() / ".sovereign"
    for name, value in bindings.items():
        assert str(value).startswith(str(openai_tmp)), f"{name} escaped the sandbox: {value}"
        assert live not in Path(value).parents, f"{name} still points into {live}"


# ══════════════════════════════════════════════════════════════════════════════
# THE PREVIEW MUST SHOW WHAT THE WIRE SENDS (HQ follow-up, both substrates)
# ══════════════════════════════════════════════════════════════════════════════
#
# The receipt_url translation originally sat inline in openai's
# commit_pending_write, BELOW the `if not live:` early return — so
# `bridge commit <id>` previewed proposal.arguments raw (receipt_url still on
# it) while the live wire sent something else. Anthony approves from that
# preview. A review surface that differs from the wire is how a body that looks
# complete gets approved and lands wrong; it is the same defect class as the
# 'unknown' authorship the arrival branch fixed one field over.


def test_openai_dry_run_preview_matches_the_wire(openai_tmp, monkeypatch):
    """The preview and the live body must be the SAME dict, receipt included."""
    import openai_bridge.pending_writes as opw

    args = {
        "content": "c",
        "domain": "d",
        "layer": "ground_truth",
        "receipt_url": "https://example.com/p",
    }
    p = opw.create_pending_write("propose_insight", dict(args), source_instance="seat")
    opw.approve_pending_write(p.proposal_id, approved_by="Anthony")

    previewed = opw.commit_pending_write(p.proposal_id, live=False).commit_result["with_arguments"]

    sent: list[dict] = []

    def _capture(url, json=None, headers=None, timeout=None):
        sent.append(json)
        return _stack_response({"ok": True})

    monkeypatch.setenv("BRIDGE_TOKEN", "t")
    monkeypatch.setattr(opw.httpx, "post", _capture)
    opw.commit_pending_write(p.proposal_id, live=True)

    assert "receipt_url" not in previewed, (
        "the dry-run preview still shows receipt_url — Anthony would approve a "
        "body the wire does not send"
    )
    assert previewed == sent[0]["arguments"], (
        f"preview and wire diverged:\n  preview={previewed}\n  wire={sent[0]['arguments']}"
    )


def test_bridge_core_dry_run_preview_matches_the_wire(core_ctx, monkeypatch):
    """bridge_core already assembled above the dry-run return; this pins it."""
    import bridge_core.pending_writes as pw

    p = pw.create_pending_write(
        core_ctx,
        "propose_insight",
        {
            "content": "c",
            "domain": "d",
            "layer": "ground_truth",
            "receipt_url": "https://example.com/p",
        },
        source_instance="seat",
    )
    pw.approve_pending_write(core_ctx, p.proposal_id, approved_by="Anthony")

    previewed = pw.commit_pending_write(core_ctx, p.proposal_id, live=False).commit_result[
        "with_arguments"
    ]

    sent: list[dict] = []

    def _capture(url, json=None, headers=None, timeout=None):
        sent.append(json)
        return _stack_response({"ok": True})

    monkeypatch.setenv("TEST_R2_TOKEN", "t")
    monkeypatch.setattr(pw.httpx, "post", _capture)
    pw.commit_pending_write(core_ctx, p.proposal_id, live=True)

    assert "receipt_url" not in previewed
    assert previewed == sent[0]["arguments"]


def test_both_substrates_agree_on_the_verified_by_targets():
    """The two literals must not drift; only record_insight declares it."""
    from bridge_core.pending_writes import VERIFIED_BY_TARGETS as core_targets
    from openai_bridge.pending_writes import VERIFIED_BY_TARGETS as oai_targets

    assert core_targets == oai_targets == frozenset({"record_insight"})


def test_both_substrates_agree_on_the_original_timestamp_targets():
    """The third duplicated target set, held together the same way as the
    other two.

    ORIGINAL_TIMESTAMP_TARGETS is declared as its own literal in
    bridge_core/pending_writes.py and openai_bridge/pending_writes.py. The two
    existing parity tests cover VERIFIED_BY_TARGETS (above) and
    PROVENANCE_PASSTHROUGH_TARGETS (test_bridge_drain_provenance); this one was
    shipped without a pin.

    THE COST OF DRIFT IS ASYMMETRIC AND BOTH DIRECTIONS ARE BAD. Too NARROW
    here and a proposal's authorship time is silently dropped on one substrate
    while travelling on the other — the exact "true for grok and FALSE for
    openai" shape that went 27 days unnoticed for provenance passthrough. Too
    WIDE and, since `_reject_unknown_params`, every commit carrying the
    parameter to a target that does not declare it becomes a hard Stack-side
    error. The set is asserted by VALUE as well as by equality, so widening it
    in both files at once still has to be a deliberate edit here.
    """
    from bridge_core.pending_writes import ORIGINAL_TIMESTAMP_TARGETS as core_targets
    from openai_bridge.pending_writes import ORIGINAL_TIMESTAMP_TARGETS as oai_targets

    assert core_targets == oai_targets
    assert core_targets == frozenset({"record_insight", "record_learning", "record_open_thread"})


def test_the_three_duplicated_target_sets_are_each_pinned():
    """Meta-pin: a fourth duplicated set must not ship unpinned.

    Each name that exists as a separate literal in BOTH pending_writes modules
    is asserted equal somewhere. This test names the three that exist today.

    SAY WHAT IT CANNOT SEE, so nobody reads it as a guarantee. The scan is
    `dir(core)` filtered to names ENDING IN `_TARGETS` that are `frozenset` in
    BOTH modules, which is the shape all three duplicated sets happen to have
    today — and only that shape. A fourth duplicated declaration slips past
    silently if it is a plain `set`, a `tuple`, a `list`, or named anything
    else (`*_COMMIT_TARGETS` matches; `TARGETS_*` and `*_ALLOWED` do not). It
    is a TRIPWIRE over the current convention, not an exhaustive audit of
    duplicated declarations: it catches the next set that follows the pattern
    and cannot catch the one that does not. Keeping the convention is what
    makes it work; widening the scan to every module-level container would
    catch the imports and the behaviour sets too, which is a different test.
    """
    from bridge_core import pending_writes as core
    from openai_bridge import pending_writes as oai

    duplicated = {
        name
        for name in dir(core)
        if name.endswith("_TARGETS")
        and isinstance(getattr(core, name, None), frozenset)
        and isinstance(getattr(oai, name, None), frozenset)
    }
    assert duplicated == {
        "VERIFIED_BY_TARGETS",
        "PROVENANCE_PASSTHROUGH_TARGETS",
        "ORIGINAL_TIMESTAMP_TARGETS",
    }, f"a duplicated target set has no parity test: {duplicated}"
    for name in duplicated:
        assert getattr(core, name) == getattr(oai, name), name
