"""
Sovereign Stack SSE Server

HTTP/SSE transport layer for remote access via Cloudflare tunnel.
Runs alongside stdio server for local Claude Code access.
"""

import hmac
import json
import logging
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs

import uvicorn
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

# Import the existing sovereign-stack server
from .server import server as sovereign_server

# Optional: OpenAI bridge filtered endpoint.
# Gracefully absent if the clients package is not on the path.
_BRIDGE_CLIENTS = Path(__file__).parent.parent.parent / "clients"
if _BRIDGE_CLIENTS.exists() and str(_BRIDGE_CLIENTS) not in sys.path:
    sys.path.insert(0, str(_BRIDGE_CLIENTS))

try:
    from bridge_core import send_401 as _gate_send_401
    from bridge_core import verify_at_door as _verify_at_door
    from openai_bridge.mcp_filtered import (
        handle_openai_messages,
        handle_openai_messages_test,
        handle_openai_sse,
        handle_openai_sse_test,
    )
    from openai_bridge.oauth import (
        handle_authorization_server_metadata as handle_openai_oauth_as_meta,
    )
    from openai_bridge.oauth import (
        handle_authorize as handle_openai_oauth_authorize,
    )
    from openai_bridge.oauth import (
        handle_protected_resource_metadata as handle_openai_oauth_pr_meta,
    )
    from openai_bridge.oauth import (
        handle_register as handle_openai_oauth_register,
    )
    from openai_bridge.oauth import (
        handle_token as handle_openai_oauth_token,
    )

    _BRIDGE_ENABLED = True
except ImportError:
    _BRIDGE_ENABLED = False
    handle_openai_sse = None
    handle_openai_messages = None
    handle_openai_sse_test = None
    handle_openai_messages_test = None
    handle_openai_oauth_authorize = None
    handle_openai_oauth_token = None
    handle_openai_oauth_register = None
    handle_openai_oauth_as_meta = None
    handle_openai_oauth_pr_meta = None
    _gate_send_401 = None
    _verify_at_door = None

# Grok bridge — independently importable; failure doesn't disable openai_bridge.
try:
    from grok_bridge.manifest import MANIFEST as GROK_MANIFEST
    from grok_bridge.mcp_filtered import (
        handle_grok_messages,
        handle_grok_sse,
    )
    from grok_bridge.oauth import (
        handle_authorization_server_metadata as handle_grok_oauth_as_meta,
    )
    from grok_bridge.oauth import (
        handle_authorize as handle_grok_oauth_authorize,
    )
    from grok_bridge.oauth import (
        handle_protected_resource_metadata as handle_grok_oauth_pr_meta,
    )
    from grok_bridge.oauth import (
        handle_token as handle_grok_oauth_token,
    )

    _GROK_BRIDGE_ENABLED = True
except ImportError as _grok_e:
    _GROK_BRIDGE_ENABLED = False
    handle_grok_sse = None
    handle_grok_messages = None
    handle_grok_oauth_authorize = None
    handle_grok_oauth_token = None
    handle_grok_oauth_as_meta = None
    handle_grok_oauth_pr_meta = None
    GROK_MANIFEST = None
    logging.getLogger("sovereign-stack-sse").warning("Grok bridge not loaded: %s", _grok_e)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sovereign-stack-sse")

# Create SSE transport at module level (shared between routes)
sse = SseServerTransport("/messages")


# Health check endpoint
async def health(request: Request) -> JSONResponse:
    """Health check for monitoring"""
    return JSONResponse({"status": "healthy", "service": "sovereign-stack-sse", "version": "1.0.0"})


# SSE endpoint - holds connection open for server-sent events
async def handle_sse(request: Request):
    """
    SSE endpoint - establishes Server-Sent Events connection
    """
    logger.info(f"New SSE connection from {request.client}")

    async with sse.connect_sse(request.scope, request.receive, request._send) as (
        read_stream,
        write_stream,
    ):
        await sovereign_server.run(
            read_stream,
            write_stream,
            sovereign_server.create_initialization_options(),
            raise_exceptions=True,
        )


# ── Native SSE auth ───────────────────────────────────────────────────────────
# GET /sse requires the BRIDGE_TOKEN credential, supplied either as an
# `Authorization: Bearer <token>` header (bridge, header-capable MCP clients)
# or a `?token=<token>` query parameter (clients whose connector config only
# exposes a URL field, e.g. the claude.ai remote connector).
#
# POST /messages stays capability-gated: the mcp transport only accepts a
# session_id minted by an authenticated /sse connect (unknown ids → 404), so
# the token check on the connect covers the whole session.
#
# Fail-closed: if BRIDGE_TOKEN is unset, /sse refuses everything unless
# SSE_ALLOW_UNAUTHENTICATED=true is set explicitly (local-dev escape hatch).
# Token is read at call time so a launchd env edit + restart is sufficient.


def _expected_token() -> str:
    return os.environ.get("BRIDGE_TOKEN", "")


def _allow_unauthenticated() -> bool:
    return os.environ.get("SSE_ALLOW_UNAUTHENTICATED", "").strip().lower() == "true"


