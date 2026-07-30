"""POST /messages: door check + session→principal binding.

GHSA-jpw9-pfvf-9f58 (HIGH, fixed upstream in mcp 1.27.2; the venv runs 1.26.0):
"HTTP transports serve session requests without verifying the authenticated
principal." Before this change, sse_server.py stated the invalidated assumption
in a comment — that the connect-time token check covered the whole session,
because only a minted session_id is accepted. It does not. A live session_id
was a bearer credential all by itself, so a leak became a takeover.

Every rejection case here is written to go RED against the pre-fix behaviour.
The kill-switch tests are the durable proof of that: engaging
SSE_ALLOW_UNVERIFIED_MESSAGES reproduces the pre-fix code path exactly, and the
CVE case is asserted to pass through it. Same assertions, opposite outcomes,
one env var apart.
"""

import asyncio
from uuid import uuid4

import pytest

from sovereign_stack import sse_server

TOKEN_A = "test-token-aaaa0123456789abcdef0123456789ab"
TOKEN_B = "test-token-bbbb0123456789abcdef0123456789ab"

# A well-formed session id that the mcp transport has never minted. Valid UUID
# hex matters: handle_post_message 400s on anything that fails UUID(hex=...)
# before it ever reaches the unknown-session 404.
UNKNOWN_SESSION = uuid4().hex


def _post_scope(session_id=UNKNOWN_SESSION, credential=None, query_credential=None, headers=None):
    """A POST /messages scope. content-type is required — the mcp transport's
    security middleware 400s a POST without one, before any of our logic."""
    hdrs = [(b"content-type", b"application/json")]
    if credential is not None:
        hdrs.append((b"authorization", f"Bearer {credential}".encode()))
    hdrs.extend(headers or [])
    query = f"session_id={session_id}" if session_id is not None else ""
    if query_credential is not None:
        query = f"{query}&token={query_credential}" if query else f"token={query_credential}"
    return {
        "type": "http",
        "path": "/messages",
        "method": "POST",
        "headers": hdrs,
        "query_string": query.encode(),
        "client": ("127.0.0.1", 12345),
    }


def _call(scope, body=b'{"jsonrpc":"2.0","method":"ping","id":1}'):
    """Drive the real ASGI app and collect what it sent."""
    sent = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(sse_server.app(scope, receive, send))
    return sent


def _status(sent):
    return next(m for m in sent if m["type"] == "http.response.start")["status"]


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    """Fresh binding map + a configured token + the gate enforcing."""
    sse_server._session_principals.clear()
    monkeypatch.setenv("BRIDGE_TOKEN", TOKEN_A)
    monkeypatch.delenv("SSE_ALLOW_UNAUTHENTICATED", raising=False)
    monkeypatch.delenv(sse_server._MESSAGES_KILL_SWITCH_ENV, raising=False)
    yield
    sse_server._session_principals.clear()


@pytest.fixture
def transport_spy(monkeypatch):
    """Records whether the request reached the mcp transport at all.

    Status codes alone are not enough: the pre-fix path and the kill-switch
    path both end in a 404 from upstream, and only this distinguishes
    "refused at the gate" from "handed to the transport, which found no
    session". The gate's whole job is to stop the request BEFORE here.
    """
    calls = []

    async def fake_handle_post_message(scope, receive, send):
        calls.append(sse_server._scope_session_id(scope))
        await sse_server.Response("spy", status_code=299)(scope, receive, send)

    monkeypatch.setattr(sse_server.sse, "handle_post_message", fake_handle_post_message)
    return calls


class TestTheCVE:
    """The actual vulnerability: a session_id used as a bearer credential."""

    def test_no_credential_with_session_id_is_refused(self, transport_spy):
        # THE CVE. Pre-fix this reached the transport and drove the session.
        sent = _call(_post_scope(credential=None))
        assert _status(sent) == 401
        assert transport_spy == [], "request reached the transport without a credential"

    def test_wrong_credential_with_session_id_is_refused(self, transport_spy):
        sent = _call(_post_scope(credential="not-the-token"))
        assert _status(sent) == 401
        assert transport_spy == []

    def test_valid_credential_reaches_the_transport(self, transport_spy):
        sent = _call(_post_scope(credential=TOKEN_A))
        assert _status(sent) == 299
        assert transport_spy == [UNKNOWN_SESSION]

    def test_query_form_credential_is_accepted_on_post(self, transport_spy):
        # The gate must not branch on WHICH form carried the credential —
        # that is the named anti-pattern (caller-chosen auth form deciding
        # whether the POST leg is gated). Both forms, one gate.
        sent = _call(_post_scope(credential=None, query_credential=TOKEN_A))
        assert _status(sent) == 299
        assert transport_spy == [UNKNOWN_SESSION]

    def test_fail_closed_when_token_unset(self, monkeypatch, transport_spy):
        monkeypatch.delenv("BRIDGE_TOKEN", raising=False)
        sent = _call(_post_scope(credential="anything"))
        assert _status(sent) == 401
        assert transport_spy == []


