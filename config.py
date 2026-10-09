"""
config.py
---------
Configuration management for the Telegram Deals Automation Bot.
Reads environment variables from a local '.env' file anchored to BASE_DIR.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Base directory for all relative paths
BASE_DIR = Path(__file__).resolve().parent

# Load environment variables from the .env file in BASE_DIR
env_path = BASE_DIR / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)

# --- TELEGRAM CREDENTIALS ---
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
PRIVATE_REVIEW_CHANNEL = os.getenv("TELEGRAM_PRIVATE_REVIEW_CHANNEL", "").strip()
PUBLIC_DEALS_CHANNEL = os.getenv("TELEGRAM_PUBLIC_DEALS_CHANNEL", "").strip()

# Target Single Myntra Product URL (optional reference)
MYNTRA_PRODUCT_URL = os.getenv("MYNTRA_PRODUCT_URL", "https://www.myntra.com/1364628").strip()

# --- SEARCH KEYWORDS ---
_raw_keywords = os.getenv("KEYWORDS", "men tshirt, sneakers, hoodie, women kurti")
KEYWORDS = [k.strip() for k in _raw_keywords.split(",") if k.strip()]


def _parse_keywords(env_val: str, fallback: list) -> list:
    if not env_val or not env_val.strip():
        return fallback
    parsed = [k.strip() for k in env_val.split(",") if k.strip()]
    return parsed if parsed else fallback


KEYWORDS_MYNTRA = _parse_keywords(os.getenv("KEYWORDS_MYNTRA", ""), KEYWORDS)
KEYWORDS_FLIPKART = _parse_keywords(os.getenv("KEYWORDS_FLIPKART", ""), KEYWORDS)
KEYWORDS_AMAZON = _parse_keywords(os.getenv("KEYWORDS_AMAZON", ""), KEYWORDS)

# Maximum keywords to query per platform in a single scan cycle
MAX_KEYWORDS_PER_SCAN = int(os.getenv("MAX_KEYWORDS_PER_SCAN", "2"))

# --- DISCOUNT & FILTERING SETTINGS ---
# Read DROP_PERCENT with fallback to DISCOUNT_THRESHOLD (default 80.0%)
DISCOUNT_THRESHOLD = float(os.getenv("DROP_PERCENT", os.getenv("DISCOUNT_THRESHOLD", "80.0")))

# Minimum sale price in INR to filter out junk/broken listings (default 99)
MIN_SALE_PRICE = float(os.getenv("MIN_SALE_PRICE", "99"))

# Maximum Telegram alerts to post per scan cycle (flood prevention)
MAX_DEALS_PER_SCAN = int(os.getenv("MAX_DEALS_PER_SCAN", "3"))

# Random delays (in seconds) between web requests
REQUEST_DELAY_MIN = float(os.getenv("REQUEST_DELAY_MIN", "5.0"))
REQUEST_DELAY_MAX = float(os.getenv("REQUEST_DELAY_MAX", "15.0"))

# --- SEPARATE SCAN INTERVALS PER PLATFORM (in minutes) ---
MYNTRA_INTERVAL_MIN = int(os.getenv("MYNTRA_INTERVAL_MIN", "15"))
FLIPKART_INTERVAL_MIN = int(os.getenv("FLIPKART_INTERVAL_MIN", "30"))
AMAZON_INTERVAL_MIN = int(os.getenv("AMAZON_INTERVAL_MIN", "60"))

# --- STORAGE & LOGGING PATHS (Anchored to BASE_DIR) ---
DUPLICATE_DB_FILE = BASE_DIR / os.getenv("DUPLICATE_DB_FILE", "posted_deals.json")
LOG_FILE = BASE_DIR / "bot.log"


def is_configured() -> bool:
    """
    Validates if real Telegram credentials are provided.
    """
    has_token = bool(BOT_TOKEN and BOT_TOKEN != "YOUR_BOT_TOKEN_HERE")
    has_private = bool(PRIVATE_REVIEW_CHANNEL and PRIVATE_REVIEW_CHANNEL != "@YOUR_PRIVATE_CHANNEL_HERE")
    return bool(has_token and has_private)
