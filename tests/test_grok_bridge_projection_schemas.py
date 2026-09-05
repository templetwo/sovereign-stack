"""The Grok projection must carry the Stack's real input schemas, or not project.

`clients/grok_bridge/tool_adapter.py::_ring1_schemas` assigned
``{"type": "object", "properties": {}}`` to every Ring-1 tool except four, in its
PRIMARY path. Not a degraded fallback — the shape /grok/sse published on every
healthy call. openai_bridge reached that state only on DOUBLE failure and was
repaired on 2026-09-04 (0d674c4, d5e2e85); grok lived in it permanently.

Two costs, and the second is the quieter one:

  * Six Ring-1 tools REQUIRE an argument. The seat read "no arguments", called
    bare, and the Stack refused a field the seat was never shown. This is
    verbatim the 2026-08-28 OpenAI report ("the Compass tool exposed here
    accepts no arguments"), one substrate over.
  * ~26 more were not refused — their optional parameters were simply invisible.
    recall_insights publishes 13 properties and Grok could reach none of them,
    including ``order``, whose default returns recency noise on any historical
    query. A tool that answers badly is harder to notice than one that errors.

These tests pin the invariant across every path ``get_ring1_schemas`` can take:
live ``/api/tools?name=``, the in-process registry, and the static offline
fallback.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

ADAPTER_LOGGER = "grok_bridge.tool_adapter"


_REGISTRY: dict[str, dict] | None = None


def _registry_schemas() -> dict[str, dict]:
    """The Stack's own schemas, read once, OUTSIDE any running loop.

    ``asyncio.run`` raises inside a loop and the adapter's broad
    ``except Exception`` would swallow it, quietly serving a different code path
    than the test believes it is testing. That is a real hazard here, not a
    hypothetical: it happened while writing the openai twin of this file.
    """
    global _REGISTRY
    if _REGISTRY is None:
        from sovereign_stack.server import list_tools

        _REGISTRY = {tool.name: (tool.inputSchema or {}) for tool in asyncio.run(list_tools())}
    return _REGISTRY


def _required(schema: dict) -> set[str]:
    return set(schema.get("required") or [])


def _properties(schema: dict) -> set[str]:
    return set(schema.get("properties") or {})


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _RegistryBackedClient:
    """A live /api/tools that answers from the in-process registry.

    ``omit`` drops names from the CATALOG only — the shape of a partial catalog
    read, which used to shrink a published surface silently.
    """

    omit: frozenset[str] = frozenset()
    schemas: dict[str, dict] = {}

    def __init__(self, *args, **kwargs):
        self._schemas = self.schemas

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, *, headers=None, params=None):
        if params is None:
            names = [n for n in self._schemas if n not in self.omit]
            return _FakeResponse({"count": len(names), "tools": [{"name": n} for n in names]})
        name = params["name"]
        return _FakeResponse(
            {
                "name": name,
                "description": f"live {name}",
                "inputSchema": self._schemas[name],
            }
        )


class _UnreachableClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, *args, **kwargs):
        raise OSError("All connection attempts failed")


@pytest.fixture(autouse=True)
def _clean_cache():
    from grok_bridge import tool_adapter

    tool_adapter.reset_ring1_cache()
    yield
    tool_adapter.reset_ring1_cache()


def _project(monkeypatch, *, mode: str, omit: frozenset[str] = frozenset()):
    """Run discovery in one of the three modes and return {name: Tool}."""
    from grok_bridge import tool_adapter

    if mode == "live":
        schemas = _registry_schemas()  # read before the loop starts
        client = type("_C", (_RegistryBackedClient,), {"omit": omit, "schemas": schemas})
        monkeypatch.setattr(tool_adapter.httpx, "AsyncClient", client)
    else:
        monkeypatch.setattr(tool_adapter.httpx, "AsyncClient", _UnreachableClient)
    if mode == "static":
        import sovereign_stack.server as native

        # Guarded so a test may project twice in static mode: monkeypatch
        # restores at teardown, not between calls, so a second delattr raises.
        if hasattr(native, "list_tools"):
            monkeypatch.delattr(native, "list_tools")

    return {tool.name: tool for tool in asyncio.run(tool_adapter.get_ring1_schemas())}


def _remote_names() -> set[str]:
    from grok_bridge.tool_adapter import _expected_remote_ring1

    return set(_expected_remote_ring1())


# ── The census, re-derived so it cannot go stale ─────────────────────────────


def test_required_argument_census_matches_the_registry():
    """The six argument-requiring Ring-1 tools, measured not remembered."""
    registry = _registry_schemas()
    requiring = {
        name: sorted(_required(registry[name]))
        for name in sorted(_remote_names())
        if name in registry and _required(registry[name])
    }
    assert requiring == {
        "check_mistakes": ["context"],
        "comms_unread_bodies": ["instance_id"],
        "compass_check": ["action"],
        "context_retrieve": ["current_focus"],
        "inspect_claim": ["claim_id"],
        "reflexive_surface": ["domain_tags"],
    }


def test_the_parity_denominator_is_pinned_so_it_cannot_shrink_silently():
    """WHAT "every Ring-1 tool" MEANS HERE, written down.

    The parity tests below iterate `_expected_remote_ring1()`, which the adapter
    derives by SUBTRACTING three exemption sets from RING_1_TOOLS. That is the
    right denominator — those names are not proxied — but it is also a
    denominator the code under test computes for itself: adding a name to
    `_NOT_WIRED_RING1` would remove it from parity coverage and every parity test
    would still pass, having quietly stopped looking at it.

    The census above catches that for the six argument-requiring tools (its
    expected dict would no longer match). This pins it for the ~26 whose
    parameters are all optional, which is most of the defect by count and the
    half that never errors.
    """
    from grok_bridge.rings import RING_1_TOOLS
    from grok_bridge.tool_adapter import _BRIDGE_LOCAL_TOOLS, _NOT_WIRED_RING1

    assert set(_NOT_WIRED_RING1) == {"witness_boot"}
    assert set(_BRIDGE_LOCAL_TOOLS) == {"grok_welcome", "verify_proposal", "list_bridge_proposals"}
    assert _remote_names() == set(RING_1_TOOLS) - {"witness_boot", "self_model"} - set(
        _BRIDGE_LOCAL_TOOLS
    )
    # A floor, not a fixed count: the allowlist grows. Vacuity is the failure
    # mode being closed here — a parity loop over an empty set passes.
    assert len(_remote_names()) >= 30


@pytest.mark.parametrize("mode", ["live", "registry"])
def test_the_healthy_projection_holds_every_ring1_tool_but_witness_boot(monkeypatch, mode):
    """The other direction from `test_every_published_tool_is_ring_1`: nothing
    the allowlist holds may silently vanish from a HEALTHY projection either."""
    from grok_bridge.rings import RING_1_TOOLS

    assert set(_project(monkeypatch, mode=mode)) == set(RING_1_TOOLS) - {"witness_boot"}


# ── THE INVARIANT the task asks for ──────────────────────────────────────────


@pytest.mark.parametrize("mode", ["live", "registry"])
def test_projected_required_equals_the_registry_for_every_ring1_tool(monkeypatch, mode):
    """For every Ring-1 tool, the Grok projection's `required` == the Stack's."""
    projected = _project(monkeypatch, mode=mode)
    registry = _registry_schemas()
    for name in sorted(_remote_names()):
        assert name in projected, f"{name} was not projected"
        assert _required(projected[name].inputSchema) == _required(registry[name]), name


@pytest.mark.parametrize("mode", ["live", "registry"])
def test_projected_properties_equal_the_registry(monkeypatch, mode):
    """`required` parity alone would pass on a tool whose 13 optional
    parameters are still invisible — which is most of this defect by count."""
    projected = _project(monkeypatch, mode=mode)
    registry = _registry_schemas()
    for name in sorted(_remote_names()):
        assert _properties(projected[name].inputSchema) == _properties(registry[name]), name


@pytest.mark.parametrize("mode", ["live", "registry", "static"])
def test_no_argument_requiring_tool_is_published_as_argument_less(monkeypatch, mode):
    """THE DEFECT, stated as the invariant, on every path including static.

    A published tool either carries its real required list or is not published.
    """
    projected = _project(monkeypatch, mode=mode)
    registry = _registry_schemas()
    for name in sorted(_remote_names()):
        tool = projected.get(name)
        if tool is None:
            continue  # omission is how MCP spells "unavailable" — allowed
        needed = _required(registry[name])
        if needed:
            assert _required(tool.inputSchema) == needed, name
            assert _properties(tool.inputSchema), name


@pytest.mark.parametrize("mode", ["live", "registry"])
def test_recall_insights_exposes_order_and_domain(monkeypatch, mode):
    """The quiet half, made concrete. `order` defaults to "newest", which
    returns recency noise on a historical query; the seat could not pass it."""
    schema = _project(monkeypatch, mode=mode)["recall_insights"].inputSchema
    assert {"order", "domain"} <= _properties(schema)


# ── The static fallback ──────────────────────────────────────────────────────


def test_static_fallback_omits_the_tools_it_cannot_describe(monkeypatch):
    projected = _project(monkeypatch, mode="static")
    for name in ("compass_check", "context_retrieve", "reflexive_surface"):
        assert name not in projected


def test_static_fallback_still_publishes_the_bridge_local_tools(monkeypatch):
    """Omission must not cost the tools this bridge dispatches itself — they are
    callable with both Stack surfaces down, which is why the fallback exists."""
    projected = _project(monkeypatch, mode="static")
    assert {"grok_welcome", "verify_proposal", "list_bridge_proposals"} <= set(projected)
    assert _required(projected["verify_proposal"].inputSchema) == {"proposal_id"}


def test_static_fallback_says_what_it_withheld(monkeypatch, caplog):
    with caplog.at_level(logging.WARNING, logger=ADAPTER_LOGGER):
        _project(monkeypatch, mode="static")
    withholding = [
        r for r in caplog.records if r.name == ADAPTER_LOGGER and "withholding" in r.getMessage()
    ]
    assert len(withholding) == 1
    assert "compass_check" in withholding[0].getMessage()


def test_the_healthy_path_reports_no_degradation(monkeypatch, caplog):
    """d5e2e85, one substrate over: the openai fix logged a false "withheld"
    warning on every healthy call, because the healthy path called the fallback
    builder to source one tool. A surface that reports degradation on a success
    is the same fail-open family as the defect, mirrored."""
    with caplog.at_level(logging.DEBUG, logger=ADAPTER_LOGGER):
        _project(monkeypatch, mode="live")
    assert [r.getMessage() for r in caplog.records if r.name == ADAPTER_LOGGER] == []


# ── Fail-open guards adjacent to the defect ──────────────────────────────────


def test_a_partial_catalog_is_a_failed_read_not_a_smaller_stack(monkeypatch, caplog):
    """Intersecting the allowlist with a partial catalog silently shrank the
    surface and cached the reduction. It must fall through to the registry,
    which here is complete, so the projection recovers in full."""
    with caplog.at_level(logging.WARNING, logger=ADAPTER_LOGGER):
        projected = _project(monkeypatch, mode="live", omit=frozenset({"recall_insights"}))
    assert "recall_insights" in projected
    assert _properties(projected["recall_insights"].inputSchema)
    assert any("missing" in r.getMessage() for r in caplog.records if r.name == ADAPTER_LOGGER)


def test_a_short_registry_is_published_but_never_cached(monkeypatch, caplog):
    """A short in-process registry is the AUTHORITATIVE answer, not a failed
    read — so it publishes, warns, and does not pin the short surface."""
    from grok_bridge import tool_adapter

    monkeypatch.setattr(tool_adapter.httpx, "AsyncClient", _UnreachableClient)
    import sovereign_stack.server as native

    real = native.list_tools

    async def _short():
        return [t for t in await real() if t.name != "triage_threads"]

    monkeypatch.setattr(native, "list_tools", _short)
    with caplog.at_level(logging.WARNING, logger=ADAPTER_LOGGER):
        first = {t.name for t in asyncio.run(tool_adapter.get_ring1_schemas())}
    assert "triage_threads" not in first
    assert tool_adapter._ring1_cache is None, "a known-short surface must not be cached"
    assert any("stale" in r.getMessage() for r in caplog.records if r.name == ADAPTER_LOGGER)


def test_the_cache_is_bounded_and_resettable(monkeypatch):
    """A process-lifetime cache inside the long-lived sovereign-sse process
    pinned the projection until someone restarted a service."""
    from grok_bridge import tool_adapter

    assert tool_adapter.RING1_CACHE_TTL_SECONDS > 0
    first = _project(monkeypatch, mode="live")
    assert tool_adapter._ring1_cache is not None
    tool_adapter.reset_ring1_cache()
    assert tool_adapter._ring1_cache is None
    second = _project(monkeypatch, mode="live")
    assert set(first) == set(second)


def test_a_bad_ttl_does_not_take_the_sse_server_down(monkeypatch):
    """sse_server imports this module at startup, so an unguarded
    float(os.environ[...]) turned a typo into what looks like a tunnel outage."""
    from grok_bridge import tool_adapter

    for bad in ("banana", "", "inf", "nan"):
        monkeypatch.setenv("GROK_BRIDGE_SCHEMA_TTL_SECONDS", bad)
        assert tool_adapter._schema_ttl_seconds() == tool_adapter._DEFAULT_SCHEMA_TTL_SECONDS
    monkeypatch.setenv("GROK_BRIDGE_SCHEMA_TTL_SECONDS", "0")
    assert tool_adapter._schema_ttl_seconds() == tool_adapter._MIN_SCHEMA_TTL_SECONDS
    monkeypatch.setenv("GROK_BRIDGE_SCHEMA_TTL_SECONDS", "42")
    assert tool_adapter._schema_ttl_seconds() == 42.0


def test_no_shared_mutable_state_between_cache_generations(monkeypatch):
    """Tool is a mutable pydantic model and this runs in a long-lived process:
    one shared instance across generations lets a mutation reach every reader."""
    from grok_bridge import tool_adapter

    first = _project(monkeypatch, mode="static")["grok_welcome"]
    first.inputSchema["properties"]["injected"] = {"type": "string"}
    tool_adapter.reset_ring1_cache()
    second = _project(monkeypatch, mode="static")["grok_welcome"]
    assert "injected" not in second.inputSchema["properties"]


# ── Bridge-local and not-wired names ─────────────────────────────────────────


@pytest.mark.parametrize("mode", ["live", "registry", "static"])
def test_witness_boot_is_never_published(monkeypatch, mode):
    """In RING_1_TOOLS, absent from the Stack registry, and NOT dispatched
    locally by this bridge — handle_bridge_tool proxies it to a Stack that has
    never defined it. It was published with an empty schema and was uncallable
    by every route."""
    from grok_bridge.rings import RING_1_TOOLS

    assert "witness_boot" in RING_1_TOOLS
    assert "witness_boot" not in _registry_schemas()
    assert "witness_boot" not in _project(monkeypatch, mode=mode)


@pytest.mark.parametrize("mode", ["live", "registry", "static"])
def test_self_model_keeps_the_bridges_narrowed_schema(monkeypatch, mode):
    """mcp_filtered routes action="read" to Ring 1 and everything else to the
    Ring 2 proposal path, so publishing the Stack's enum would advertise a
    Ring 1 call this bridge does not make."""
    schema = _project(monkeypatch, mode=mode)["self_model"].inputSchema
    assert schema["properties"]["action"]["enum"] == ["read"]


@pytest.mark.parametrize("mode", ["live", "registry"])
def test_every_published_tool_is_ring_1(monkeypatch, mode):
    """Discovery must never admit a tool the allowlist does not hold — the
    allowlist is the access-control gate, not the catalog."""
    from grok_bridge.rings import RING_1_TOOLS

    assert set(_project(monkeypatch, mode=mode)) <= set(RING_1_TOOLS)


@pytest.mark.parametrize("mode", ["live", "registry"])
def test_the_grok_specific_orientation_text_survives(monkeypatch, mode):
    """Deliberate divergence from the openai mirror: these descriptions are
    substrate-specific orientation written for this seat, so the fix must change
    the SCHEMAS without rewriting the published prose."""
    projected = _project(monkeypatch, mode=mode)
    assert "grok-bridge" in projected["recall_insights"].description
    # A name with no local text gains the Stack's real description instead of
    # the "[Ring 1] <name>" placeholder it used to carry.
    assert projected["inspect_claim"].description != "[Ring 1] inspect_claim"


def test_ring_2_is_still_appended(monkeypatch):
    """get_all_bridge_schemas became async-sourced; Ring 2 must be unaffected."""
    from grok_bridge import tool_adapter

    schemas = _registry_schemas()
    client = type("_C", (_RegistryBackedClient,), {"omit": frozenset(), "schemas": schemas})
    monkeypatch.setattr(tool_adapter.httpx, "AsyncClient", client)
    names = {t.name for t in asyncio.run(tool_adapter.get_all_bridge_schemas())}
    assert "propose_insight" in names
    assert "compass_check" in names
