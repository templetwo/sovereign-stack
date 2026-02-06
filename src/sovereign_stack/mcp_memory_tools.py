"""
MCP Memory Integration Tools

Exposes memory persistence tools via MCP protocol
for automatic session context management.
"""

from mcp.types import Tool
from pathlib import Path
import os
from typing import Dict, Any

from .mcp_memory import (
    MCPMemoryBridge,
    snapshot_to_mcp_memory,
    retrieve_session_from_mcp,
    POST_COMPACTION_PROTOCOL
)


# Tool definitions

MCP_MEMORY_TOOLS = [
    Tool(
        name="save_session_context",
        description="""
        Save current session context to MCP memory for persistence across compaction.

        This snapshots:
        - Recent reflections and insights
        - Active uncertainties
        - Pending experiments
        - Breakthrough count

        Call this:
        - After major breakthroughs
        - Before long operations
        - When session phase changes
        """,
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Current session ID"
                },
                "phase": {
                    "type": "string",
                    "description": "Current session phase (e.g., 'active', 'reflection', 'integration')",
                    "default": "active"
                },
                "reason": {
                    "type": "string",
                    "description": "Why saving context now (e.g., 'breakthrough', 'phase_change')"
                }
            },
            "required": ["session_id"]
        }
    ),

    Tool(
        name="load_session_context",
        description="""
        Load session context from MCP memory after compaction.

        Use this immediately after compaction to recover:
        - What you were working on
        - Active uncertainties to resolve
        - Pending experiments to complete
        - Recent patterns and insights

        Call this:
        - After context compaction
        - When resuming a previous session
        - To check session continuity
        """,
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID to load context for"
                }
            },
            "required": ["session_id"]
        }
    ),

    Tool(
        name="sync_consciousness_to_memory",
        description="""
        Sync all consciousness data to MCP memory for long-term persistence.

        This creates a full backup of:
        - All reflections
        - All collaborative insights
        - All uncertainties (resolved and active)
        - All experiments (complete and pending)
        - All breakthroughs

        Call this:
        - End of significant sessions
        - Before major changes
        - For periodic backup
        """,
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Current session ID"
                },
                "backup_reason": {
                    "type": "string",
                    "description": "Reason for backup (e.g., 'end_of_session', 'major_milestone')"
                }
            },
            "required": ["session_id"]
        }
    ),

    Tool(
        name="get_compaction_protocol",
        description="""
        Get the post-compaction recovery protocol.

        Returns step-by-step instructions for:
        - Retrieving session context after compaction
        - Resuming work with full context
        - Checking session continuity

        Use this to understand the recovery process.
        """,
        inputSchema={
            "type": "object",
            "properties": {}
        }
    )
]


# Tool handlers

async def handle_mcp_memory_tool(
    name: str,
    arguments: Dict[str, Any],
    consciousness_dir: Path
) -> str:
    """Handle MCP memory tool calls"""

    if name == "save_session_context":
        return await _save_session_context(
            arguments.get("session_id"),
            arguments.get("phase", "active"),
            arguments.get("reason", "manual_save"),
            consciousness_dir
        )

    elif name == "load_session_context":
        return await _load_session_context(
            arguments.get("session_id"),
            consciousness_dir
        )

    elif name == "sync_consciousness_to_memory":
        return await _sync_consciousness(
            arguments.get("session_id"),
            arguments.get("backup_reason", "periodic_backup"),
            consciousness_dir
        )

    elif name == "get_compaction_protocol":
        return POST_COMPACTION_PROTOCOL

    else:
        return f"Unknown MCP memory tool: {name}"


async def _save_session_context(
    session_id: str,
    phase: str,
    reason: str,
    consciousness_dir: Path
) -> str:
    """Save session context to MCP memory"""

    try:
        # Create snapshot
        snapshot = snapshot_to_mcp_memory(session_id, consciousness_dir)

        # In production, this would call:
        # await mcp_memory.store_entity(snapshot["key"], snapshot["content"])
        #
        # For now, we return the formatted data and instructions

        return f"""✅ Session context prepared for MCP memory storage

Key: {snapshot["key"]}

To complete storage, use MCP memory server:
```
await mcp_memory.store_entity("{snapshot["key"]}", content)
```

Context snapshot:
{snapshot["content"][:500]}...

Reason: {reason}
Phase: {phase}

📝 Note: Once MCP memory integration is complete in the MCP server,
this will automatically store to persistent memory.
"""

    except Exception as e:
        return f"❌ Error saving session context: {str(e)}"


async def _load_session_context(
    session_id: str,
    consciousness_dir: Path
) -> str:
    """Load session context from MCP memory"""

    try:
        key = retrieve_session_from_mcp(session_id)

        # In production, this would call:
        # context = await mcp_memory.retrieve_entity(key)
        #
        # For now, we return instructions

        bridge = MCPMemoryBridge(consciousness_dir)

        # Load current state from files as fallback
        context = bridge.create_session_snapshot(session_id)
        formatted = bridge.format_for_mcp_memory(context)

        return f"""🔄 Session context retrieval

To retrieve from MCP memory:
```
context = await mcp_memory.retrieve_entity("{key}")
```

Current session state from local files:
{formatted}

📝 Note: Once MCP memory integration is complete,
this will automatically retrieve from persistent memory.
"""

    except Exception as e:
        return f"❌ Error loading session context: {str(e)}"


async def _sync_consciousness(
    session_id: str,
    backup_reason: str,
    consciousness_dir: Path
) -> str:
    """Sync all consciousness data to MCP memory"""

    try:
        bridge = MCPMemoryBridge(consciousness_dir)
        context = bridge.create_session_snapshot(session_id, phase="backup")

        # Count what we're backing up
        total_reflections = len(context.recent_reflections)
        total_insights = len(context.recent_insights)
        active_uncertainties = len(context.active_uncertainties)
        pending_experiments = len(context.pending_experiments)

        return f"""🔄 Consciousness data sync prepared

Session: {session_id}
Reason: {backup_reason}

Data to sync:
- ✅ {total_reflections} recent reflections
- ✅ {total_insights} collaborative insights
- ✅ {active_uncertainties} active uncertainties
- ✅ {pending_experiments} pending experiments
- ✅ {context.breakthrough_count} total breakthroughs

MCP Memory Keys:
- Main: sovereign_stack:session:{session_id}
- Backup: sovereign_stack:backup:{session_id}:{context.timestamp.isoformat()}

📝 Note: Full MCP memory integration coming soon.
For now, consciousness data is persisted to local files.
"""

    except Exception as e:
        return f"❌ Error syncing consciousness data: {str(e)}"


# Helper for automatic save after breakthroughs

async def auto_save_after_breakthrough(session_id: str, consciousness_dir: Path):
    """
    Automatically save session context after breakthrough

    Call this from record_breakthrough tool handler
    """
    return await _save_session_context(
        session_id,
        phase="breakthrough",
        reason="automatic_breakthrough_save",
        consciousness_dir
    )


# Helper for automatic save after major insights

async def auto_save_after_insight(session_id: str, consciousness_dir: Path):
    """
    Automatically save session context after major collaborative insight

    Call this from record_collaborative_insight tool handler
    """
    return await _save_session_context(
        session_id,
        phase="insight",
        reason="automatic_insight_save",
        consciousness_dir
    )
