#!/bin/bash
# Israel Event Intelligence Pipeline — One-Command Setup
# Usage: bash setup.sh

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'
BOLD='\033[1m'

echo ""
echo -e "${GREEN}${BOLD}  ISRAEL EVENT INTELLIGENCE — SETUP${NC}"
echo -e "${GREEN}  Hebrew news ingestion + resolution-aware market analysis${NC}"
echo ""

# --- Check Python ---
PYTHON=""
for cmd in python3.12 python3.11 python3.10 python3; do
    if command -v $cmd &> /dev/null; then
        version=$($cmd --version 2>&1 | awk '{print $2}')
        major=$(echo "$version" | cut -d. -f1)
        minor=$(echo "$version" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
            PYTHON=$cmd
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo -e "${RED}ERROR: Python 3.10+ is required.${NC}"
    echo "Install it with: brew install python@3.12"
    exit 1
fi

echo -e "${GREEN}✓${NC} Found $($PYTHON --version)"

# --- Create virtual environment ---
if [ ! -d ".venv" ]; then
    echo -e "${YELLOW}Creating virtual environment...${NC}"
    $PYTHON -m venv .venv
    echo -e "${GREEN}✓${NC} Virtual environment created"
else
    echo -e "${GREEN}✓${NC} Virtual environment exists"
fi

source .venv/bin/activate

# --- Install dependencies ---
echo -e "${YELLOW}Installing dependencies...${NC}"
pip install --upgrade pip -q 2>/dev/null
pip install -r requirements.txt -q 2>/dev/null
echo -e "${GREEN}✓${NC} Dependencies installed"

# --- Setup .env ---
if [ -f ".env" ]; then
    echo -e "${GREEN}✓${NC} .env file exists"
else
    echo ""
    echo -e "${BOLD}Let's configure your API keys.${NC}"
    echo ""

    # OpenAI
    echo -e "${YELLOW}1. OpenAI API Key${NC} (required — get one at platform.openai.com)"
    read -s -p "   Enter your OpenAI API key: " OPENAI_KEY
    echo ""

    # X (optional)
    echo -e "${YELLOW}2. X API v2 Bearer Token${NC} (optional — for enabled X profiles)"
    read -s -p "   Enter Twitter bearer token (or press Enter to skip): " TWITTER_KEY
    echo ""

    # Telegram (optional)
    echo -e "${YELLOW}3. Telegram Bot Token${NC} (optional — for enabled Telegram profiles)"
    read -s -p "   Enter Telegram bot token (or press Enter to skip): " TELEGRAM_KEY
    TELEGRAM_ALERT_CHAT=""
    if [ -n "$TELEGRAM_KEY" ]; then
        read -p "   Enter alert destination chat ID (or Enter to skip): " TELEGRAM_ALERT_CHAT
    fi
    echo ""

    # Polymarket (optional)
    echo -e "${YELLOW}4. Polymarket V2 signing key${NC} (optional — live trading only)"
    read -s -p "   Enter private key (or press Enter to stay dry-run): " POLY_PRIV
    echo ""

    # Write .env
    cat > .env << ENVEOF
# OpenAI (required)
OPENAI_API_KEY=${OPENAI_KEY}
OPENAI_MODEL=gpt-5.4-mini

# Source profiles are configured in sources.json.
SOURCE_CONFIG_PATH=sources.json

# X API v2 (optional)
TWITTER_BEARER_TOKEN=${TWITTER_KEY}

# Telegram (optional)
TELEGRAM_BOT_TOKEN=${TELEGRAM_KEY}
TELEGRAM_ALERT_CHAT_ID=${TELEGRAM_ALERT_CHAT}

# Polymarket CLOB V2 (optional — live trading)
POLYMARKET_PRIVATE_KEY=${POLY_PRIV}
POLYMARKET_FUNDER_ADDRESS=
POLYMARKET_SIGNATURE_TYPE=
LIVE_TRADING_ACK=

# Pipeline Settings
DRY_RUN=true
MAX_BET_USD=25
DAILY_LOSS_LIMIT_USD=100
MAX_OPEN_EXPOSURE_USD=100
MAX_SLIPPAGE_BPS=50
EDGE_THRESHOLD=0.10
MIN_SOURCE_RELEVANCE=0.65
CORROBORATION_WINDOW_SECONDS=900
MARKET_MATCH_THRESHOLD=0.18
MARKET_SEARCH_QUERIES=Israel,Netanyahu,Gaza,Hamas,Hezbollah,Iran
LIVE_SOURCE_ALLOWLIST=

# Market and classification settings
MAX_VOLUME_USD=500000
MIN_VOLUME_USD=1000
MATERIALITY_THRESHOLD=0.6
SPEED_TARGET_SECONDS=5
ENVEOF

    echo -e "${GREEN}✓${NC} .env file created"
fi

# --- Verify ---
echo ""
echo -e "${YELLOW}Running verification...${NC}"
echo ""
$PYTHON cli.py verify

echo ""
echo -e "${GREEN}${BOLD}  SETUP COMPLETE${NC}"
echo ""
echo "  Next steps:"
echo "    source .venv/bin/activate"
echo "    edit sources.json               # Add reviewed Hebrew sources"
echo "    python cli.py watch             # Event-driven dry run"
echo "    python cli.py run               # V1: Synchronous pipeline"
echo "    python cli.py dashboard         # Live terminal dashboard"
echo "    python cli.py backtest          # Validate strategy first"
echo ""
