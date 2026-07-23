#!/bin/bash
# Polymarket Pipeline V2 — One-Command Setup
# Usage: bash setup.sh

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'
BOLD='\033[1m'

echo ""
echo -e "${GREEN}${BOLD}  POLYMARKET PIPELINE V2 — SETUP${NC}"
echo -e "${GREEN}  Breaking News Detector + AI Classifier + Niche Market Trader${NC}"
echo ""

# --- Check Python ---
PYTHON=""
for cmd in python3.12 python3.11 python3.10 python3; do
    if command -v $cmd &> /dev/null; then
        version=$($cmd --version 2>&1 | awk '{print $2}')
        major=$(echo "$version" | cut -d. -f1)
        minor=$(echo "$version" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 9 ]; then
            PYTHON=$cmd
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo -e "${RED}ERROR: Python 3.9+ is required.${NC}"
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

    # Twitter (optional)
    echo -e "${YELLOW}2. Twitter API v2 Bearer Token${NC} (optional — enables real-time news stream)"
    read -s -p "   Enter Twitter bearer token (or press Enter to skip): " TWITTER_KEY
    echo ""

    # Telegram (optional)
    echo -e "${YELLOW}3. Telegram Bot Token${NC} (optional — enables channel monitoring)"
    read -s -p "   Enter Telegram bot token (or press Enter to skip): " TELEGRAM_KEY
    TELEGRAM_CHANNELS=""
    TELEGRAM_ALERT_CHAT=""
    if [ -n "$TELEGRAM_KEY" ]; then
        read -p "   Enter channel IDs (comma-separated, or Enter to skip): " TELEGRAM_CHANNELS
        read -p "   Enter alert destination chat ID (or Enter to skip): " TELEGRAM_ALERT_CHAT
    fi
    echo ""

    # Polymarket (optional)
    echo -e "${YELLOW}4. Polymarket API Credentials${NC} (optional — needed only for live trading)"
    read -s -p "   Enter Polymarket API key (or press Enter to skip): " POLY_KEY
    POLY_SECRET=""
    POLY_PASS=""
    POLY_PRIV=""
    if [ -n "$POLY_KEY" ]; then
        read -s -p "   Enter Polymarket API secret: " POLY_SECRET
        read -s -p "   Enter Polymarket API passphrase: " POLY_PASS
        read -s -p "   Enter Polymarket private key: " POLY_PRIV
    fi
    echo ""

    # NewsAPI (optional)
    echo -e "${YELLOW}5. NewsAPI Key${NC} (optional — broader RSS coverage, newsapi.org)"
    read -p "   Enter NewsAPI key (or press Enter to skip): " NEWSAPI
    echo ""

    # Write .env
    cat > .env << ENVEOF
# OpenAI (required)
OPENAI_API_KEY=${OPENAI_KEY}
OPENAI_MODEL=gpt-5.4-mini

# Twitter API v2 (optional — real-time news)
TWITTER_BEARER_TOKEN=${TWITTER_KEY}

# Telegram (optional — channel monitoring)
TELEGRAM_BOT_TOKEN=${TELEGRAM_KEY}
TELEGRAM_CHANNEL_IDS=${TELEGRAM_CHANNELS}
TELEGRAM_ALERT_CHAT_ID=${TELEGRAM_ALERT_CHAT}

# Polymarket CLOB API (optional — live trading)
POLYMARKET_API_KEY=${POLY_KEY}
POLYMARKET_API_SECRET=${POLY_SECRET}
POLYMARKET_API_PASSPHRASE=${POLY_PASS}
POLYMARKET_PRIVATE_KEY=${POLY_PRIV}
POLYMARKET_FUNDER_ADDRESS=
POLYMARKET_SIGNATURE_TYPE=
LIVE_TRADING_ACK=

# NewsAPI.org (optional)
NEWSAPI_KEY=${NEWSAPI}

# Pipeline Settings
DRY_RUN=true
MAX_BET_USD=25
DAILY_LOSS_LIMIT_USD=100
MAX_OPEN_EXPOSURE_USD=100
MAX_SLIPPAGE_BPS=50
EDGE_THRESHOLD=0.10

# V2 Settings
MAX_VOLUME_USD=500000
MIN_VOLUME_USD=1000
MATERIALITY_THRESHOLD=0.6
SPEED_TARGET_SECONDS=5
MAX_NEWS_AGE_SECONDS=300
LIVE_ALLOWED_NEWS_SOURCES=rss
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
echo "    python cli.py watch             # V2: Real-time event-driven pipeline"
echo "    python cli.py run               # V1: Synchronous pipeline"
echo "    python cli.py dashboard         # Live terminal dashboard"
echo "    python cli.py backtest          # Validate strategy first"
echo ""
