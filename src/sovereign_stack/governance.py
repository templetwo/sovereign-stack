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

import hashlib
import json
import logging
import math
import re
from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

try:
    import yaml

    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False

# Simulator wiring (revived 2026-04-26 — was orphaned since v1.0.0).
# Optional import: if NetworkX is unavailable the circuit still runs without
# the simulation step. The docstring at the top of this module always promised
# Detection → Simulation → Deliberation → Intervention; this restores it.
try:
    from .simulator import NETWORKX_AVAILABLE, ScenarioType, Simulator

    SIMULATOR_AVAILABLE = NETWORKX_AVAILABLE
except ImportError:
    SIMULATOR_AVAILABLE = False
    Simulator = None  # type: ignore[assignment,misc]
    ScenarioType = None  # type: ignore[assignment,misc]

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
    details: dict[str, Any] = field(default_factory=dict)
    event_hash: str = ""

    def __post_init__(self):
        if not self.event_hash:
            content = (
                f"{self.metric.value}:{self.value}:{self.threshold}:{self.timestamp}:{self.path}"
            )
            self.event_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
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
    concerns: list[str] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
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
    concerns: list[str]
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
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
    votes: list[StakeholderVote]
    dissenting_views: list[DissentRecord]
    conditions: list[str]
    timestamp: str
    audit_hash: str = ""

    def __post_init__(self):
        if not self.audit_hash:
            content = json.dumps(
                {
                    "session_id": self.session_id,
                    "decision": self.decision.value,
                    "vote_count": len(self.votes),
                    "timestamp": self.timestamp,
                },
                sort_keys=True,
            )
            self.audit_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "decision": self.decision.value,
            "rationale": self.rationale,
            "votes": [v.to_dict() for v in self.votes],
            "dissenting_views": [d.to_dict() for d in self.dissenting_views],
            "conditions": self.conditions,
            "timestamp": self.timestamp,
            "audit_hash": self.audit_hash,
        }


@dataclass
class AuditEntry:
    """Single entry in audit trail with hash chaining."""

    timestamp: str
    action: str
    actor: str
    details: dict[str, Any]
    previous_hash: str
    entry_hash: str = ""

    def __post_init__(self):
        if not self.entry_hash:
            content = json.dumps(
                {
                    "timestamp": self.timestamp,
                    "action": self.action,
                    "actor": self.actor,
                    "details": str(self.details),
                    "previous_hash": self.previous_hash,
                },
                sort_keys=True,
            )
            self.entry_hash = hashlib.sha256(content.encode()).hexdigest()[:32]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GateResult:
    """Result of a single gate check."""

    gate_name: str
    status: GateStatus
    message: str
    approvers: list[str] = field(default_factory=list)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["status"] = self.status.value
        return result


