"""
Simulator - Outcome Modeling Engine

Models "what-if" scenarios using graph-based state representation.
Provides predictions that inform deliberation without making decisions.

Key features:
- Reproducibility: Fixed seeds, deterministic algorithms
- Graphs as state: NetworkX DAGs represent system structure
- Uncertainty as feature: Monte Carlo runs produce confidence intervals
- No ML required: Pure graph operations and statistics

Distilled from threshold-protocols/simulation/simulator.py
"""

import json
import random
import hashlib
import logging
from enum import Enum
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime

try:
    import networkx as nx
    NETWORKX_AVAILABLE = True
except ImportError:
    NETWORKX_AVAILABLE = False
    nx = None

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("simulation")


# =============================================================================
# DATA STRUCTURES
# =============================================================================

class ScenarioType(Enum):
    """Types of scenarios to simulate."""
    REORGANIZE = "reorganize"
    PARTIAL_REORGANIZE = "partial"
    DEFER = "defer"
    ROLLBACK = "rollback"
    INCREMENTAL = "incremental"


@dataclass
class Outcome:
    """A single simulated outcome."""
    scenario: ScenarioType
    name: str
    probability: float
    reversibility: float
    side_effects: List[str]
    state_hash: str
    confidence_interval: Tuple[float, float] = (0.0, 1.0)
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["scenario"] = self.scenario.value
        return result


@dataclass
class Prediction:
    """Complete prediction from simulation."""
    event_hash: str
    model: str
    outcomes: List[Outcome]
    timestamp: str
    seed: int
    monte_carlo_runs: int
    prediction_hash: str = ""

    def __post_init__(self):
        if not self.prediction_hash:
            content = json.dumps({
                "event_hash": self.event_hash,
                "model": self.model,
                "outcome_count": len(self.outcomes),
                "seed": self.seed,
                "timestamp": self.timestamp,
            }, sort_keys=True)
            self.prediction_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_hash": self.event_hash,
            "model": self.model,
            "outcomes": [o.to_dict() for o in self.outcomes],
            "timestamp": self.timestamp,
            "seed": self.seed,
            "monte_carlo_runs": self.monte_carlo_runs,
            "prediction_hash": self.prediction_hash,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def best_outcome(self) -> Optional[Outcome]:
        """Return highest probability outcome."""
        if not self.outcomes:
            return None
        return max(self.outcomes, key=lambda o: o.probability)

    def most_reversible(self) -> Optional[Outcome]:
        """Return most reversible outcome."""
        if not self.outcomes:
            return None
        return max(self.outcomes, key=lambda o: o.reversibility)


@dataclass
class SimulationConfig:
    """Configuration for simulation runs."""
    monte_carlo_runs: int = 100
    seed: int = 42


# =============================================================================
# SIMULATOR
# =============================================================================

