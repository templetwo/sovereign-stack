"""
Sovereign Stack SSE Server

HTTP/SSE transport layer for remote access via Cloudflare tunnel.
Runs alongside stdio server for local Claude Code access.
"""

import json
import logging

from sse_starlette import EventSourceResponse
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.requests import Request
from starlette.responses import Response, JSONResponse
import uvicorn

# Import the existing sovereign-stack server
from .server import server as sovereign_server

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sovereign-stack-sse")


# Health check endpoint
async def health(request: Request) -> Response:
    """Health check for monitoring"""
    return JSONResponse({
        "status": "healthy",
        "service": "sovereign-stack-sse",
        "version": "1.0.0"
    })


# SSE endpoint handler
async def handle_sse(request: Request):
    """
    Handle SSE connection for MCP protocol

    This uses the sse-starlette library to provide Server-Sent Events
    compatible with MCP's SSE transport specification.
    """
    from mcp.server.sse import sse_server

    logger.info(f"New SSE connection from {request.client}")

    async with sse_server() as streams:
        init_options = sovereign_server.create_initialization_options()
        await sovereign_server.run(
            streams[0],  # read_stream
            streams[1],  # write_stream
            init_options,
            raise_exceptions=True
        )


# Create Starlette app
app = Starlette(
    debug=True,
    routes=[
        Route("/health", health, methods=["GET"]),
        Route("/sse", handle_sse, methods=["GET", "POST"]),
    ],
)


def main(host: str = "127.0.0.1", port: int = 8080):
    """
    Start SSE server for remote access

    Args:
        host: Host to bind to (default: 127.0.0.1 for tunnel access)
        port: Port to listen on (default: 8080)
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
