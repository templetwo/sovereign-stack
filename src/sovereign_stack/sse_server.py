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

    This endpoint holds the connection open and streams events to the client.
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


# Messages endpoint - receives JSON-RPC messages from client
async def handle_messages(request: Request):
    """
    Messages endpoint - receives POST messages from client

    This endpoint receives JSON-RPC messages from the client
    and forwards them to the MCP server.
    """
    logger.info(f"Message received from {request.client}")

    return await sse.handle_post_message(
        request.scope,
        request.receive,
        request._send
    )


# Create Starlette app with both SSE and message routes
app = Starlette(
    debug=True,
    routes=[
        Route("/health", health, methods=["GET"]),
        Route("/sse", handle_sse, methods=["GET"]),
        Route("/messages", handle_messages, methods=["POST"]),
    ],
)


def main(host: str = "127.0.0.1", port: int = 3434):
    """
    Start SSE server for remote access

    Args:
        host: Host to bind to (default: 127.0.0.1 for tunnel access)
        port: Port to listen on (default: 3434)
    """
    logger.info(f"🌀 Sovereign Stack SSE Server starting on {host}:{port}")
    logger.info("📡 Cloudflare tunnel should point to this endpoint")
    logger.info(f"🔗 SSE endpoint: http://{host}:{port}/sse")
    logger.info(f"❤️  Health check: http://{host}:{port}/health")

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        access_log=True
    )


if __name__ == "__main__":
    main()