class Simulator:
    """
    Graph-based outcome modeling engine.

    Uses NetworkX to represent system states as directed graphs,
    then simulates scenarios by applying transformations.
    """

    def __init__(self, model: str = "generic", seed: int = 42,
                 config: Optional[SimulationConfig] = None):
        if not NETWORKX_AVAILABLE:
            raise ImportError("NetworkX required: pip install networkx")

        self.model_name = model
        self.seed = seed
        self.config = config or SimulationConfig(seed=seed)
        self._rng = random.Random(seed)
        self.graph: nx.DiGraph = nx.DiGraph()
        self._initial_state: Optional[nx.DiGraph] = None

    def model(self, event: Dict[str, Any], scenarios: List[ScenarioType]) -> Prediction:
        """
        Model outcomes for given scenarios.

        Args:
            event: ThresholdEvent as dict (from detection layer)
            scenarios: List of scenario types to simulate

        Returns:
            Prediction with outcomes for each scenario
        """
        self._build_state_from_event(event)
        self._initial_state = self.graph.copy()

        outcomes: List[Outcome] = []

        for scenario in scenarios:
            outcome = self._simulate_scenario(scenario, event)
            outcomes.append(outcome)

        # Normalize probabilities
        total_prob = sum(o.probability for o in outcomes)
        if total_prob > 0:
            for o in outcomes:
                o.probability /= total_prob

        return Prediction(
            event_hash=event.get("event_hash", "unknown"),
            model=self.model_name,
            outcomes=outcomes,
            timestamp=datetime.utcnow().isoformat(),
            seed=self.seed,
            monte_carlo_runs=self.config.monte_carlo_runs,
        )

    def _build_state_from_event(self, event: Dict[str, Any]) -> None:
        """Build graph representation of system state from event."""
        self.graph.clear()

        metric = event.get("metric", "unknown")
        value = event.get("value", 0)
        path = event.get("path", "/")

        self.graph.add_node("root", type="directory", path=path)

        if metric == "file_count":
            file_count = int(min(value, 200))  # Cap for performance
            for i in range(file_count):
                node_id = f"file_{i}"
                self.graph.add_node(node_id, type="file", index=i)
                self.graph.add_edge("root", node_id)

        elif metric == "directory_depth":
            depth = int(value)
            parent = "root"
            for d in range(depth):
                node_id = f"dir_level_{d}"
                self.graph.add_node(node_id, type="directory", level=d)
                self.graph.add_edge(parent, node_id)
                parent = node_id

        else:
            self.graph.add_node("generic_state", metric=metric, value=value)
            self.graph.add_edge("root", "generic_state")

    def _simulate_scenario(self, scenario: ScenarioType, event: Dict[str, Any]) -> Outcome:
        """Simulate a single scenario using Monte Carlo runs."""
        results = []

        for run in range(self.config.monte_carlo_runs):
            run_seed = self.seed + run
            final_state, effects = self._apply_scenario(scenario, run_seed)
            reversibility = self._calculate_reversibility(final_state)
            results.append({
                "reversibility": reversibility,
                "effects": effects,
                "state_hash": self._hash_state(final_state),
            })

        avg_reversibility = sum(r["reversibility"] for r in results) / len(results)
        all_effects = list(set(e for r in results for e in r["effects"]))

        reversibilities = sorted(r["reversibility"] for r in results)
        ci_low = reversibilities[int(len(reversibilities) * 0.05)]
        ci_high = reversibilities[int(len(reversibilities) * 0.95)]

        probability = self._estimate_probability(scenario, avg_reversibility, event)

        return Outcome(
            scenario=scenario,
            name=self._scenario_name(scenario),
            probability=probability,
            reversibility=avg_reversibility,
            side_effects=all_effects,
            state_hash=results[0]["state_hash"],
            confidence_interval=(ci_low, ci_high),
            details={"monte_carlo_runs": self.config.monte_carlo_runs},
        )

    def _apply_scenario(self, scenario: ScenarioType, run_seed: int) -> Tuple[nx.DiGraph, List[str]]:
        """Apply scenario transformation to graph."""
        rng = random.Random(run_seed)
        effects: List[str] = []
        state = self.graph.copy()

        if scenario == ScenarioType.REORGANIZE:
            nodes = list(state.nodes())
            if len(nodes) > 2:
                edges_to_remove = list(state.edges())[:len(state.edges()) // 3]
                state.remove_edges_from(edges_to_remove)

                for _ in range(len(edges_to_remove)):
                    src = rng.choice(nodes)
                    dst = rng.choice(nodes)
                    if src != dst:
                        state.add_edge(src, dst)

                effects.extend(["structure_changed", "potential_path_loss"])

        elif scenario == ScenarioType.PARTIAL_REORGANIZE:
            nodes = list(state.nodes())
            subset_size = max(1, len(nodes) // 4)
            subset = rng.sample(nodes, min(subset_size, len(nodes)))

            for node in subset:
                if state.out_degree(node) > 0:
                    successors = list(state.successors(node))
                    if successors:
                        state.remove_edge(node, rng.choice(successors))

            effects.append("partial_modification")

        elif scenario == ScenarioType.DEFER:
            if rng.random() < 0.3:
                effects.append("organic_growth_risk")
            if rng.random() < 0.2:
                effects.append("threshold_may_increase")

        elif scenario == ScenarioType.ROLLBACK:
            state = self._initial_state.copy() if self._initial_state else state

            if state.number_of_nodes() > 10:
                to_remove = list(state.nodes())[-5:]
                state.remove_nodes_from(to_remove)

            effects.extend(["data_loss_risk", "requires_backup_verification"])

        elif scenario == ScenarioType.INCREMENTAL:
            nodes = list(state.nodes())
            if nodes:
                new_node = f"staged_{run_seed}"
                state.add_node(new_node, type="staged")
                state.add_edge(rng.choice(nodes), new_node)

            effects.append("minimal_disruption")

        return state, effects

    def _calculate_reversibility(self, final_state: nx.DiGraph) -> float:
        """Calculate reversibility as normalized graph edit distance."""
        if self._initial_state is None:
            return 0.5

        initial_nodes = set(self._initial_state.nodes())
        final_nodes = set(final_state.nodes())
        initial_edges = set(self._initial_state.edges())
        final_edges = set(final_state.edges())

        nodes_added = len(final_nodes - initial_nodes)
        nodes_removed = len(initial_nodes - final_nodes)
        edges_added = len(final_edges - initial_edges)
        edges_removed = len(initial_edges - final_edges)

        total_operations = nodes_added + nodes_removed + edges_added + edges_removed
        max_operations = len(initial_nodes) + len(final_nodes) + len(initial_edges) + len(final_edges)

        if max_operations == 0:
            return 1.0

        edit_distance_normalized = total_operations / max_operations
        return 1.0 - min(edit_distance_normalized, 1.0)

    def _estimate_probability(self, scenario: ScenarioType,
                            reversibility: float, event: Dict[str, Any]) -> float:
        """Estimate scenario probability."""
        base_probs = {
            ScenarioType.REORGANIZE: 0.3,
            ScenarioType.PARTIAL_REORGANIZE: 0.25,
            ScenarioType.DEFER: 0.2,
            ScenarioType.ROLLBACK: 0.1,
            ScenarioType.INCREMENTAL: 0.15,
        }

        prob = base_probs.get(scenario, 0.2)

        # Adjust by severity
        severity = event.get("severity", "info")
        severity_multipliers = {"info": 1.0, "warning": 1.1, "critical": 1.3, "emergency": 1.5}
        prob *= severity_multipliers.get(severity, 1.0)

        # High reversibility slightly increases probability
        prob *= 0.8 + 0.4 * reversibility

        return min(prob, 1.0)

    def _scenario_name(self, scenario: ScenarioType) -> str:
        """Human-readable scenario name."""
        names = {
            ScenarioType.REORGANIZE: "Full Reorganization",
            ScenarioType.PARTIAL_REORGANIZE: "Partial Reorganization",
            ScenarioType.DEFER: "Defer Action",
            ScenarioType.ROLLBACK: "Rollback to Previous",
            ScenarioType.INCREMENTAL: "Incremental Changes",
        }
        return names.get(scenario, scenario.value)

    def _hash_state(self, state: nx.DiGraph) -> str:
        """Create reproducible hash of graph state."""
        adj_str = str(sorted(state.edges()))
        return hashlib.sha256(adj_str.encode()).hexdigest()[:16]


# =============================================================================
# PARADIGM
# =============================================================================

if __name__ == "__main__":
    print("Graphs as state. Uncertainty as feature.")
    print("Prediction informs deliberation, not replaces it.")