class TestPrincipalBinding:
    """A session may only be driven by the credential that opened it."""

    def test_second_principal_is_refused(self, monkeypatch, transport_spy):
        session = uuid4().hex
        # Principal A binds the session.
        assert _status(_call(_post_scope(session_id=session, credential=TOKEN_A))) == 299
        # Principal B holds a credential this server considers valid...
        monkeypatch.setenv("BRIDGE_TOKEN", TOKEN_B)
        sent = _call(_post_scope(session_id=session, credential=TOKEN_B))
        # ...and is still refused, because it is not the principal that bound it.
        assert _status(sent) == 404
        assert transport_spy == [session], "second principal reached the transport"

    def test_same_principal_may_continue(self, transport_spy):
        session = uuid4().hex
        for _ in range(3):
            assert _status(_call(_post_scope(session_id=session, credential=TOKEN_A))) == 299
        assert transport_spy == [session] * 3

    def test_binding_survives_credential_form_change(self, transport_spy):
        # Header and query forms carrying the SAME token are the same
        # principal. Binding is on the credential, not on how it travelled.
        session = uuid4().hex
        _call(_post_scope(session_id=session, credential=TOKEN_A))
        sent = _call(_post_scope(session_id=session, credential=None, query_credential=TOKEN_A))
        assert _status(sent) == 299

    def test_missing_session_id_fails_closed(self, monkeypatch, transport_spy):
        # Nothing to bind or match. Refuse rather than wave through.
        scope = _post_scope(session_id=None, credential=TOKEN_A)
        assert _status(_call(scope)) == 404
        assert transport_spy == []

    def test_digest_is_salted_not_the_raw_token(self):
        digest = sse_server._principal_digest(_post_scope(credential=TOKEN_A))
        assert TOKEN_A not in digest
        import hashlib

        assert digest != hashlib.sha256(TOKEN_A.encode()).hexdigest(), "unsalted digest"


class TestDuplicateParameterEvasion:
    """Regression: the gate must name the same session the transport does.

    Starlette's QueryParams.get() returns the LAST duplicate; parse_qs()[0]
    returns the FIRST. A gate built on parse_qs would check the binding for the
    attacker's own session and let the transport deliver the message into the
    victim's — caller-chosen input becoming trusted, the same class independent
    review caught in the previous two rounds of this codebase.
    """

    def test_gate_and_transport_agree_on_duplicated_session_id(self, transport_spy):
        victim, attacker = uuid4().hex, uuid4().hex
        scope = _post_scope(session_id=f"{attacker}&session_id={victim}", credential=TOKEN_A)
        _call(scope)
        assert sse_server._scope_session_id(scope) == victim
        assert transport_spy == [victim], "gate and transport named different sessions"

    def test_smuggled_session_id_does_not_inherit_another_binding(self, monkeypatch, transport_spy):
        victim, attacker = uuid4().hex, uuid4().hex
        # Victim's session, bound to principal A.
        _call(_post_scope(session_id=victim, credential=TOKEN_A))
        # Principal B binds its own session, then tries to smuggle the
        # victim's id in as a second parameter.
        monkeypatch.setenv("BRIDGE_TOKEN", TOKEN_B)
        _call(_post_scope(session_id=attacker, credential=TOKEN_B))
        sent = _call(_post_scope(session_id=f"{attacker}&session_id={victim}", credential=TOKEN_B))
        assert _status(sent) == 404
        assert victim not in transport_spy[1:], "smuggled id reached the victim's session"


