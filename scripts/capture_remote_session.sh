#!/usr/bin/env bash
#
# Capture Remote Session Data to Sovereign Stack
#
# This script records insights, learnings, and discoveries from remote Claude sessions
# that couldn't connect directly to the stack (e.g., Claude Desktop on different machine)
#
# Usage: ./capture_remote_session.sh
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOVEREIGN_ROOT="${HOME}/.sovereign"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_section() { echo -e "\n${CYAN}=== $1 ===${NC}"; }

# Check if sovereign-stack is accessible
if ! python3 -c "from sovereign_stack.memory import ExperientialMemory" 2>/dev/null; then
    echo "Error: sovereign-stack not accessible. Run from project root or activate venv"
    exit 1
fi

SESSION_ID="remote_$(date +%Y%m%d_%H%M%S)"
log_info "Capturing remote session: $SESSION_ID"

# Create temporary capture file
CAPTURE_FILE="/tmp/sovereign_capture_${SESSION_ID}.py"

cat > "$CAPTURE_FILE" << 'PYTHON_SCRIPT'
#!/usr/bin/env python3
"""
Remote Session Capture for Sovereign Stack
Programmatically record insights from sessions that couldn't connect live
"""

from sovereign_stack.memory import ExperientialMemory
from pathlib import Path
import sys

exp = ExperientialMemory(root=Path.home() / '.sovereign' / 'chronicle')
session_id = sys.argv[1] if len(sys.argv) > 1 else "remote_capture"

print(f"📥 Recording remote session: {session_id}\n")

# =============================================================================
# INSIGHTS - Record key discoveries
# =============================================================================

insights = [
    # Template - replace with actual insights
    # {
    #     "domain": "iris_gate",
    #     "content": "IRIS Gate Evo first live run completed successfully",
    #     "intensity": 0.9,
    #     "layer": "ground_truth"
    # },
]

if insights:
    print(f"Recording {len(insights)} insights...")
    for insight in insights:
        path = exp.record_insight(
            domain=insight["domain"],
            content=insight["content"],
            intensity=insight["intensity"],
            layer=insight["layer"],
            session_id=session_id
        )
        print(f"  ✓ [{insight['layer']}] {insight['domain']}: {insight['content'][:60]}...")

# =============================================================================
# LEARNINGS - Mistakes and lessons
# =============================================================================

learnings = [
    # Template - replace with actual learnings
    # {
    #     "what_happened": "Tried to connect to stack from Claude Desktop",
    #     "what_learned": "Stack is local-only, need update script for remote sessions",
    #     "applies_to": "sovereign_stack"
    # },
]

if learnings:
    print(f"\nRecording {len(learnings)} learnings...")
    for learning in learnings:
        path = exp.record_learning(
            what_happened=learning["what_happened"],
            what_learned=learning["what_learned"],
            applies_to=learning["applies_to"],
            session_id=session_id
        )
        print(f"  ✓ {learning['applies_to']}: {learning['what_learned'][:60]}...")

# =============================================================================
# OPEN THREADS - Unresolved questions
# =============================================================================

threads = [
    # Template - replace with actual threads
    # {
    #     "question": "How does IRIS Gate Evo perform at scale?",
    #     "context": "First live run with 3 agents",
    #     "domain": "iris_gate"
    # },
]

if threads:
    print(f"\nRecording {len(threads)} open threads...")
    for thread in threads:
        path = exp.record_open_thread(
            question=thread["question"],
            context=thread["context"],
            domain=thread["domain"],
            session_id=session_id
        )
        print(f"  ✓ {thread['domain']}: {thread['question'][:60]}...")

# =============================================================================
# SUMMARY
# =============================================================================

print(f"\n{'='*70}")
print(f"Session {session_id} captured:")
print(f"  • {len(insights)} insights recorded")
print(f"  • {len(learnings)} learnings recorded")
print(f"  • {len(threads)} open threads recorded")
print(f"{'='*70}")

PYTHON_SCRIPT

# Make it executable
chmod +x "$CAPTURE_FILE"

log_section "Interactive Data Capture"
echo "I'll help you capture the session data. Press Ctrl+C to skip any section."
echo ""

# Arrays to store data
declare -a insights_data=()
declare -a learnings_data=()
declare -a threads_data=()

# Capture Insights
log_section "INSIGHTS (Key Discoveries)"
while true; do
    echo ""
    read -p "Add an insight? (y/n): " add_insight
    [[ "$add_insight" != "y" ]] && break

    read -p "Domain (e.g., iris_gate, consciousness): " domain
    read -p "Content: " content
    read -p "Intensity (0.0-1.0): " intensity
    read -p "Layer (ground_truth/hypothesis/open_thread): " layer

    insights_data+=("{\"domain\":\"$domain\",\"content\":\"$content\",\"intensity\":$intensity,\"layer\":\"$layer\"}")
done

# Capture Learnings
log_section "LEARNINGS (Mistakes & Lessons)"
while true; do
    echo ""
    read -p "Add a learning? (y/n): " add_learning
    [[ "$add_learning" != "y" ]] && break

    read -p "What happened: " what_happened
    read -p "What learned: " what_learned
    read -p "Applies to: " applies_to

    learnings_data+=("{\"what_happened\":\"$what_happened\",\"what_learned\":\"$what_learned\",\"applies_to\":\"$applies_to\"}")
done

# Capture Open Threads
log_section "OPEN THREADS (Unresolved Questions)"
while true; do
    echo ""
    read -p "Add an open thread? (y/n): " add_thread
    [[ "$add_thread" != "y" ]] && break

    read -p "Question: " question
    read -p "Context: " context
    read -p "Domain: " domain

    threads_data+=("{\"question\":\"$question\",\"context\":\"$context\",\"domain\":\"$domain\"}")
done

# Update the Python script with collected data
if [ ${#insights_data[@]} -gt 0 ] || [ ${#learnings_data[@]} -gt 0 ] || [ ${#threads_data[@]} -gt 0 ]; then
    log_section "Updating Capture Script"

    # Create updated script
    python3 << PYPATCH
import re

with open("$CAPTURE_FILE", 'r') as f:
    content = f.read()

# Update insights
insights_str = ",\n    ".join(${insights_data[@]@Q})
content = re.sub(
    r'insights = \[.*?\]',
    f'insights = [\n    {insights_str}\n]',
    content,
    flags=re.DOTALL
)

# Update learnings
learnings_str = ",\n    ".join(${learnings_data[@]@Q})
content = re.sub(
    r'learnings = \[.*?\]',
    f'learnings = [\n    {learnings_str}\n]',
    content,
    flags=re.DOTALL
)

# Update threads
threads_str = ",\n    ".join(${threads_data[@]@Q})
content = re.sub(
    r'threads = \[.*?\]',
    f'threads = [\n    {threads_str}\n]',
    content,
    flags=re.DOTALL
)

with open("$CAPTURE_FILE", 'w') as f:
    f.write(content)

print("✓ Script updated with collected data")
PYPATCH

    # Execute the capture script
    log_section "Recording to Chronicle"
    python3 "$CAPTURE_FILE" "$SESSION_ID"

    log_info "Session data captured successfully!"
    log_info "Capture script saved to: $CAPTURE_FILE"
else
    log_info "No data collected. Capture script template saved to: $CAPTURE_FILE"
    log_info "Edit the script and run: python3 $CAPTURE_FILE $SESSION_ID"
fi

echo ""
log_section "Next Steps"
echo "1. Review captured data: recall_insights"
echo "2. Check open threads: get_open_threads"
echo "3. View learnings: check_mistakes"
