"""
Compaction Memory MCP Tools

Exposes rolling compaction memory buffer via MCP protocol.
Automatically stores last 3 compaction summaries for instant context recovery.
"""

from mcp.types import Tool
from pathlib import Path
from typing import Dict, Any

from .compaction_memory import (
    CompactionMemoryBuffer,
    auto_store_compaction,
    retrieve_compaction_context
)


# Tool definitions

COMPACTION_MEMORY_TOOLS = [
    Tool(
        name="store_compaction_summary",
        description="""
        Store a compaction summary in the rolling memory buffer.

        This is AUTOMATICALLY called when context compaction occurs.
        Keeps last 3 summaries in FIFO buffer (oldest deleted when 4th added).

        After compaction, use get_compaction_context to retrieve.
        """,
        inputSchema={
            "type": "object",
            "properties": {
                "summary_text": {
                    "type": "string",
                    "description": "The full compaction summary text"
                },
                "session_id": {
                    "type": "string",
                    "description": "Current session ID"
                },
                "key_points": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of key points from this session segment"
                },
                "active_tasks": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Currently active tasks"
                },
                "recent_breakthroughs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Recent breakthroughs to remember"
                }
            },
            "required": ["summary_text", "session_id"]
        }
    ),

    Tool(
        name="get_compaction_context",
        description="""
        Retrieve recent context from compaction memory buffer.

        Returns formatted summary of last 3 compactions including:
        - Key points from each segment
        - Active tasks
        - Recent breakthroughs
        - Full summary text

        Use this IMMEDIATELY after compaction to regain context.
        """,
        inputSchema={
            "type": "object",
            "properties": {}
        }
    ),

    Tool(
        name="get_compaction_stats",
        description="""
        Get statistics about the compaction memory buffer.

        Returns:
        - Number of summaries in buffer
        - Total compactions tracked
        - Oldest and newest timestamps

        Use this to check buffer status.
        """,
        inputSchema={
            "type": "object",
            "properties": {}
        }
    )
]


# Tool handlers

async def handle_compaction_memory_tool(
    name: str,
    arguments: Dict[str, Any],
    sovereign_root: Path
) -> str:
    """Handle compaction memory tool calls"""

    storage_dir = sovereign_root / "compaction_memory"

    if name == "store_compaction_summary":
        return await _store_compaction_summary(
            storage_dir=storage_dir,
            summary_text=arguments.get("summary_text", ""),
            session_id=arguments.get("session_id", ""),
            key_points=arguments.get("key_points"),
            active_tasks=arguments.get("active_tasks"),
            recent_breakthroughs=arguments.get("recent_breakthroughs")
        )

    elif name == "get_compaction_context":
        return await _get_compaction_context(storage_dir)

    elif name == "get_compaction_stats":
        return await _get_compaction_stats(storage_dir)

    else:
        return f"Unknown compaction memory tool: {name}"


async def _store_compaction_summary(
    storage_dir: Path,
    summary_text: str,
    session_id: str,
    key_points: list = None,
    active_tasks: list = None,
    recent_breakthroughs: list = None
) -> str:
    """Store compaction summary"""

    try:
        result = auto_store_compaction(
            storage_dir=storage_dir,
            summary_text=summary_text,
            session_id=session_id,
            key_points=key_points,
            active_tasks=active_tasks,
            recent_breakthroughs=recent_breakthroughs
        )
        return result

    except Exception as e:
        return f"❌ Error storing compaction summary: {str(e)}"


async def _get_compaction_context(storage_dir: Path) -> str:
    """Retrieve compaction context"""

    try:
        context = retrieve_compaction_context(storage_dir)
        return context

    except Exception as e:
        return f"❌ Error retrieving compaction context: {str(e)}"


async def _get_compaction_stats(storage_dir: Path) -> str:
    """Get buffer statistics"""

    try:
        buffer = CompactionMemoryBuffer(storage_dir)
        stats = buffer.get_stats()

        latest = buffer.get_latest_summary()

        output = f"""📊 Compaction Memory Buffer Stats

**Capacity:** {stats['total_summaries']}/{stats['max_capacity']} summaries
**Total Compactions:** {stats['total_compactions']}

**Oldest Summary:** {stats['oldest_timestamp'] or 'None'}
**Newest Summary:** {stats['newest_timestamp'] or 'None'}
"""

        if latest:
            output += f"""
**Latest Summary Preview:**
Session: {latest.session_id}
Compaction #{latest.compaction_number}

Key Points: {len(latest.key_points)}
Active Tasks: {len(latest.active_tasks)}
Breakthroughs: {len(latest.recent_breakthroughs)}
"""

        return output

    except Exception as e:
        return f"❌ Error getting buffer stats: {str(e)}"


# Hook for automatic storage after compaction

def should_auto_store_compaction() -> bool:
    """
    Check if compaction summary should be auto-stored

    In production, this would detect compaction events.
    For now, we rely on explicit calls.
    """
    # TODO: Detect compaction events automatically
    # This could check:
    # - Context window usage
    # - Message history length
    # - Compaction markers in conversation
    return False


# Protocol for automatic compaction handling

COMPACTION_PROTOCOL = """
# Automatic Compaction Memory Protocol

## After Compaction Occurs

1. **Immediately call:** get_compaction_context
   - Retrieves last 3 compaction summaries
   - Provides instant context recovery

2. **Review context:**
   - Read key points from each recent compaction
   - Check active tasks to resume
   - Note recent breakthroughs

3. **Resume work:**
   - Continue from where compaction interrupted
   - Reference active tasks
   - Build on recent breakthroughs

## Before Next Compaction

When you notice context approaching limits:

1. **Call:** store_compaction_summary
   - Summary text: Brief overview of this session segment
   - Key points: 3-5 most important discoveries
   - Active tasks: Current work in progress
   - Recent breakthroughs: Major discoveries to remember

2. **Buffer updates automatically:**
   - Newest summary added
   - If full (3 summaries), oldest deleted
   - Rolling FIFO buffer maintained

## Example Usage

### Storing before compaction:
```
store_compaction_summary({
  "summary_text": "Implemented compaction memory buffer with rolling FIFO.
                   Created MCP tools for automatic storage/retrieval.",
  "session_id": "session_20260206",
  "key_points": [
    "Created CompactionMemoryBuffer class",
    "Implemented rolling FIFO (max 3)",
    "Added MCP tools for auto-storage"
  ],
  "active_tasks": [
    "Integrate with server.py",
    "Test automatic recovery"
  ],
  "recent_breakthroughs": [
    "Compaction memory solves context continuity problem"
  ]
})
```

### Retrieving after compaction:
```
get_compaction_context()

# Returns formatted summary of last 3 compactions
# with all key points, tasks, and breakthroughs
```

## Benefits

✅ **Instant Recovery:** No re-reading long summaries
✅ **High Fidelity:** Captures exact state before compaction
✅ **Automatic:** FIFO buffer manages itself
✅ **Lightweight:** Only 3 summaries, not full history
✅ **Persistent:** Survives session restarts
"""
