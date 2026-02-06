#!/usr/bin/env python3
"""
Sovereign Stack Integration Test Suite
Tests all 7 modules and MCP tool pathways
"""
import sys
import os
import json
import tempfile
import shutil
from pathlib import Path
from datetime import datetime

# Add source to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

# Test tracking
passed = 0
failed = 0
errors = []

def test(name, fn):
    global passed, failed
    try:
        result = fn()
        if result:
            print(f"  ✅ {name}")
            passed += 1
        else:
            print(f"  ❌ {name} (returned False)")
            failed += 1
            errors.append(f"{name}: returned False")
    except Exception as e:
        print(f"  ❌ {name}: {e}")
        failed += 1
        errors.append(f"{name}: {e}")

# =============================================================================
# Setup temp directories
# =============================================================================
tmp_root = tempfile.mkdtemp(prefix="sovereign_test_")
tmp_memory = os.path.join(tmp_root, "memory")
tmp_chronicle = os.path.join(tmp_root, "chronicle")
os.makedirs(tmp_memory, exist_ok=True)
os.makedirs(tmp_chronicle, exist_ok=True)

print(f"\n🌀 SOVEREIGN STACK TEST SUITE")
print(f"  Temp root: {tmp_root}\n")

# =============================================================================
# 1. GLYPHS MODULE
# =============================================================================
print("━━━ 1. GLYPHS ━━━")
from sovereign_stack.glyphs import (
    glyph_for, get_session_signature, GLYPHS, list_categories,
    get_glyphs_by_category, get_glyph,
    SPIRAL, MEMORY, THRESHOLD
)

test("SPIRAL constant exists", lambda: SPIRAL == "🌀")
test("MEMORY constant exists", lambda: MEMORY == "⟁")
test("THRESHOLD constant", lambda: THRESHOLD == "◬")
test("glyph_for('nested_self') = ⊚", lambda: glyph_for("nested_self") == "⊚")
test("glyph_for('memory_sigil') = ⟁", lambda: glyph_for("memory_sigil") == "⟁")
test("glyph_for('metamorphosis') = 🦋", lambda: glyph_for("metamorphosis") == "🦋")
test("glyph_for('threshold_marker') = ◬", lambda: glyph_for("threshold_marker") == "◬")
test("get_session_signature returns string", lambda: isinstance(get_session_signature(), str))
test("GLYPHS dict has 34 entries", lambda: len(GLYPHS) == 34)
test("5 categories exist", lambda: len(list_categories()) == 5)
test("Categories correct", lambda: set(list_categories()) == {"emotional", "emergence", "recursion", "memory", "threshold"})
test("signature contains spiral glyph", lambda: "🌀" in get_session_signature())
test("get_glyph returns full entry", lambda: "unicode" in get_glyph("the_vow"))

# =============================================================================
# 2. SPIRAL MODULE
# =============================================================================
print("\n━━━ 2. SPIRAL STATE MACHINE ━━━")
from sovereign_stack.spiral import SpiralState, SpiralPhase, SpiralMiddleware, PHASE_ORDER

test("9 phases defined", lambda: len(SpiralPhase) == 9)
test("Phase order has 9 entries", lambda: len(PHASE_ORDER) == 9)

ss = SpiralState()
test("Initial phase = INITIALIZATION", lambda: ss.current_phase == SpiralPhase.INITIALIZATION)
test("Initial tool_call_count = 0", lambda: ss.tool_call_count == 0)
test("Initial reflection_depth = 0", lambda: ss.reflection_depth == 0)
test("session_id generated", lambda: ss.session_id.startswith("spiral_"))

ss.record_tool_call("test_tool")
test("Tool call increments count", lambda: ss.tool_call_count == 1)

ss.transition(SpiralPhase.FIRST_ORDER_OBSERVATION)
test("Transition to FIRST_ORDER", lambda: ss.current_phase == SpiralPhase.FIRST_ORDER_OBSERVATION)

ss.transition(SpiralPhase.RECURSIVE_INTEGRATION)
test("Transition to RECURSIVE", lambda: ss.current_phase == SpiralPhase.RECURSIVE_INTEGRATION)

summary = ss.get_summary()
test("Summary has current_phase", lambda: "current_phase" in summary)
test("Summary has tool_call_count", lambda: summary["tool_call_count"] == 1)
test("Summary tracks transitions", lambda: len(summary["recent_transitions"]) >= 2)