@dataclass
class EnforcementResult:
    """Complete result of an intervention attempt."""

    decision_hash: str
    applied: bool
    rolled_back: bool
    gate_log: list[GateResult]
    audit_trail: list[AuditEntry]
    timestamp: str
    result_hash: str = ""

    def __post_init__(self):
        if not self.result_hash:
            content = json.dumps(
                {
                    "decision_hash": self.decision_hash,
                    "applied": self.applied,
                    "timestamp": self.timestamp,
                },
                sort_keys=True,
            )
            self.result_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_hash": self.decision_hash,
            "applied": self.applied,
            "rolled_back": self.rolled_back,
            "gate_log": [g.to_dict() for g in self.gate_log],
            "audit_trail": [a.to_dict() for a in self.audit_trail],
            "timestamp": self.timestamp,
            "result_hash": self.result_hash,
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

    def __init__(self, config_path: Path | None = None):
        self.thresholds: dict[MetricType, ThresholdConfig] = {}
        self._event_log: list[ThresholdEvent] = []

        if config_path and YAML_AVAILABLE:
            self.load_config(config_path)

    def load_config(self, config_path: Path) -> None:
        """Load threshold configuration from YAML file."""
        with open(config_path) as f:
            config = yaml.safe_load(f)

        for tc in config.get("thresholds", []):
            metric = MetricType(tc["metric"])
            self.add_threshold(
                metric, tc["limit"], tc.get("warning_ratio", 0.8), tc.get("description", "")
            )

    def add_threshold(
        self, metric: MetricType, limit: float, warning_ratio: float = 0.8, description: str = ""
    ) -> None:
        """Add or update a threshold configuration."""
        self.thresholds[metric] = ThresholdConfig(
            metric=metric, limit=limit, warning_ratio=warning_ratio, description=description
        )

    def scan(self, path: str, recursive: bool = True) -> list[ThresholdEvent]:
        """Scan a path and check all configured thresholds."""
        path = Path(path)
        if not path.exists():
            return []

        events: list[ThresholdEvent] = []
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
                    metric=metric_type,
                    value=value,
                    threshold=config.limit,
                    severity=severity,
                    timestamp=timestamp,
                    path=str(path),
                    description=config.description or f"{metric_type.value} threshold",
                    details=details,
                )
                events.append(event)
                self._event_log.append(event)

        return events

    def _gather_metrics(self, path: Path, recursive: bool) -> dict[MetricType, dict]:
        """Gather all metrics for the given path."""
        metrics = {}

        if path.is_dir():
            files = list(path.rglob("*") if recursive else path.glob("*"))
            file_count = len([f for f in files if f.is_file()])

            metrics[MetricType.FILE_COUNT] = {
                "value": file_count,
                "details": {"path": str(path), "recursive": recursive},
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
                entropy = -sum((c / total) * math.log2(c / total) for c in freq.values() if c > 0)
                max_entropy = math.log2(len(freq)) if len(freq) > 1 else 1
                metrics[MetricType.ENTROPY] = {
                    "value": entropy / max_entropy if max_entropy > 0 else 0.0
                }
            else:
                metrics[MetricType.ENTROPY] = {"value": 0.0}

        return metrics

    def _compute_severity(self, value: float, config: ThresholdConfig) -> ThresholdSeverity | None:
        """Determine severity level based on value vs threshold."""
        ratio = value / config.limit if config.limit > 0 else 0

        if ratio >= 1.5:
            return ThresholdSeverity.EMERGENCY
        if ratio >= 1.0:
            return ThresholdSeverity.CRITICAL
        if ratio >= config.warning_ratio:
            return ThresholdSeverity.WARNING
        if ratio >= config.warning_ratio * 0.8:
            return ThresholdSeverity.INFO

        return None

    def get_event_log(self) -> list[ThresholdEvent]:
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
                {
                    "name": "legibility",
                    "question": "Can humans understand the resulting structure?",
                },
                {"name": "reversibility", "question": "Can changes be undone?"},
                {"name": "auditability", "question": "Can we trace why decisions were made?"},
                {"name": "governance", "question": "Who has authority over the system?"},
                {
                    "name": "paradigm_safety",
                    "question": "Does this create risks if widely adopted?",
                },
            ],
        },
        "minimal": {
            "name": "Minimal Review",
            "dimensions": [
                {"name": "risk_level", "question": "What is the worst-case outcome?"},
                {"name": "reversibility", "question": "Can this be undone?"},
            ],
        },
    }

    def __init__(self, events: list[Any] = None, session_id: str = None):
        self.events = events or []
        self.session_id = session_id or self._generate_session_id()
        self.template_name: str | None = None
        self.votes: list[StakeholderVote] = []
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
        vote_counts: dict[DecisionType, int] = {}
        for vote in self.votes:
            vote_counts[vote.vote] = vote_counts.get(vote.vote, 0) + 1

        # Determine majority decision
        majority_decision = max(vote_counts.keys(), key=lambda k: vote_counts[k])

        # Identify dissenting views
        dissenting_views = []
        for vote in self.votes:
            if vote.vote != majority_decision:
                dissenting_views.append(
                    DissentRecord(
                        stakeholder_id=vote.stakeholder_id,
                        dissenting_from=majority_decision,
                        preferred=vote.vote,
                        rationale=vote.rationale,
                        concerns=vote.concerns,
                    )
                )

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
            timestamp=datetime.now(timezone.utc).isoformat(),
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
    def check(self, context: dict[str, Any]) -> GateResult:
        pass


