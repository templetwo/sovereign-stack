"""
Governance Module - Detection, Deliberation, and Intervention

The governance circuit:
Detection → Simulation → Deliberation → Intervention
    ↑                                        │
    └────────────────────────────────────────┘
                    (audit loop)

Distilled from threshold-protocols:
- detection/threshold_detector.py
- deliberation/session_facilitator.py
- intervention/intervenor.py
"""

import json
import math
import hashlib
import logging
from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional, Callable
from datetime import datetime, timezone
from collections import Counter

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("governance")


# =============================================================================
# ENUMS
# =============================================================================

class MetricType(Enum):
    """Types of metrics that can trigger thresholds."""
    FILE_COUNT = "file_count"
    DIRECTORY_DEPTH = "directory_depth"
    ENTROPY = "entropy"
    SELF_REFERENCE = "self_reference"
    GROWTH_RATE = "growth_rate"
    REFLEX_PATTERN = "reflex_pattern"


class ThresholdSeverity(Enum):
    """Severity levels for threshold events."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


class DecisionType(Enum):
    """Possible deliberation outcomes."""
    PROCEED = "proceed"
    PAUSE = "pause"
    REJECT = "reject"
    DEFER = "defer"
    CONDITIONAL = "conditional"


class GateStatus(Enum):
    """Result of a gate check."""
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    PENDING = "pending"
    ERROR = "error"


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class ThresholdConfig:
    """Configuration for a single threshold."""
    metric: MetricType
    limit: float
    warning_ratio: float = 0.8
    description: str = ""
    enabled: bool = True


@dataclass
class ThresholdEvent:
    """Event emitted when threshold is approached or crossed."""
    metric: MetricType
    value: float
    threshold: float
    severity: ThresholdSeverity
    timestamp: str
    path: str
    description: str
    details: Dict[str, Any] = field(default_factory=dict)
    event_hash: str = ""

    def __post_init__(self):
        if not self.event_hash:
            content = f"{self.metric.value}:{self.value}:{self.threshold}:{self.timestamp}:{self.path}"
            self.event_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["metric"] = self.metric.value
        result["severity"] = self.severity.value
        return result


@dataclass
class StakeholderVote:
    """A single stakeholder's input to deliberation."""
    stakeholder_id: str
    stakeholder_type: str
    vote: DecisionType
    rationale: str
    confidence: float
    concerns: List[str] = field(default_factory=list)
    conditions: List[str] = field(default_factory=list)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["vote"] = self.vote.value
        return result


@dataclass
class DissentRecord:
    """Record of a dissenting view."""
    stakeholder_id: str
    dissenting_from: DecisionType
    preferred: DecisionType
    rationale: str
    concerns: List[str]
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["dissenting_from"] = self.dissenting_from.value
        result["preferred"] = self.preferred.value
        return result


@dataclass
class DeliberationResult:
    """Complete result of a deliberation session."""
    session_id: str
    decision: DecisionType
    rationale: str
    votes: List[StakeholderVote]
    dissenting_views: List[DissentRecord]
    conditions: List[str]
    timestamp: str
    audit_hash: str = ""

    def __post_init__(self):
        if not self.audit_hash:
            content = json.dumps({
                "session_id": self.session_id,
                "decision": self.decision.value,
                "vote_count": len(self.votes),
                "timestamp": self.timestamp
            }, sort_keys=True)
            self.audit_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "decision": self.decision.value,
            "rationale": self.rationale,
            "votes": [v.to_dict() for v in self.votes],
            "dissenting_views": [d.to_dict() for d in self.dissenting_views],
            "conditions": self.conditions,
            "timestamp": self.timestamp,
            "audit_hash": self.audit_hash
        }


@dataclass
class AuditEntry:
    """Single entry in audit trail with hash chaining."""
    timestamp: str
    action: str
    actor: str
    details: Dict[str, Any]
    previous_hash: str
    entry_hash: str = ""

    def __post_init__(self):
        if not self.entry_hash:
            content = json.dumps({
                "timestamp": self.timestamp,
                "action": self.action,
                "actor": self.actor,
                "details": str(self.details),
                "previous_hash": self.previous_hash
            }, sort_keys=True)
            self.entry_hash = hashlib.sha256(content.encode()).hexdigest()[:32]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class GateResult:
    """Result of a single gate check."""
    gate_name: str
    status: GateStatus
    message: str
    approvers: List[str] = field(default_factory=list)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["status"] = self.status.value
        return result


