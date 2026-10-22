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

# --- SEARCH KEYWORDS (Girls / Women Categories Ordered by Importance) ---
# Importance: Ethnic Wear (100%), Beauty & Skincare (90%), Fashion Jewellery (80%),
# Footwear (70%), Handbags (65%), Hair & Personal Care (60%), Watches (45%), Home & Kitchen (40%)
_DEFAULT_WOMEN_KEYWORDS = (
    "kurta set, saree, silk saree, cotton saree, women top, women kurti, women dress, bra, "
    "sunscreen spf 50, face serum, moisturizer, lipstick, kajal, "
    "earrings, necklace set, bangles, oxidised jewellery, "
    "women flats, sandals, women heels, women sneakers, "
    "sling bags, tote bags, handbags, "
    "shampoo, hair oil, hair accessories, "
    "women watch, bedsheet, home decor, kitchen organizer"
)
_raw_keywords = os.getenv("KEYWORDS", _DEFAULT_WOMEN_KEYWORDS)
KEYWORDS = [k.strip() for k in _raw_keywords.split(",") if k.strip()]


def _parse_keywords(env_val: str, fallback: list) -> list:
    if not env_val or not env_val.strip():
        return fallback
    parsed = [k.strip() for k in env_val.split(",") if k.strip()]
    return parsed if parsed else fallback


KEYWORDS_MYNTRA = _parse_keywords(os.getenv("KEYWORDS_MYNTRA", ""), KEYWORDS)
KEYWORDS_FLIPKART = _parse_keywords(os.getenv("KEYWORDS_FLIPKART", ""), KEYWORDS)
KEYWORDS_AMAZON = _parse_keywords(os.getenv("KEYWORDS_AMAZON", ""), KEYWORDS)

# Category Importance Weights
CATEGORY_WEIGHTS = {
    "ethnic_wear": 1.00,       # 100%
    "beauty_skincare": 0.90,   # 90%
    "fashion_jewellery": 0.80, # 80%
    "footwear": 0.70,          # 70%
    "handbags": 0.65,          # 65%
    "personal_care": 0.60,     # 60%
    "watches": 0.45,           # 45%
    "home_kitchen": 0.40,      # 40%
}


def get_keyword_weight(keyword: str) -> float:
    """Calculates relative sampling weight for a keyword based on category importance."""
    kw = keyword.lower().replace("-", " ")
    # 1. Ethnic wear (100%)
    if any(k in kw for k in ["kurta", "saree", "sari", "top", "dress", "kurti", "ethnic", "bra", "lehenga"]):
        return 1.00
    # 2. Beauty & skincare (90%)
    if any(k in kw for k in ["sunscreen", "spf", "serum", "moisturizer", "lipstick", "kajal", "beauty", "skincare"]):
        return 0.90
    # 3. Fashion jewellery (80%)
    if any(k in kw for k in ["earring", "necklace", "bangle", "jewellery", "jewelry", "jhumka", "oxidised"]):
        return 0.80
    # 4. Footwear (70%)
    if any(k in kw for k in ["flat", "sandal", "heel", "sneaker", "footwear"]):
        return 0.70
    # 5. Handbags (65%)
    if any(k in kw for k in ["sling", "tote", "handbag", "bag"]):
        return 0.65
    # 6. Hair care & personal care (60%)
    if any(k in kw for k in ["shampoo", "hair", "personal care"]):
        return 0.60
    # 7. Watches (45%)
    if any(k in kw for k in ["watch"]):
        return 0.45
    # 8. Home & kitchen (40%)
    if any(k in kw for k in ["bedsheet", "home", "kitchen", "decor", "cookware", "organizer"]):
        return 0.40
    return 0.50

# Maximum keywords to query per platform in a single scan cycle (default 4 for faster coverage)
MAX_KEYWORDS_PER_SCAN = int(os.getenv("MAX_KEYWORDS_PER_SCAN", "4"))

# --- DISCOUNT & FILTERING SETTINGS ---
# Read DROP_PERCENT with fallback to DISCOUNT_THRESHOLD (default 70.0% for high-frequency deals)
DISCOUNT_THRESHOLD = float(os.getenv("DROP_PERCENT", os.getenv("DISCOUNT_THRESHOLD", "70.0")))

# Minimum sale price in INR to filter out junk/broken listings (default 99)
MIN_SALE_PRICE = float(os.getenv("MIN_SALE_PRICE", "99"))

# Maximum Telegram alerts to post per scan cycle (flood prevention, default 5)
MAX_DEALS_PER_SCAN = int(os.getenv("MAX_DEALS_PER_SCAN", "5"))

# Random delays (in seconds) between web requests
REQUEST_DELAY_MIN = float(os.getenv("REQUEST_DELAY_MIN", "4.0"))
REQUEST_DELAY_MAX = float(os.getenv("REQUEST_DELAY_MAX", "10.0"))

# --- SEPARATE SCAN INTERVALS PER PLATFORM (in minutes) ---
MYNTRA_INTERVAL_MIN = int(os.getenv("MYNTRA_INTERVAL_MIN", "8"))
FLIPKART_INTERVAL_MIN = int(os.getenv("FLIPKART_INTERVAL_MIN", "12"))
AMAZON_INTERVAL_MIN = int(os.getenv("AMAZON_INTERVAL_MIN", "20"))

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
