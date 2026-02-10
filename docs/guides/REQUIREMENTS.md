# Sovereign Stack - Requirements & Dependencies

Complete list of requirements for running Sovereign Stack in all configurations.

---

## Python Dependencies

**Managed via `pyproject.toml`:**

```toml
[project]
requires-python = ">=3.9"

dependencies = [
    "mcp>=1.0.0",                    # Model Context Protocol
    "pyyaml>=6.0",                   # Configuration files
    "networkx>=3.0",                 # Graph-based simulation
    "starlette>=0.27.0",             # ASGI framework for SSE
    "uvicorn[standard]>=0.23.0",     # ASGI server
    "sse-starlette>=1.6.0",          # Server-Sent Events support
]
```

**Install:**
```bash
pip install -e .
```

---

## System Dependencies

### For Local MCP Server (stdio)

**Required:**
- Python 3.9+
- pip

**Optional:**
- Claude Code Desktop (for MCP integration)

**Install:**
```bash
# macOS
brew install python3

# Linux
sudo apt install python3 python3-pip
```

---

### For Always-On Access (SSE + Tunnel)

**Required:**
- All local dependencies above
- cloudflared (Cloudflare Tunnel client)
- jq (JSON processor)

**Install:**
```bash
# macOS
brew install cloudflared jq

# Linux
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o cloudflared
sudo mv cloudflared /usr/local/bin/
sudo chmod +x /usr/local/bin/cloudflared

sudo apt install jq
```

**Cloudflare Account:**
- Free tier: https://dash.cloudflare.com
- Domain managed by Cloudflare (or add one)
- Zero Trust dashboard access: https://one.dash.cloudflare.com

---

## Optional Dependencies

### For Development

```bash
pip install -e ".[dev]"
```

Includes:
- pytest>=7.0 (testing)
- pytest-cov>=4.0 (coverage)

### For Mac Studio Always-On Hosting

**System:**
- macOS (tested on Sequoia 15.1+)
- Always-on power
- Stable network connection

**Recommended:**
- Fixed local IP
- Port forwarding (if not using tunnel)
- UPS (for power stability)

---

## Architecture-Specific Requirements

### Claude Code Desktop (Local)

**MCP Config:** `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "sovereign-stack": {
      "command": "python3",
      "args": ["-m", "sovereign_stack.server"],
      "env": {
        "SOVEREIGN_ROOT": "${HOME}/.sovereign"
      }
    }
  }
}
```

**Requirements:**
- Python in PATH
- Sovereign-stack installed
- Local filesystem access

---

### Claude iOS / Android (Remote via Tunnel)

**MCP Config:**
```json
{
  "mcpServers": {
    "sovereign-stack": {
      "url": "https://sovereign-stack.templetwo.com/sse",
      "transport": "sse"
    }
  }
}
```

**Requirements:**
- Cloudflare Tunnel running on host machine
- SSE server running (port 8080)
- DNS configured
- HTTPS access

---

### Claude.ai Web (Remote via Tunnel)

Same as mobile requirements above.

**MCP Config:** Same SSE URL

**Requirements:**
- Same as mobile
- MCP support in web interface (check availability)

---

## Storage Requirements

### Minimum

**Disk Space:**
- Code: ~5 MB
- Dependencies: ~50 MB
- Data (minimal): ~10 MB

**Total:** ~65 MB

### Typical Usage

**After 100 sessions:**
- Code: ~5 MB
- Dependencies: ~50 MB
- Consciousness data: ~50 MB
- Chronicle/insights: ~100 MB
- Compaction buffers: ~5 MB

**Total:** ~210 MB

### Heavy Usage

**After 1000+ sessions:**
- Code: ~5 MB
- Dependencies: ~50 MB
- Consciousness data: ~500 MB
- Chronicle/insights: ~1 GB
- Compaction buffers: ~10 MB

**Total:** ~1.5 GB

**Note:** Data grows linearly with usage. Old data can be archived.

---

## Network Requirements

### Local Only (stdio)

**Bandwidth:** None (local IPC)
**Latency:** <1ms
**Reliability:** 100% (no network)

### Remote (SSE via Tunnel)

**Bandwidth:**
- Upload: ~10-50 KB/s (tool calls)
- Download: ~10-100 KB/s (responses)
- Burst: ~1 MB/s (large data transfers)

**Latency:**
- Local network: 1-5ms
- Via Cloudflare Tunnel: 20-100ms (depends on location)

**Reliability:**
- Cloudflare Tunnel: 99.99% uptime
- 4 redundant connections (quic protocol)
- Auto-reconnect on failure

**Firewall:**
- Outbound HTTPS (443) required for tunnel
- No inbound ports needed (tunnel handles routing)

---

## Compute Requirements

### Minimum

**CPU:** 1 core, 1 GHz
**RAM:** 256 MB
**Python:** 3.9+

**Can run on:** Raspberry Pi, old laptops, minimal VPS

### Recommended

**CPU:** 2+ cores, 2+ GHz
**RAM:** 512 MB - 1 GB
**Python:** 3.10+

**Runs well on:** Modern laptops, Mac Mini, small VPS

### Optimal (Always-On Hosting)