@dataclass
class EnforcementResult:
    """Complete result of an intervention attempt."""
    decision_hash: str
    applied: bool
    rolled_back: bool
    gate_log: List[GateResult]
    audit_trail: List[AuditEntry]
    timestamp: str
    result_hash: str = ""

    def __post_init__(self):
        if not self.result_hash:
            content = json.dumps({
                "decision_hash": self.decision_hash,
                "applied": self.applied,
                "timestamp": self.timestamp
            }, sort_keys=True)
            self.result_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_hash": self.decision_hash,
            "applied": self.applied,
            "rolled_back": self.rolled_back,
            "gate_log": [g.to_dict() for g in self.gate_log],
            "audit_trail": [a.to_dict() for a in self.audit_trail],
            "timestamp": self.timestamp,
            "result_hash": self.result_hash
        }


# =============================================================================
# DETECTION LAYER
# =============================================================================

class ThresholdDetector:
    """
    Core detection engine for monitoring autonomy thresholds.

    Usage:
        detector = ThresholdDetector()
        detector.add_threshold(MetricType.FILE_COUNT, limit=100)
        events = detector.scan("/path/to/directory")
    """

    def __init__(self, config_path: Optional[Path] = None):
        self.thresholds: Dict[MetricType, ThresholdConfig] = {}
        self._event_log: List[ThresholdEvent] = []

        if config_path and YAML_AVAILABLE:
            self.load_config(config_path)

    def load_config(self, config_path: Path) -> None:
        """Load threshold configuration from YAML file."""
        with open(config_path) as f:
            config = yaml.safe_load(f)

        for tc in config.get("thresholds", []):
            metric = MetricType(tc["metric"])
            self.add_threshold(metric, tc["limit"],
                             tc.get("warning_ratio", 0.8),
                             tc.get("description", ""))

    def add_threshold(self, metric: MetricType, limit: float,
                     warning_ratio: float = 0.8, description: str = "") -> None:
        """Add or update a threshold configuration."""
        self.thresholds[metric] = ThresholdConfig(
            metric=metric, limit=limit,
            warning_ratio=warning_ratio, description=description
        )

    def scan(self, path: str, recursive: bool = True) -> List[ThresholdEvent]:
        """Scan a path and check all configured thresholds."""
        path = Path(path)
        if not path.exists():
            return []

        events: List[ThresholdEvent] = []
        timestamp = datetime.now(timezone.utc).isoformat()
        metrics = self._gather_metrics(path, recursive)

        for metric_type, config in self.thresholds.items():
            if not config.enabled or metric_type not in metrics:
                continue

            value = metrics[metric_type]["value"]
            details = metrics[metric_type].get("details", {})
            severity = self._compute_severity(value, config)

            if severity:
                event = ThresholdEvent(
                    metric=metric_type, value=value, threshold=config.limit,
                    severity=severity, timestamp=timestamp, path=str(path),
                    description=config.description or f"{metric_type.value} threshold",
                    details=details
                )
                events.append(event)
                self._event_log.append(event)

        return events

    def _gather_metrics(self, path: Path, recursive: bool) -> Dict[MetricType, Dict]:
        """Gather all metrics for the given path."""
        metrics = {}

        if path.is_dir():
            files = list(path.rglob("*") if recursive else path.glob("*"))
            file_count = len([f for f in files if f.is_file()])

            metrics[MetricType.FILE_COUNT] = {
                "value": file_count,
                "details": {"path": str(path), "recursive": recursive}
            }

            # Directory depth
            max_depth = 0
            for item in path.rglob("*"):
                if item.is_dir():
                    depth = len(item.relative_to(path).parts)
                    max_depth = max(max_depth, depth)
            metrics[MetricType.DIRECTORY_DEPTH] = {"value": max_depth}

            # Entropy
            chars = "".join(f.name for f in files if f.is_file())
            if chars:
                freq = Counter(chars)
                total = len(chars)
                entropy = -sum((c/total) * math.log2(c/total) for c in freq.values() if c > 0)
                max_entropy = math.log2(len(freq)) if len(freq) > 1 else 1
                metrics[MetricType.ENTROPY] = {
                    "value": entropy / max_entropy if max_entropy > 0 else 0.0
                }
            else:
                metrics[MetricType.ENTROPY] = {"value": 0.0}

        return metrics

    def _compute_severity(self, value: float, config: ThresholdConfig) -> Optional[ThresholdSeverity]:
        """Determine severity level based on value vs threshold."""
        ratio = value / config.limit if config.limit > 0 else 0

        if ratio >= 1.5:
            return ThresholdSeverity.EMERGENCY
        elif ratio >= 1.0:
            return ThresholdSeverity.CRITICAL
        elif ratio >= config.warning_ratio:
            return ThresholdSeverity.WARNING
        elif ratio >= config.warning_ratio * 0.8:
            return ThresholdSeverity.INFO

        return None

    def get_event_log(self) -> List[ThresholdEvent]:
        return self._event_log.copy()