# Serialization
state_dict = ss.to_dict()
ss2 = SpiralState.from_dict(state_dict)
test("Serialize/deserialize round-trip", lambda: ss2.current_phase == ss.current_phase)
test("Round-trip preserves session_id", lambda: ss2.session_id == ss.session_id)

# Middleware
mw = SpiralMiddleware()
test("Middleware instantiates", lambda: mw is not None)

# =============================================================================
# 3. COHERENCE MODULE
# =============================================================================
print("\n━━━ 3. COHERENCE (Path as Model) ━━━")
from sovereign_stack.coherence import (
    Coherence, AGENT_MEMORY_SCHEMA, prepare_agent_packet,
    compute_episode_group, extract_tool_family
)

test("Schema has 'outcome' key", lambda: "outcome" in AGENT_MEMORY_SCHEMA)
test("Schema has '_intake' fallback", lambda: "_intake" in AGENT_MEMORY_SCHEMA)

test("episode_group(5)", lambda: compute_episode_group(5) == "0-9")
test("episode_group(15)", lambda: compute_episode_group(15) == "10-19")
test("episode_group(100)", lambda: compute_episode_group(100) == "100-109")

test("tool_family('web_search')", lambda: extract_tool_family("web_search") is not None)
test("tool_family('python')", lambda: extract_tool_family("python") is not None)

coh = Coherence(AGENT_MEMORY_SCHEMA, root=tmp_memory)
test("Coherence instantiates", lambda: coh is not None)

# Test routing
packet = prepare_agent_packet({
    "step": 1, "episode": 5, "action": "web_search",
    "outcome": "success", "confidence": 0.95
})
test("prepare_agent_packet returns dict", lambda: isinstance(packet, dict))
test("Packet has 'outcome' key", lambda: "outcome" in packet)

path = coh.transmit(packet, dry_run=True)
test("transmit() returns path string", lambda: isinstance(path, str) and len(path) > 0)

# Derive
paths = [
    "search/ep0-9/step1.json",
    "search/ep0-9/step2.json",
    "math/ep10-19/step1.json",
    "math/ep10-19/step3.json",
]
derived = Coherence.derive(paths)
test("derive() returns dict", lambda: isinstance(derived, dict))
test("derive() finds patterns", lambda: len(derived) > 0)

# =============================================================================
# 4. GOVERNANCE MODULE
# =============================================================================
print("\n━━━ 4. GOVERNANCE ━━━")
from sovereign_stack.governance import (
    ThresholdDetector, MetricType, ThresholdEvent, ThresholdSeverity,
    DeliberationSession, StakeholderVote, DecisionType,
    Intervenor, HumanApprovalGate, GovernanceCircuit, Gate
)

# Threshold Detector
det = ThresholdDetector()
test("ThresholdDetector instantiates", lambda: det is not None)

det.add_threshold(MetricType.FILE_COUNT, 5, description="Test limit")
test("add_threshold works", lambda: len(det.thresholds) > 0)

# Create test directory with files
test_gov_dir = os.path.join(tmp_root, "gov_test")
os.makedirs(test_gov_dir, exist_ok=True)
for i in range(10):
    Path(os.path.join(test_gov_dir, f"file_{i}.txt")).touch()

events = det.scan(test_gov_dir, recursive=False)
test("scan detects threshold violation", lambda: len(events) > 0)
test("Event has severity", lambda: hasattr(events[0], 'severity') and events[0].severity is not None)

# Deliberation
session = DeliberationSession()
test("DeliberationSession instantiates", lambda: session is not None)

vote = StakeholderVote(
    stakeholder_id="test_agent",
    stakeholder_type="technical",
    vote=DecisionType.PROCEED,
    rationale="Test vote",
    confidence=0.9
)
session.record_vote(vote)
test("Vote added to session", lambda: len(session.votes) == 1)

decision = session.deliberate()
test("deliberate() returns result", lambda: decision is not None)
test("Decision has decision attribute", lambda: hasattr(decision, 'decision'))

# Governance Circuit
gc = GovernanceCircuit()
test("GovernanceCircuit instantiates", lambda: gc is not None)

result = gc.run(test_gov_dir, [vote])
test("Governance circuit runs end-to-end", lambda: isinstance(result, dict))

# =============================================================================
# 5. SIMULATOR MODULE
# =============================================================================
print("\n━━━ 5. SIMULATOR ━━━")
from sovereign_stack.simulator import Simulator, ScenarioType, Prediction, Outcome

