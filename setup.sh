#!/bin/bash
set -e

# Sovereign Stack - One-Command Setup
# Works on: macOS (Intel/Apple Silicon), Linux
# Usage: ./setup.sh

echo "🌀 Sovereign Stack Setup"
echo ""

# Check Python version
echo "📍 Checking Python..."
PYTHON=""
for cmd in python3.12 python3.11 python3.10 python3; do
    if command -v $cmd &> /dev/null; then
        VERSION=$($cmd -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
        if [ "$(echo "$VERSION >= 3.10" | bc -l)" -eq 1 ] 2>/dev/null || [[ "$VERSION" > "3.10" ]] || [[ "$VERSION" == "3.10" ]]; then
            PYTHON=$cmd
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "❌ Python 3.10+ not found"
    echo ""
    echo "Install Python 3.10+:"
    echo "  macOS: brew install python@3.12"
    echo "  Linux: sudo apt install python3.12"
    exit 1
fi

echo "✅ Found $PYTHON (version $VERSION)"

# Create virtual environment
echo ""
echo "📍 Creating virtual environment..."
if [ -d "venv" ]; then
    echo "   venv already exists, skipping..."
else
    $PYTHON -m venv venv
    echo "✅ Virtual environment created"
fi

# Activate and install
echo ""
echo "📍 Installing dependencies..."
source venv/bin/activate
pip install --quiet --upgrade pip
pip install -e . --quiet

echo "✅ Dependencies installed"

# Create data directory
echo ""
echo "📍 Setting up data directory..."
mkdir -p ~/.sovereign
echo "✅ Data directory: ~/.sovereign/"

# Test installation
echo ""
echo "📍 Verifying installation..."
if command -v sovereign &> /dev/null && command -v sovereign-sse &> /dev/null; then
    echo "✅ Commands ready: sovereign, sovereign-sse"
else
    echo "⚠️  Commands not in PATH (venv needs activation)"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🌀 Sovereign Stack - Installation Complete"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "QUICK START:"
echo ""
echo "1️⃣  Local MCP (Claude Desktop):"
echo "   Add to ~/.config/Claude/claude_desktop_config.json:"
echo ""
echo '   {
     "mcpServers": {
       "sovereign-stack": {
         "command": "'$(pwd)'/venv/bin/sovereign"
       }
     }
   }'
echo ""
echo "2️⃣  Always-On Access (Optional):"
echo "   ./start_always_on.sh"
echo ""
echo "3️⃣  Restart Claude Desktop and you're ready!"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
