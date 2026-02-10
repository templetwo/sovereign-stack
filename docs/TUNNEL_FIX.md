# Cloudflare Tunnel SSE Timeout Fix

## Problem

The Cloudflare tunnel at `stack.templetwo.com` returns 503 errors because it times out long-lived SSE (Server-Sent Events) connections.

**Root Cause:** Cloudflare's tunnel proxy drops SSE streams after the default timeout period.

**Status:** Local SSE works perfectly (localhost:3434). Only remote tunnel connections fail.

---

## Solution Options

### Option 1: Configure Tunnel Keepalive (Quick Fix)

Add `--proxy-keepalive-timeout` flag to tunnel configuration.

**Update ~/.cloudflared/config.yml:**
```yaml
tunnel: 9880c855-27dd-4e25-9bd4-e72438bdcb0b
credentials-file: /Users/tony_studio/.cloudflared/9880c855-27dd-4e25-9bd4-e72438bdcb0b.json

# Add proxy keepalive settings
proxy-keepalive-connections: 100
proxy-keepalive-timeout: 300s  # 5 minutes
no-tls-verify: false

ingress:
  - hostname: stack.templetwo.com
    service: http://localhost:3434
    # Optional: Add origin request timeout
    originRequest:
      noTLSVerify: false
      connectTimeout: 30s
      keepAliveTimeout: 300s
  - service: http_status:404
```

**Restart tunnel:**
```bash
./scripts/manage stop
cloudflared tunnel run sovereign-stack
```

**Pros:**
- Quick fix, minimal code changes
- Keeps existing SSE transport

**Cons:**
- May still have timeout issues with very long connections
- Cloudflare proxies aren't optimized for SSE

---

### Option 2: Switch to WebSocket Transport (Robust Fix)

Replace SSE with WebSocket for better proxy compatibility.

**Changes Required:**

1. **Update sse_server.py** to support WebSocket alongside SSE
2. **Add WebSocket handler** using `starlette.websockets`
3. **Update MCP client libraries** to support WebSocket transport
4. **Update connectivity guide** with WebSocket instructions

**Example Implementation:**
```python
from starlette.websockets import WebSocket

@app.websocket_route("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    # MCP over WebSocket implementation
    # ...
```

**Pros:**
- WebSockets designed for long-lived bidirectional connections
- Better proxy/tunnel compatibility
- Standard protocol for real-time communication

**Cons:**
- Requires MCP SDK changes
- More complex than SSE
- Need to maintain both transports (SSE for local, WS for remote)

---

### Option 3: Hybrid Approach (Recommended)

Keep SSE for local connections, add WebSocket for remote.

**Architecture:**
```
Local clients (Claude Desktop on Mac)
  └─> SSE: http://localhost:3434/sse

Remote clients (claude.ai, other devices)
  └─> WebSocket: wss://stack.templetwo.com/ws
      └─> Cloudflare Tunnel
          └─> WebSocket: http://localhost:3434/ws
```

**Benefits:**
- Best of both worlds
- SSE for simple local use
- WebSocket for robust remote access
- Gradual migration path

---

## Implementation Checklist

### Quick Fix (Keepalive)
- [ ] Update ~/.cloudflared/config.yml with keepalive settings
- [ ] Restart tunnel
- [ ] Test remote connection: `curl https://stack.templetwo.com/health`
- [ ] Test SSE: `curl -N https://stack.templetwo.com/sse`
- [ ] Update documentation if successful

### WebSocket Migration (Future)
- [ ] Research MCP WebSocket transport spec
- [ ] Add WebSocket endpoint to sse_server.py
- [ ] Implement WS handler with MCP protocol
- [ ] Create WS client bridge script
- [ ] Test local WebSocket connection
- [ ] Test remote WebSocket through tunnel
- [ ] Update CLAUDE_AI_CONNECTIVITY_GUIDE.md
- [ ] Deprecate pure SSE remote access

---

## Testing Commands

**Test local SSE:**
```bash
curl -N -s --max-time 10 http://localhost:3434/sse | head -5
```

**Test remote SSE (currently fails):**
```bash
curl -N -s --max-time 10 https://stack.templetwo.com/sse | head -5
```

**After implementing WebSocket:**
```bash
# Install wscat: npm install -g wscat
wscat -c wss://stack.templetwo.com/ws
```

---

## References

- Cloudflare Tunnel Config: https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/configuration/
- MCP Transport Spec: https://spec.modelcontextprotocol.io/specification/2024-11-05/transport/
- Starlette WebSockets: https://www.starlette.io/websockets/

---

**Status:** Documented for future implementation
**Priority:** Medium (local access works fine, remote is nice-to-have)
**Assigned:** Deferred to future session