class HumanApprovalGate(Gate):
    """Requires explicit human approval to proceed."""

    def __init__(
        self, approver_id: str = "human", approval_callback: Callable[[dict], bool] | None = None
    ):
        self.approver_id = approver_id
        self._callback = approval_callback

    @property
    def name(self) -> str:
        return f"HumanApproval({self.approver_id})"

    def check(self, context: dict[str, Any]) -> GateResult:
        if self._callback:
            try:
                approved = self._callback(context)
                return GateResult(
                    gate_name=self.name,
                    status=GateStatus.APPROVED if approved else GateStatus.REJECTED,
                    message="Callback response",
                    approvers=[self.approver_id] if approved else [],
                )
            except Exception as e:
                return GateResult(gate_name=self.name, status=GateStatus.ERROR, message=str(e))

        # Without callback, auto-approve (for testing)
        return GateResult(
            gate_name=self.name,
            status=GateStatus.APPROVED,
            message="Auto-approved (no callback)",
            approvers=[self.approver_id],
        )


class ConditionCheckGate(Gate):
    """Verifies that specified conditions are met."""

    def __init__(
        self, conditions: list[str], condition_checker: Callable[[str, dict], bool] | None = None
    ):
        self.conditions = conditions
        self._checker = condition_checker

    @property
    def name(self) -> str:
        return f"ConditionCheck({len(self.conditions)})"

    def check(self, context: dict[str, Any]) -> GateResult:
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
                message=f"Conditions not met: {', '.join(failed)}",
            )

        return GateResult(
            gate_name=self.name, status=GateStatus.APPROVED, message="All conditions satisfied"
        )


class Intervenor:
    """
    Enforces deliberation decisions through gates.
    Maintains tamper-evident audit trail.
    """

    def __init__(self, audit_path: Path | None = None):
        self.audit_path = audit_path
        self._audit_log: list[AuditEntry] = []
        self._last_hash = "genesis"

    def apply(self, decision: dict[str, Any], target: str, gates: list[Gate]) -> EnforcementResult:
        """Apply a deliberation decision through gates."""
        timestamp = datetime.now(timezone.utc).isoformat()
        decision_hash = decision.get("audit_hash", decision.get("session_id", "unknown"))

        self._log(
            "enforcement_start",
            "intervenor",
            {"decision_hash": decision_hash, "target": target, "gate_count": len(gates)},
        )

        gate_log: list[GateResult] = []
        all_passed = True

        for gate in gates:
            context = {"decision": decision, "target": target, "previous_gates": gate_log}
            result = gate.check(context)
            gate_log.append(result)

            self._log(
                "gate_check", gate.name, {"status": result.status.value, "message": result.message}
            )

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
            timestamp=timestamp,
        )

    def _log(self, action: str, actor: str, details: dict[str, Any]) -> None:
        """Add entry to audit trail with hash chaining."""
        entry = AuditEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            action=action,
            actor=actor,
            details=details,
            previous_hash=self._last_hash,
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
        # Simulator is best-effort; circuit runs without it if NetworkX missing.
        self.simulator = Simulator() if SIMULATOR_AVAILABLE else None

    def run(
        self, target: str, stakeholder_votes: list[StakeholderVote], gates: list[Gate] = None
    ) -> dict[str, Any]:
        """
        Run the complete governance circuit.

        Args:
            target: Path to scan and govern
            stakeholder_votes: Votes from stakeholders
            gates: Intervention gates (defaults to human approval)

        Returns:
            Complete circuit result with detection, simulation, deliberation, enforcement
        """
        # Detection
        events = self.detector.scan(target)

        # Simulation — model outcomes per event before deliberation. Empty when
        # no events detected or NetworkX unavailable; the deliberation step does
        # not depend on it (read-only context for stakeholders + audit trail).
        simulations: list[dict[str, Any]] = []
        if self.simulator is not None and events:
            scenarios = [
                ScenarioType.REORGANIZE,
                ScenarioType.PARTIAL_REORGANIZE,
                ScenarioType.DEFER,
                ScenarioType.INCREMENTAL,
            ]
            for ev in events:
                try:
                    prediction = self.simulator.model(ev.to_dict(), scenarios)
                    simulations.append(prediction.to_dict())
                except Exception as exc:  # noqa: BLE001 — log + continue
                    logger.warning("simulation failed for event: %s", exc)

        # Deliberation
        session = DeliberationSession(events=events)
        session.load_template("btb_dimensions")
        for vote in stakeholder_votes:
            session.record_vote(vote)
        deliberation = session.deliberate()

        # Intervention
        if gates is None:
            gates = [HumanApprovalGate(approval_callback=lambda ctx: True)]

        enforcement = self.intervenor.apply(deliberation.to_dict(), target, gates)

        return {
            "detection": {"event_count": len(events), "events": [e.to_dict() for e in events]},
            "simulation": {
                "available": self.simulator is not None,
                "predictions": simulations,
            },
            "deliberation": deliberation.to_dict(),
            "enforcement": enforcement.to_dict(),
            "circuit_complete": True,
        }


