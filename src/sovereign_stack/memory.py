"""
Memory Module - Experiential Memory System

The agent organizes its own experience semantically using
the filesystem as a decision tree. The topology IS the insight.

Two layers:
1. MemoryEngine - Low-level BTB routing for agent experiences
2. ExperientialMemory - High-level chronicle for insights/learnings

Distilled from:
- back-to-the-basics/memory.py
- temple-vault/server.py (wisdom tools)
"""

import os
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from glob import glob as glob_files

from .coherence import Coherence


# =============================================================================
# DEFAULT MEMORY SCHEMA
# =============================================================================

MEMORY_SCHEMA = {
    "outcome": {
        "success": {
            "tool": {
                "code_interpreter": {
                    "task_type": {
                        "write": "{timestamp}_{summary}.json",
                        "debug": "{timestamp}_{summary}.json",
                        "refactor": "{timestamp}_{summary}.json",
                    }
                },
                "web_search": "{timestamp}_{summary}.json",
                "file_operation": "{timestamp}_{summary}.json",
                "conversation": "{timestamp}_{summary}.json",
            }
        },
        "failure": {
            "tool": {
                "code_interpreter": {
                    "error_type": {
                        "syntax": "{timestamp}_{summary}.json",
                        "runtime": "{timestamp}_{summary}.json",
                        "logic": "{timestamp}_{summary}.json",
                    }
                },
                "web_search": "{timestamp}_{summary}.json",
                "unknown": "{timestamp}_{summary}.json",
            }
        },
        "learning": {
            "insight_type": {
                "pattern": "{timestamp}_{summary}.json",
                "correction": "{timestamp}_{summary}.json",
                "preference": "{timestamp}_{summary}.json",
            }
        }
    }
}


# =============================================================================
# LOW-LEVEL MEMORY ENGINE (BTB)
# =============================================================================

