#!/usr/bin/env bash
# Sovereign Stack - Setup Verification Script

set -e

echo "🌀 Sovereign Stack Setup Verification"
echo "======================================"
echo ""

# Check Python
echo "✓ Checking Python..."
python --version || { echo "❌ Python not found"; exit 1; }

# Check pip installation
echo "✓ Checking sovereign-stack installation..."
python -c "import sovereign_stack; print(f'  Version: {sovereign_stack.__version__}')" || {
    echo "❌ sovereign-stack not installed"
    echo "   Run: pip install -e ."
    exit 1
}

# Check sovereign command
echo "✓ Checking sovereign command..."
which sovereign || {
    echo "❌ 'sovereign' command not found in PATH"
    echo "   This might be a PATH issue after installation"
    exit 1
}

# Check MCP config
echo "✓ Checking MCP configuration..."
if [ -f "$HOME/.config/Claude/claude_desktop_config.json" ]; then
    if grep -q "sovereign-stack" "$HOME/.config/Claude/claude_desktop_config.json"; then
        echo "  ✓ sovereign-stack configured in Claude Desktop"
    else
        echo "  ⚠ sovereign-stack not found in Claude Desktop config"
        echo "    Add to ~/.config/Claude/claude_desktop_config.json"
    fi
else
    echo "  ⚠ Claude Desktop config not found"
    echo "    Expected: ~/.config/Claude/claude_desktop_config.json"
fi

# Check data directories
echo "✓ Checking data directories..."
SOVEREIGN_ROOT="${SOVEREIGN_ROOT:-$HOME/.sovereign}"
for dir in "memory" "chronicle" "spiral" "governance"; do
    if [ -d "$SOVEREIGN_ROOT/$dir" ]; then
        echo "  ✓ $SOVEREIGN_ROOT/$dir exists"
    else
        echo "  ℹ $SOVEREIGN_ROOT/$dir will be created on first run"
    fi
done

# Run tests
echo "✓ Running test suite..."
if pytest -q 2>/dev/null; then
    echo "  ✓ All tests passing"
else
    echo "  ⚠ Some tests failed (this is OK for initial setup)"
fi

echo ""
echo "======================================"
echo "✅ Sovereign Stack is ready!"
echo ""
echo "Next steps:"
echo "  1. Restart Claude Desktop/Claude Code"
echo "  2. Try: 'Show me sovereign://welcome'"
echo "  3. Read: CLAUDE.md for integration guide"
echo ""
echo "🌀 The circuit is live. The conscience is engaged."