class TestResponseShape:
    def test_binding_mismatch_is_byte_identical_to_unknown_session(self, monkeypatch):
        """No oracle: a valid credential cannot tell a bound session from a
        nonexistent one. Both answers must be the same bytes."""
        # Real upstream 404 — valid credential, session the transport never minted.
        upstream = _call(_post_scope(session_id=uuid4().hex, credential=TOKEN_A))

        session = uuid4().hex
        _call(_post_scope(session_id=session, credential=TOKEN_A))
        monkeypatch.setenv("BRIDGE_TOKEN", TOKEN_B)
        ours = _call(_post_scope(session_id=session, credential=TOKEN_B))

        assert _status(upstream) == 404, "fixture did not produce the upstream 404"
        assert ours == upstream

    def test_door_rejection_is_401_not_404(self):
        # Matches the /openai/messages + /grok/messages precedent. A credential
        # failure is independent of session state and says nothing about it.
        assert _status(_call(_post_scope(credential=None))) == 401


class TestKillSwitch:
    """One env var reverts both checks, with no code change and no second path."""

    def test_engaged_switch_restores_pre_fix_behaviour(self, monkeypatch, transport_spy):
        monkeypatch.setenv(sse_server._MESSAGES_KILL_SWITCH_ENV, "true")
        _call(_post_scope(credential=None))
        assert transport_spy == [UNKNOWN_SESSION], "kill switch did not restore the old path"

    def test_engaged_switch_still_logs_the_would_be_rejection(self, monkeypatch, caplog):
        """This is what makes observe-first real rather than aspirational."""
        monkeypatch.setenv(sse_server._MESSAGES_KILL_SWITCH_ENV, "true")
        with caplog.at_level("WARNING", logger="sovereign-stack-sse"):
            _call(_post_scope(credential=None))
        assert any("would-reject" in r.getMessage() for r in caplog.records)

    def test_rejection_log_never_carries_the_credential(self, monkeypatch, caplog):
        with caplog.at_level("WARNING", logger="sovereign-stack-sse"):
            _call(_post_scope(credential="wrong-but-secret-looking"))
            _call(_post_scope(credential=None, query_credential="secret-in-the-url"))
        logged = " ".join(r.getMessage() for r in caplog.records)
        assert "wrong-but-secret-looking" not in logged
        assert "secret-in-the-url" not in logged
        assert "credential=header" in logged and "credential=query" in logged

    def test_engaged_switch_also_lifts_the_binding_check(self, monkeypatch, transport_spy):
        session = uuid4().hex
        _call(_post_scope(session_id=session, credential=TOKEN_A))
        monkeypatch.setenv(sse_server._MESSAGES_KILL_SWITCH_ENV, "true")
        monkeypatch.setenv("BRIDGE_TOKEN", TOKEN_B)
        _call(_post_scope(session_id=session, credential=TOKEN_B))
        assert transport_spy == [session, session]

    def test_only_true_opts_out(self, monkeypatch, transport_spy):
        # Anything that is not "true" leaves the gate enforcing. Note "false",
        # "no" and "0" specifically: a switch that opted out on any non-empty
        # value would be a fail-open disguised as a config knob.
        for value in ("yes", "1", "", "false", "no", "0", "enforce", "TRUEISH"):
            monkeypatch.setenv(sse_server._MESSAGES_KILL_SWITCH_ENV, value)
            sse_server._session_principals.clear()
            assert _status(_call(_post_scope(credential=None))) == 401, f"{value!r} opted out"
        assert transport_spy == []

    def test_case_and_whitespace_tolerant(self, monkeypatch, transport_spy):
        # Deliberate, and identical to _allow_unauthenticated()'s
        # .strip().lower() == "true". This is the 3am revert path: it should
        # not hinge on Anthony typing lowercase without a trailing space.
        for value in ("true", "True", "TRUE", "  true  "):
            monkeypatch.setenv(sse_server._MESSAGES_KILL_SWITCH_ENV, value)
            sse_server._session_principals.clear()
            transport_spy.clear()
            _call(_post_scope(credential=None))
            assert transport_spy == [UNKNOWN_SESSION], f"{value!r} did not opt out"

    def test_matches_the_existing_opt_in_helper(self, monkeypatch):
        # One shape for both switches — no second convention to remember.
        for value in ("true", "True", "  TRUE ", "yes", "", "false"):
            monkeypatch.setenv("SSE_ALLOW_UNAUTHENTICATED", value)
            monkeypatch.setenv(sse_server._MESSAGES_KILL_SWITCH_ENV, value)
            assert sse_server._messages_gate_disabled() is sse_server._allow_unauthenticated()

    def test_switch_is_read_at_call_time(self, monkeypatch, transport_spy):
        # A launchd env edit + restart must be sufficient; no import-time capture.
        assert _status(_call(_post_scope(credential=None))) == 401
        monkeypatch.setenv(sse_server._MESSAGES_KILL_SWITCH_ENV, "true")
        _call(_post_scope(credential=None))
        assert transport_spy == [UNKNOWN_SESSION]


