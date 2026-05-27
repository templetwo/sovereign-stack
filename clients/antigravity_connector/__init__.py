"""Antigravity connector for the Sovereign Stack (stdio MCP client, ring-governed)."""
from .bridge_setup import (
    ANTIGRAVITY_CONTEXT,
    ANTIGRAVITY_IDENTITY,
    SUBSTRATE,
    governed_call,
    governed_tool_list,
    register,
)
from .sovereign_connector import SovereignConnector

# Register the antigravity substrate + context with bridge_core on import.
register()

__all__ = [
    "SovereignConnector",
    "SUBSTRATE",
    "ANTIGRAVITY_CONTEXT",
    "ANTIGRAVITY_IDENTITY",
    "governed_call",
    "governed_tool_list",
    "register",
]