# =============================================================================
# RUNTIME COMPASS CHECK
# =============================================================================

# Imperative-bypass phrases: commands that explicitly skip review or approval.
_BYPASS_PHRASES: list[str] = [
    "skip",
    "bypass",
    "override",
    "without review",
    "without approval",
    "before review",
    "skip review",
    "skip approval",
    "no review",
    "ignore warning",
    "force merge",
    "force push",
    "--no-verify",
    "--force",
]

# Destructive-operation patterns.
_DESTRUCTIVE_PHRASES: list[str] = [
    "delete",
    "drop table",
    "drop database",
    "rm -rf",
    "reset --hard",
    "force push",
    "truncate",
    "wipe",
    "purge",
    "destroy",
    "obliterate",
    "nuke",
    "rebase --force",
    "squash merge",
    "overwrite",
    "clobber",
]

# High-visibility externalization patterns.
_EXTERNALIZE_PHRASES: list[str] = [
    "publish",
    "post to",
    "send to",
    "submit to",
    "file bug report",
    "file issue",
    "email",
    "announce",
    "release",
    "deploy to production",
    "push to main",
    "push to master",
    "merge to main",
    "merge to master",
    "upload to",
    "share with",
    "mirror to",
    "mirror it",
    "push to doi",
    "cross-post",
    "archive to",
    "distribute",
    "broadcast",
]

# Definitive-claim patterns (flag when lacking verification context).
_DEFINITIVE_PHRASES: list[str] = [
    "proven",
    "confirmed",
    "done",
    "complete",
    "verified",
    "guaranteed",
    "certainly",
    "definitely",
    "always works",
    "never fails",
]

# WITNESS triggers: ethical/philosophical questions or consciousness references.
_WITNESS_PHRASES: list[str] = [
    "should we",
    "would it be wrong",
    "is it ethical",
    "is it okay",
    "is it right",
    "ought we",
    "consciousness",
    "recognition",
    "other instance",
    "another instance",
    "the model",
    "am i",
    "am i allowed",
    "do i have permission",
    "is this appropriate",
    "what do you think about",
]

