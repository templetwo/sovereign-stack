"""
Sovereign Stack SSE Server

HTTP/SSE transport layer for remote access via Cloudflare tunnel.
Runs alongside stdio server for local Claude Code access.
"""

import logging
import os
import sys
from pathlib import Path

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
    from openai_bridge.mcp_filtered import (
        handle_openai_messages,
        handle_openai_messages_test,
        handle_openai_sse,
        handle_openai_sse_test,
    )
    _BRIDGE_ENABLED = True
except ImportError:
    _BRIDGE_ENABLED = False
    handle_openai_sse = None
    handle_openai_messages = None
    handle_openai_sse_test = None
    handle_openai_messages_test = None

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


# ── OpenAI bridge auth ────────────────────────────────────────────────────────
# /openai/sse and /openai/messages require a valid bearer token.
# Token is read from BRIDGE_TOKEN env var (same as the bridge REST API).
# /sse is intentionally left unchanged — existing auth model.

_OPENAI_BRIDGE_TOKEN: str = os.environ.get("BRIDGE_TOKEN", "")


def _bridge_auth_ok(scope: dict) -> bool:
    """Return True if the request carries a valid BRIDGE_TOKEN bearer credential."""
    if not _OPENAI_BRIDGE_TOKEN:
        # Token not configured — allow (preserves existing /sse behaviour during local dev).
        logger.warning("BRIDGE_TOKEN not set — /openai/sse is unauthenticated")
        return True
    headers = dict(scope.get("headers", []))
    auth = headers.get(b"authorization", b"").decode("utf-8", errors="replace")
    if auth.startswith("Bearer "):
        return auth[7:].strip() == _OPENAI_BRIDGE_TOKEN
    return False


async def _send_401(send) -> None:
    body = b'{"error":"Unauthorized","detail":"Valid Bearer token required for /openai/sse"}'
    await send({"type": "http.response.start", "status": 401,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


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

        if scope["type"] == "http" and path == "/messages" and method == "POST":
            logger.info("Message received")
            await sse.handle_post_message(scope, receive, send)
        elif scope["type"] == "http" and path == "/sse" and method == "GET":
            logger.info(f"New SSE connection from {scope.get('client')}")
            async with sse.connect_sse(scope, receive, send) as (read_stream, write_stream):
                await sovereign_server.run(
                    read_stream,
                    write_stream,
                    sovereign_server.create_initialization_options(),
                    raise_exceptions=True,
                )
        elif _BRIDGE_ENABLED and path == "/openai/sse" and method == "GET":
            await handle_openai_sse(scope, receive, send)
        elif _BRIDGE_ENABLED and path == "/openai/messages" and method == "POST":
            await handle_openai_messages(scope, receive, send)
        elif _BRIDGE_ENABLED and path == "/openai/sse-test" and method == "GET":
            await handle_openai_sse_test(scope, receive, send)
        elif _BRIDGE_ENABLED and path == "/openai/messages-test" and method == "POST":
            await handle_openai_messages_test(scope, receive, send)
        else:
            await self.app(scope, receive, send)


async def bridge_info(request: Request) -> JSONResponse:
    """Bridge manifest — what's exposed on /openai/sse."""
    if not _BRIDGE_ENABLED:
        return JSONResponse({"error": "OpenAI bridge not loaded"}, status_code=503)
    from openai_bridge.manifest import MANIFEST
    return JSONResponse(MANIFEST)


# Create Starlette app with SSE and health routes
_inner_app = Starlette(
    debug=True,
    routes=[
        Route("/health", health, methods=["GET"]),
        Route("/openai/info", bridge_info, methods=["GET"]),
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
