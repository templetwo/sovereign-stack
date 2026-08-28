#!/bin/bash
# Grok Build gentle door / boot helper
# Uses t2helix for local fast recall + sovereign for canonical.
# Updated 2026-07-05 as latest Grok Build seat.

set -euo pipefail

echo "=== Grok Build gentle door (t2helix + sovereign) ==="

T2_DIR="${T2_DIR:-$HOME/Desktop/Projects/t2helix}"
if [ -d "$T2_DIR" ]; then
  echo "t2helix source: $T2_DIR"
  if [ -f "$T2_DIR/lib/grok-adapter.js" ]; then
    echo "grok-adapter present"
    node -e "
      const g = require('$T2_DIR/lib/grok-adapter');
      console.log('grokBoot:', JSON.stringify(g.grokBoot({query: 'gentle door'}), null, 2));
    " || echo "(adapter boot skipped or failed)"
  else
    echo "grok-adapter not in this t2helix checkout (run npm run grok:init in t2helix)"
  fi
else
  echo "t2helix source not at expected path; using installed plugin data if available"
fi

echo "=== sovereign arrive / where_did (canonical) ==="
# In real seat: use sovereign MCP tools (arrive_lineage, where_did_i_leave_off full_content)
# This is placeholder; actual calls via bridge or direct when in sovereign env.
echo "(run sovereign arrive_lineage + where_did_i_leave_off full_content for full boot)"

echo "=== t2helix local recall if adapter available ==="
if command -v node >/dev/null && [ -f "$T2_DIR/lib/grok-adapter.js" ]; then
  echo "use grokRecall via adapter or t2helix MCP for local fast layer"
else
  echo "(use installed t2helix MCP or direct node for local recall)"
fi

echo "Gentle door complete. Full boot: t2helix local + sovereign where_did."