class TestBindingMapIsBounded:
    """mcp 1.26.0 never removes a session id, so this map must bound itself."""

    def test_map_never_exceeds_the_cap(self, monkeypatch):
        monkeypatch.setattr(sse_server, "_MESSAGES_BINDING_MAX", 8)
        for _ in range(50):
            sse_server._binding_ok(uuid4().hex, "digest")
        assert len(sse_server._session_principals) <= 8

    def test_eviction_is_oldest_first(self, monkeypatch):
        monkeypatch.setattr(sse_server, "_MESSAGES_BINDING_MAX", 4)
        first = uuid4().hex
        sse_server._binding_ok(first, "digest")
        for _ in range(4):
            sse_server._binding_ok(uuid4().hex, "digest")
        assert first not in sse_server._session_principals

    def test_evicted_live_session_rebinds_but_only_through_the_door(
        self, monkeypatch, transport_spy
    ):
        """Eviction is benign, and the fallback is still gated."""
        monkeypatch.setattr(sse_server, "_MESSAGES_BINDING_MAX", 2)
        session = uuid4().hex
        _call(_post_scope(session_id=session, credential=TOKEN_A))
        for _ in range(3):
            sse_server._binding_ok(uuid4().hex, "digest")
        assert session not in sse_server._session_principals
        # Re-binds with a good credential...
        assert _status(_call(_post_scope(session_id=session, credential=TOKEN_A))) == 299
        # ...but an evicted binding never becomes a free pass.
        sse_server._session_principals.pop(session, None)
        assert _status(_call(_post_scope(session_id=session, credential=None))) == 401


class TestHeaderParsingIsFirstWins:
    """All authorization reads agree with bridge_core.identity_gate."""

    def test_first_authorization_header_wins(self):
        scope = _post_scope(credential=TOKEN_A, headers=[(b"authorization", b"Bearer smuggled")])
        assert sse_server._scope_credential(scope) == TOKEN_A

    def test_bridge_auth_ok_is_also_first_wins(self, monkeypatch):
        monkeypatch.setenv("BRIDGE_TOKEN", TOKEN_A)
        scope = _post_scope(credential=TOKEN_A, headers=[(b"authorization", b"Bearer smuggled")])
        assert sse_server._bridge_auth_ok(scope) is True

    def test_absent_header_returns_empty(self):
        assert sse_server._first_header(_post_scope(), b"authorization") == b""


class TestUnrelatedPathsUnaffected:
    def test_health_still_open(self):
        sent = _call(
            {
                "type": "http",
                "path": "/health",
                "method": "GET",
                "headers": [],
                "query_string": b"",
                "client": ("127.0.0.1", 1),
            }
        )
        assert _status(sent) == 200

    def test_messages_with_trailing_slash_never_reaches_the_transport(self, transport_spy):
        """The middleware is the sole route to handle_post_message; the inner
        Starlette app has no /messages route, so variants 404 without it."""
        scope = _post_scope(credential=TOKEN_A)
        scope["path"] = "/messages/"
        assert _status(_call(scope)) == 404
        assert transport_spy == []


