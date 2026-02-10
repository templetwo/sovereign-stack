#!/usr/bin/env python3
"""
Quick Capture - Drop session data into sovereign-stack

For when Claude Desktop (or any remote Claude) can't connect to the stack directly.
Edit the data arrays below and run this script.

Usage: python3 quick_capture.py
"""

import sys
from pathlib import Path

# Add parent directory to path so we can import sovereign_stack
sys.path.insert(0, str(Path(__file__).parent.parent))

from sovereign_stack.memory import ExperientialMemory
from datetime import datetime

exp = ExperientialMemory(root=Path.home() / '.sovereign' / 'chronicle')
session_id = f"remote_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

print(f"📥 Quick Capture Session: {session_id}\n")

# =============================================================================
# EDIT BELOW THIS LINE
# =============================================================================

# INSIGHTS - Key discoveries from the session
insights = [
    {
        "domain": "sovereign_stack",
        "content": "Claude Desktop attempted MCP connection but stack is local-only (Mac). Need capture script for remote sessions.",
        "intensity": 0.8,
        "layer": "ground_truth"
    },
    # Add more insights here
    # {
    #     "domain": "iris_gate",
    #     "content": "Your insight here",
    #     "intensity": 0.9,
    #     "layer": "ground_truth"  # or "hypothesis" or "open_thread"
    # },
]

# LEARNINGS - What happened → What learned
learnings = [
    {
        "what_happened": "Claude Desktop tried to connect to sovereign-stack MCP server but got 'Neither local nor remote is reachable'",
        "what_learned": "Stack runs on Mac Studio, not accessible from other environments. Need async capture workflow for remote sessions.",
        "applies_to": "sovereign_stack"
    },
    # Add more learnings here
    # {
    #     "what_happened": "...",
    #     "what_learned": "...",
    #     "applies_to": "context"
    # },
]

# OPEN THREADS - Unresolved questions
threads = [
    {
        "question": "Should sovereign-stack support remote SSE connections from Claude Desktop on different machines?",
        "context": "Currently local-only. Tunnel exists (stack.templetwo.com) but may need auth/security for multi-device access.",
        "domain": "sovereign_stack"
    },
    # Add more threads here
    # {
    #     "question": "...",
    #     "context": "...",
    #     "domain": "..."
    # },
]

# BREAKTHROUGHS (optional) - Significant moments
breakthroughs = [
    # {
    #     "description": "..."
    # },
]

# =============================================================================
# DO NOT EDIT BELOW THIS LINE
# =============================================================================

# Record insights
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
        print(f"  ✓ [{insight['layer']}] {insight['domain']}")

# Record learnings
if learnings:
    print(f"\nRecording {len(learnings)} learnings...")
    for learning in learnings:
        path = exp.record_learning(
            what_happened=learning["what_happened"],
            what_learned=learning["what_learned"],
            applies_to=learning["applies_to"],
            session_id=session_id
        )
        print(f"  ✓ {learning['applies_to']}")

# Record threads
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

# Summary
print(f"\n{'='*70}")
print(f"✓ Session {session_id} captured")
print(f"  • {len(insights)} insights")
print(f"  • {len(learnings)} learnings")
print(f"  • {len(threads)} open threads")
print(f"{'='*70}")
print(f"\nData saved to ~/.sovereign/chronicle/")
print(f"\nVerify with:")
print(f"  recall_insights --limit 5")
print(f"  get_open_threads --limit 5")