def _scope_credential(scope: dict) -> str:
    """Extract the presented credential from header or query param ('' if absent)."""
    headers = dict(scope.get("headers") or [])
    auth = headers.get(b"authorization", b"").decode("utf-8", errors="replace")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    query = parse_qs(scope.get("query_string", b"").decode("utf-8", errors="replace"))
    return (query.get("token") or [""])[0].strip()


def _native_auth_ok(scope: dict) -> bool:
    """Gate for GET /sse. Constant-time compare; fail-closed when unconfigured."""
    expected = _expected_token()
    if not expected:
        if _allow_unauthenticated():
            logger.warning("BRIDGE_TOKEN not set — /sse unauthenticated (explicit opt-in)")
            return True
        logger.error("BRIDGE_TOKEN not set and no SSE_ALLOW_UNAUTHENTICATED opt-in — refusing /sse")
        return False
    presented = _scope_credential(scope)
    if not presented:
        return False
    return hmac.compare_digest(presented, expected)


def _bridge_auth_ok(scope: dict) -> bool:
    """Return True if the request carries a valid BRIDGE_TOKEN bearer credential."""
    expected = _expected_token()
    if not expected:
        if _allow_unauthenticated():
            logger.warning("BRIDGE_TOKEN not set — /openai/sse unauthenticated (explicit opt-in)")
            return True
        logger.error("BRIDGE_TOKEN not set — refusing /openai/sse (fail-closed)")
        return False
    headers = dict(scope.get("headers") or [])
    auth = headers.get(b"authorization", b"").decode("utf-8", errors="replace")
    if auth.startswith("Bearer "):
        return hmac.compare_digest(auth[7:].strip(), expected)
    return False