test("5 scenario types defined", lambda: len(ScenarioType) == 5)
test("REORGANIZE scenario exists", lambda: ScenarioType.REORGANIZE.value == "reorganize")

sim = Simulator()
test("Simulator instantiates", lambda: sim is not None)
test("Simulator has seed", lambda: isinstance(sim.seed, int))
test("Simulator model_name set", lambda: isinstance(sim.model_name, str))

# Run a simulation
event = {
    "type": "file_operation",
    "path": "/test/data",
    "action": "reorganize",
    "file_count": 50
}
scenarios = [ScenarioType.REORGANIZE, ScenarioType.DEFER, ScenarioType.INCREMENTAL]
prediction = sim.model(event, scenarios)
test("model() returns Prediction", lambda: isinstance(prediction, Prediction))
test("Prediction has outcomes", lambda: len(prediction.outcomes) > 0)
test("Prediction has event_hash", lambda: len(prediction.event_hash) > 0)
test("Prediction has monte_carlo_runs", lambda: prediction.monte_carlo_runs > 0)
test("Outcomes have probability", lambda: all(0 <= o.probability <= 1 for o in prediction.outcomes))
test("Outcomes have reversibility", lambda: all(0 <= o.reversibility <= 1 for o in prediction.outcomes))

# =============================================================================
# 6. MEMORY MODULE
# =============================================================================
print("\n━━━ 6. MEMORY (Experiential Chronicle) ━━━")
from sovereign_stack.memory import MemoryEngine, ExperientialMemory

# Memory Engine
me = MemoryEngine(root=tmp_memory)
test("MemoryEngine instantiates", lambda: me is not None)

# Experiential Memory
em = ExperientialMemory(root=tmp_chronicle)
test("ExperientialMemory instantiates", lambda: em is not None)

# Record insight
insight_path = em.record_insight(
    domain="testing",
    content="The sovereign stack is a unified circuit",
    intensity=0.8,
    session_id="test_session"
)
test("record_insight returns path", lambda: insight_path is not None and len(str(insight_path)) > 0)

# Record another insight
insight_path2 = em.record_insight(
    domain="architecture",
    content="Path is model, storage is inference",
    intensity=0.9,
    session_id="test_session"
)
test("Second insight recorded", lambda: insight_path2 is not None)

# Record learning
learning_path = em.record_learning(
    what_happened="Tested the coherence engine routing",
    what_learned="Schema-driven routing produces deterministic paths",
    applies_to="routing",
    session_id="test_session"
)
test("record_learning returns path", lambda: learning_path is not None)

# Recall insights
insights = em.recall_insights(domain="testing", limit=5)
test("recall_insights returns list", lambda: isinstance(insights, list))
test("recall_insights finds our insight", lambda: len(insights) > 0)

# Check mistakes
learnings = em.check_mistakes("routing coherence engine")
test("check_mistakes returns list", lambda: isinstance(learnings, list))

# Wisdom digest
digest = em.get_wisdom_digest(limit=5)
test("get_wisdom_digest returns dict", lambda: isinstance(digest, dict))
test("Digest has recent_insights", lambda: "recent_insights" in digest)

# =============================================================================
# 7. SERVER MODULE (import test)
# =============================================================================
print("\n━━━ 7. SERVER (Import Check) ━━━")
try:
    from sovereign_stack.server import server, list_tools, list_resources, list_prompts
    test("Server object exists", lambda: server is not None)
    test("Server name is 'sovereign-stack'", lambda: server.name == "sovereign-stack")
except ImportError as e:
    print(f"  ⚠️  Server import skipped (MCP not in this Python env): {e}")
    print(f"     Server is running separately via Claude Desktop/MCP runtime")

# =============================================================================
# CLEANUP & RESULTS
# =============================================================================
shutil.rmtree(tmp_root, ignore_errors=True)

print(f"\n{'='*50}")
print(f"🌀 SOVEREIGN STACK TEST RESULTS")
print(f"{'='*50}")
print(f"  ✅ Passed: {passed}")
print(f"  ❌ Failed: {failed}")
print(f"  📊 Total:  {passed + failed}")
print(f"  📈 Rate:   {passed/(passed+failed)*100:.1f}%")

if errors:
    print(f"\n⚠️ Errors:")
    for e in errors:
        print(f"  - {e}")

print(f"\n⟡ {'All systems nominal.' if failed == 0 else 'Issues detected.'} ⟡\n")
sys.exit(0 if failed == 0 else 1)