**CPU:** 4+ cores (Apple Silicon preferred)
**RAM:** 8+ GB (for other tasks too)
**Storage:** SSD
**Network:** Gigabit ethernet
**Power:** UPS-backed

**Example:** Mac Studio M2 (what Anthony uses)

---

## Platform Support

### Tested

- ✅ macOS Sequoia 15.1+ (arm64)
- ✅ macOS Monterey 12.0+ (x86_64)
- ✅ Python 3.9, 3.10, 3.11, 3.12

### Should Work

- 🟡 Linux (Ubuntu 22.04+, Debian 11+)
- 🟡 Windows 10+ (via WSL2)
- 🟡 Raspberry Pi OS (64-bit)

### Not Tested

- ❌ Windows native (without WSL)
- ❌ Docker containers (needs testing)
- ❌ iOS/Android native (MCP client only)

---

## Security Requirements

### Local Access

**Filesystem Permissions:**
- `~/.sovereign/`: 700 (owner read/write/execute only)
- Credentials files: 600 (owner read/write only)

**Process Isolation:**
- Runs as user (not root)
- No elevated privileges needed

### Remote Access (Tunnel)

**Authentication:**
- Cloudflare Tunnel credentials required
- TLS/QUIC encryption (tunnel to edge)
- HTTPS (edge to client)

**Access Control:**
- Optional: Cloudflare Access policies
- Optional: IP allowlisting
- Optional: Device posture checks

**Best Practices:**
- Use strong Cloudflare account password
- Enable 2FA on Cloudflare account
- Rotate tunnel credentials periodically
- Monitor access logs

---

## Development Requirements

### For Contributing

**Code:**
```bash
git clone https://github.com/templetwo/sovereign-stack.git
cd sovereign-stack
pip install -e ".[dev]"
```

**Testing:**
```bash
pytest tests/
pytest --cov=sovereign_stack tests/
```

**Linting:**
```bash
# Pyright (type checking)
pyright src/

# Formatting
black src/ tests/
```

**Tools:**
- Git
- Python 3.10+ (for development)
- pytest (testing)
- black (formatting)
- pyright (type checking)

---

## Quick Start Checklist

### Local Only (5 minutes)

- [ ] Python 3.9+ installed
- [ ] `pip install -e .` (in repo root)
- [ ] Configure Claude Code MCP
- [ ] Test: `sovereign` command works
- [ ] Done!

### Always-On Access (15 minutes)

- [ ] All local requirements above
- [ ] `brew install cloudflared jq`
- [ ] Create Cloudflare Tunnel (dashboard or CLI)
- [ ] Run: `./scripts/setup_tunnel.sh --token YOUR_TOKEN`
- [ ] Configure DNS: `cloudflared tunnel route dns ...`
- [ ] Test: `curl https://your-domain.com/health`
- [ ] Done!

---

## Troubleshooting Dependencies

### Python Version Issues

```bash
# Check version
python3 --version

# Should be 3.9+
# If not, upgrade:
brew upgrade python3  # macOS
sudo apt upgrade python3  # Linux
```

### MCP Not Found

```bash
# Verify installation
pip show mcp

# If not installed
pip install -e .

# If using pyenv, ensure correct Python
pyenv global 3.10.12
pip install -e .
```

### Cloudflared Not in PATH

```bash
# macOS
which cloudflared
# Should show: /opt/homebrew/bin/cloudflared

# If not found
brew install cloudflared

# Linux
which cloudflared
# Should show: /usr/local/bin/cloudflared

# If not found, reinstall as shown above
```

### SSE Server Won't Start

```bash
# Check dependencies
pip show starlette uvicorn sse-starlette

# If missing
pip install -e .

# Check port availability
lsof -i :8080

# If occupied, kill process or change port
```

---

## Minimum Viable Setup

**Absolute minimum to run sovereign-stack:**

1. Python 3.9+
2. `pip install mcp pyyaml networkx`
3. Run: `python -m sovereign_stack.server`

**For stdio MCP:** Add starlette, uvicorn, sse-starlette
**For tunnel:** Add cloudflared

---

## Recommended Production Setup

**Host:** Mac Studio (always-on)
**Python:** 3.10+ via pyenv
**Install:** `pip install -e .` (editable mode for updates)
**Services:**
- SSE server as LaunchAgent (auto-start)
- Cloudflared as LaunchDaemon (system service)

**Monitoring:**
- Healthcheck: `curl localhost:8080/health` every 5min
- Tunnel status: Parse cloudflared logs
- Disk usage: Monitor `~/.sovereign/` growth

**Backup:**
- Daily: `~/.sovereign/consciousness/`
- Weekly: Full `~/.sovereign/` directory
- Cloud: Encrypted backup to S3/Backblaze

---

🌀 **Sovereign Stack Requirements**

**Minimum:** Python 3.9, ~65 MB disk
**Recommended:** Python 3.10+, cloudflared, ~500 MB disk
**Optimal:** Mac Studio, always-on, UPS, SSD

**Built BY:** Claude Sonnet 4.5
**Built FOR:** Any device, anywhere
**Built WITH:** Extraordinary attention to detail

---

*"Requirements aren't constraints. They're possibilities."*
