"""A projected Ring-1 tool must carry the Stack's real input schema, or not be projected.

The OpenAI seat reported on 2026-08-28 that the Compass "accepts no arguments"
here, then had its call rejected for a missing ``action`` field; the same report
named ``context_retrieve`` (requires ``current_focus``) and ``reflexive_surface``
(requires ``domain_tags``).  Both halves of that symptom come from one shape: a
projection that publishes a description-only tool for a backend that requires
arguments.  It reads as callable, so the seat calls it bare, and the Stack —
correctly — refuses a field the seat was never shown.  Server-side validation is
what converts the silent lie into a rejected call; it is not a substitute for
publishing the schema.

These tests pin the invariant across every path ``get_ring1_schemas`` can take:
live ``/api/tools?name=``, the in-process registry, and the static offline
fallback.  The offline fallback is where the defect actually lived, and it is the
path a test must force, because the live path repaired itself in ``677b65c``
while the fallback kept the old shape.
"""

from __future__ import annotations

import asyncio

import pytest

# Ring-1 tools whose Stack handler rejects an argument-less call.  Measured from
# the in-process registry on 2026-09-04; `test_required_argument_census_matches_
# the_registry` re-derives it so this list cannot quietly go stale.
RING1_REQUIRING_ARGUMENTS = {
    "check_mistakes": ["context"],
    "comms_unread_bodies": ["instance_id"],
    "compass_check": ["action"],
    "context_retrieve": ["current_focus"],
    "inspect_claim": ["claim_id"],
    "reflexive_surface": ["domain_tags"],
}

# my_toolkit is deliberately a connector-local contract (it describes the bridge
# surface, not the Stack catalog), so it is exempt from registry conformance.
CONNECTOR_LOCAL = {"my_toolkit", "witness_boot"}


_REGISTRY: dict[str, dict] | None = None


def _registry_schemas() -> dict[str, dict]:
    """The Stack's own schemas, read once.

    Must never be called from inside a running loop: ``asyncio.run`` raises
    there, and the adapter's broad ``except Exception`` would swallow it and
    quietly serve a different code path than the test believes it is testing.
    That happened while writing this file — the "live" cases passed without ever
    reaching the live branch.
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

    ``omit`` drops names from the *catalog* only — the shape of a partial
    catalog read, which used to shrink the published surface silently.
    """

    omit: frozenset[str] = frozenset()
    schemas: dict[str, dict] = {}

    def __init__(self, *args, **kwargs):
        # Constructed inside the adapter's running loop — do no async work here.
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
    from openai_bridge import tool_adapter

    tool_adapter._ring1_cache = None
    tool_adapter._ring1_cache_at = 0.0
    yield
    tool_adapter._ring1_cache = None
    tool_adapter._ring1_cache_at = 0.0


def _project(monkeypatch, *, mode: str, omit: frozenset[str] = frozenset()):
    """Run discovery in one of the three modes and return {name: Tool}."""
    from openai_bridge import tool_adapter

    if mode == "live":
        schemas = _registry_schemas()  # read before the loop starts
        client = type("_C", (_RegistryBackedClient,), {"omit": omit, "schemas": schemas})
        monkeypatch.setattr(tool_adapter.httpx, "AsyncClient", client)
    else:
        monkeypatch.setattr(tool_adapter.httpx, "AsyncClient", _UnreachableClient)
    if mode == "static":
        import sovereign_stack.server as native

        monkeypatch.delattr(native, "list_tools")

    projected = {tool.name: tool for tool in asyncio.run(tool_adapter.get_ring1_schemas())}

    if mode == "live" and not omit:
        # Positive control: the adapter swallows any exception from the live
        # branch and falls through, so assert the live branch actually answered
        # rather than trusting that it did.  The fake stamps "live " on every
        # description it serves.  (With `omit` set the fall-through is the
        # behaviour under test, so the control does not apply.)
        proxied = [name for name in projected if name not in CONNECTOR_LOCAL]
        assert proxied, "live mode projected nothing to check"
        assert all((projected[name].description or "").startswith("live ") for name in proxied), (
            "live mode fell through to a fallback path — the test is not testing what it claims"
        )

    return projected


def test_required_argument_census_matches_the_registry():
    """The census this file asserts against is re-derived, not remembered."""
    from bridge_core.rings import CANONICAL_RING_1

    registry = _registry_schemas()
    live = {
        name: sorted(_required(schema))
        for name, schema in registry.items()
        if name in CANONICAL_RING_1 and _required(schema)
    }
    assert live == {k: sorted(v) for k, v in RING1_REQUIRING_ARGUMENTS.items()}


