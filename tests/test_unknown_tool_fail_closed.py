"""An unrecognised tool name FAILS — it is never reported as a success.

THE DEFECT (found 2026-08-23 during the MCP shim build): the final
fallthrough of ``_dispatch_tool`` was

    return [TextContent(type="text", text=f"Unknown tool: {name}")]

a success-shaped return. The MCP SDK wrapped it into a ``CallToolResult``
with ``isError=False`` (mcp/server/lowlevel/server.py:578-584), so the REST
bridge's ``call_mcp_tool`` skipped its isError branch (bridge.py:355-357)
and emitted HTTP 200 ``{"ok": true, "result": "Unknown tool: X"}``. A typo,
a stale client, or a fabricated tool name read as SUCCESS at every caller
of every surface — the house's SOP #2 fail-open class, one layer up from
the 2026-07-19 write-path instance that this suite already pins in
tests/test_p1_fail_closed.py.

THE FIX: raise ``ValueError(f"Unknown tool: {name}")``. The framework
converts a handler exception into ``_make_error_result`` (lowlevel
server.py:589-590 → :473-480), i.e. a ``CallToolResult`` with
``isError=True`` — exactly the flag the bridge already inspects.

PROOF PROTOCOL (standing experimental law #2 — a gate must demonstrably be
able to FAIL): revert the single line in ``src/sovereign_stack/server.py``
and every test in ``TestUnknownToolFailsClosed`` fails for its stated
reason. ``TestKnownToolStillSucceeds`` is the positive control (law #3):
it must PASS both before and after, so a fix that simply errors on
everything cannot masquerade as correct.

ISOLATION: ``_dispatch_tool`` calls ``save_spiral_state`` before matching
any name, and ``handle_tool``'s except branch feeds Nape. Both write under
the sovereign root. Every test here runs inside ``_isolated_server`` from
tests/test_nape_autohook.py with ``nape_daemon`` additionally rebound to a
tmp-rooted daemon — nothing touches the live ~/.sovereign tree.
"""

from __future__ import annotations

import asyncio
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest
from mcp import types

from sovereign_stack.nape_daemon import NapeDaemon
from tests.test_nape_autohook import _isolated_server

# A name that is not, and must never become, a registered tool.
UNKNOWN = "definitely_not_a_registered_tool_xyz"


def _run(coro):
    return asyncio.run(coro)


@contextmanager
def _sandbox(session_id: str):
    """_isolated_server plus a tmp-rooted Nape daemon.

    handle_tool's exception path calls nape_daemon.observe(result="ERROR: ..."),
    and nape_daemon is a module-level singleton built from DEFAULT_ROOT at
    import time — the same shape of leak that put 51 'healthy probe' handoffs
    into the live store before _isolated_server learned to patch
    handoff_engine (see that fixture's docstring).
    """
    with _isolated_server(session_id) as (srv, tmp_root):
        nape_dir = Path(tempfile.mkdtemp())
        original_daemon = srv.nape_daemon
        srv.nape_daemon = NapeDaemon(root=str(nape_dir))
        try:
            yield srv, tmp_root
        finally:
            srv.nape_daemon = original_daemon


async def _call_through_framework(srv, name: str, arguments: dict):
    """Invoke the handler the MCP framework itself registered.

    This is the object the transport calls and whose isError flag the REST
    bridge reads, so asserting on it proves the fail-closed claim end to end
    without standing up a socket.
    """
    handler = srv.server.request_handlers[types.CallToolRequest]
    req = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name=name, arguments=arguments),
    )
    server_result = await handler(req)
    return server_result.root  # ServerResult is a RootModel


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


