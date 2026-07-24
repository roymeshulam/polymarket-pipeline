import os
from pathlib import Path

from dotenv import load_dotenv

from source_config import load_source_profiles

load_dotenv()

# --- OpenAI ---
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")

# --- Polymarket CLOB V2 ---
POLYMARKET_PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")
POLYMARKET_FUNDER_ADDRESS = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
_signature_type = os.getenv("POLYMARKET_SIGNATURE_TYPE", "").split("#", 1)[0].strip()
POLYMARKET_SIGNATURE_TYPE = int(_signature_type or "-1")
LIVE_TRADING_ACK = os.getenv("LIVE_TRADING_ACK", "")
POLYMARKET_HOST = "https://clob.polymarket.com"
POLYMARKET_WS_HOST = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
POLYMARKET_CHAIN_ID = 137

# --- Ingestion credentials ---
TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ALERT_CHAT_ID = os.getenv("TELEGRAM_ALERT_CHAT_ID", "")

# Each source owns its freshness, relevance, trust, and confirmation policy.
_default_sources_path = Path(__file__).with_name("sources.json")
_configured_sources_path = Path(
    os.getenv("SOURCE_CONFIG_PATH", str(_default_sources_path))
)
SOURCE_CONFIG_PATH = str(
    _configured_sources_path
    if _configured_sources_path.is_absolute()
    else Path(__file__).parent / _configured_sources_path
)
SOURCE_PROFILES = load_source_profiles(SOURCE_CONFIG_PATH)

# --- Pipeline Settings ---
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
MAX_BET_USD = float(os.getenv("MAX_BET_USD", "25"))
DAILY_LOSS_LIMIT_USD = float(os.getenv("DAILY_LOSS_LIMIT_USD", "100"))
MAX_OPEN_EXPOSURE_USD = float(os.getenv("MAX_OPEN_EXPOSURE_USD", "100"))
MAX_SLIPPAGE_BPS = int(os.getenv("MAX_SLIPPAGE_BPS", "50"))
EDGE_THRESHOLD = float(os.getenv("EDGE_THRESHOLD", "0.10"))
NEWS_LOOKBACK_HOURS = 6
MIN_SOURCE_RELEVANCE = float(os.getenv("MIN_SOURCE_RELEVANCE", "0.65"))
CORROBORATION_WINDOW_SECONDS = int(os.getenv("CORROBORATION_WINDOW_SECONDS", "900"))
MARKET_MATCH_THRESHOLD = float(os.getenv("MARKET_MATCH_THRESHOLD", "0.18"))
MARKET_SEARCH_QUERIES = [
    value.strip()
    for value in os.getenv(
        "MARKET_SEARCH_QUERIES",
        "Israel,Netanyahu,Gaza,Hamas,Hezbollah,Iran",
    ).split(",")
    if value.strip()
]

# --- V2 Settings ---
MAX_VOLUME_USD = float(os.getenv("MAX_VOLUME_USD", "500000"))
MIN_VOLUME_USD = float(os.getenv("MIN_VOLUME_USD", "1000"))
MATERIALITY_THRESHOLD = float(os.getenv("MATERIALITY_THRESHOLD", "0.6"))
SPEED_TARGET_SECONDS = float(os.getenv("SPEED_TARGET_SECONDS", "5"))
LIVE_SOURCE_ALLOWLIST = {
    value.strip().lower()
    for value in os.getenv("LIVE_SOURCE_ALLOWLIST", "").split(",")
    if value.strip()
}

# --- Categories to track ---
MARKET_CATEGORIES = [
    "israel",
]