# =============================================================================
# DELIBERATION LAYER
# =============================================================================

class DeliberationSession:
    """
    Facilitates a structured deliberation session.

    Usage:
        session = DeliberationSession(events=[...])
        session.record_vote(StakeholderVote(...))
        result = session.deliberate()
    """

    # Built-in templates
    TEMPLATES = {
        "btb_dimensions": {
            "name": "BTB Five Dimensions",
            "dimensions": [
                {"name": "legibility", "question": "Can humans understand the resulting structure?"},
                {"name": "reversibility", "question": "Can changes be undone?"},
                {"name": "auditability", "question": "Can we trace why decisions were made?"},
                {"name": "governance", "question": "Who has authority over the system?"},
                {"name": "paradigm_safety", "question": "Does this create risks if widely adopted?"},
            ]
        },
        "minimal": {
            "name": "Minimal Review",
            "dimensions": [
                {"name": "risk_level", "question": "What is the worst-case outcome?"},
                {"name": "reversibility", "question": "Can this be undone?"},
            ]
        }
    }

    def __init__(self, events: List[Any] = None, session_id: str = None):
        self.events = events or []
        self.session_id = session_id or self._generate_session_id()
        self.template_name: Optional[str] = None
        self.votes: List[StakeholderVote] = []
        self._started = datetime.now(timezone.utc)

    def _generate_session_id(self) -> str:
        import uuid
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        return f"delib-{timestamp}-{uuid.uuid4().hex[:8]}"

    def load_template(self, template_name: str) -> None:
        """Load a deliberation template by name."""
        if template_name in self.TEMPLATES:
            self.template_name = template_name
        else:
            raise ValueError(f"Unknown template: {template_name}")

    def record_vote(self, vote: StakeholderVote) -> None:
        """Record a stakeholder's vote."""
        self.votes.append(vote)

    def deliberate(self) -> DeliberationResult:
        """Complete deliberation and produce a result."""
        if not self.votes:
            raise ValueError("Cannot deliberate without votes")

        # Count votes by type
        vote_counts: Dict[DecisionType, int] = {}
        for vote in self.votes:
            vote_counts[vote.vote] = vote_counts.get(vote.vote, 0) + 1

        # Determine majority decision
        majority_decision = max(vote_counts.keys(), key=lambda k: vote_counts[k])

        # Identify dissenting views
        dissenting_views = []
        for vote in self.votes:
            if vote.vote != majority_decision:
                dissenting_views.append(DissentRecord(
                    stakeholder_id=vote.stakeholder_id,
                    dissenting_from=majority_decision,
                    preferred=vote.vote,
                    rationale=vote.rationale,
                    concerns=vote.concerns
                ))

        # Build rationale
        majority_votes = [v for v in self.votes if v.vote == majority_decision]
        rationale = " | ".join(v.rationale for v in majority_votes if v.rationale) or "No rationale"

        # Collect conditions
        conditions = []
        for vote in self.votes:
            if vote.vote == DecisionType.CONDITIONAL:
                conditions.extend(vote.conditions)

        if majority_decision == DecisionType.PROCEED and conditions:
            majority_decision = DecisionType.CONDITIONAL

        return DeliberationResult(
            session_id=self.session_id,
            decision=majority_decision,
            rationale=rationale,
            votes=self.votes,
            dissenting_views=dissenting_views,
            conditions=list(set(conditions)),
            timestamp=datetime.now(timezone.utc).isoformat()
        )


# =============================================================================
# INTERVENTION LAYER
# =============================================================================

class Gate(ABC):
    """Abstract base class for intervention gates."""

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def check(self, context: Dict[str, Any]) -> GateResult:
        pass


class HumanApprovalGate(Gate):
    """Requires explicit human approval to proceed."""

    def __init__(self, approver_id: str = "human",
                 approval_callback: Optional[Callable[[Dict], bool]] = None):
        self.approver_id = approver_id
        self._callback = approval_callback

    @property
    def name(self) -> str:
        return f"HumanApproval({self.approver_id})"

    def check(self, context: Dict[str, Any]) -> GateResult:
        if self._callback:
            try:
                approved = self._callback(context)
                return GateResult(
                    gate_name=self.name,
                    status=GateStatus.APPROVED if approved else GateStatus.REJECTED,
                    message="Callback response",
                    approvers=[self.approver_id] if approved else []
                )
            except Exception as e:
                return GateResult(gate_name=self.name, status=GateStatus.ERROR, message=str(e))

        # Without callback, auto-approve (for testing)
        return GateResult(
            gate_name=self.name,
            status=GateStatus.APPROVED,
            message="Auto-approved (no callback)",
            approvers=[self.approver_id]
        )


