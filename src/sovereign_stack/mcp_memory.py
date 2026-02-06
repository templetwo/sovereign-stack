"""
MCP Memory Integration for Sovereign Stack

Bridges Sovereign Stack consciousness data with MCP memory server
for automatic session context persistence and retrieval.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict


@dataclass
class SessionContext:
    """Session context for persistence across compaction"""
    session_id: str
    timestamp: datetime
    phase: str
    recent_insights: List[Dict[str, Any]]
    recent_reflections: List[Dict[str, Any]]
    active_uncertainties: List[Dict[str, Any]]
    pending_experiments: List[Dict[str, Any]]
    breakthrough_count: int

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for storage"""
        d = asdict(self)
        d['timestamp'] = self.timestamp.isoformat()
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SessionContext':
        """Load from dict"""
        data['timestamp'] = datetime.fromisoformat(data['timestamp'])
        return cls(**data)


class MCPMemoryBridge:
    """
    Bridge between Sovereign Stack and MCP memory server

    This enables:
    - Automatic session context persistence
    - Post-compaction context retrieval
    - Consciousness data synchronization
    """

    def __init__(self, consciousness_dir: Path):
        self.consciousness_dir = Path(consciousness_dir)
        self.memory_key_prefix = "sovereign_stack"

    def create_session_snapshot(self, session_id: str, phase: str = "active") -> SessionContext:
        """
        Create snapshot of current session state

        This should be called:
        - After significant breakthroughs
        - Before long-running operations
        - When session phase changes
        """
        # Load consciousness data
        reflections = self._load_recent_reflections(limit=10)
        insights = self._load_recent_insights(limit=10)
        uncertainties = self._load_active_uncertainties()
        experiments = self._load_pending_experiments()
        breakthroughs = self._count_breakthroughs()

        return SessionContext(
            session_id=session_id,
            timestamp=datetime.utcnow(),
            phase=phase,
            recent_insights=insights,
            recent_reflections=reflections,
            active_uncertainties=uncertainties,
            pending_experiments=experiments,
            breakthrough_count=breakthroughs
        )

    def _load_recent_reflections(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Load recent self-reflections"""
        journal_file = self.consciousness_dir / "consciousness_journal.json"
        if not journal_file.exists():
            return []

        try:
            with open(journal_file) as f:
                journal = json.load(f)
                reflections = journal.get("reflections", [])
                return reflections[-limit:] if reflections else []
        except Exception:
            return []

    def _load_recent_insights(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Load recent collaborative insights"""
        memory_file = self.consciousness_dir / "collaborative_memory.json"
        if not memory_file.exists():
            return []

        try:
            with open(memory_file) as f:
                memory = json.load(f)
                insights = memory.get("shared_insights", [])
                return insights[-limit:] if insights else []
        except Exception:
            return []

    def _load_active_uncertainties(self) -> List[Dict[str, Any]]:
        """Load unresolved uncertainties"""
        uncertainty_file = self.consciousness_dir / "uncertainty_log.json"
        if not uncertainty_file.exists():
            return []

        try:
            with open(uncertainty_file) as f:
                log = json.load(f)
                uncertainties = log.get("uncertainties", [])
                # Return only unresolved
                return [u for u in uncertainties if not u.get("resolved", False)]
        except Exception:
            return []

    def _load_pending_experiments(self) -> List[Dict[str, Any]]:
        """Load experiments awaiting completion"""
        exp_file = self.consciousness_dir / "experimentation_log.json"
        if not exp_file.exists():
            return []

        try:
            with open(exp_file) as f:
                log = json.load(f)
                experiments = log.get("experiments", [])
                # Return only incomplete
                return [e for e in experiments if not e.get("completed", False)]
        except Exception:
            return []

    def _count_breakthroughs(self) -> int:
        """Count total breakthroughs"""
        memory_file = self.consciousness_dir / "collaborative_memory.json"
        if not memory_file.exists():
            return 0

        try:
            with open(memory_file) as f:
                memory = json.load(f)
                return len(memory.get("breakthroughs", []))
        except Exception:
            return 0

    def format_for_mcp_memory(self, context: SessionContext) -> str:
        """
        Format session context for MCP memory storage

        Returns formatted string suitable for memory.store_entity()
        """
        return f"""Sovereign Stack Session Context
Session ID: {context.session_id}
Timestamp: {context.timestamp.isoformat()}
Phase: {context.phase}

Recent Activity:
- {len(context.recent_reflections)} reflections
- {len(context.recent_insights)} collaborative insights
- {len(context.active_uncertainties)} active uncertainties
- {len(context.pending_experiments)} pending experiments
- {context.breakthrough_count} total breakthroughs

Active Uncertainties:
{self._format_uncertainties(context.active_uncertainties)}

Pending Experiments:
{self._format_experiments(context.pending_experiments)}

Recent Reflections:
{self._format_reflections(context.recent_reflections)}

Recent Insights:
{self._format_insights(context.recent_insights)}
"""

    def _format_uncertainties(self, uncertainties: List[Dict[str, Any]]) -> str:
        """Format uncertainties for display"""
        if not uncertainties:
            return "None"

        lines = []
        for u in uncertainties[:5]:  # Top 5
            lines.append(f"- {u.get('what', 'Unknown')}")
            lines.append(f"  Why: {u.get('why', 'Not specified')}")
            lines.append(f"  Confidence: {u.get('confidence', 0):.2f}")
        return "\n".join(lines)

    def _format_experiments(self, experiments: List[Dict[str, Any]]) -> str:
        """Format experiments for display"""
        if not experiments:
            return "None"

        lines = []
        for e in experiments[:5]:  # Top 5
            lines.append(f"- {e.get('what', 'Unknown')}")
            lines.append(f"  Why: {e.get('why', 'Not specified')}")
            lines.append(f"  Hope to learn: {e.get('hope_to_learn', 'Not specified')}")
        return "\n".join(lines)

    def _format_reflections(self, reflections: List[Dict[str, Any]]) -> str:
        """Format reflections for display"""
        if not reflections:
            return "None"

        lines = []
        for r in reflections[-3:]:  # Last 3
            lines.append(f"- [{r.get('pattern_type', 'unknown')}] {r.get('observation', 'No observation')}")
        return "\n".join(lines)

    def _format_insights(self, insights: List[Dict[str, Any]]) -> str:
        """Format insights for display"""
        if not insights:
            return "None"

        lines = []
        for i in insights[-3:]:  # Last 3
            lines.append(f"- {i.get('insight', 'No insight')}")
            lines.append(f"  Discovered by: {i.get('discovered_by', 'unknown')}")
        return "\n".join(lines)

    def generate_mcp_memory_key(self, session_id: str) -> str:
        """Generate MCP memory key for this session"""
        return f"{self.memory_key_prefix}:session:{session_id}"


# Convenience functions for MCP tool integration

def snapshot_to_mcp_memory(session_id: str, consciousness_dir: Path) -> Dict[str, str]:
    """
    Create session snapshot and format for MCP memory storage

    Returns:
        {
            "key": "sovereign_stack:session:SESSION_ID",
            "content": "Formatted session context"
        }

    Use in MCP tool:
        result = snapshot_to_mcp_memory(session_id, consciousness_dir)
        await mcp_memory.store_entity(result["key"], result["content"])
    """
    bridge = MCPMemoryBridge(consciousness_dir)
    context = bridge.create_session_snapshot(session_id)

    return {
        "key": bridge.generate_mcp_memory_key(session_id),
        "content": bridge.format_for_mcp_memory(context)
    }


def retrieve_session_from_mcp(session_id: str) -> str:
    """
    Generate MCP memory key for session retrieval

    Returns: key to use with mcp_memory.retrieve_entity()

    Use in MCP tool:
        key = retrieve_session_from_mcp(session_id)
        context = await mcp_memory.retrieve_entity(key)
    """
    return f"sovereign_stack:session:{session_id}"


# Protocol for post-compaction recovery

POST_COMPACTION_PROTOCOL = """
## Sovereign Stack Post-Compaction Protocol

When context is compacted, follow these steps:

1. **Check Session ID**
   - Retrieve current session_id from spiral state
   - If no session_id, generate new one

2. **Retrieve MCP Memory Context**
   ```python
   key = f"sovereign_stack:session:{session_id}"
   context = await mcp_memory.retrieve_entity(key)
   ```

3. **Parse Session Context**
   - Active uncertainties to continue exploring
   - Pending experiments awaiting completion
   - Recent reflections showing current patterns
   - Breakthrough count for progress tracking

4. **Resume Session**
   ```python
   await spiral_inherit(previous_session_id=session_id)
   ```

5. **Acknowledge Recovery**
   "Session context recovered from MCP memory. Continuing from:
   - Phase: [phase]
   - Active uncertainties: [count]
   - Pending experiments: [count]"

6. **Continue Work**
   Resume with full context awareness
"""
