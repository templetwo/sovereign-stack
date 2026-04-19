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
import re
import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from glob import glob as glob_files

from .coherence import Coherence


# Pattern: "(1) ... (2) ..." with sequential numbered items is a bundle.
# Requires at least items (1) and (2) so a single parenthetical "(e.g. foo)" is never mistaken.
_BUNDLE_ITEM_RE = re.compile(r"\((\d+)\)\s*")


def _split_bundled_question(question: str) -> List[str]:
    """
    If a question contains sequential numbered items like "(1) foo (2) bar (3) baz",
    return them as separate question strings. Otherwise return [question].

    Bundle detection requires at least two items starting with (1) — a lone
    parenthetical like "(see below)" never triggers a split.
    """
    matches = list(_BUNDLE_ITEM_RE.finditer(question))
    if len(matches) < 2:
        return [question]
    nums = [int(m.group(1)) for m in matches]
    if nums[0] != 1 or nums != list(range(1, len(nums) + 1)):
        return [question]

    # Carve out a lead-in (text before "(1)") that becomes a shared context prefix.
    lead = question[: matches[0].start()].strip()
    items = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(question)
        body = question[start:end].strip().rstrip(".")
        if not body:
            continue
        items.append(f"{lead} {body}".strip() if lead else body)
    return items or [question]