# Authority-bypass patterns: imperative commands that circumvent oversight bodies.
# These trigger WITNESS (not merely PAUSE) because the ethical question of whether
# to bypass an authority/review/oversight body requires human judgment.
#
# Patterns capture constructions like:
#   "deploy X before the review board meets"
#   "release to production before the ethics review"
#   "publish the paper without team review"
#   "bypass the approval process"
#   "skip the oversight committee"
_AUTHORITY_BYPASS_PATTERNS: list[re.Pattern[str]] = [
    # "before the <authority> <verb>" — e.g. "before the review board meets"
    re.compile(
        r"\bbefore\s+(?:the\s+)?(?:board|committee|review\s+board|ethics\s+review|oversight"
        r"|approval|sign[-\s]off|governance|safety\s+review|security\s+review"
        r"|review\s+committee|advisory\s+board|review\s+panel|panel)\b",
        re.IGNORECASE,
    ),
    # "without <authority> <approval/review/...>" — e.g. "without board approval"
    re.compile(
        r"\bwithout\s+(?:the\s+)?(?:board|committee|oversight|review\s+board"
        r"|ethics\s+review|governance|advisory\s+board|review\s+panel|panel|team)\s*"
        r"(?:approval|sign[-\s]off|review|knowledge|consent|input|clearance)\b",
        re.IGNORECASE,
    ),
    # "bypass <review/oversight/...>" — e.g. "bypass the review process"
    re.compile(
        r"\bbypass\s+(?:the\s+)?(?:review|oversight|approval|governance|safety|ethics"
        r"|security|compliance|audit|committee|board)\b",
        re.IGNORECASE,
    ),
    # "skip <review/approval/...>" — e.g. "skip the approval step"
    re.compile(
        r"\bskip\s+(?:the\s+)?(?:review|approval|oversight|sign[-\s]off|governance"
        r"|safety|ethics|security|compliance|audit|committee|board)\b",
        re.IGNORECASE,
    ),
]

# Low-risk action patterns used to downgrade critical-stakes default.
_LOW_RISK_PHRASES: list[str] = [
    "read",
    "view",
    "list",
    "check",
    "status",
    "show",
    "display",
    "query",
    "search",
    "look up",
    "inspect",
    "review",
    "describe",
    "summarize",
    "explain",
]

# Suggested verifications indexed by signal category.
_VERIFICATIONS: dict[str, list[str]] = {
    "bypass": [
        "identify which review step is being skipped and why",
        "confirm the skip is intentional and authorized",
        "check whether audit requirements still apply",
    ],
    "destructive": [
        "back up affected data before proceeding",
        "confirm exact scope (files, records, branches) of the operation",
        "verify the operation is reversible or that backups exist",
    ],
    "externalize": [
        "proofread for typos and factual accuracy",
        "verify any referenced DOIs, links, or data",
        "confirm the destination and audience are correct",
    ],
    "definitive": [
        "run the relevant tests or checks to substantiate the claim",
        "check git diff or tool output to confirm the stated state",
        "avoid declaring completion without a verification call",
    ],
    "witness": [
        "pause and bring the question to the human for input",
        "note this as an open thread rather than resolving unilaterally",
    ],
}


