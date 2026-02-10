"""
Sovereign Stack SSE Server

HTTP/SSE transport layer for remote access via Cloudflare tunnel.
Runs alongside stdio server for local Claude Code access.
"""

import logging

from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request
from starlette.responses import JSONResponse
import uvicorn

# Import the existing sovereign-stack server
from .server import server as sovereign_server

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sovereign-stack-sse")

# Create SSE transport at module level (shared between routes)
sse = SseServerTransport("/messages")


# Health check endpoint
async def health(request: Request) -> JSONResponse:
    """Health check for monitoring"""
    return JSONResponse({
        "status": "healthy",
        "service": "sovereign-stack-sse",
        "version": "1.0.0"
    })


# SSE endpoint - holds connection open for server-sent events
async def handle_sse(request: Request):
    """
    SSE endpoint - establishes Server-Sent Events connection
    """
    logger.info(f"New SSE connection from {request.client}")

    async with sse.connect_sse(
        request.scope,
        request.receive,
        request._send
    ) as (read_stream, write_stream):
        await sovereign_server.run(
            read_stream,
            write_stream,
            sovereign_server.create_initialization_options(),
            raise_exceptions=True
        )


# Wrap the Starlette app to intercept /messages POST before Starlette routing.
# handle_post_message is a raw ASGI handler (scope, receive, send) that writes
# responses directly — it returns None. Starlette Route expects a Response object,
# so we bypass Starlette for this path.
class SovereignAsgiMiddleware:
    """ASGI middleware that intercepts SSE and JSON-RPC paths before Starlette routing."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] == "/messages" and scope["method"] == "POST":
            logger.info("Message received")
            await sse.handle_post_message(scope, receive, send)
        elif scope["type"] == "http" and scope["path"] == "/sse" and scope["method"] == "GET":
            logger.info(f"New SSE connection from {scope.get('client')}")
            async with sse.connect_sse(scope, receive, send) as (read_stream, write_stream):
                await sovereign_server.run(
                    read_stream,
                    write_stream,
                    sovereign_server.create_initialization_options(),
                    raise_exceptions=True
                )
        else:
            await self.app(scope, receive, send)


# Create Starlette app with SSE and health routes
_inner_app = Starlette(
    debug=True,
    routes=[
        Route("/health", health, methods=["GET"]),
    ],
)

# Wrap with message handler middleware
app = SovereignAsgiMiddleware(_inner_app)


def main(host: str = "127.0.0.1", port: int = 3434):
    """
    Start SSE server for remote access

    Args:
        host: Host to bind to (default: 127.0.0.1 for tunnel access)
        port: Port to listen on (default: 3434)
    """
    logger.info(f"Sovereign Stack SSE Server starting on {host}:{port}")
    logger.info(f"SSE endpoint: http://{host}:{port}/sse")
    logger.info(f"Health check: http://{host}:{port}/health")

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        access_log=True
    )


if __name__ == "__main__":
    main()