@pytest.mark.parametrize("mode", ["live", "registry", "static"])
def test_projection_never_publishes_an_argument_less_tool_that_requires_arguments(
    monkeypatch, mode
):
    """The reported defect, pinned on every discovery path.

    Fails on main in ``static`` mode: the old fallback handed each unlisted tool
    ``{"type": "object", "properties": {}}``.
    """
    projected = _project(monkeypatch, mode=mode)

    for name, required in RING1_REQUIRING_ARGUMENTS.items():
        tool = projected.get(name)
        if tool is None:
            continue  # omission is how MCP spells "unavailable" — that is allowed
        schema = tool.inputSchema or {}
        assert _properties(schema), (
            f"{mode}: {name} is published with no properties, but its Stack handler "
            f"requires {required}. A seat shown this schema calls it bare and is rejected."
        )
        assert _required(schema) == set(required), (
            f"{mode}: {name} is published with required={sorted(_required(schema))}, "
            f"but the Stack requires {required}."
        )


@pytest.mark.parametrize("mode", ["live", "registry"])
def test_compass_check_is_projected_with_required_action(monkeypatch, mode):
    """The seat's headline complaint, stated positively."""
    tool = _project(monkeypatch, mode=mode)["compass_check"]
    schema = tool.inputSchema or {}

    assert _required(schema) == {"action"}
    assert {"action", "context", "stakes", "with_simulation"} <= _properties(schema)


def test_static_fallback_withholds_compass_check_rather_than_faking_it():
    """Fails on main: the fallback published all three, argument-less."""
    from openai_bridge.tool_adapter import _minimal_ring1_fallback

    published = {tool.name: tool for tool in _minimal_ring1_fallback()}

    for name in ("compass_check", "context_retrieve", "reflexive_surface"):
        tool = published.get(name)
        if tool is None:
            continue
        assert _properties(tool.inputSchema or {}), (
            f"static fallback advertises {name} with no arguments; its backend "
            f"requires {RING1_REQUIRING_ARGUMENTS[name]}"
        )


def test_static_fallback_still_serves_the_tools_it_can_describe():
    """Withholding must not become withholding everything."""
    from openai_bridge.tool_adapter import _minimal_ring1_fallback

    published = {tool.name: tool for tool in _minimal_ring1_fallback()}

    # witness_boot is dispatched by this bridge and genuinely takes no arguments.
    assert "witness_boot" in published
    assert "my_toolkit" in published
    assert _required(published["inspect_claim"].inputSchema) == {"claim_id"}


def test_every_published_schema_conforms_to_the_stack_registry(monkeypatch):
    """The conformance check the 2026-08-28 report asked for, in its own words:
    'advertised schema tested against backend validation for every Ring-1 tool'.
    """
    registry = _registry_schemas()

    for mode in ("live", "registry", "static"):
        with monkeypatch.context() as m:
            projected = _project(m, mode=mode)
        for name, tool in projected.items():
            if name in CONNECTOR_LOCAL:
                continue
            assert name in registry, f"{mode}: published {name}, absent from the Stack registry"
            schema = tool.inputSchema or {}
            assert _required(schema) == _required(registry[name]), (
                f"{mode}: {name} advertises required={sorted(_required(schema))}, "
                f"registry says {sorted(_required(registry[name]))}"
            )
            assert _properties(schema) <= _properties(registry[name]), (
                f"{mode}: {name} advertises arguments the Stack does not accept: "
                f"{sorted(_properties(schema) - _properties(registry[name]))}"
            )


def test_a_partial_catalog_does_not_silently_shrink_the_published_surface(monkeypatch):
    """Fails on main: ``allowlist & live_names`` dropped the missing tools and
    cached the reduced surface for the process lifetime, with no coverage signal.
    """
    projected = _project(
        monkeypatch,
        mode="live",
        omit=frozenset({"compass_check", "reflexive_surface"}),
    )

    assert "compass_check" in projected
    assert _required(projected["compass_check"].inputSchema) == {"action"}
    assert "reflexive_surface" in projected


def test_the_schema_cache_is_bounded_and_resettable(monkeypatch):
    """Fails on main: the cache was a process-lifetime global with no way out.

    The projection runs inside the long-lived sovereign-sse process, so an
    unbounded cache pins whatever was reachable at the first discovery call.
    """
    from openai_bridge import tool_adapter

    assert tool_adapter.RING1_CACHE_TTL_SECONDS > 0

    first = _project(monkeypatch, mode="live")
    assert tool_adapter._ring1_cache is not None

    tool_adapter.reset_ring1_cache()
    assert tool_adapter._ring1_cache is None

    second = _project(monkeypatch, mode="live")
    assert set(first) == set(second)

    # An expired entry is refetched rather than served forever.
    monkeypatch.setattr(tool_adapter, "RING1_CACHE_TTL_SECONDS", 0.0)
    monkeypatch.setattr(tool_adapter.httpx, "AsyncClient", _UnreachableClient)
    third = {t.name: t for t in asyncio.run(tool_adapter.get_ring1_schemas())}
    assert set(third) == set(second)
