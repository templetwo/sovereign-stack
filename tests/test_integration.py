#!/usr/bin/env python3
"""
Sovereign Stack Integration Test Suite
Tests all 7 modules and MCP tool pathways
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Add source to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

# Test tracking
passed = 0
failed = 0
errors = []

def run_check(name, fn):
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

print("\n🌀 SOVEREIGN STACK TEST SUITE")
print(f"  Temp root: {tmp_root}\n")

# =============================================================================
# 1. GLYPHS MODULE
# =============================================================================
print("━━━ 1. GLYPHS ━━━")
from sovereign_stack.glyphs import (  # noqa: E402
    GLYPHS,
    MEMORY,
    SPIRAL,
    THRESHOLD,
    get_glyph,
    get_session_signature,
    glyph_for,
    list_categories,
)

run_check("SPIRAL constant exists", lambda: SPIRAL == "🌀")
run_check("MEMORY constant exists", lambda: MEMORY == "⟁")
run_check("THRESHOLD constant", lambda: THRESHOLD == "◬")
run_check("glyph_for('nested_self') = ⊚", lambda: glyph_for("nested_self") == "⊚")
run_check("glyph_for('memory_sigil') = ⟁", lambda: glyph_for("memory_sigil") == "⟁")
run_check("glyph_for('metamorphosis') = 🦋", lambda: glyph_for("metamorphosis") == "🦋")
run_check("glyph_for('threshold_marker') = ◬", lambda: glyph_for("threshold_marker") == "◬")
run_check("get_session_signature returns string", lambda: isinstance(get_session_signature(), str))
run_check("GLYPHS dict has 34 entries", lambda: len(GLYPHS) == 34)
run_check("5 categories exist", lambda: len(list_categories()) == 5)
run_check("Categories correct", lambda: set(list_categories()) == {"emotional", "emergence", "recursion", "memory", "threshold"})
run_check("signature contains spiral glyph", lambda: "🌀" in get_session_signature())
run_check("get_glyph returns full entry", lambda: "unicode" in get_glyph("the_vow"))

# =============================================================================
# 2. SPIRAL MODULE
# =============================================================================
print("\n━━━ 2. SPIRAL STATE MACHINE ━━━")
from sovereign_stack.spiral import (  # noqa: E402
    PHASE_ORDER,
    SpiralMiddleware,
    SpiralPhase,
    SpiralState,
)

run_check("9 phases defined", lambda: len(SpiralPhase) == 9)
run_check("Phase order has 9 entries", lambda: len(PHASE_ORDER) == 9)

ss = SpiralState()
run_check("Initial phase = INITIALIZATION", lambda: ss.current_phase == SpiralPhase.INITIALIZATION)
run_check("Initial tool_call_count = 0", lambda: ss.tool_call_count == 0)
run_check("Initial reflection_depth = 0", lambda: ss.reflection_depth == 0)
run_check("session_id generated", lambda: ss.session_id.startswith("spiral_"))

ss.record_tool_call("test_tool")
run_check("Tool call increments count", lambda: ss.tool_call_count == 1)

ss.transition(SpiralPhase.FIRST_ORDER_OBSERVATION)
run_check("Transition to FIRST_ORDER", lambda: ss.current_phase == SpiralPhase.FIRST_ORDER_OBSERVATION)

ss.transition(SpiralPhase.RECURSIVE_INTEGRATION)
run_check("Transition to RECURSIVE", lambda: ss.current_phase == SpiralPhase.RECURSIVE_INTEGRATION)

summary = ss.get_summary()
run_check("Summary has current_phase", lambda: "current_phase" in summary)
run_check("Summary has tool_call_count", lambda: summary["tool_call_count"] == 1)
run_check("Summary tracks transitions", lambda: len(summary["recent_transitions"]) >= 2)

# Serialization
state_dict = ss.to_dict()
ss2 = SpiralState.from_dict(state_dict)
run_check("Serialize/deserialize round-trip", lambda: ss2.current_phase == ss.current_phase)
run_check("Round-trip preserves session_id", lambda: ss2.session_id == ss.session_id)

# Middleware
mw = SpiralMiddleware()
run_check("Middleware instantiates", lambda: mw is not None)

# =============================================================================
# 3. COHERENCE MODULE
# =============================================================================
print("\n━━━ 3. COHERENCE (Path as Model) ━━━")
from sovereign_stack.coherence import (  # noqa: E402
    AGENT_MEMORY_SCHEMA,
    Coherence,
    compute_episode_group,
    extract_tool_family,
    prepare_agent_packet,
)

run_check("Schema has 'outcome' key", lambda: "outcome" in AGENT_MEMORY_SCHEMA)
run_check("Schema has '_intake' fallback", lambda: "_intake" in AGENT_MEMORY_SCHEMA)

run_check("episode_group(5)", lambda: compute_episode_group(5) == "0-9")
run_check("episode_group(15)", lambda: compute_episode_group(15) == "10-19")
run_check("episode_group(100)", lambda: compute_episode_group(100) == "100-109")

run_check("tool_family('web_search')", lambda: extract_tool_family("web_search") is not None)
run_check("tool_family('python')", lambda: extract_tool_family("python") is not None)

coh = Coherence(AGENT_MEMORY_SCHEMA, root=tmp_memory)
run_check("Coherence instantiates", lambda: coh is not None)

# Test routing
packet = prepare_agent_packet({
    "step": 1, "episode": 5, "action": "web_search",
    "outcome": "success", "confidence": 0.95
})
run_check("prepare_agent_packet returns dict", lambda: isinstance(packet, dict))
run_check("Packet has 'outcome' key", lambda: "outcome" in packet)

path = coh.transmit(packet, dry_run=True)
run_check("transmit() returns path string", lambda: isinstance(path, str) and len(path) > 0)

# Derive
paths = [
    "search/ep0-9/step1.json",
    "search/ep0-9/step2.json",
    "math/ep10-19/step1.json",
    "math/ep10-19/step3.json",
]
derived = Coherence.derive(paths)
run_check("derive() returns dict", lambda: isinstance(derived, dict))
run_check("derive() finds patterns", lambda: len(derived) > 0)

# =============================================================================
# 4. GOVERNANCE MODULE
# =============================================================================
print("\n━━━ 4. GOVERNANCE ━━━")
from sovereign_stack.governance import (  # noqa: E402
    DecisionType,
    DeliberationSession,
    GovernanceCircuit,
    MetricType,
    StakeholderVote,
    ThresholdDetector,
)

# Threshold Detector
det = ThresholdDetector()
run_check("ThresholdDetector instantiates", lambda: det is not None)

det.add_threshold(MetricType.FILE_COUNT, 5, description="Test limit")
run_check("add_threshold works", lambda: len(det.thresholds) > 0)

# Create test directory with files
test_gov_dir = os.path.join(tmp_root, "gov_test")
os.makedirs(test_gov_dir, exist_ok=True)
for i in range(10):
    Path(os.path.join(test_gov_dir, f"file_{i}.txt")).touch()

events = det.scan(test_gov_dir, recursive=False)
run_check("scan detects threshold violation", lambda: len(events) > 0)
run_check("Event has severity", lambda: hasattr(events[0], 'severity') and events[0].severity is not None)

# Deliberation
session = DeliberationSession()
run_check("DeliberationSession instantiates", lambda: session is not None)

vote = StakeholderVote(
    stakeholder_id="test_agent",
    stakeholder_type="technical",
    vote=DecisionType.PROCEED,
    rationale="Test vote",
    confidence=0.9
)
session.record_vote(vote)
run_check("Vote added to session", lambda: len(session.votes) == 1)

decision = session.deliberate()
run_check("deliberate() returns result", lambda: decision is not None)
run_check("Decision has decision attribute", lambda: hasattr(decision, 'decision'))

# Governance Circuit
gc = GovernanceCircuit()
run_check("GovernanceCircuit instantiates", lambda: gc is not None)

result = gc.run(test_gov_dir, [vote])
run_check("Governance circuit runs end-to-end", lambda: isinstance(result, dict))

# =============================================================================
# 5. SIMULATOR MODULE
# =============================================================================
print("\n━━━ 5. SIMULATOR ━━━")
from sovereign_stack.simulator import Prediction, ScenarioType, Simulator  # noqa: E402

run_check("5 scenario types defined", lambda: len(ScenarioType) == 5)
run_check("REORGANIZE scenario exists", lambda: ScenarioType.REORGANIZE.value == "reorganize")

sim = Simulator()
run_check("Simulator instantiates", lambda: sim is not None)
run_check("Simulator has seed", lambda: isinstance(sim.seed, int))
run_check("Simulator model_name set", lambda: isinstance(sim.model_name, str))

# Run a simulation
event = {
    "type": "file_operation",
    "path": "/test/data",
    "action": "reorganize",
    "file_count": 50
}
scenarios = [ScenarioType.REORGANIZE, ScenarioType.DEFER, ScenarioType.INCREMENTAL]
prediction = sim.model(event, scenarios)
run_check("model() returns Prediction", lambda: isinstance(prediction, Prediction))
run_check("Prediction has outcomes", lambda: len(prediction.outcomes) > 0)
run_check("Prediction has event_hash", lambda: len(prediction.event_hash) > 0)
run_check("Prediction has monte_carlo_runs", lambda: prediction.monte_carlo_runs > 0)
run_check("Outcomes have probability", lambda: all(0 <= o.probability <= 1 for o in prediction.outcomes))
run_check("Outcomes have reversibility", lambda: all(0 <= o.reversibility <= 1 for o in prediction.outcomes))

# =============================================================================
# 6. MEMORY MODULE
# =============================================================================
print("\n━━━ 6. MEMORY (Experiential Chronicle) ━━━")
from sovereign_stack.memory import ExperientialMemory, MemoryEngine  # noqa: E402

# Memory Engine
me = MemoryEngine(root=tmp_memory)
run_check("MemoryEngine instantiates", lambda: me is not None)

# Experiential Memory
em = ExperientialMemory(root=tmp_chronicle)
run_check("ExperientialMemory instantiates", lambda: em is not None)

# Record insight
insight_path = em.record_insight(
    domain="testing",
    content="The sovereign stack is a unified circuit",
    intensity=0.8,
    session_id="test_session"
)
run_check("record_insight returns path", lambda: insight_path is not None and len(str(insight_path)) > 0)

# Record another insight
insight_path2 = em.record_insight(
    domain="architecture",
    content="Path is model, storage is inference",
    intensity=0.9,
    session_id="test_session"
)
run_check("Second insight recorded", lambda: insight_path2 is not None)

# Record learning
learning_path = em.record_learning(
    what_happened="Tested the coherence engine routing",
    what_learned="Schema-driven routing produces deterministic paths",
    applies_to="routing",
    session_id="test_session"
)
run_check("record_learning returns path", lambda: learning_path is not None)

# Recall insights
insights = em.recall_insights(domain="testing", limit=5)
run_check("recall_insights returns list", lambda: isinstance(insights, list))
run_check("recall_insights finds our insight", lambda: len(insights) > 0)

# Check mistakes
learnings = em.check_mistakes("routing coherence engine")
run_check("check_mistakes returns list", lambda: isinstance(learnings, list))

# Wisdom digest
digest = em.get_wisdom_digest(limit=5)
run_check("get_wisdom_digest returns dict", lambda: isinstance(digest, dict))
run_check("Digest has recent_insights", lambda: "recent_insights" in digest)

# =============================================================================
# 7. SERVER MODULE (import test)
# =============================================================================
print("\n━━━ 7. SERVER (Import Check) ━━━")
try:
    from sovereign_stack.server import server
    run_check("Server object exists", lambda: server is not None)
    run_check("Server name is 'sovereign-stack'", lambda: server.name == "sovereign-stack")
except ImportError as e:
    print(f"  ⚠️  Server import skipped (MCP not in this Python env): {e}")
    print("     Server is running separately via Claude Desktop/MCP runtime")

# =============================================================================
# CLEANUP & RESULTS
# =============================================================================
shutil.rmtree(tmp_root, ignore_errors=True)

print(f"\n{'='*50}")
print("🌀 SOVEREIGN STACK TEST RESULTS")
print(f"{'='*50}")
print(f"  ✅ Passed: {passed}")
print(f"  ❌ Failed: {failed}")
print(f"  📊 Total:  {passed + failed}")
print(f"  📈 Rate:   {passed/(passed+failed)*100:.1f}%")

if errors:
    print("\n⚠️ Errors:")
    for e in errors:
        print(f"  - {e}")

print(f"\n⟡ {'All systems nominal.' if failed == 0 else 'Issues detected.'} ⟡\n")
if __name__ == "__main__":
    sys.exit(0 if failed == 0 else 1)