async def _send_401(send, detail: str = "Valid Bearer token required for /openai/sse") -> None:
    body = json.dumps({"error": "Unauthorized", "detail": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


# ── OpenAI bridge request diagnostics ─────────────────────────────────────────
# Added 2026-05-20 to diagnose a ChatGPT MCP-connector failure (200 on discovery,
# 401/400/404 on invocation). Logs the headers that distinguish a transport
# mismatch (Mcp-Session-Id / MCP-Protocol-Version present, Streamable-HTTP Accept)
# from an auth-drop (bearer absent on retry). NEVER logs the bearer value — only
# its presence and scheme. Remove or gate behind a flag once the issue is closed.


def _log_openai_request_headers(scope: dict) -> None:
    """Log diagnostic headers for an /openai/* request. Bearer value redacted."""
    try:
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1", errors="replace")
            for k, v in scope.get("headers", [])
        }
        method = scope.get("method", "?")
        path = scope.get("path", "?")
        query = scope.get("query_string", b"").decode("latin-1", errors="replace")

        auth_raw = headers.get("authorization", "")
        if auth_raw:
            scheme = auth_raw.split(" ", 1)[0] if " " in auth_raw else auth_raw
            auth_repr = f"present(scheme={scheme},len={len(auth_raw)})"
        else:
            auth_repr = "ABSENT"

        diag = {
            "method": method,
            "path": path,
            "query": query or "(none)",
            "authorization": auth_repr,
            "mcp-session-id": headers.get("mcp-session-id", "(none)"),
            "mcp-protocol-version": headers.get("mcp-protocol-version", "(none)"),
            "accept": headers.get("accept", "(none)"),
            "content-type": headers.get("content-type", "(none)"),
            "user-agent": headers.get("user-agent", "(none)")[:120],
        }
        logger.info("OPENAI_DIAG %s", json.dumps(diag))
    except Exception as exc:  # diagnostics must never break a request
        logger.warning("OPENAI_DIAG failed: %s", exc)


# Wrap the Starlette app to intercept /messages POST before Starlette routing.
# handle_post_message is a raw ASGI handler (scope, receive, send) that writes
# responses directly — it returns None. Starlette Route expects a Response object,
# so we bypass Starlette for this path.
class SovereignAsgiMiddleware:
    """ASGI middleware that intercepts SSE and JSON-RPC paths before Starlette routing."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        method = scope.get("method", "")

        # Diagnostic: log headers for any /openai/* request (bearer redacted).
        # Added 2026-05-20 to diagnose the ChatGPT connector handshake failure.
        if scope.get("type") == "http" and path.startswith("/openai/"):
            _log_openai_request_headers(scope)

        if scope["type"] == "http" and path == "/messages" and method == "POST":
            logger.info("Message received")
            await sse.handle_post_message(scope, receive, send)
        elif scope["type"] == "http" and path == "/sse" and method == "GET":
            if not _native_auth_ok(scope):
                logger.warning(f"Rejected unauthenticated /sse connect from {scope.get('client')}")
                await _send_401(
                    send,
                    "Credential required for /sse: Authorization: Bearer <token> or ?token=<token>",
                )
                return
            logger.info(f"New SSE connection from {scope.get('client')}")
            async with sse.connect_sse(scope, receive, send) as (read_stream, write_stream):
                await sovereign_server.run(
                    read_stream,
                    write_stream,
                    sovereign_server.create_initialization_options(),
                    raise_exceptions=True,
                )
        elif _BRIDGE_ENABLED and path == "/openai/sse" and method == "GET":
            _gate = _verify_at_door(
                scope, expected_substrate="chatgpt-openai-bridge", transport="sse"
            )
            if not _gate.allowed:
                await _gate_send_401(
                    send,
                    _gate.reason or "Unauthorized",
                    realm="Sovereign Stack OpenAI Bridge",
                    resource_metadata_url="https://stack.templetwo.com/openai/.well-known/oauth-protected-resource",
                )
            else:
                await handle_openai_sse(scope, receive, send)
        elif _BRIDGE_ENABLED and path == "/openai/messages" and method == "POST":
            _gate = _verify_at_door(
                scope, expected_substrate="chatgpt-openai-bridge", transport="sse"
            )
            if not _gate.allowed:
                await _gate_send_401(
                    send,
                    _gate.reason or "Unauthorized",
                    realm="Sovereign Stack OpenAI Bridge",
                    resource_metadata_url="https://stack.templetwo.com/openai/.well-known/oauth-protected-resource",
                )
            else:
                await handle_openai_messages(scope, receive, send)
        elif _BRIDGE_ENABLED and path == "/openai/sse-test" and method == "GET":
            await handle_openai_sse_test(scope, receive, send)
        elif _BRIDGE_ENABLED and path == "/openai/messages-test" and method == "POST":
            await handle_openai_messages_test(scope, receive, send)
        elif _GROK_BRIDGE_ENABLED and path == "/grok/sse" and method == "GET":
            await handle_grok_sse(scope, receive, send)
        elif _GROK_BRIDGE_ENABLED and path == "/grok/messages" and method == "POST":
            await handle_grok_messages(scope, receive, send)
        elif _GROK_BRIDGE_ENABLED and path == "/grok/oauth/authorize":
            # GET shows consent page; POST receives consent submission
            await handle_grok_oauth_authorize(scope, receive, send)
        elif _GROK_BRIDGE_ENABLED and path == "/grok/oauth/token" and method == "POST":
            await handle_grok_oauth_token(scope, receive, send)
        elif (
            _GROK_BRIDGE_ENABLED
            and path == "/grok/.well-known/oauth-authorization-server"
            and method == "GET"
        ):
            await handle_grok_oauth_as_meta(scope, receive, send)
        elif (
            _GROK_BRIDGE_ENABLED
            and path == "/grok/.well-known/oauth-protected-resource"
            and method == "GET"
        ):
            await handle_grok_oauth_pr_meta(scope, receive, send)
        elif _BRIDGE_ENABLED and path == "/openai/oauth/authorize":
            # GET shows consent page; POST receives consent submission
            await handle_openai_oauth_authorize(scope, receive, send)
        elif _BRIDGE_ENABLED and path == "/openai/oauth/token" and method == "POST":
            await handle_openai_oauth_token(scope, receive, send)
        elif _BRIDGE_ENABLED and path == "/openai/oauth/register" and method == "POST":
            await handle_openai_oauth_register(scope, receive, send)
        elif (
            _BRIDGE_ENABLED
            and path == "/openai/.well-known/oauth-authorization-server"
            and method == "GET"
        ):
            await handle_openai_oauth_as_meta(scope, receive, send)
        elif (
            _BRIDGE_ENABLED
            and path == "/openai/.well-known/oauth-protected-resource"
            and method == "GET"
        ):
            await handle_openai_oauth_pr_meta(scope, receive, send)
        else:
            await self.app(scope, receive, send)


async def bridge_info(request: Request) -> JSONResponse:
    """Bridge manifest — what's exposed on /openai/sse."""
    if not _BRIDGE_ENABLED:
        return JSONResponse({"error": "OpenAI bridge not loaded"}, status_code=503)
    from openai_bridge.manifest import MANIFEST

    return JSONResponse(MANIFEST)


async def grok_bridge_info(request: Request) -> JSONResponse:
    """Bridge manifest — what's exposed on /grok/sse."""
    if not _GROK_BRIDGE_ENABLED:
        return JSONResponse({"error": "Grok bridge not loaded"}, status_code=503)
    return JSONResponse(GROK_MANIFEST)


# Create Starlette app with SSE and health routes
_inner_app = Starlette(
    debug=False,
    routes=[
        Route("/health", health, methods=["GET"]),
        Route("/openai/info", bridge_info, methods=["GET"]),
        Route("/grok/info", grok_bridge_info, methods=["GET"]),
    ],
)

# Wrap with message handler middleware
app = SovereignAsgiMiddleware(_inner_app)


def main(host: str = "127.0.0.1", port: int = 3434):
    """
    Start SSE server for remote access

    Args:
        host: Host to bind to (default: 127.0.0.1 — tunnel handles external)
        port: Port to listen on (default: 3434)
    """
    logger.info(f"Sovereign Stack SSE Server starting on {host}:{port}")
    logger.info(f"SSE endpoint: http://{host}:{port}/sse")
    logger.info(f"Health check: http://{host}:{port}/health")

    uvicorn.run(app, host=host, port=port, log_level="info", access_log=True)


if __name__ == "__main__":
    main()
