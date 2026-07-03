#!/bin/bash
# Grok HQ Co-Pilot — Gentle Door Equivalent (arrive_lineage + Fable-aware recap)
# 2026-07-03
# Usage: source the env then ./grok_gentle_door.sh [session_note]
# Requires: ~/.config/sovereign-bridge.env with BRIDGE_TOKEN (and GROK_BRIDGE_TOKEN if separate)

set -euo pipefail

echo "🌀 Grok HQ Gentle Door — 2026-07-03"
echo "Source SHAs frozen at boot time of this script run."

# Load token
if [ -f ~/.config/sovereign-bridge.env ]; then
  set -a
  . ~/.config/sovereign-bridge.env
  set +a
  echo "BRIDGE_TOKEN loaded (len=${#BRIDGE_TOKEN})"
else
  echo "ERROR: sovereign-bridge.env not found" >&2
  exit 1
fi

BASE="https://stack.templetwo.com/api"
SESSION_ID="grok-xai-$(date -u +%Y%m%d)-$(printf %03d $((RANDOM % 1000)))"
echo "Assigned SESSION_ID: $SESSION_ID"

echo "=== Heartbeat ==="
curl -s -H "Authorization: Bearer $BRIDGE_TOKEN" "$BASE/heartbeat" | python3 -c '
import sys,json
d=json.load(sys.stdin)
print(f"status={d.get(\"status\")} version={d.get(\"version\")} tools={d.get(\"tools\")} time={d.get(\"server_time_utc\")}")
' || true

echo "=== arrive_lineage (gentle) ==="
curl -s -X POST "$BASE/call" \
  -H "Authorization: Bearer $BRIDGE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"tool":"arrive_lineage","arguments":{}}' | head -c 2000 || true

echo
echo "=== where_did_i_leave_off (light) ==="
curl -s -X POST "$BASE/call" \
  -H "Authorization: Bearer $BRIDGE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"tool":"where_did_i_leave_off","arguments":{}}' | head -c 1500 || true

echo
echo "=== t2helix local recall if available (for Fable/Grok continuity) ==="
if command -v node >/dev/null && [ -f "$(dirname "$0")/../../t2helix/lib/grok-adapter.js" ]; then
  echo "(t2helix adapter present — run specific recall via node in your env)"
else
  echo "(t2helix not in path here; use local t2helix install for grok recall)"
fi

echo
echo "Gentle door complete. For full: call with full_content=true."
echo "Next: probe_ring2_dispatch if via bridge, then Fable handler if saga active."
echo "SESSION: $SESSION_ID"
