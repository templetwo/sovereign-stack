#!/bin/bash
set -e

# Sovereign Stack - Always-On Setup
# Starts SSE server + Cloudflare Tunnel for remote access

echo "🌀 Sovereign Stack - Always-On Setup"
echo ""

# Check if venv exists
if [ ! -d "venv" ]; then
    echo "❌ Virtual environment not found. Run ./setup.sh first"
    exit 1
fi

# Activate venv
source venv/bin/activate

# Check cloudflared
echo "📍 Checking Cloudflare Tunnel..."
CLOUDFLARED=""
for path in /opt/homebrew/bin/cloudflared /usr/local/bin/cloudflared cloudflared; do
    if command -v $path &> /dev/null; then
        CLOUDFLARED=$path
        break
    fi
done

if [ -z "$CLOUDFLARED" ]; then
    echo "❌ cloudflared not found"
    echo ""
    echo "Install Cloudflare Tunnel:"
    echo "  macOS: brew install cloudflared"
    echo "  Linux: See https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
    exit 1
fi

echo "✅ Found cloudflared: $CLOUDFLARED"

# Check for tunnel config
if [ ! -f ~/.cloudflared/config.yml ]; then
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "🔧 First-Time Tunnel Setup"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    echo "OPTION 1: Quick Tunnel (Easiest - Temporary URL)"
    echo "  No config needed, instant setup"
    echo "  URL changes each time you restart"
    echo ""
    echo "OPTION 2: Named Tunnel (Recommended - Permanent URL)"
    echo "  Requires Cloudflare account (free)"
    echo "  Permanent URL you control"
    echo ""
    read -p "Choose [1=Quick, 2=Named]: " choice

    if [ "$choice" = "1" ]; then
        USE_QUICK_TUNNEL=true
    else
        echo ""
        echo "Create a Named Tunnel:"
        echo ""
        echo "1. Create tunnel:"
        echo "   $CLOUDFLARED tunnel create sovereign-stack"
        echo ""
        echo "2. Note the tunnel ID from the output"
        echo ""
        echo "3. Create ~/.cloudflared/config.yml:"
        echo "   tunnel: YOUR_TUNNEL_ID"
        echo "   credentials-file: ~/.cloudflared/YOUR_TUNNEL_ID.json"
        echo "   ingress:"
        echo "     - service: http://localhost:3434"
        echo ""
        echo "4. Run this script again"
        echo ""
        exit 0
    fi
else
    USE_QUICK_TUNNEL=false
fi

# Start SSE server
echo ""
echo "📍 Starting SSE server..."

# Check if already running
if lsof -ti:3434 &> /dev/null; then
    echo "⚠️  Port 3434 already in use (SSE server may already be running)"
    read -p "Kill existing process and restart? [y/N]: " kill_choice
    if [ "$kill_choice" = "y" ]; then
        lsof -ti:3434 | xargs kill
        sleep 2
    else
        echo "Using existing SSE server..."
    fi
fi

# Start in background if not running
if ! lsof -ti:3434 &> /dev/null; then
    nohup sovereign-sse > ~/.sovereign/sse.log 2>&1 &
    sleep 2
    echo "✅ SSE server started (logs: ~/.sovereign/sse.log)"
else
    echo "✅ SSE server running"
fi

# Test locally
if curl -s http://localhost:3434/health | grep -q "healthy"; then
    echo "✅ Health check passed"
else
    echo "❌ SSE server not responding"
    echo "   Check logs: tail -f ~/.sovereign/sse.log"
    exit 1
fi

# Start tunnel
echo ""
echo "📍 Starting Cloudflare Tunnel..."

if [ "$USE_QUICK_TUNNEL" = true ]; then
    echo ""
    echo "Starting Quick Tunnel (temporary URL)..."
    echo "Press Ctrl+C to stop"
    echo ""
    $CLOUDFLARED tunnel --url http://localhost:3434
else
    # Check if tunnel already running
    if pgrep -f "cloudflared tunnel run" > /dev/null; then
        echo "⚠️  Cloudflare Tunnel already running"
        read -p "Restart tunnel? [y/N]: " restart_choice
        if [ "$restart_choice" = "y" ]; then
            pkill -f "cloudflared tunnel run"
            sleep 2
        else
            echo "Using existing tunnel..."
        fi
    fi

    # Start tunnel if not running
    if ! pgrep -f "cloudflared tunnel run" > /dev/null; then
        nohup $CLOUDFLARED tunnel run > ~/.sovereign/tunnel.log 2>&1 &
        sleep 3
        echo "✅ Cloudflare Tunnel started (logs: ~/.sovereign/tunnel.log)"
    else
        echo "✅ Cloudflare Tunnel running"
    fi

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "🌀 Always-On Access Ready"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    echo "Services running:"
    echo "  • SSE Server: http://localhost:3434"
    echo "  • Cloudflare Tunnel: (check dashboard for URL)"
    echo ""
    echo "Configure MCP clients with your tunnel URL:"
    echo '  {
    "mcpServers": {
      "sovereign-stack": {
        "url": "https://your-tunnel-url.com/sse",
        "transport": "sse"
      }
    }
  }'
    echo ""
    echo "Monitor services:"
    echo "  tail -f ~/.sovereign/sse.log"
    echo "  tail -f ~/.sovereign/tunnel.log"
    echo ""
fi
