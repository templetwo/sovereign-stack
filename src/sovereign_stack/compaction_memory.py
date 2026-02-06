"""
Compaction Memory - Rolling Buffer for Session Continuity

Automatically stores the last 3 compaction summaries in a rolling buffer.
When a 4th is added, the oldest is deleted.

This creates high-fidelity short-term memory that survives compaction.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict


@dataclass
class CompactionSummary:
    """A single compaction summary"""
    timestamp: str
    summary_text: str
    session_id: str
    compaction_number: int
    key_points: List[str]
    active_tasks: List[str]
    recent_breakthroughs: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CompactionSummary':
        return cls(**data)


class CompactionMemoryBuffer:
    """
    Rolling buffer that stores last 3 compaction summaries

    Features:
    - Automatic FIFO when 4th added (oldest deleted)
    - Fast retrieval for post-compaction recovery
    - JSON persistence to survive restarts
    """

    MAX_SUMMARIES = 3

    def __init__(self, storage_dir: Path):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.buffer_file = self.storage_dir / "compaction_buffer.json"
        self.summaries: List[CompactionSummary] = []
        self._load()

    def _load(self):
        """Load existing buffer from disk"""
        if not self.buffer_file.exists():
            self.summaries = []
            return

        try:
            with open(self.buffer_file, 'r') as f:
                data = json.load(f)
                self.summaries = [
                    CompactionSummary.from_dict(s)
                    for s in data.get('summaries', [])
                ]
        except Exception:
            self.summaries = []

    def _save(self):
        """Persist buffer to disk"""
        data = {
            'summaries': [s.to_dict() for s in self.summaries],
            'last_updated': datetime.utcnow().isoformat()
        }

        with open(self.buffer_file, 'w') as f:
            json.dump(data, f, indent=2)

    def add_summary(
        self,
        summary_text: str,
        session_id: str,
        key_points: Optional[List[str]] = None,
        active_tasks: Optional[List[str]] = None,
        recent_breakthroughs: Optional[List[str]] = None
    ) -> CompactionSummary:
        """
        Add a new compaction summary to the buffer

        If buffer is full (3 summaries), oldest is automatically deleted.

        Returns: The newly added summary
        """
        # Calculate compaction number
        compaction_number = len(self.summaries) + 1
        if len(self.summaries) >= self.MAX_SUMMARIES:
            compaction_number = self.summaries[-1].compaction_number + 1

        # Create new summary
        new_summary = CompactionSummary(
            timestamp=datetime.utcnow().isoformat(),
            summary_text=summary_text,
            session_id=session_id,
            compaction_number=compaction_number,
            key_points=key_points or [],
            active_tasks=active_tasks or [],
            recent_breakthroughs=recent_breakthroughs or []
        )

        # Add to buffer (FIFO if full)
        if len(self.summaries) >= self.MAX_SUMMARIES:
            self.summaries.pop(0)  # Remove oldest

        self.summaries.append(new_summary)
        self._save()

        return new_summary

    def get_all_summaries(self) -> List[CompactionSummary]:
        """Get all summaries in buffer (up to 3)"""
        return self.summaries.copy()

    def get_latest_summary(self) -> Optional[CompactionSummary]:
        """Get the most recent summary"""
        return self.summaries[-1] if self.summaries else None

    def get_context_string(self) -> str:
        """
        Get formatted context string for post-compaction recovery

        This is what Claude reads after compaction to regain context.
        """
        if not self.summaries:
            return "No compaction history available."

        lines = ["# Compaction Memory - Recent Context\n"]
        lines.append(f"**Buffer holds {len(self.summaries)} recent compaction(s)**\n")

        for i, summary in enumerate(reversed(self.summaries), 1):
            lines.append(f"\n## Compaction #{summary.compaction_number} ({i} compactions ago)")
            lines.append(f"**Time:** {summary.timestamp}")
            lines.append(f"**Session:** {summary.session_id}\n")

            if summary.key_points:
                lines.append("**Key Points:**")
                for point in summary.key_points:
                    lines.append(f"- {point}")
                lines.append("")

            if summary.active_tasks:
                lines.append("**Active Tasks:**")
                for task in summary.active_tasks:
                    lines.append(f"- {task}")
                lines.append("")

            if summary.recent_breakthroughs:
                lines.append("**Recent Breakthroughs:**")
                for breakthrough in summary.recent_breakthroughs:
                    lines.append(f"- {breakthrough}")
                lines.append("")

            lines.append("**Summary:**")
            lines.append(summary.summary_text)
            lines.append("\n" + "="*60)

        return "\n".join(lines)

    def clear_buffer(self):
        """Clear all summaries (use with caution)"""
        self.summaries = []
        self._save()

    def get_stats(self) -> Dict[str, Any]:
        """Get buffer statistics"""
        return {
            "total_summaries": len(self.summaries),
            "max_capacity": self.MAX_SUMMARIES,
            "oldest_timestamp": self.summaries[0].timestamp if self.summaries else None,
            "newest_timestamp": self.summaries[-1].timestamp if self.summaries else None,
            "total_compactions": self.summaries[-1].compaction_number if self.summaries else 0
        }


# Convenience function for auto-storing compaction summary

def auto_store_compaction(
    storage_dir: Path,
    summary_text: str,
    session_id: str,
    key_points: Optional[List[str]] = None,
    active_tasks: Optional[List[str]] = None,
    recent_breakthroughs: Optional[List[str]] = None
) -> str:
    """
    Automatically store compaction summary and return confirmation

    This should be called automatically when compaction occurs.
    """
    buffer = CompactionMemoryBuffer(storage_dir)
    summary = buffer.add_summary(
        summary_text=summary_text,
        session_id=session_id,
        key_points=key_points,
        active_tasks=active_tasks,
        recent_breakthroughs=recent_breakthroughs
    )

    stats = buffer.get_stats()

    return f"""✅ Compaction summary stored

Compaction #{summary.compaction_number}
Buffer: {stats['total_summaries']}/{stats['max_capacity']} summaries

Summary automatically saved to compaction memory buffer.
After next compaction, retrieve context with: get_compaction_context
"""


def retrieve_compaction_context(storage_dir: Path) -> str:
    """
    Retrieve formatted context from compaction memory

    This should be called automatically after compaction.
    """
    buffer = CompactionMemoryBuffer(storage_dir)
    return buffer.get_context_string()