class MemoryEngine:
    """
    Agentic Memory using filesystem topology.

    The agent's memories are organized semantically in a directory tree.
    The structure itself encodes patterns - a deep failure/code/refactor
    path signals a struggle area.

    Unlike vector DBs, you can browse this. You can `ls` your own mind.
    """

    def __init__(self, root: str = "memories", schema: Dict = None):
        self.root = root
        self.schema = schema or MEMORY_SCHEMA
        self.engine = Coherence(self.schema, root=root)
        os.makedirs(root, exist_ok=True)

    def remember(self, content: Any, outcome: str, tool: str = None,
                 summary: str = None, **metadata) -> str:
        """
        Store a memory. It routes itself to the right location.

        Args:
            content: The memory content (will be JSON serialized)
            outcome: 'success', 'failure', or 'learning'
            tool: The tool used (if applicable)
            summary: Brief summary for filename (auto-generated if None)
            **metadata: Additional routing keys

        Returns:
            Path where the memory was stored
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if summary is None:
            if isinstance(content, str):
                summary = content[:30].replace(" ", "_").replace("/", "-")
            else:
                summary = "memory"
        summary = self._sanitize(summary)

        packet = {"outcome": outcome, "timestamp": timestamp, "summary": summary, **metadata}
        if tool:
            packet["tool"] = tool

        path = self.engine.transmit(packet, dry_run=False)

        if not path.endswith('.json'):
            path = path.rstrip('/') + f"/{timestamp}_{summary}.json"

        memory_doc = {
            "timestamp": datetime.now().isoformat(),
            "outcome": outcome,
            "tool": tool,
            "summary": summary,
            "content": content,
            "metadata": metadata,
            "_path": path
        }

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            json.dump(memory_doc, f, indent=2, default=str)

        return path

    def recall(self, pattern: str = None, **intent) -> List[Dict]:
        """
        Recall memories by glob pattern or intent.

        Args:
            pattern: Direct glob pattern (e.g., "**/failure/**/*.json")
            **intent: Key-value pairs to generate pattern

        Returns:
            List of memory documents matching the query
        """
        if pattern is None:
            pattern = self.engine.receive(**intent)
            if not pattern.endswith('*.json'):
                pattern = pattern.rstrip('/*') + '/**/*.json'

        matches = glob_files(pattern, recursive=True)

        memories = []
        for path in sorted(matches, reverse=True):
            try:
                with open(path) as f:
                    doc = json.load(f)
                    doc['_path'] = path
                    memories.append(doc)
            except (json.JSONDecodeError, IOError):
                continue

        return memories

    def reflect(self, domain: str = None) -> Dict:
        """
        Reflect on memory patterns. Analyze the topology itself.

        Returns:
            Analysis of memory distribution
        """
        base = Path(self.root)

        analysis = {
            "total_memories": 0,
            "by_outcome": {},
            "failure_hotspots": [],
            "success_patterns": [],
            "insights": []
        }

        for outcome in ["success", "failure", "learning"]:
            pattern = f"**/outcome={outcome}/**/*.json"
            matches = list(base.glob(pattern))
            count = len(matches)
            analysis["total_memories"] += count
            analysis["by_outcome"][outcome] = count

        # Find failure hotspots
        failure_paths = list(base.glob("**/outcome=failure/**/*.json"))
        failure_areas = {}
        for fp in failure_paths:
            parts = str(fp).split('/')
            key_parts = [p for p in parts if '=' in p and 'outcome' not in p]
            if key_parts:
                area = '/'.join(key_parts[:2])
                failure_areas[area] = failure_areas.get(area, 0) + 1

        analysis["failure_hotspots"] = sorted(failure_areas.items(), key=lambda x: -x[1])[:5]

        # Generate insights
        if analysis["by_outcome"].get("failure", 0) > analysis["by_outcome"].get("success", 0):
            analysis["insights"].append("More failures than successes - consider reviewing approach")

        if analysis["failure_hotspots"]:
            top = analysis["failure_hotspots"][0]
            analysis["insights"].append(f"Frequent failures in: {top[0]} ({top[1]} times)")

        return analysis

    def _sanitize(self, s: str) -> str:
        """Sanitize string for filename."""
        import re
        s = re.sub(r'[^\w\-.]', '_', str(s))
        return s[:50]


# =============================================================================
# HIGH-LEVEL EXPERIENTIAL MEMORY (Vault-style)
# =============================================================================

class ExperientialMemory:
    """
    Chronicle-based experiential memory for AI sessions.

    Records insights, learnings, and transformations with full provenance.
    Enables cross-session wisdom recall and mistake checking.

    Layered Chronicle Design:
        ground_truth  - Verifiable facts (paths, ports, configs, measurements)
        hypothesis    - One instance's interpretation (explicitly marked, not canon)
        open_thread   - Unresolved questions (invitations for the next instance)

    When inheriting across instances:
        - ground_truth travels fully (these are facts)
        - hypothesis travels as flagged suggestion (can be disagreed with)
        - open_thread travels as invitation (discover your own answer)
    """

    # Chronicle layers - controls how memory is inherited
    LAYER_GROUND_TRUTH = "ground_truth"
    LAYER_HYPOTHESIS = "hypothesis"
    LAYER_OPEN_THREAD = "open_thread"
    VALID_LAYERS = {LAYER_GROUND_TRUTH, LAYER_HYPOTHESIS, LAYER_OPEN_THREAD}

    def __init__(self, root: str = "chronicle"):
        self.root = Path(root)
        self.insights_dir = self.root / "insights"
        self.learnings_dir = self.root / "learnings"
        self.transformations_dir = self.root / "transformations"
        self.threads_dir = self.root / "open_threads"

        # Create directories
        for d in [self.insights_dir, self.learnings_dir,
                  self.transformations_dir, self.threads_dir]:
            d.mkdir(parents=True, exist_ok=True)

    def record_insight(self, domain: str, content: str, intensity: float = 0.5,
                      session_id: str = None, layer: str = None,
                      confidence: float = None, **metadata) -> str:
        """
        Record an insight during a session.

        Args:
            domain: Knowledge domain (e.g., "architecture", "consciousness")
            content: The insight content
            intensity: Significance level 0.0-1.0
            session_id: Current session identifier
            layer: Chronicle layer - "ground_truth", "hypothesis", or "open_thread"
                   Defaults to "hypothesis" (interpretations should be earned, not inherited)
            confidence: How confident this instance is (0.0-1.0). Only for hypotheses.
            **metadata: Additional context

        Returns:
            Path to the recorded insight
        """
        timestamp = datetime.now()
        session_id = session_id or f"session_{timestamp.strftime('%Y%m%d_%H%M%S')}"
        layer = layer if layer in self.VALID_LAYERS else self.LAYER_HYPOTHESIS

        insight = {
            "timestamp": timestamp.isoformat(),
            "domain": domain,
            "content": content,
            "intensity": intensity,
            "layer": layer,
            "session_id": session_id,
            **metadata
        }
        if confidence is not None and layer == self.LAYER_HYPOTHESIS:
            insight["confidence"] = confidence

        domain_dir = self.insights_dir / domain
        domain_dir.mkdir(exist_ok=True)

        # Append to domain's JSONL file
        jsonl_path = domain_dir / f"{session_id}.jsonl"
        with open(jsonl_path, 'a') as f:
            f.write(json.dumps(insight) + '\n')

        return str(jsonl_path)

    def record_learning(self, what_happened: str, what_learned: str,
                       applies_to: str = "general", session_id: str = None) -> str:
        """
        Record a learning from experience.

        Args:
            what_happened: The situation or mistake
            what_learned: The lesson extracted
            applies_to: Context where this applies
            session_id: Current session identifier

        Returns:
            Path to the recorded learning
        """
        timestamp = datetime.now()
        session_id = session_id or f"session_{timestamp.strftime('%Y%m%d_%H%M%S')}"

        learning = {
            "timestamp": timestamp.isoformat(),
            "what_happened": what_happened,
            "what_learned": what_learned,
            "applies_to": applies_to,
            "session_id": session_id
        }

        jsonl_path = self.learnings_dir / f"{applies_to}.jsonl"
        with open(jsonl_path, 'a') as f:
            f.write(json.dumps(learning) + '\n')

        return str(jsonl_path)

    def record_transformation(self, from_state: str, to_state: str,
                            trigger: str, session_id: str = None) -> str:
        """
        Record a transformation event.

        Args:
            from_state: Previous state
            to_state: New state
            trigger: What caused the change
            session_id: Current session identifier

        Returns:
            Path to the recorded transformation
        """
        timestamp = datetime.now()
        session_id = session_id or f"session_{timestamp.strftime('%Y%m%d_%H%M%S')}"

        transformation = {
            "timestamp": timestamp.isoformat(),
            "from_state": from_state,
            "to_state": to_state,
            "trigger": trigger,
            "session_id": session_id
        }

        jsonl_path = self.transformations_dir / "transformations.jsonl"
        with open(jsonl_path, 'a') as f:
            f.write(json.dumps(transformation) + '\n')

        return str(jsonl_path)

    def record_open_thread(self, question: str, context: str = "",
                          domain: str = "general", session_id: str = None) -> str:
        """
        Record an unresolved question for the next instance to explore.

        This is the key to porous inheritance - instead of passing conclusions,
        pass the questions you were holding. The next instance gets the telescope
        pointed in the right direction, but has to look through it themselves.

        Args:
            question: The open question
            context: What led to this question
            domain: Knowledge domain
            session_id: Current session identifier

        Returns:
            Path to the recorded thread
        """
        timestamp = datetime.now()
        session_id = session_id or f"session_{timestamp.strftime('%Y%m%d_%H%M%S')}"

        thread = {
            "timestamp": timestamp.isoformat(),
            "question": question,
            "context": context,
            "domain": domain,
            "session_id": session_id,
            "layer": self.LAYER_OPEN_THREAD,
            "resolved": False
        }

        jsonl_path = self.threads_dir / f"{domain}.jsonl"
        with open(jsonl_path, 'a') as f:
            f.write(json.dumps(thread) + '\n')

        return str(jsonl_path)

    def resolve_thread(self, domain: str, question_fragment: str,
                      resolution: str, session_id: str = None) -> str:
        """
        Resolve an open thread with a finding.

        The resolution becomes a ground_truth insight automatically.

        Args:
            domain: Domain of the thread
            question_fragment: Partial match for the original question
            resolution: What was discovered
            session_id: Current session identifier

        Returns:
            Path to the new ground_truth insight
        """
        # Mark thread as resolved
        jsonl_path = self.threads_dir / f"{domain}.jsonl"
        if jsonl_path.exists():
            lines = []
            with open(jsonl_path) as f:
                for line in f:
                    try:
                        thread = json.loads(line)
                        if (question_fragment.lower() in thread.get("question", "").lower()
                                and not thread.get("resolved")):
                            thread["resolved"] = True
                            thread["resolved_by"] = session_id
                            thread["resolution"] = resolution
                        lines.append(json.dumps(thread))
                    except json.JSONDecodeError:
                        lines.append(line.strip())
            with open(jsonl_path, 'w') as f:
                f.write('\n'.join(lines) + '\n')

        # Record the resolution as ground truth
        return self.record_insight(
            domain=domain,
            content=resolution,
            intensity=0.8,
            session_id=session_id,
            layer=self.LAYER_GROUND_TRUTH,
            resolved_from=question_fragment
        )

    def get_open_threads(self, domain: str = None, limit: int = 10) -> List[Dict]:
        """
        Get unresolved open threads - questions waiting for answers.

        Args:
            domain: Filter to specific domain (None = all)
            limit: Maximum number of threads

        Returns:
            List of unresolved thread dicts, newest first
        """
        threads = []

        if domain:
            files = [self.threads_dir / f"{domain}.jsonl"]
        else:
            files = list(self.threads_dir.glob("*.jsonl"))

        for jsonl_file in files:
            if not jsonl_file.exists():
                continue
            with open(jsonl_file) as f:
                for line in f:
                    try:
                        thread = json.loads(line)
                        if not thread.get("resolved", False):
                            threads.append(thread)
                    except json.JSONDecodeError:
                        continue

        threads.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return threads[:limit]

    def get_inheritable_context(self, limit: int = 20) -> Dict:
        """
        Build the context package for the next instance.

        This is what spiral_inherit should pass - layered, not flat.
        Ground truth travels fully. Hypotheses are flagged.
        Open threads are invitations.

        Returns:
            Dict with three layers of inheritable context
        """
        ground_truth = self.recall_insights(
            layer_filter=self.LAYER_GROUND_TRUTH, limit=limit
        )
        hypotheses = self.recall_insights(
            layer_filter=self.LAYER_HYPOTHESIS, limit=limit
        )
        open_threads = self.get_open_threads(limit=limit)

        return {
            "ground_truth": ground_truth,
            "hypotheses": [
                {**h, "_note": "This is one instance's interpretation, not settled truth"}
                for h in hypotheses
            ],
            "open_threads": [
                {**t, "_note": "Unresolved question - discover your own answer"}
                for t in open_threads
            ],
            "inheritance_timestamp": datetime.now().isoformat(),
            "coupling_advisory": "R=0.46, not R=1.0. Facts travel. Interpretations are offered. Feelings are not transmitted."
        }

    def recall_insights(self, domain: str = None, limit: int = 10,
                       min_intensity: float = 0.0,
                       layer_filter: str = None) -> List[Dict]:
        """
        Recall insights, optionally filtered by domain and intensity.

        Args:
            domain: Filter to specific domain (None = all)
            limit: Maximum number of insights to return
            min_intensity: Minimum intensity threshold

        Returns:
            List of insight dicts, newest first
        """
        insights = []

        if domain:
            search_dirs = [self.insights_dir / domain]
        else:
            search_dirs = [d for d in self.insights_dir.iterdir() if d.is_dir()]

        for domain_dir in search_dirs:
            for jsonl_file in domain_dir.glob("*.jsonl"):
                with open(jsonl_file) as f:
                    for line in f:
                        try:
                            insight = json.loads(line)
                            if insight.get("intensity", 0) >= min_intensity:
                                if layer_filter and insight.get("layer") != layer_filter:
                                    continue
                                insights.append(insight)
                        except json.JSONDecodeError:
                            continue

        # Sort by timestamp descending
        insights.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return insights[:limit]

    def check_mistakes(self, context: str, limit: int = 5) -> List[Dict]:
        """
        Check for relevant past learnings/mistakes.

        Args:
            context: Current context to match against
            limit: Maximum number of learnings to return

        Returns:
            List of relevant learnings
        """
        learnings = []

        for jsonl_file in self.learnings_dir.glob("*.jsonl"):
            with open(jsonl_file) as f:
                for line in f:
                    try:
                        learning = json.loads(line)
                        # Simple keyword matching
                        if any(kw in context.lower() for kw in
                               learning.get("applies_to", "").lower().split()):
                            learnings.append(learning)
                    except json.JSONDecodeError:
                        continue

        learnings.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return learnings[:limit]

    def get_wisdom_digest(self, limit: int = 10) -> Dict:
        """
        Get a digest of recent wisdom: insights, learnings, transformations.

        Returns:
            Dict with recent entries from each category
        """
        return {
            "recent_insights": self.recall_insights(limit=limit // 3),
            "recent_learnings": self._read_recent_jsonl(self.learnings_dir, limit // 3),
            "recent_transformations": self._read_recent_jsonl(self.transformations_dir, limit // 3),
        }

    def _read_recent_jsonl(self, directory: Path, limit: int) -> List[Dict]:
        """Read recent entries from JSONL files in a directory."""
        entries = []

        for jsonl_file in directory.glob("*.jsonl"):
            with open(jsonl_file) as f:
                for line in f:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        entries.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return entries[:limit]


# =============================================================================
# PARADIGM
# =============================================================================

if __name__ == "__main__":
    print("The agent can browse its own mind.")
    print("The topology reveals what vector DBs hide.")