class TestUnknownToolFailsClosed:
    def test_dispatch_raises(self):
        """UNFIXED: returns [TextContent('Unknown tool: ...')] and pytest.raises
        fails with DID NOT RAISE. FIXED: a deliberate ValueError naming the tool."""
        with _sandbox("test-unknown-dispatch"):
            from sovereign_stack.server import _dispatch_tool

            with pytest.raises(ValueError, match=f"Unknown tool: {UNKNOWN}"):
                _run(_dispatch_tool(UNKNOWN, {}))

    def test_handle_tool_reraises_after_nape(self):
        """UNFIXED: handle_tool returns the success-shaped TextContent — the
        Nape wrapper has no exception to re-raise. FIXED: the ValueError
        propagates through the wrapper (Nape observes it, never swallows it)."""
        with (
            _sandbox("test-unknown-handle") as (srv, _),
            pytest.raises(ValueError, match="Unknown tool"),
        ):
            _run(srv.handle_tool(UNKNOWN, {}))

    def test_framework_result_is_error(self):
        """THE CONTRACT THIS FILE EXISTS FOR.

        UNFIXED: isError is False and content[0].text is 'Unknown tool: ...' —
        a failed call wearing a success envelope. FIXED: isError is True with
        the same text, which is the only difference every downstream surface
        actually reads."""
        with _sandbox("test-unknown-framework") as (srv, _):
            result = _run(_call_through_framework(srv, UNKNOWN, {}))

            assert result.isError is True, (
                "unknown tool returned a success-shaped CallToolResult; "
                "every caller of every surface reads this as ok:true"
            )
            assert "Unknown tool" in result.content[0].text

    def test_bridge_maps_it_to_ok_false(self):
        """The REST bridge decides ok:false with exactly this predicate
        (~/sovereign-bridge/bridge.py:355 — `if getattr(result, "isError", False)`).
        Pinning the predicate here means the bridge needs no change, and a future
        edit that re-opens the hole fails in this repo rather than silently
        downstream.

        UNFIXED: the predicate is False, the bridge falls through to the
        {"ok": true, "result": text} branch. FIXED: it is True."""
        with _sandbox("test-unknown-bridge") as (srv, _):
            result = _run(_call_through_framework(srv, UNKNOWN, {}))

            # Verbatim reproduction of the bridge's branch.
            if getattr(result, "isError", False):
                text = result.content[0].text if result.content else "tool error (no detail)"
                envelope = {"ok": False, "error": text, "failure_class": "tool"}
            else:
                envelope = {"ok": True, "result": result.content[0].text}

            assert envelope["ok"] is False
            assert "Unknown tool" in envelope["error"]
            assert envelope["failure_class"] == "tool"


# ---------------------------------------------------------------------------
# Positive control (standing experimental law #3)
# ---------------------------------------------------------------------------


class TestKnownToolStillSucceeds:
    """Must PASS on unfixed AND fixed code. Without it, 'unknown tools error'
    is also satisfied by a dispatcher that errors on everything."""

    def test_known_tool_returns_success_envelope(self):
        with _sandbox("test-known-control") as (srv, _):
            result = _run(_call_through_framework(srv, "derive", {"paths": []}))

            assert result.isError is False
            assert getattr(result, "isError", False) is False  # bridge → ok:true

    def test_every_registered_tool_is_dispatchable(self):
        """The regression guard for the fix itself.

        Raising is only safe while no REGISTERED tool falls through to it. If a
        tool is ever added to list_tools() without a dispatch branch, it used to
        degrade to a soft 'Unknown tool' string; it now becomes a hard error. This
        asserts the fallthrough stays unreachable for live tools.
        """
        import inspect
        import re

        from sovereign_stack import server as srv

        registered = {t.name for t in _run(srv.list_tools())}
        body = inspect.getsource(srv._dispatch_tool)

        covered = set(re.findall(r'name\s*==\s*["\']([^"\']+)["\']', body))
        for group in re.findall(r"name\s+in\s+[\(\[]([^\)\]]*)[\)\]]", body):
            covered |= set(re.findall(r'["\']([^"\']+)["\']', group))
        for list_name in re.findall(r"name\s+in\s+\[t\.name\s+for\s+t\s+in\s+(\w+)\]", body):
            covered |= {t.name for t in getattr(srv, list_name, [])}

        unmatched = sorted(registered - covered)
        assert not unmatched, (
            f"{len(unmatched)} registered tool(s) reach the fail-closed "
            f"fallthrough and would now raise: {unmatched}"
        )