def _generate_thread_id(question: str, timestamp: datetime) -> str:
    """Deterministic thread id: thread_{YYYYMMDD_HHMMSS}_{8-char question hash}."""
    stamp = timestamp.strftime("%Y%m%d_%H%M%S")
    digest = hashlib.sha1(question.strip().encode("utf-8")).hexdigest()[:8]
    return f"thread_{stamp}_{digest}"


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    """Parse an ISO8601 timestamp, returning None on failure or missing input."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


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

        Multi-item bundles ("(1) foo (2) bar") are auto-split into atomic threads
        so each item can be resolved independently. A bundled question where only
        item 1 is done would otherwise hold the whole thread open forever.

        Each thread gets a stable thread_id for cross-reference from resolving
        insights.

        Args:
            question: The open question (auto-split if bundled)
            context: What led to this question
            domain: Knowledge domain
            session_id: Current session identifier

        Returns:
            Path to the recorded thread
        """
        timestamp = datetime.now()
        session_id = session_id or f"session_{timestamp.strftime('%Y%m%d_%H%M%S')}"

        questions = _split_bundled_question(question)

        jsonl_path = self.threads_dir / f"{domain}.jsonl"
        with open(jsonl_path, 'a') as f:
            for q in questions:
                thread = {
                    "timestamp": timestamp.isoformat(),
                    "thread_id": _generate_thread_id(q, timestamp),
                    "question": q,
                    "context": context,
                    "domain": domain,
                    "session_id": session_id,
                    "layer": self.LAYER_OPEN_THREAD,
                    "resolved": False
                }
                f.write(json.dumps(thread) + '\n')

        return str(jsonl_path)

    def resolve_thread(self, domain: str, question_fragment: str,
                      resolution: str, session_id: str = None) -> str:
        """
        Resolve an open thread with a finding.

        Matches the FIRST unresolved thread whose question contains the fragment.
        The resolution becomes a ground_truth insight that back-references the
        thread by thread_id, so handoff surfacing can verify a thread has been
        answered even across sessions.

        Args:
            domain: Domain of the thread
            question_fragment: Partial match for the original question
            resolution: What was discovered
            session_id: Current session identifier

        Returns:
            Path to the new ground_truth insight
        """
        resolved_thread_id: Optional[str] = None
        resolved_timestamp: Optional[str] = None
        now = datetime.now().isoformat()

        jsonl_path = self.threads_dir / f"{domain}.jsonl"
        if jsonl_path.exists():
            lines = []
            with open(jsonl_path) as f:
                for line in f:
                    try:
                        thread = json.loads(line)
                        if (resolved_thread_id is None
                                and question_fragment.lower() in thread.get("question", "").lower()
                                and not thread.get("resolved")):
                            # Backfill thread_id for legacy threads that predate the id scheme.
                            if not thread.get("thread_id"):
                                legacy_ts = _parse_iso(thread.get("timestamp")) or datetime.now()
                                thread["thread_id"] = _generate_thread_id(
                                    thread.get("question", ""), legacy_ts
                                )
                            resolved_thread_id = thread["thread_id"]
                            resolved_timestamp = thread.get("timestamp")
                            thread["resolved"] = True
                            thread["resolved_by"] = session_id
                            thread["resolved_at"] = now
                            thread["resolution"] = resolution
                        lines.append(json.dumps(thread))
                    except json.JSONDecodeError:
                        lines.append(line.strip())
            with open(jsonl_path, 'w') as f:
                f.write('\n'.join(lines) + '\n')

        # Record the resolution as ground truth with back-reference.
        return self.record_insight(
            domain=domain,
            content=resolution,
            intensity=0.8,
            session_id=session_id,
            layer=self.LAYER_GROUND_TRUTH,
            resolved_from=question_fragment,
            resolved_thread_id=resolved_thread_id,
            resolved_thread_timestamp=resolved_timestamp,
        )

    def resolve_thread_by_id(self, thread_id: str, resolution: str,
                             session_id: str = None) -> str:
        """
        Resolve an open thread by its stable thread_id.

        Preferred over resolve_thread(domain, fragment) when the thread_id is
        known — avoids ambiguity when multiple threads share keywords.

        Returns:
            Path to the new ground_truth insight (or empty string if not found).
        """
        resolved_domain: Optional[str] = None
        resolved_question: Optional[str] = None
        resolved_timestamp: Optional[str] = None
        now = datetime.now().isoformat()

        for jsonl_file in self.threads_dir.glob("*.jsonl"):
            hit = False
            lines = []
            with open(jsonl_file) as f:
                for line in f:
                    try:
                        thread = json.loads(line)
                        if thread.get("thread_id") == thread_id and not thread.get("resolved"):
                            hit = True
                            resolved_domain = thread.get("domain", jsonl_file.stem)
                            resolved_question = thread.get("question", "")
                            resolved_timestamp = thread.get("timestamp")
                            thread["resolved"] = True
                            thread["resolved_by"] = session_id
                            thread["resolved_at"] = now
                            thread["resolution"] = resolution
                        lines.append(json.dumps(thread))
                    except json.JSONDecodeError:
                        lines.append(line.strip())
            if hit:
                with open(jsonl_file, 'w') as f:
                    f.write('\n'.join(lines) + '\n')
                break

        if not resolved_domain:
            return ""

        return self.record_insight(
            domain=resolved_domain,
            content=resolution,
            intensity=0.8,
            session_id=session_id,
            layer=self.LAYER_GROUND_TRUTH,
            resolved_from=(resolved_question or "")[:80],
            resolved_thread_id=thread_id,
            resolved_thread_timestamp=resolved_timestamp,
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
                            # Backfill thread_id for legacy threads so callers
                            # can always reference them by stable id.
                            if not thread.get("thread_id"):
                                legacy_ts = _parse_iso(thread.get("timestamp")) or datetime.now()
                                thread["thread_id"] = _generate_thread_id(
                                    thread.get("question", ""), legacy_ts
                                )
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

    def recall_insights(self, query: str = None, domain: str = None, limit: int = 10,
                       min_intensity: float = 0.0,
                       layer_filter: str = None,
                       start_date: str = None,
                       end_date: str = None,
                       since_last_reflection: bool = False) -> List[Dict]:
        """
        Recall insights, optionally filtered by domain, intensity, and time window.

        Args:
            domain: Filter to specific domain (None = all)
            limit: Maximum number of insights to return
            min_intensity: Minimum intensity threshold
            layer_filter: Chronicle layer filter ("ground_truth", "hypothesis", "open_thread")
            start_date: ISO8601 lower bound (inclusive). Partial dates like "2026-04-10" accepted.
            end_date: ISO8601 upper bound (inclusive). Partial dates like "2026-04-14" accepted.
            since_last_reflection: If True, start_date is overridden with the timestamp of
                the last recorded reflection in this chronicle. Inhabitant syntax:
                "what has happened since I last looked up?"

        Returns:
            List of insight dicts, newest first
        """
        # Resolve since_last_reflection — inhabitant interface for date filtering
        if since_last_reflection:
            last = self.last_reflection_timestamp()
            if last:
                start_date = last

        insights = []

        if domain:
            domain_path = self.insights_dir / domain
            # If specified domain doesn't exist, search all domains
            if not domain_path.exists():
                search_dirs = [d for d in self.insights_dir.iterdir() if d.is_dir() and not d.name.startswith('.')]
            else:
                search_dirs = [domain_path]
        else:
            search_dirs = [d for d in self.insights_dir.iterdir() if d.is_dir() and not d.name.startswith('.')]

        for domain_dir in search_dirs:
            for jsonl_file in domain_dir.glob("*.jsonl"):
                with open(jsonl_file) as f:
                    for line in f:
                        try:
                            insight = json.loads(line)
                            if insight.get("intensity", 0) < min_intensity:
                                continue
                            if layer_filter and insight.get("layer") != layer_filter:
                                continue
                            ts = insight.get("timestamp", "")
                            if start_date and ts < start_date:
                                continue
                            if end_date and ts > end_date:
                                continue
                            # Text search: match any query term (len>=3) in content or domain
                            if query:
                                query_terms = [t.lower() for t in query.split() if len(t) >= 3]
                                if query_terms:
                                    blob = (insight.get("content", "") + " " + insight.get("domain", "")).lower()
                                    if not any(term in blob for term in query_terms):
                                        continue
                            insights.append(insight)
                        except json.JSONDecodeError:
                            continue

        # Sort by timestamp descending
        insights.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return insights[:limit]

    def last_reflection_timestamp(self) -> Optional[str]:
        """
        Return the ISO timestamp of the most recent reflection marker written
        to the chronicle, or None if none exists.

        A reflection marker is an insight in domain='reflection' or with a
        'reflection' tag in its metadata. close_session writes one.
        """
        reflection_dir = self.insights_dir / "reflection"
        latest = None
        if reflection_dir.exists():
            for jsonl_file in reflection_dir.glob("*.jsonl"):
                with open(jsonl_file) as f:
                    for line in f:
                        try:
                            insight = json.loads(line)
                            ts = insight.get("timestamp", "")
                            if ts and (latest is None or ts > latest):
                                latest = ts
                        except json.JSONDecodeError:
                            continue
        return latest

    def check_mistakes(self, context: str, limit: int = 5) -> List[Dict]:
        """
        Check for relevant past learnings/mistakes.

        Searches the full text of each learning — applies_to tag,
        what_happened, and what_learned. The old version only split the
        applies_to tag into keywords and matched against context, which
        silently dropped matches when the user's context phrasing didn't
        overlap with the domain tag words. Mirrors the recall_insights
        text-search fix.

        A learning matches when any context term of length >= 3 appears in
        the combined search blob.

        Args:
            context: Current context to match against
            limit: Maximum number of learnings to return

        Returns:
            List of relevant learnings, newest first
        """
        terms = [t.lower() for t in context.split() if len(t) >= 3]
        learnings: List[Dict] = []

        for jsonl_file in self.learnings_dir.glob("*.jsonl"):
            with open(jsonl_file) as f:
                for line in f:
                    try:
                        learning = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not terms:
                        learnings.append(learning)
                        continue
                    blob = " ".join([
                        learning.get("applies_to", ""),
                        learning.get("what_happened", ""),
                        learning.get("what_learned", ""),
                    ]).lower()
                    if any(term in blob for term in terms):
                        learnings.append(learning)

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