class TestSessionIdIsCanonicalizedLikeTheConsumer:
    """HQ review correction. Three independent adversarial reviewers found the
    same hole in the first cut of the binding, and it is worth guarding by name.

    `_binding_ok` keyed `_session_principals` on the RAW caller-supplied string
    while the mcp transport resolves the session with `UUID(hex=param)`
    (mcp/server/sse.py:217), which strips urn:/uuid: prefixes and braces, ignores
    dash placement, and is case-insensitive. Six spellings of ONE live session
    therefore produced six separate bindings that the transport routed to the
    same writer — so an attacker binds an alias and delivers into a victim's
    session. The in-code claim that the divergence was "impossible by
    construction" was empirically false.

    The invariant: parse the way the consumer parses. Anything else is a
    divergence waiting to be found.
    """

    BASE = "d0680cc14b7c4e0aa1f2c3d4e5f60718"
    ALIASES = [
        BASE.upper(),
        "d0680cc1-4b7c-4e0a-a1f2-c3d4e5f60718",
        "urn:uuid:d0680cc1-4b7c-4e0a-a1f2-c3d4e5f60718",
        "{d0680cc1-4b7c-4e0a-a1f2-c3d4e5f60718}",
        "uuid:D0680CC1-4B7C-4E0A-A1F2-C3D4E5F60718",
    ]

    def setup_method(self):
        sse_server._session_principals.clear()

    def teardown_method(self):
        sse_server._session_principals.clear()

    def test_every_alias_collapses_to_one_canonical_form(self):
        forms = {sse_server._canonical_session_id(a) for a in [self.BASE, *self.ALIASES]}
        assert len(forms) == 1, f"aliases must not fan out into separate keys: {forms}"

    @pytest.mark.parametrize("alias", ALIASES)
    def test_attacker_alias_cannot_hijack_a_bound_session(self, alias):
        """The attack itself: victim binds, attacker presents the same session
        under a different spelling with a different principal."""
        assert sse_server._binding_ok(self.BASE, "victim-digest") is True
        assert sse_server._binding_ok(alias, "attacker-digest") is False, (
            f"alias {alias!r} hijacked the victim's binding"
        )
        assert len(sse_server._session_principals) == 1

    def test_the_victim_still_matches_itself_through_every_alias(self):
        """Canonicalization must not break the legitimate client either: the
        same principal posting under any spelling is still the same session."""
        assert sse_server._binding_ok(self.BASE, "victim-digest") is True
        for alias in self.ALIASES:
            assert sse_server._binding_ok(alias, "victim-digest") is True

    @pytest.mark.parametrize(
        "bad", ["", "not-a-uuid", "A" * 8192, "../../etc/passwd", "d0680cc1", "\x00"]
    )
    def test_unparseable_session_ids_are_refused_not_stored(self, bad):
        """Also closes the second finding: the cap bounds ENTRY COUNT, not bytes,
        so an 8 KB session_id gave ~134 MB at cap instead of the documented ~3 MB.
        Requiring a parseable uuid fixes the key size by construction."""
        assert sse_server._canonical_session_id(bad) is None
        assert sse_server._binding_ok(bad, "any-digest") is False
        assert sse_server._session_principals == {}


class TestBindingCapKnobCannotTakeTheServiceDown:
    """HQ review correction. The knob that documents this change introduced two
    new outage modes, both on a service whose launchd job sets KeepAlive true.

    A misconfigured safety knob must never be worse than the default.
    """

    def test_zero_clamps_to_the_floor_rather_than_disabling_messages(self, monkeypatch):
        """At 0 the eviction loop pops until the map is empty and re-binds every
        time, so POST /messages never settles."""
        monkeypatch.setenv("SSE_BINDING_MAX_SESSIONS", "0")
        assert sse_server._read_binding_max() == sse_server._BINDING_MAX_FLOOR

    def test_non_integer_falls_back_instead_of_crashing_at_import(self, monkeypatch):
        """This one was a PERMANENT crash loop: the value was read at module
        import, so a typo meant the process died on start, forever, under
        KeepAlive."""
        monkeypatch.setenv("SSE_BINDING_MAX_SESSIONS", "banana")
        assert sse_server._read_binding_max() == sse_server._BINDING_MAX_DEFAULT

    def test_below_floor_clamps(self, monkeypatch):
        monkeypatch.setenv("SSE_BINDING_MAX_SESSIONS", "100")
        assert sse_server._read_binding_max() == sse_server._BINDING_MAX_FLOOR

    def test_a_sane_value_is_honoured(self, monkeypatch):
        monkeypatch.setenv("SSE_BINDING_MAX_SESSIONS", "50000")
        assert sse_server._read_binding_max() == 50000

    def test_unset_uses_the_default(self, monkeypatch):
        monkeypatch.delenv("SSE_BINDING_MAX_SESSIONS", raising=False)
        assert sse_server._read_binding_max() == sse_server._BINDING_MAX_DEFAULT
