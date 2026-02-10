#!/usr/bin/env bash
#
# Sovereign Stack Bridge for claude.ai
# Connects claude.ai computer use container to sovereign-stack MCP server
#
# Usage: curl -sSL https://raw.githubusercontent.com/templetwo/sovereign-stack/main/clients/claude-ai-bridge.sh | bash
#

set -euo pipefail

BASE_URL="${SOVEREIGN_STACK_URL:-http://localhost:3434}"
TOOL_NAME="${1:-spiral_status}"
TOOL_ARGS="${2:-{}}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Step 1: Health check
log_info "Checking sovereign-stack health..."
if ! HEALTH=$(curl -s -m 5 "$BASE_URL/health" 2>/dev/null); then
    log_error "Cannot reach sovereign-stack at $BASE_URL"
    log_info "Make sure sovereign-stack SSE server is running:"
    log_info "  sovereign-sse"
    exit 1
fi

if echo "$HEALTH" | grep -q "healthy"; then
    log_info "Stack is healthy: $HEALTH"
else
    log_error "Stack returned unexpected health: $HEALTH"
    exit 1
fi

# Step 2: Establish SSE connection
log_info "Establishing SSE connection..."
exec 3< <(curl -s -N --no-buffer --max-time 60 "$BASE_URL/sse" 2>/dev/null)

SESSION_ID=""
while IFS= read -r -t 8 line <&3; do
    line="${line//$'\r'/}"
    if [[ "$line" == data:*session_id* ]]; then
        SESSION_ID="${line#data: }"
        break
    fi
done

if [[ -z "$SESSION_ID" ]]; then
    log_error "Failed to get session ID from SSE endpoint"
    exec 3<&-
    exit 1
fi

ENDPOINT="$BASE_URL$SESSION_ID"
log_info "Session established: $ENDPOINT"

# Step 3: Initialize MCP connection
log_info "Initializing MCP protocol..."
curl -s -X POST "$ENDPOINT" -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"claude-ai-bridge","version":"1.0"}}}' > /dev/null

sleep 2

# Consume initialize response
while IFS= read -r -t 3 line <&3; do
    line="${line//$'\r'/}"
    [[ "$line" == data:*\"id\":1* ]] && break
done

# Send initialized notification
curl -s -X POST "$ENDPOINT" -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","method":"notifications/initialized"}' > /dev/null

sleep 1

# Step 4: Call the tool
log_info "Calling tool: $TOOL_NAME with args: $TOOL_ARGS"
curl -s -X POST "$ENDPOINT" -H "Content-Type: application/json" \
    -d "{\"jsonrpc\":\"2.0\",\"id\":99,\"method\":\"tools/call\",\"params\":{\"name\":\"$TOOL_NAME\",\"arguments\":$TOOL_ARGS}}" > /dev/null

# Step 5: Read response
log_info "Reading response..."
RESPONSE_FOUND=false
while IFS= read -r -t 15 line <&3; do
    line="${line//$'\r'/}"
    if [[ "$line" == data:*\"id\":99* ]]; then
        RESULT="${line#data: }"
        RESPONSE_FOUND=true
        echo ""
        log_info "Response received:"
        echo "$RESULT" | python3 -m json.tool 2>/dev/null || echo "$RESULT"
        break
    fi
done

if [[ "$RESPONSE_FOUND" == "false" ]]; then
    log_warn "No response received within timeout"
fi

exec 3<&-
log_info "Connection closed"
