# Sovereign Stack Scripts

Utility scripts for working with sovereign-stack.

## Management Scripts

### manage (Service Manager)
**Manage SSE server and Cloudflare tunnel**

```bash
./scripts/manage status    # Show service status (default)
./scripts/manage start      # Start all services
./scripts/manage stop       # Stop all services
./scripts/manage restart    # Restart all services
./scripts/manage health     # Run detailed health checks
./scripts/manage logs sse   # Tail SSE server logs
./scripts/manage logs tunnel # Tail tunnel logs
```

### monitor (Auto-restart)
**Monitor services and auto-restart on failure**

```bash
./scripts/monitor           # Check every 30s (default)
./scripts/monitor 60        # Check every 60s
```

Press Ctrl+C to stop monitoring. Runs in foreground.

---

## Session Capture Scripts

### quick_capture.py (Recommended)
**Use this when:** Claude Desktop or remote Claude sessions can't connect to the stack directly.

Simple Python script - just edit the data arrays at the top and run:
```bash
python3 scripts/quick_capture.py
```

Pre-filled with the connection attempt insight. Add your IRIS Gate Evo session data.

### capture_remote_session.sh (Interactive)
**Use this when:** You want guided prompts to capture session data.

Interactive bash script that walks you through capturing insights, learnings, and threads:
```bash
./scripts/capture_remote_session.sh
```

## Usage Pattern

1. Work in Claude Desktop (or claude.ai)
2. Session can't reach the stack (local-only on Mac)
3. Copy session highlights
4. Edit `quick_capture.py` with your data
5. Run on Mac: `python3 scripts/quick_capture.py`
6. Data flows into chronicle for next session

## What Gets Captured

- **Insights**: Key discoveries (domain-tagged, layered)
- **Learnings**: Mistake → lesson pairs
- **Open Threads**: Unresolved questions
- **Breakthroughs**: Significant moments (optional)

All data goes to `~/.sovereign/chronicle/` and becomes available to future instances via `spiral_inherit`.