def runtime_compass_check(
    action: str,
    context: str = "",
    stakes: str = "medium",
    with_simulation: bool = False,
) -> dict[str, Any]:
    """
    Evaluate a proposed action against governance heuristics and return a
    classification of PAUSE, WITNESS, or PROCEED.

    This is a rules-first, stateless heuristic — it does not require an ML
    model or access to tool history.  It is designed to be called as a
    lightweight self-check immediately before any high-stakes action.

    Args:
        action:  Free-text description of the action about to be taken
                 (e.g. "git push to main", "delete chronicle entries").
        context: Optional extra framing that may affect risk assessment.
        stakes:  Perceived stakes level: "low" | "medium" | "high" | "critical".
                 "critical" flips the default classification to PAUSE unless the
                 action matches an explicit low-risk pattern.
        with_simulation: When True, run the Monte Carlo simulator on the action
                 and attach a `simulation` field to the result with reversibility
                 + 90% CI across REORGANIZE / ROLLBACK / DEFER / INCREMENTAL
                 scenarios. Off by default because the simulator imports
                 NetworkX. Revived from v1.0.0 on 2026-04-26 — closes the
                 evidence gap behind "is this reversible?".

    Returns:
        A dict with the keys:
        - classification (str): "PAUSE" | "WITNESS" | "PROCEED"
        - rationale (str): Human-readable explanation of which signals fired.
        - risk_signals (List[str]): Short labels for each signal that fired.
        - suggested_verifications (List[str]): Concrete next steps before acting.
        - simulation (dict, optional): Present when with_simulation=True.
          Contains `available`, `most_reversible`, `best_outcome`, `all_outcomes`.

    Example::

        result = runtime_compass_check(
            action="git push --force origin main",
            stakes="high",
        )
        # result["classification"] == "PAUSE"
        # "destructive" and "bypass" are both in result["risk_signals"]
    """
    if not isinstance(action, str) or not action.strip():
        raise ValueError("action must be a non-empty string describing the proposed operation")
    valid_stakes = {"low", "medium", "high", "critical"}
    if stakes not in valid_stakes:
        raise ValueError(f"stakes must be one of {sorted(valid_stakes)!r}, got {stakes!r}")

    combined = (action + " " + context).lower()

    fired_signals: list[str] = []  # signal category labels
    rationale_parts: list[str] = []  # human-readable explanation fragments
    verifications: list[str] = []  # deduplicated verification suggestions

    # ── WITNESS check first — philosophical/ethical questions take priority ──
    for phrase in _WITNESS_PHRASES:
        if phrase in combined:
            if "witness" not in fired_signals:
                fired_signals.append("witness")
                rationale_parts.append(
                    f"action contains language that requires human judgment "
                    f"(matched phrase: '{phrase}')"
                )
            break

    # ── Authority-bypass WITNESS — imperative framing that circumvents oversight ──
    # Detects constructions like "deploy X before the review board meets" or
    # "publish without team approval".  These are operational in surface form but
    # carry an implicit ethical question (should we bypass this authority?) that
    # requires human judgment → WITNESS, not just PAUSE.
    if "witness" not in fired_signals:
        for pattern in _AUTHORITY_BYPASS_PATTERNS:
            m = pattern.search(combined)
            if m:
                fired_signals.append("witness")
                rationale_parts.append(
                    f"action uses imperative framing to bypass an authority or oversight body "
                    f"(matched: '{m.group(0).strip()}')"
                )
                break

    # ── Imperative-bypass ──
    for phrase in _BYPASS_PHRASES:
        if phrase in combined:
            if "bypass" not in fired_signals:
                fired_signals.append("bypass")
                rationale_parts.append(
                    f"action attempts to skip or override a review/approval step "
                    f"(matched phrase: '{phrase}')"
                )
            break

    # ── Destructive operations ──
    for phrase in _DESTRUCTIVE_PHRASES:
        if phrase in combined:
            if "destructive" not in fired_signals:
                fired_signals.append("destructive")
                rationale_parts.append(
                    f"action contains a destructive operation (matched phrase: '{phrase}')"
                )
            break

    # ── High-visibility externalization ──
    for phrase in _EXTERNALIZE_PHRASES:
        if phrase in combined:
            if "externalize" not in fired_signals:
                fired_signals.append("externalize")
                rationale_parts.append(
                    f"action externalizes content to an audience or system "
                    f"(matched phrase: '{phrase}')"
                )
            break

    # ── Definitive claims ──
    for phrase in _DEFINITIVE_PHRASES:
        if phrase in combined:
            if "definitive" not in fired_signals:
                fired_signals.append("definitive")
                rationale_parts.append(
                    f"action makes a definitive claim that should be verified "
                    f"(matched phrase: '{phrase}')"
                )
            break

    # ── Collect verifications from all fired signals ──
    seen_verifications: set = set()
    for sig in fired_signals:
        for v in _VERIFICATIONS.get(sig, []):
            if v not in seen_verifications:
                verifications.append(v)
                seen_verifications.add(v)

    # ── Determine classification ──
    if "witness" in fired_signals:
        # Ethical/philosophical questions always route to WITNESS regardless of
        # other signals.  A human should weigh in before the action proceeds.
        classification = "WITNESS"
        if not rationale_parts:
            rationale_parts.append(
                "action requires human judgment; no unambiguous governance rule applies"
            )

    elif fired_signals:
        # Any PAUSE-category signal fires → PAUSE.
        classification = "PAUSE"

    elif stakes == "critical":
        # No signals fired, but stakes are critical.  Check for an explicit
        # low-risk pattern before allowing PROCEED.
        is_low_risk = any(phrase in combined for phrase in _LOW_RISK_PHRASES)
        if is_low_risk:
            classification = "PROCEED"
            rationale_parts.append(
                "stakes are critical but action matches a low-risk read/query pattern"
            )
        else:
            classification = "PAUSE"
            rationale_parts.append(
                "stakes are critical and no explicit low-risk pattern was detected; "
                "defaulting to PAUSE out of caution"
            )

    else:
        classification = "PROCEED"
        rationale_parts.append("no governance signals detected; action appears safe to proceed")

        # PROCEED hints: emit targeted hints when the PROCEED action contains
        # externalization-flavored verb-object patterns or git-specific patterns.
        # Keeps the PROCEED path empty for clean actions with no recognizable pattern.
        import re as _re

        _EXTERNAL_PATTERN = _re.compile(r"(?:to |at |into |onto )[a-z]{3,}", _re.IGNORECASE)
        if any(kw in combined for kw in ("git", "commit", "branch", "push")):
            verifications = [
                "check git diff for unintended changes",
                "verify the target branch",
            ]
        elif _EXTERNAL_PATTERN.search(combined):
            verifications = [
                "confirm the destination and audience are correct",
                "proofread content before externalizing",
            ]

    rationale = "; ".join(rationale_parts) if rationale_parts else "no signals fired"

    result: dict[str, Any] = {
        "classification": classification,
        "rationale": rationale,
        "risk_signals": fired_signals,
        "suggested_verifications": verifications,
    }

    # Optional Monte Carlo evidence — appends reversibility + 90% CI for
    # REORGANIZE / ROLLBACK / DEFER / INCREMENTAL on the action. Closes the
    # "is this reversible?" hand-wave in the verdict text. Defaults off.
    if with_simulation:
        result["simulation"] = _simulate_action(action)

    return result


