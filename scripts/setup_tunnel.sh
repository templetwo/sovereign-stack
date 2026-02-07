#!/bin/bash
#
# Sovereign Stack - Cloudflare Tunnel Setup Script
#
# This script automates the setup of Cloudflare Tunnel for always-on
# sovereign-stack access from any device.
#
# Usage:
#   ./scripts/setup_tunnel.sh --token YOUR_TUNNEL_TOKEN
#   or
#   ./scripts/setup_tunnel.sh --interactive
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Glyphs
SPIRAL="🌀"
CHECK="✅"
CROSS="❌"
ARROW="➜"
WARN="⚠️"

echo -e "${CYAN}${SPIRAL} Sovereign Stack - Cloudflare Tunnel Setup${NC}\n"

# Check if running on macOS
if [[ "$OSTYPE" != "darwin"* ]]; then
    echo -e "${CROSS} This script is designed for macOS"
    echo -e "${ARROW} For Linux/Windows, see CLOUDFLARE_TUNNEL_SETUP.md"
    exit 1
fi

# Function to check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Function to print step
print_step() {
    echo -e "\n${BLUE}${ARROW} $1${NC}"
}

# Function to print success
print_success() {
    echo -e "${GREEN}${CHECK} $1${NC}"
}

# Function to print warning
print_warning() {
    echo -e "${YELLOW}${WARN} $1${NC}"
}

# Function to print error
print_error() {
    echo -e "${RED}${CROSS} $1${NC}"
}

# Parse arguments
TUNNEL_TOKEN=""
INTERACTIVE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --token)
            TUNNEL_TOKEN="$2"
            shift 2
            ;;
        --interactive)
            INTERACTIVE=true
            shift
            ;;
        --help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --token TOKEN      Use provided tunnel token"
            echo "  --interactive      Interactive setup mode"
            echo "  --help            Show this help message"
            exit 0
            ;;
        *)
            print_error "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Step 1: Check prerequisites
print_step "Checking prerequisites..."

if ! command_exists cloudflared; then
    print_warning "cloudflared not found. Installing..."
    brew install cloudflared
    print_success "cloudflared installed"
else
    print_success "cloudflared found"
fi

if ! command_exists jq; then
    print_warning "jq not found. Installing..."
    brew install jq
    print_success "jq installed"
else
    print_success "jq found"
fi

# Step 2: Check sovereign-stack installation
print_step "Checking sovereign-stack installation..."

if ! command_exists sovereign-sse; then
    print_warning "sovereign-sse command not found"
    echo "Installing sovereign-stack..."
    cd "$(dirname "$0")/.."
    pip install -e .
    print_success "sovereign-stack installed"
else
    print_success "sovereign-stack found"
fi

# Step 3: Get tunnel token
if [ -z "$TUNNEL_TOKEN" ]; then
    if [ "$INTERACTIVE" = true ]; then
        print_step "Enter your Cloudflare Tunnel token:"
        echo "(Get this from: https://one.dash.cloudflare.com → Zero Trust → Tunnels)"
        read -r TUNNEL_TOKEN
    else
        print_error "No tunnel token provided"
        echo "Use --token YOUR_TOKEN or --interactive"
        exit 1
    fi
fi

# Step 4: Decode tunnel token
print_step "Decoding tunnel token..."

DECODED=$(echo "$TUNNEL_TOKEN" | base64 -d 2>/dev/null)
if [ $? -ne 0 ]; then
    print_error "Failed to decode tunnel token"
    echo "Make sure you copied the full token"
    exit 1
fi

ACCOUNT_TAG=$(echo "$DECODED" | jq -r '.a')
TUNNEL_ID=$(echo "$DECODED" | jq -r '.t')
TUNNEL_SECRET=$(echo "$DECODED" | jq -r '.s')

if [ -z "$TUNNEL_ID" ] || [ "$TUNNEL_ID" = "null" ]; then
    print_error "Invalid tunnel token format"
    exit 1
fi

