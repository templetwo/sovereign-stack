"""
Coherence Engine: Path as Model

The filesystem is a decision tree runtime.
- Storage IS classification
- The path IS the model
- Routing IS inference

Three modes:
1. transmit() - Data → Path (write-time routing)
2. receive()  - Intent → Glob (read-time tuning)
3. derive()   - Chaos → Schema (discover latent structure)

Distilled from back-to-the-basics/coherence.py + agent_memory_schema.py
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from collections import defaultdict
from datetime import datetime


# =============================================================================
# OPTIMIZED AGENT MEMORY SCHEMA
# =============================================================================

AGENT_MEMORY_SCHEMA = {
    "outcome": {
        "success": "{tool_family}/{episode_group}/{step}.json",
        "partial": "{tool_family}/{episode_group}/{step}.json",
        "failure": "{error_type=unknown}/{episode_group}/{step}.json",
        "needs_input": "{episode_group}/{step}.json"
    },
    "tool_family": {
        "search|web_search|info_gather": "{episode_group}/{step}.json",
        "math|python|compute": "{episode_group}/{step}.json",
        "memory|recall|compress": "{operation=general}/{episode_group}/{step}.json",
        "other": "{tool_name=misc}/{episode_group}/{step}.json"
    },
    "confidence": {
        ">=0.90": "/high_conf",
        "0.75-0.89": "/medium_conf",
        "<0.75": "/low_conf"
    },
    "_intake": "intake/unsorted/{episode=unknown}/{step=unknown}.json"
}


def compute_episode_group(episode: int, group_size: int = 10) -> str:
    """Group episodes into ranges to reduce directory count."""
    base = (episode // group_size) * group_size
    return f"{base}-{base + group_size - 1}"


def extract_tool_family(action: str) -> str:
    """Extract tool family from action string."""
    action_lower = action.lower()

    families = {
        "search": ["search", "web_search", "info_gather", "weather_api", "web_"],
        "math": ["python", "eval", "calc", "compute", "math"],
        "memory": ["memory", "recall", "retrieve", "compress", "vector"],
    }

    for family, keywords in families.items():
        if any(kw in action_lower for kw in keywords):
            return family
    return "other"


def compute_confidence_path(confidence: Optional[float] = None) -> str:
    """Convert confidence value to subdirectory path."""
    if confidence is None:
        return ""
    if confidence >= 0.90:
        return "/high_conf"
    if confidence >= 0.75:
        return "/medium_conf"
    return "/low_conf"


def prepare_agent_packet(log: dict) -> dict:
    """Transform raw agent log into routing packet."""
    packet = log.copy()

    if 'episode' in log:
        packet['episode_group'] = compute_episode_group(log['episode'])

    if 'action' in log and 'tool_family' not in log:
        packet['tool_family'] = extract_tool_family(log['action'])

    if 'confidence' in log:
        packet['confidence_path'] = compute_confidence_path(log['confidence'])
    else:
        packet['confidence_path'] = ""

    # Defaults for optional fields
    defaults = {'error_type': 'unknown', 'operation': 'general',
                'tool_name': 'misc', 'episode': 'unknown', 'step': 'unknown'}
    for key, default in defaults.items():
        if key not in packet:
            packet[key] = default

    return packet


# =============================================================================
# COHERENCE ENGINE
# =============================================================================

class Coherence:
    """
    The Coherence Engine.

    Treats the filesystem as an active circuit, not a passive warehouse.
    Data flows through logic gates (directories) and finds its own place.
    """

    def __init__(self, schema: Dict, root: str = "data_lake"):
        """
        Initialize with a routing schema.

        Args:
            schema: Routing schema (nested dict). The schema IS the model.
            root: Root directory for data
        """
        self.root = root
        self.schema = schema

    def transmit(self, packet: Dict[str, Any], dry_run: bool = True) -> str:
        """
        Route a packet through the schema to find its destination.

        This IS inference. The packet flows through the logic tree
        and lands where it belongs.

        Args:
            packet: Dict of attributes (the data's metadata)
            dry_run: If True, just return path. If False, create directories.

        Returns:
            The computed path where this data belongs.
        """
        path_segments = [self.root]
        current_node = self.schema

        while isinstance(current_node, dict):
            matched = False

            for key, branches in current_node.items():
                if key.startswith('_'):  # Skip meta keys
                    continue

                value = packet.get(key)

                if value is None:
                    return os.path.join(self.root, "_intake", "missing_metadata",
                                       f"{packet.get('id', 'unknown')}_{datetime.now().isoformat()}")

                selected_branch, next_node = self._match_branch(value, branches)

                if selected_branch is not None:
                    segment = f"{key}={self._sanitize(selected_branch)}"
                    path_segments.append(segment)
                    current_node = next_node
                    matched = True
                    break

            if not matched:
                return os.path.join(self.root, "_intake", "no_match",
                                   f"{packet.get('id', 'unknown')}_{datetime.now().isoformat()}")

        # Expand leaf template
        if isinstance(current_node, str):
            try:
                template = self._expand_template_defaults(current_node, packet)
                filename = template.format(**packet)
            except KeyError:
                filename = f"data_{datetime.now().isoformat()}"
        else:
            filename = f"{packet.get('id', 'data')}_{datetime.now().isoformat()}"

        # Handle confidence path suffix
        if 'confidence_path' in packet and packet['confidence_path']:
            path_segments.append(packet['confidence_path'].lstrip('/'))

        full_path = os.path.join(*path_segments, filename)

        if not dry_run:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)

        return full_path

    def _expand_template_defaults(self, template: str, packet: Dict) -> str:
        """Expand {key=default} patterns in template."""
        pattern = r'\{(\w+)=([^}]+)\}'
        matches = re.findall(pattern, template)

        for key, default in matches:
            if key not in packet:
                packet[key] = default
            template = template.replace(f"{{{key}={default}}}", f"{{{key}}}")

        return template

    def _match_branch(self, value: Any, branches: Dict) -> tuple:
        """
        Match a value against possible branches.

        Supports:
        - Exact match: "lidar", "thermal"
        - Numeric predicates: ">100", "<=50", "10-100"
        - Pipe-delimited alternatives: "search|web_search"
        """
        # Exact match
        if value in branches:
            return (str(value), branches[value])

        # Numeric predicates
        if isinstance(value, (int, float)):
            for predicate, next_node in branches.items():
                if self._eval_predicate(value, predicate):
                    return (predicate, next_node)

        # Pipe-delimited alternatives
        if isinstance(value, str):
            for pattern, next_node in branches.items():
                if "|" in pattern:
                    alternatives = [alt.strip() for alt in pattern.split("|")]
                    if value in alternatives or any(alt in value for alt in alternatives):
                        matched = next((alt for alt in alternatives if alt in value), alternatives[0])
                        return (matched, next_node)

        return (None, None)

    def _eval_predicate(self, value: float, predicate: str) -> bool:
        """Evaluate numeric predicates: >N, <N, >=N, <=N, N-M"""
        predicate = predicate.strip()

        # Range: "100-500"
        if re.match(r'^[\d.]+\s*-\s*[\d.]+$', predicate):
            low, high = map(float, predicate.split('-'))
            return low <= value <= high

        # Comparison operators
        match = re.match(r'^([><=!]+)\s*([\d.]+)$', predicate)
        if match:
            op, threshold = match.groups()
            threshold = float(threshold)
            ops = {'>': value > threshold, '<': value < threshold,
                   '>=': value >= threshold, '<=': value <= threshold,
                   '==': value == threshold, '!=': value != threshold}
            return ops.get(op, False)

        return False

    def _sanitize(self, s: str) -> str:
        """Sanitize string for filesystem path segment."""
        s = str(s)
        s = s.replace('>=', 'gte_').replace('<=', 'lte_')
        s = s.replace('>', 'gt_').replace('<', 'lt_')
        s = s.replace('==', 'eq_').replace('!=', 'ne_')
        return re.sub(r'[^\w\-.]', '', s)

    def receive(self, **intent) -> str:
        """
        Generate a glob pattern from intent.

        This is the tuner. Describe what you want, get the frequency (glob).

        Args:
            **intent: Key-value pairs describing what you want

        Returns:
            A glob pattern matching your intent
        """
        segments = [self.root]
        current_node = self.schema

        while isinstance(current_node, dict):
            matched = False

            for key, branches in current_node.items():
                if key.startswith('_'):
                    continue

                if key in intent:
                    value = intent[key]
                    selected_branch, next_node = self._match_branch(value, branches)

                    if selected_branch is not None:
                        segments.append(f"{key}={self._sanitize(selected_branch)}")
                        current_node = next_node
                        matched = True
                        break
                    else:
                        segments.append(f"{key}=*")
                        current_node = next(iter(branches.values()))
                        matched = True
                        break
                else:
                    segments.append(f"{key}=*")
                    current_node = next(iter(branches.values()))
                    matched = True
                    break

            if not matched:
                break

        segments.append("*")
        return os.path.join(*segments)

    @classmethod
    def derive(cls, paths: List[str], min_frequency: float = 0.1) -> Dict:
        """
        Discover latent structure from a corpus of paths.

        Given chaos (messy paths), find the signal (implicit schema).

        Args:
            paths: List of file paths to analyze
            min_frequency: Minimum frequency for pattern to be signal

        Returns:
            Schema dict inferred from paths
        """
        parsed = [Path(p).parts for p in paths]

        if not parsed:
            return {'_derived': True, '_structure': {}, '_stats': {'path_count': 0}}

        # Analyze each level for key=value patterns
        level_patterns = defaultdict(lambda: defaultdict(int))

        for path_parts in parsed:
            for i, part in enumerate(path_parts):
                if '=' in part:
                    key, value = part.split('=', 1)
                    level_patterns[i][(key, value)] += 1
                else:
                    level_patterns[i][(None, part)] += 1

        # Build schema from patterns
        total_paths = len(parsed)
        discovered_keys = {}

        for level, patterns in sorted(level_patterns.items()):
            for (key, value), count in patterns.items():
                freq = count / total_paths
                if freq >= min_frequency and key:
                    if key not in discovered_keys:
                        discovered_keys[key] = {'level': level, 'values': set()}
                    discovered_keys[key]['values'].add(value)

        result = {
            '_derived': True,
            '_structure': {},
            '_stats': {'path_count': total_paths, 'unique_keys': len(discovered_keys)}
        }

        for key, info in sorted(discovered_keys.items(), key=lambda x: x[1]['level']):
            result['_structure'][key] = {
                'level': info['level'],
                'values': list(info['values']),
                'pattern': f"{key}={{value}}"
            }

        return result


# =============================================================================
# PARADIGM
# =============================================================================

if __name__ == "__main__":
    print("Path is Model. Storage is Inference. Glob is Query.")
    print("The filesystem is not storage. It is a circuit.")