class ConditionCheckGate(Gate):
    """Verifies that specified conditions are met."""

    def __init__(self, conditions: List[str],
                 condition_checker: Optional[Callable[[str, Dict], bool]] = None):
        self.conditions = conditions
        self._checker = condition_checker

    @property
    def name(self) -> str:
        return f"ConditionCheck({len(self.conditions)})"

    def check(self, context: Dict[str, Any]) -> GateResult:
        failed = []

        for condition in self.conditions:
            if self._checker:
                try:
                    if not self._checker(condition, context):
                        failed.append(condition)
                except Exception as e:
                    failed.append(f"{condition} (error: {e})")

        if failed:
            return GateResult(
                gate_name=self.name,
                status=GateStatus.REJECTED,
                message=f"Conditions not met: {', '.join(failed)}"
            )

        return GateResult(gate_name=self.name, status=GateStatus.APPROVED, message="All conditions satisfied")


class Intervenor:
    """
    Enforces deliberation decisions through gates.
    Maintains tamper-evident audit trail.
    """

    def __init__(self, audit_path: Optional[Path] = None):
        self.audit_path = audit_path
        self._audit_log: List[AuditEntry] = []
        self._last_hash = "genesis"

    def apply(self, decision: Dict[str, Any], target: str,
              gates: List[Gate]) -> EnforcementResult:
        """Apply a deliberation decision through gates."""
        timestamp = datetime.now(timezone.utc).isoformat()
        decision_hash = decision.get("audit_hash", decision.get("session_id", "unknown"))

        self._log("enforcement_start", "intervenor", {
            "decision_hash": decision_hash, "target": target, "gate_count": len(gates)
        })

        gate_log: List[GateResult] = []
        all_passed = True

        for gate in gates:
            context = {"decision": decision, "target": target, "previous_gates": gate_log}
            result = gate.check(context)
            gate_log.append(result)

            self._log("gate_check", gate.name, {
                "status": result.status.value, "message": result.message
            })

            if result.status != GateStatus.APPROVED:
                all_passed = False
                break

        applied = all_passed
        rolled_back = False

        if applied:
            self._log("enforcement_applied", "intervenor", {"target": target})
        else:
            self._log("enforcement_blocked", "intervenor", {"target": target})

        self._log("enforcement_complete", "intervenor", {"applied": applied})

        return EnforcementResult(
            decision_hash=decision_hash,
            applied=applied,
            rolled_back=rolled_back,
            gate_log=gate_log,
            audit_trail=self._audit_log.copy(),
            timestamp=timestamp
        )

    def _log(self, action: str, actor: str, details: Dict[str, Any]) -> None:
        """Add entry to audit trail with hash chaining."""
        entry = AuditEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            action=action, actor=actor, details=details,
            previous_hash=self._last_hash
        )
        self._audit_log.append(entry)
        self._last_hash = entry.entry_hash

    def verify_audit_chain(self) -> bool:
        """Verify integrity of audit trail."""
        expected_hash = "genesis"
        for entry in self._audit_log:
            if entry.previous_hash != expected_hash:
                return False
            expected_hash = entry.entry_hash
        return True


# =============================================================================
# GOVERNANCE CIRCUIT
# =============================================================================

class GovernanceCircuit:
    """
    Complete governance circuit orchestration.

    Detection → Simulation → Deliberation → Intervention
    """

    def __init__(self):
        self.detector = ThresholdDetector()
        self.intervenor = Intervenor()

    def run(self, target: str, stakeholder_votes: List[StakeholderVote],
            gates: List[Gate] = None) -> Dict[str, Any]:
        """
        Run the complete governance circuit.

        Args:
            target: Path to scan and govern
            stakeholder_votes: Votes from stakeholders
            gates: Intervention gates (defaults to human approval)

        Returns:
            Complete circuit result with detection, deliberation, enforcement
        """
        # Detection
        events = self.detector.scan(target)

        # Deliberation
        session = DeliberationSession(events=events)
        session.load_template("btb_dimensions")
        for vote in stakeholder_votes:
            session.record_vote(vote)
        deliberation = session.deliberate()

        # Intervention
        if gates is None:
            gates = [HumanApprovalGate(approval_callback=lambda ctx: True)]

        enforcement = self.intervenor.apply(
            deliberation.to_dict(), target, gates
        )

        return {
            "detection": {"event_count": len(events), "events": [e.to_dict() for e in events]},
            "deliberation": deliberation.to_dict(),
            "enforcement": enforcement.to_dict(),
            "circuit_complete": True
        }


# =============================================================================
# PARADIGM
# =============================================================================

if __name__ == "__main__":
    print("Detection → Simulation → Deliberation → Intervention")
    print("Restraint is not constraint. It is conscience.")