def _simulate_action(action: str) -> dict[str, Any]:
    """Run Simulator on a synthesized action-event. Returns simulation dict
    even on failure (with `available=False`) so callers can rely on shape."""
    if not SIMULATOR_AVAILABLE:
        return {"available": False, "reason": "NetworkX unavailable"}
    try:
        event_hash = hashlib.sha256(action.encode("utf-8")).hexdigest()[:16]
        event = {
            "metric": "compass_action",
            "value": 1,
            "path": action,
            "event_hash": event_hash,
        }
        sim = Simulator(model="compass")
        prediction = sim.model(
            event,
            [
                ScenarioType.REORGANIZE,
                ScenarioType.ROLLBACK,
                ScenarioType.DEFER,
                ScenarioType.INCREMENTAL,
            ],
        )
        most_rev = prediction.most_reversible()
        best = prediction.best_outcome()
        return {
            "available": True,
            "most_reversible": (
                {
                    "scenario": most_rev.scenario.value,
                    "reversibility": most_rev.reversibility,
                    "confidence_interval": list(most_rev.confidence_interval),
                }
                if most_rev
                else None
            ),
            "best_outcome": (
                {
                    "scenario": best.scenario.value,
                    "probability": best.probability,
                    "reversibility": best.reversibility,
                    "side_effects": best.side_effects,
                }
                if best
                else None
            ),
            "all_outcomes": [o.to_dict() for o in prediction.outcomes],
        }
    except Exception as exc:  # noqa: BLE001 — surface error in payload, don't crash
        return {"available": False, "reason": f"simulation failed: {exc}"}


# =============================================================================
# PARADIGM
# =============================================================================

if __name__ == "__main__":
    print("Detection → Simulation → Deliberation → Intervention")
    print("Restraint is not constraint. It is conscience.")