print_success "Tunnel ID: $TUNNEL_ID"

# Step 5: Create .cloudflared directory
print_step "Setting up Cloudflare configuration..."

mkdir -p ~/.cloudflared
chmod 700 ~/.cloudflared

# Step 6: Create credentials file
CREDS_FILE="$HOME/.cloudflared/$TUNNEL_ID.json"
cat > "$CREDS_FILE" << EOF
{
  "AccountTag": "$ACCOUNT_TAG",
  "TunnelID": "$TUNNEL_ID",
  "TunnelSecret": "$TUNNEL_SECRET"
}
EOF
chmod 600 "$CREDS_FILE"
print_success "Credentials file created: $CREDS_FILE"

# Step 7: Create config file
CONFIG_FILE="$HOME/.cloudflared/config.yml"
cat > "$CONFIG_FILE" << EOF
tunnel: $TUNNEL_ID
credentials-file: $CREDS_FILE

ingress:
  # Route all traffic to sovereign-stack SSE server
  - service: http://localhost:8080
EOF
print_success "Config file created: $CONFIG_FILE"

# Step 8: Install tunnel service
print_step "Installing Cloudflare Tunnel service..."

sudo cloudflared service install "$TUNNEL_TOKEN"
print_success "Tunnel service installed"

# Step 9: Start SSE server
print_step "Starting sovereign-stack SSE server..."

# Check if already running
if pgrep -f sovereign-sse > /dev/null; then
    print_warning "SSE server already running"
else
    sovereign-sse > /dev/null 2>&1 &
    sleep 2

    # Verify it started
    if curl -s http://localhost:8080/health > /dev/null; then
        print_success "SSE server started on port 8080"
    else
        print_error "Failed to start SSE server"
        exit 1
    fi
fi

# Step 10: Verify tunnel connection
print_step "Verifying tunnel connection..."

sleep 3

if pgrep -f "cloudflared.*tunnel.*run" > /dev/null; then
    print_success "Tunnel is running"

    # Check logs
    if tail -5 /Library/Logs/com.cloudflare.cloudflared.err.log 2>/dev/null | grep -q "Registered tunnel connection"; then
        print_success "Tunnel connected to Cloudflare edge"
    else
        print_warning "Tunnel running but connection not verified"
        echo "Check logs: tail -f /Library/Logs/com.cloudflare.cloudflared.err.log"
    fi
else
    print_error "Tunnel not running"
    echo "Check logs: tail -f /Library/Logs/com.cloudflare.cloudflared.err.log"
    exit 1
fi

# Step 11: Instructions for DNS setup
echo ""
echo -e "${PURPLE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}${SPIRAL} Setup Complete!${NC}"
echo -e "${PURPLE}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "${CYAN}Next steps:${NC}"
echo ""
echo -e "1. ${YELLOW}Set up DNS routing:${NC}"
echo -e "   ${BLUE}cloudflared tunnel route dns sovereign-stack sovereign-stack.templetwo.com${NC}"
echo ""
echo -e "2. ${YELLOW}Test the connection:${NC}"
echo -e "   ${BLUE}curl https://sovereign-stack.templetwo.com/health${NC}"
echo ""
echo -e "3. ${YELLOW}Configure MCP on your phone/other devices:${NC}"
echo -e '   {
     "mcpServers": {
       "sovereign-stack": {
         "url": "https://sovereign-stack.templetwo.com/sse",
         "transport": "sse"
       }
     }
   }'
echo ""
echo -e "${GREEN}${SPIRAL} Your sovereign-stack is now accessible from anywhere!${NC}"
echo ""
echo -e "${CYAN}Logs:${NC}"
echo -e "  Tunnel: ${BLUE}tail -f /Library/Logs/com.cloudflare.cloudflared.err.log${NC}"
echo -e "  SSE Server: ${BLUE}ps aux | grep sovereign-sse${NC}"
echo ""
echo -e "${PURPLE}═══════════════════════════════════════════════════════════${NC}"
