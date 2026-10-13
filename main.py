"""
main.py
-------
Production-ready daemon for scraping deals on Myntra, Flipkart, and Amazon India
and publishing alerts to a Telegram review channel.

Key Capabilities:
- Robust multi-platform scraping (Myntra, Flipkart, Amazon).
- Separate platform scan intervals and polite random delays (5-15s).
- Cooldown backoff on 403/429/503/CAPTCHA blocks (3h -> 6h -> 12h -> 24h).
- Single-row persistent duplicate tracking with price-drop detection.
- Telegram HTML escaping, RetryAfter rate-limit handling, and photo fallback.
- Structured logging with RotatingFileHandler (bot.log) and clean SIGTERM shutdown.
"""

import asyncio
import html
import json
import logging
from logging.handlers import RotatingFileHandler
import fcntl
import os
import random
import re
import shutil
import signal
import sys
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup
import telegram
import telegram.error

import config
try:
    from earnkaro_converter import convert_to_earnkaro_link
except ImportError:
    def convert_to_earnkaro_link(url: str) -> str:
        return url

# --- STRUCTURED LOGGING SETUP (RotatingFileHandler + Console) ---
logger = logging.getLogger("DealBot")
logger.setLevel(logging.INFO)

log_formatter = logging.Formatter(
    fmt="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# File handler: 2MB per file, 3 backups
file_handler = RotatingFileHandler(
    config.LOG_FILE,
    maxBytes=2 * 1024 * 1024,
    backupCount=3,
    encoding="utf-8"
)
file_handler.setFormatter(log_formatter)
file_handler.setLevel(logging.INFO)

# Console handler
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)
console_handler.setLevel(logging.INFO)

# Avoid duplicate handlers on reloads
if not logger.handlers:
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)


# File descriptor for single-instance process lock (retained for process lifetime)
_lock_file_fd = None


# --- EXCEPTION CLASSES FOR ANTI-BOT BACKOFF ---
class PlatformBlockedException(Exception):
    """Raised when an anti-bot challenge, CAPTCHA, or rate-limit HTTP status is encountered."""
    def __init__(self, platform: str, reason: str):
        super().__init__(f"[{platform}] {reason}")
        self.platform = platform
        self.reason = reason


# --- PLATFORM COOLDOWN & STATE TRACKER ---
class PlatformState:
    """Tracks schedule intervals and exponential cooldown backoffs per platform."""
    def __init__(self, name: str, interval_min: int):
        self.name = name
        self.interval_min = interval_min
        self.last_scanned: Optional[datetime] = None
        self.cooldown_until: Optional[datetime] = None
        self.consecutive_blocks: int = 0
        self.session = requests.Session()
        self._init_session()

    def _init_session(self):
        # Configure standard modern browser headers
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Connection": "keep-alive",
        })

    def is_due(self) -> Tuple[bool, str]:
        now = datetime.now()
        # 1. Cooldown check
        if self.cooldown_until:
            if now < self.cooldown_until:
                remaining = self.cooldown_until - now
                return False, f"in cooldown for {int(remaining.total_seconds() // 60)} more mins (until {self.cooldown_until.strftime('%H:%M:%S')})"
            else:
                logger.info("⏳ [%s] Cooldown expired. Resuming scans.", self.name)
                self.cooldown_until = None

        # 2. Interval check
        if self.last_scanned is not None:
            elapsed = (now - self.last_scanned).total_seconds()
            interval_sec = self.interval_min * 60
            if elapsed < interval_sec:
                remaining = int((interval_sec - elapsed) // 60)
                return False, f"interval not elapsed ({remaining} mins remaining)"

        return True, "ready"

    def record_success(self):
        self.last_scanned = datetime.now()
        if self.consecutive_blocks > 0 or self.cooldown_until:
            logger.info("✅ [%s] Successful scan completed. Resetting cooldown backoff.", self.name)
        self.consecutive_blocks = 0
        self.cooldown_until = None

    def record_block(self, reason: str):
        self.consecutive_blocks += 1
        # Cooldown: 3 hours, then 6h, 12h, max 24h
        hours = min(24.0, 3.0 * (2 ** (self.consecutive_blocks - 1)))
        self.cooldown_until = datetime.now() + timedelta(hours=hours)
        logger.warning(
            "🚫 [%s] Block/Challenge encountered (%s). Strike %d! Entering cooldown for %gh (until %s).",
            self.name,
            reason,
            self.consecutive_blocks,
            hours,
            self.cooldown_until.strftime("%Y-%m-%d %H:%M:%S")
        )


# --- UTILITY HELPERS ---

def clean_price(price_str: str) -> float:
    """Cleans currency strings (e.g. '₹999.00', ' 1,499 ') and converts to float."""
    if not price_str:
        return 0.0
    clean_str = re.sub(r"[^\d.]", "", str(price_str))
    try:
        return float(clean_str)
    except ValueError:
        return 0.0


# --- PLATFORM SCRAPERS ---

def scrape_myntra(keyword: str, session: Optional[requests.Session] = None) -> List[Dict]:
    """
    Crawls Myntra search landing pages and parses window.__myx JSON state block.
    (Preserves exact original Myntra scraping logic, with platform prefix added).
    """
    client = session or requests.Session()
    query_dashed = keyword.strip().lower().replace(" ", "-")
    url = f"https://www.myntra.com/{query_dashed}"

    logger.info("📡 [Myntra] Crawling search page: %s", url)

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-IN,en;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Referer": "https://www.myntra.com/",
    }

    products = []
    max_retries = 2
    response = None

    for attempt in range(max_retries):
        try:
            response = client.get(url, headers=headers, timeout=12)
            if response.status_code == 200:
                break
            if response.status_code in (403, 429, 503):
                raise PlatformBlockedException("Myntra", f"HTTP {response.status_code}")
            logger.warning("   ⚠️ Myntra returned HTTP %d. Retrying (Attempt %d/%d)...", response.status_code, attempt + 1, max_retries)
            time.sleep(2)
        except (requests.exceptions.RequestException, PlatformBlockedException) as e:
            if isinstance(e, PlatformBlockedException):
                raise
            if attempt == max_retries - 1:
                logger.error("   ❌ Myntra request failed: %s", e)
                return []
            time.sleep(2)

    if not response or response.status_code != 200:
        return []

    try:
        match = re.search(r"window\.__myx\s*=\s*(\{.+?\});?\s*(?:window\.__INITIAL_STATE__|</script>|$)", response.text)
        if not match:
            if "captcha" in response.text.lower() or "blocked" in response.text.lower():
                raise PlatformBlockedException("Myntra", "CAPTCHA or security challenge")
            logger.warning("   ⚠️ [Myntra] Could not locate window.__myx JSON script tag.")
            return []

        state_data = json.loads(match.group(1))
        search_results = state_data.get("searchData", {}).get("results", {})
        product_list = search_results.get("products", [])

        for item in product_list:
            brand = item.get("brand", "")
            product_name = item.get("productName", "")
            product_title = f"{brand} - {product_name}" if brand else product_name
            if not product_title:
                product_title = "Myntra Fashion Item"

            mrp = float(item.get("mrp", 0))
            sale_price = float(item.get("price", 0))
            if mrp <= 0 or sale_price <= 0:
                continue

            discount_percent = round(((mrp - sale_price) / mrp) * 100, 2)
            landing_url = item.get("landingPageUrl", "")
            product_url = f"https://www.myntra.com/{landing_url}" if landing_url else url

            image_url = item.get("searchImage", "")
            if image_url and image_url.startswith("http://"):
                image_url = image_url.replace("http://", "https://")

            prod_id = str(item.get("productId", ""))

            products.append({
                "id": f"myntra:{prod_id}",
                "name": product_title,
                "original_price": mrp,
                "sale_price": sale_price,
                "discount_percentage": discount_percent,
                "url": product_url,
                "image_url": image_url,
                "source": "Myntra"
            })

        logger.info("   🔎 [Myntra] Extracted %d products for '%s'.", len(products), keyword)
        return products
    except PlatformBlockedException:
        raise
    except Exception as e:
        logger.error("   ❌ [Myntra] Unexpected parsing error: %s", e)
        return []


def scrape_flipkart(keyword: str, session: Optional[requests.Session] = None) -> List[Dict]:
    """
    Crawls Flipkart search listings.
    Primary: Extracts window.__INITIAL_STATE__ JSON (stable hook, exact prices).
    Fallback: HTML parsing with div[data-id] and /p/ URL anchors.
    """
    client = session or requests.Session()
    query = keyword.strip().replace(" ", "+")
    url = f"https://www.flipkart.com/search?q={query}"

    logger.info("📡 [Flipkart] Crawling search page: %s", url)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-IN,en;q=0.9",
        "Referer": "https://www.flipkart.com/",
    }

    try:
        response = client.get(url, headers=headers, timeout=12)
        if response.status_code in (403, 429, 503):
            raise PlatformBlockedException("Flipkart", f"HTTP {response.status_code}")
        if response.status_code != 200:
            logger.warning("   ⚠️ Flipkart returned HTTP %d", response.status_code)
            return []
    except PlatformBlockedException:
        raise
    except Exception as e:
        logger.error("   ❌ Flipkart network error: %s", e)
        return []

    # 1. Primary Method: Embedded JSON State Block (window.__INITIAL_STATE__)
    try:
        m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.+?\});\s*</script>", response.text)
        if m:
            data = json.loads(m.group(1))
            slots = data.get("pageDataV4", {}).get("page", {}).get("data", {})
            products = []

            def extract_from_slots(obj):
                if isinstance(obj, dict):
                    if "productInfo" in obj:
                        val = obj["productInfo"].get("value", {})
                        pid = val.get("id")
                        if pid:
                            titles = val.get("titles", {})
                            brand = val.get("productBrand") or titles.get("superTitle", "")
                            name = titles.get("title") or titles.get("newTitle", "")
                            title = f"{brand} - {name}" if brand and name and brand not in name else (name or brand or "Fashion Item")

                            pricing = val.get("pricing", {})
                            final_price = pricing.get("finalPrice", {}).get("value") or 0
                            mrp = pricing.get("mrp", {}).get("value") or final_price
                            total_discount = pricing.get("totalDiscount")

                            sale_price = float(final_price)
                            original_price = float(mrp)
                            if original_price <= 0:
                                original_price = sale_price

                            if original_price > 0 and sale_price > 0:
                                discount = float(total_discount) if total_discount is not None else round(((original_price - sale_price) / original_price) * 100, 2)
                            else:
                                discount = 0.0

                            base_url = val.get("baseUrl", "")
                            prod_url = f"https://www.flipkart.com{base_url}" if base_url.startswith("/") else f"https://www.flipkart.com/{base_url}"

                            media = val.get("media", {})
                            images = media.get("images", [])
                            image_url = ""
                            if images and isinstance(images, list):
                                raw_img = images[0].get("url", "")
                                if raw_img:
                                    image_url = raw_img.replace("{@width}", "600").replace("{@height}", "720").replace("{@quality}", "80")
                                    if image_url.startswith("http://"):
                                        image_url = image_url.replace("http://", "https://")

                            products.append({
                                "id": f"flipkart:{pid}",
                                "name": title,
                                "sale_price": sale_price,
                                "original_price": original_price,
                                "discount_percentage": discount,
                                "url": prod_url,
                                "image_url": image_url,
                                "source": "Flipkart"
                            })
                    for v in obj.values():
                        extract_from_slots(v)
                elif isinstance(obj, list):
                    for item in obj:
                        extract_from_slots(item)

            extract_from_slots(slots)
            if products:
                logger.info("   🔎 [Flipkart] Extracted %d products via JSON state for '%s'.", len(products), keyword)
                return products
    except Exception as e:
        logger.debug("   ℹ️ Flipkart JSON parser fallback triggered: %s", e)

    # 2. Fallback Method: HTML DOM parsing (using div[data-id] and /p/ links)
    try:
        soup = BeautifulSoup(response.content, "html.parser")
        cards = soup.select("div[data-id]") or soup.select("div._1xHGtK") or soup.select("div._4ddC5M")
        products = []

        for card in cards[:25]:
            prod_id = card.get("data-id")
            # Anchor with /p/ path or pid parameter
            link_a = card.select_one("a[href*='/p/']") or card.select_one("a[href*='pid=']") or card.select_one("a")
            if not link_a or not link_a.get("href"):
                continue

            href = link_a.get("href")
            prod_url = f"https://www.flipkart.com{href}" if href.startswith("/") else href
            prod_url = prod_url.split("?")[0]

            if not prod_id and "pid=" in href:
                pid_match = re.search(r"pid=([A-Z0-9]+)", href)
                if pid_match:
                    prod_id = pid_match.group(1)
            if not prod_id:
                prod_id = prod_url.split("/")[-1]

            # Title
            title_el = card.select_one("a.IRpwTa") or card.select_one(".p0C73x a") or card.select_one("a[title]")
            title = title_el.get("title") or title_el.get_text(strip=True) if title_el else ""
            brand_el = card.select_one("div._2WkVRV")
            if brand_el and brand_el.get_text(strip=True):
                brand = brand_el.get_text(strip=True)
                title = f"{brand} - {title}" if title else brand

            if not title:
                title = "Flipkart Fashion Item"

            # Prices
            sale_el = card.select_one("div._30jeq3") or card.select_one("div.Nx9bqj")
            mrp_el = card.select_one("div._3I9aeH") or card.select_one("div.yRaY8j")

            sale_price = clean_price(sale_el.get_text(strip=True)) if sale_el else 0.0
            original_price = clean_price(mrp_el.get_text(strip=True)) if mrp_el else sale_price
            if original_price <= 0:
                original_price = sale_price

            if sale_price <= 0:
                continue

            discount = round(((original_price - sale_price) / original_price) * 100, 2) if original_price > 0 else 0.0

            img_el = card.select_one("img")
            img_url = img_el.get("src") or img_el.get("data-src") or "" if img_el else ""

            products.append({
                "id": f"flipkart:{prod_id}",
                "name": title,
                "sale_price": sale_price,
                "original_price": original_price,
                "discount_percentage": discount,
                "url": prod_url,
                "image_url": img_url,
                "source": "Flipkart"
            })

        logger.info("   🔎 [Flipkart] Extracted %d products via HTML DOM for '%s'.", len(products), keyword)
        return products
    except Exception as e:
        logger.error("   ❌ [Flipkart] Parsing error: %s", e)
        return []


def scrape_amazon(keyword: str, session: Optional[requests.Session] = None) -> List[Dict]:
    """
    Crawls Amazon India search listings.
    Uses data-asin attributes, /dp/ product links, price-whole selectors, and fallback regex.
    Detects CAPTCHA challenges and blocks.
    """
    client = session or requests.Session()
    query = keyword.strip().replace(" ", "+")
    url = f"https://www.amazon.in/s?k={query}"

    logger.info("📡 [Amazon] Crawling search page: %s", url)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9,en-IN;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Referer": "https://www.google.com/",
    }

    try:
        response = client.get(url, headers=headers, timeout=12)
        if response.status_code in (403, 429, 503):
            raise PlatformBlockedException("Amazon", f"HTTP {response.status_code}")
        if response.status_code != 200:
            logger.warning("   ⚠️ Amazon returned HTTP %d", response.status_code)
            return []

        # Anti-bot detection
        text_lower = response.text.lower()
        if (
            "api-services-support@amazon.com" in response.text or
            "robot check" in text_lower or
            ("captcha" in text_lower and "enter the characters" in text_lower)
        ):
            raise PlatformBlockedException("Amazon", "Robot check / CAPTCHA challenge")
    except PlatformBlockedException:
        raise
    except Exception as e:
        logger.error("   ❌ Amazon network error: %s", e)
        return []

    products = []
    # Fast regex chunk parser (robust against class mutations across categories)
    try:
        asin_blocks = re.split(r'(data-asin="[A-Z0-9]{10}")', response.text)
        for i in range(1, len(asin_blocks), 2):
            asin_match = re.search(r'data-asin="([A-Z0-9]{10})"', asin_blocks[i])
            if not asin_match:
                continue
            asin = asin_match.group(1)
            chunk = asin_blocks[i + 1] if i + 1 < len(asin_blocks) else ""

            # Title
            title_m = re.search(r"<h2[^>]*>.*?<span[^>]*>(.*?)</span>.*?</h2>", chunk, re.DOTALL)
            if not title_m:
                title_m = re.search(r'class="[^"]*a-size-base-plus[^"]*">([^<]+)</span>', chunk)
            if not title_m:
                continue
            title = re.sub(r"<[^>]+>", "", title_m.group(1)).strip()
            if not title:
                continue

            # Product URL with /dp/ pattern
            url_m = re.search(r'href="(/[^"?]+/[dD][pP]/[A-Z0-9]{10}[^"?]*)"', chunk)
            if url_m:
                prod_url = "https://www.amazon.in" + url_m.group(1).split("?")[0]
            else:
                prod_url = f"https://www.amazon.in/dp/{asin}"

            # Sale Price
            price_m = re.search(r'class="a-price-whole">([0-9,]+)', chunk)
            if not price_m:
                continue
            sale_price = float(price_m.group(1).replace(",", ""))

            # MRP (Original Price)
            mrp_m = re.search(r'class="a-price a-text-price[^"]*".*?class="a-offscreen">₹?([0-9,.]+)', chunk, re.DOTALL)
            if mrp_m:
                original_price = float(mrp_m.group(1).replace(",", ""))
            else:
                original_price = sale_price

            if original_price <= 0 or sale_price <= 0:
                continue

            discount = round(((original_price - sale_price) / original_price) * 100, 2)

            # Image
            img_m = re.search(r'class="[^"]*s-image[^"]*"[^>]*src="([^"]+)"', chunk)
            img_url = img_m.group(1) if img_m else ""

            products.append({
                "id": f"amazon:{asin}",
                "name": title,
                "sale_price": sale_price,
                "original_price": original_price,
                "discount_percentage": discount,
                "url": prod_url,
                "image_url": img_url,
                "source": "Amazon India"
            })

        logger.info("   🔎 [Amazon] Extracted %d products for '%s'.", len(products), keyword)
        return products
    except Exception as e:
        logger.error("   ❌ [Amazon] Parsing error: %s", e)
        return []


# --- PERSISTENT DUPLICATE & PRICE-DROP STORAGE ---

def load_posted_deals() -> Dict[str, Dict]:
    """
    Loads posted deals from config.DUPLICATE_DB_FILE.
    Maintains backwards compatibility with legacy list-of-IDs format.
    Normalizes legacy keys without platform prefix:
    - all digits -> "myntra:<key>"
    - alphanumeric of length 16 -> "flipkart:<key>"
    - alphanumeric of length 10 -> "amazon:<key>"
    Writes normalized dictionary back to file atomically if any legacy keys were normalized.
    If the JSON file exists but fails to parse, preserves copy to posted_deals.corrupt.<timestamp>.json and returns {}.
    Schema per item: {"last_alert_price": Optional[float], "last_alert_ts": Optional[str]}
    """
    if not config.DUPLICATE_DB_FILE.exists():
        return {}
    try:
        with open(config.DUPLICATE_DB_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as parse_err:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        corrupt_backup = config.DUPLICATE_DB_FILE.parent / f"posted_deals.corrupt.{ts}.json"
        try:
            shutil.copy2(config.DUPLICATE_DB_FILE, corrupt_backup)
            logger.error("❌ Corrupted duplicate DB detected! Backed up to %s", corrupt_backup)
        except Exception as copy_err:
            logger.error("❌ Failed to backup corrupted duplicate DB: %s", copy_err)
        logger.error("⚠️ Error parsing duplicate DB (%s): %s", config.DUPLICATE_DB_FILE, parse_err)
        return {}

    try:
        raw_dict: Dict[str, Dict] = {}
        if isinstance(data, list):
            # Migrate legacy list to new dictionary schema
            raw_dict = {str(item): {"last_alert_price": None, "last_alert_ts": None} for item in data}
        elif isinstance(data, dict):
            raw_dict = data
        else:
            return {}

        normalized: Dict[str, Dict] = {}
        needs_write = isinstance(data, list)

        for k, v in raw_dict.items():
            key_str = str(k).strip()
            norm_key = key_str
            if ":" not in key_str:
                if key_str.isdigit():
                    norm_key = f"myntra:{key_str}"
                elif key_str.isalnum() and len(key_str) == 16:
                    norm_key = f"flipkart:{key_str}"
                elif key_str.isalnum() and len(key_str) == 10:
                    norm_key = f"amazon:{key_str}"

            if norm_key != key_str:
                needs_write = True

            val = v if isinstance(v, dict) else {"last_alert_price": None, "last_alert_ts": None}
            normalized[norm_key] = val

        if needs_write:
            try:
                tmp_file = str(config.DUPLICATE_DB_FILE) + ".tmp"
                with open(tmp_file, "w", encoding="utf-8") as f:
                    json.dump(normalized, f, indent=4)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_file, config.DUPLICATE_DB_FILE)
                logger.info("💾 Normalized deal keys and updated %s", config.DUPLICATE_DB_FILE)
            except Exception as write_err:
                logger.error("⚠️ Error saving normalized duplicate DB (%s): %s", config.DUPLICATE_DB_FILE, write_err)

        return normalized
    except Exception as e:
        logger.error("⚠️ Error loading duplicate DB (%s): %s", config.DUPLICATE_DB_FILE, e)
        return {}


def save_posted_deal(product_id: str, sale_price: float = 0.0):
    """
    Saves/updates single row per product with (last_alert_price, last_alert_ts) atomically.
    """
    deals = load_posted_deals()
    deals[str(product_id)] = {
        "last_alert_price": float(sale_price) if sale_price is not None else 0.0,
        "last_alert_ts": datetime.now().isoformat()
    }
    try:
        tmp_file = str(config.DUPLICATE_DB_FILE) + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(deals, f, indent=4)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_file, config.DUPLICATE_DB_FILE)
    except Exception as e:
        logger.error("⚠️ Error saving duplicate DB (%s): %s", config.DUPLICATE_DB_FILE, e)


# --- TELEGRAM FORMATTING & DISPATCH ---

def format_deal_message(product: dict, discount_percent: float) -> str:
    """
    Formats a shorter clean deal message for public channel broadcasts.
    """
    saving = int(round(product["original_price"] - product["sale_price"]))
    mrp = int(round(product["original_price"]))
    deal = int(round(product["sale_price"]))
    escaped_name = html.escape(product["name"])
    escaped_url = html.escape(product["url"])
    label = "🔥 <b>MEGA DEAL ALERT</b> 🔥" if discount_percent >= 90.0 else "⚡ <b>HOT DEAL ALERT</b> ⚡"

    return (
        f"{label}\n\n"
        f"📦 <b>{escaped_name}</b>\n\n"
        f"💰 <b>MRP:</b> <s>₹{mrp}</s> | <b>Deal Price:</b> ₹{deal}\n"
        f"🎯 <b>Discount:</b> {int(round(discount_percent))}% OFF (Save ₹{saving})\n\n"
        f"🛒 <b>Order Here:</b>\n"
        f"👉 {escaped_url}"
    )


def format_review_message(
    product: dict,
    discount_percent: float,
    is_price_drop: bool = False,
    old_price: Optional[float] = None,
    affiliate_url: Optional[str] = None
) -> str:
    """
    Builds clean, minimal HTML deal message containing only:
    Product name, MRP, Deal Price, Discount, You Save, and single Product URL (EarnKaro link).
    """
    saving = int(round(product["original_price"] - product["sale_price"]))
    mrp = int(round(product["original_price"]))
    deal = int(round(product["sale_price"]))
    escaped_name = html.escape(product["name"])

    final_url = affiliate_url if affiliate_url else product.get("url", "")
    escaped_url = html.escape(final_url)

    message = (
        f"📦 <b>Product:</b> {escaped_name}\n\n"
        f"💵 <b>MRP:</b> <s>₹{mrp}</s>\n"
        f"💰 <b>Deal Price:</b> ₹{deal}\n"
        f"📉 <b>Discount:</b> {int(round(discount_percent))}% OFF\n"
        f"💸 <b>You Save:</b> ₹{saving}\n\n"
        f"🔗 <b>Product URL:</b>\n"
        f"{escaped_url}"
    )
    return message


async def send_telegram_message(
    bot_client: telegram.Bot,
    channel_chat_id: str,
    message: str,
    image_url: str,
    product_url: str = None
) -> bool:
    """
    Sends photo card or text message to Telegram channel with inline Buy Now button.
    Includes RetryAfter flood-control backoff and photo failure fallbacks.
    Returns True if sent successfully, False otherwise.
    """
    reply_markup = None
    if product_url:
        keyboard = [[telegram.InlineKeyboardButton("🛒 Buy Now", url=product_url)]]
        reply_markup = telegram.InlineKeyboardMarkup(keyboard)

    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            if image_url and image_url.startswith("http"):
                try:
                    await bot_client.send_photo(
                        chat_id=channel_chat_id,
                        photo=image_url,
                        caption=message,
                        parse_mode="HTML",
                        reply_markup=reply_markup
                    )
                    return True
                except telegram.error.RetryAfter:
                    raise
                except Exception as photo_err:
                    logger.warning("   ⚠️ Photo upload failed (%s). Falling back to text message...", photo_err)

            # Fallback text message
            await bot_client.send_message(
                chat_id=channel_chat_id,
                text=message,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
            return True

        except telegram.error.RetryAfter as rate_err:
            wait_time = int(rate_err.retry_after) + 1
            logger.warning("   ⏳ Telegram Flood Control (RetryAfter): Sleeping for %ds...", wait_time)
            await asyncio.sleep(wait_time)
        except Exception as e:
            logger.error("   ❌ Telegram broadcast failed (Attempt %d/%d): %s", attempt + 1, max_attempts, e)
            if attempt < max_attempts - 1:
                await asyncio.sleep(2)

    return False


# --- SCAN WORKFLOW LOGIC ---

async def scan_platform(
    platform_state: PlatformState,
    scraper_func,
    bot_client: Optional[telegram.Bot],
    channel_chat_id: str,
    keywords_batch: List[str],
    shutdown_event: Optional[asyncio.Event] = None
):
    """
    Scans a single platform across a batch of keywords.
    Each platform execution is independent.
    """
    name = platform_state.name
    logger.info("▶️ [%s] Starting scheduled deal scan across %d keywords...", name, len(keywords_batch))

    posted_deals = load_posted_deals()
    threshold = config.DISCOUNT_THRESHOLD
    max_posts = config.MAX_DEALS_PER_SCAN
    total_found = 0
    candidate_deals: List[Dict] = []
    seen_candidate_ids = set()

    try:
        for idx, keyword in enumerate(keywords_batch):
            if shutdown_event and shutdown_event.is_set():
                logger.info("🛑 [%s] Shutdown requested. Halting keyword scraping.", name)
                break

            # Polite random delay between requests (5-15s)
            if idx > 0:
                delay = random.uniform(config.REQUEST_DELAY_MIN, config.REQUEST_DELAY_MAX)
                logger.info("   ⏳ Polite delay: sleeping %.1fs before next keyword...", delay)
                if shutdown_event:
                    try:
                        await asyncio.wait_for(shutdown_event.wait(), timeout=delay)
                        logger.info("🛑 [%s] Shutdown requested during delay. Halting scan.", name)
                        break
                    except asyncio.TimeoutError:
                        pass
                else:
                    await asyncio.sleep(delay)

            if shutdown_event and shutdown_event.is_set():
                break

            try:
                products = scraper_func(keyword, session=platform_state.session)
            except PlatformBlockedException as block_err:
                platform_state.record_block(block_err.reason)
                return

            total_found += len(products)

            for product in products:
                discount = product.get("discount_percentage", 0.0)
                sale_price = product.get("sale_price", 0.0)
                original_price = product.get("original_price", 0.0)
                product_id = product.get("id")

                if not product_id or product_id in seen_candidate_ids:
                    continue

                # 1. Sanity filters & discount threshold
                if (
                    sale_price < config.MIN_SALE_PRICE
                    or original_price < sale_price
                    or discount > 99.0
                    or discount < threshold
                ):
                    continue

                # 2. Duplicate & Price-Drop Check
                is_price_drop = False
                old_alert_price = None

                if product_id in posted_deals:
                    prev_info = posted_deals[product_id]
                    last_price = prev_info.get("last_alert_price")
                    last_ts = prev_info.get("last_alert_ts")

                    # If last_alert_price is None (legacy entry), treat it as already posted and skip.
                    if last_price is None:
                        continue

                    # Alert again only if sale_price <= last_alert_price * 0.95
                    # OR if more than 7 days passed since last_alert_ts and discount >= threshold
                    more_than_7_days = False
                    if last_ts:
                        try:
                            alert_dt = datetime.fromisoformat(last_ts)
                            if datetime.now() - alert_dt > timedelta(days=7):
                                more_than_7_days = True
                        except (ValueError, TypeError):
                            more_than_7_days = False

                    is_drop = (sale_price <= last_price * 0.95)
                    repost_7d = (more_than_7_days and discount >= threshold)

                    if not (is_drop or repost_7d):
                        continue

                    if is_drop:
                        is_price_drop = True
                        old_alert_price = last_price

                seen_candidate_ids.add(product_id)
                candidate_deals.append({
                    "product": product,
                    "discount_percentage": discount,
                    "is_price_drop": is_price_drop,
                    "old_alert_price": old_alert_price
                })

        # Sort candidate deals by discount_percentage descending
        candidate_deals.sort(key=lambda x: x["discount_percentage"], reverse=True)

        # Post up to MAX_DEALS_PER_SCAN
        posts_in_scan = 0
        for item in candidate_deals:
            if shutdown_event and shutdown_event.is_set():
                logger.info("🛑 [%s] Shutdown requested. Halting deal dispatch.", name)
                break

            if posts_in_scan >= max_posts:
                logger.info("   ⚠️ [%s] Rate limit (%d deals) reached for this scan. Halting posts.", name, max_posts)
                break

            product = item["product"]
            discount = item["discount_percentage"]
            sale_price = product["sale_price"]
            product_id = product["id"]
            is_price_drop = item["is_price_drop"]
            old_alert_price = item["old_alert_price"]

            logger.info(
                "   🔥 [%s] ALERT: %s | ₹%.0f (MRP ₹%.0f, %d%% off)",
                name, product["name"][:35], sale_price, product["original_price"], int(discount)
            )

            # Auto-convert product URL to EarnKaro affiliate link
            orig_url = product.get("url", "")
            affiliate_url = await asyncio.to_thread(convert_to_earnkaro_link, orig_url)

            deal_msg = format_review_message(
                product=product,
                discount_percent=discount,
                is_price_drop=is_price_drop,
                old_price=old_alert_price,
                affiliate_url=affiliate_url
            )

            sent_ok = False
            if bot_client and channel_chat_id:
                sent_ok = await send_telegram_message(
                    bot_client=bot_client,
                    channel_chat_id=channel_chat_id,
                    message=deal_msg,
                    image_url=product.get("image_url", ""),
                    product_url=affiliate_url or orig_url
                )
            else:
                logger.error("   ❌ Telegram credentials or review channel missing for broadcast.")

            if sent_ok:
                save_posted_deal(product_id, sale_price)
                posted_deals[product_id] = {
                    "last_alert_price": sale_price,
                    "last_alert_ts": datetime.now().isoformat()
                }
                posts_in_scan += 1

                # Polite delay after sending Telegram message
                if shutdown_event:
                    try:
                        await asyncio.wait_for(shutdown_event.wait(), timeout=random.uniform(3.0, 6.0))
                        logger.info("🛑 [%s] Shutdown requested during post delay. Exiting loop.", name)
                        break
                    except asyncio.TimeoutError:
                        pass
                else:
                    await asyncio.sleep(random.uniform(3.0, 6.0))
            else:
                logger.error("   ❌ Failed to send Telegram alert for %s. Deal not recorded as posted.", product_id)

        # Successfully scanned without blockades
        platform_state.record_success()
        logger.info(
            "⏹️ [%s] Scan complete. Found %d products (%d candidates), published %d alerts.",
            name, total_found, len(candidate_deals), posts_in_scan
        )

    except PlatformBlockedException as block_err:
        platform_state.record_block(block_err.reason)
    except Exception as e:
        logger.exception("❌ [%s] Unexpected error in scan: %s", name, e)


# --- MAIN DAEMON & SCHEDULER LOOP ---

async def main():
    global _lock_file_fd
    lock_path = config.BASE_DIR / "bot.lock"
    try:
        _lock_file_fd = open(lock_path, "a+")
        fcntl.flock(_lock_file_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        logger.error("Another instance is already running. Exiting.")
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("🤖 TELEGRAM DEALS BOT DAEMON INITIALIZING 🤖")
    logger.info("⚙️ Mode: 24/7 Linux VPS (systemd managed)")
    logger.info("=" * 60)

    # Validate Telegram configuration
    if not config.is_configured():
        logger.error("❌ Telegram credentials missing in .env! Open .env and set TELEGRAM_BOT_TOKEN and TELEGRAM_PRIVATE_REVIEW_CHANNEL.")
        sys.exit(1)

    bot_client = telegram.Bot(token=config.BOT_TOKEN)
    review_channel = config.PRIVATE_REVIEW_CHANNEL

    # Send startup test message
    try:
        logger.info("📨 Sending startup notification to Review Channel (%s)...", review_channel)
        startup_msg = (
            "🤖 <b>Deal Bot Online & Active!</b>\n\n"
            "Monitoring Myntra, Flipkart, and Amazon India for <b>80%+ discounts</b> and price drops."
        )
        await bot_client.send_message(chat_id=review_channel, text=startup_msg, parse_mode="HTML")
        logger.info("✅ Telegram connection verified successfully.")
    except Exception as e:
        logger.warning("⚠️ Could not send startup message: %s. (Ensure bot is an Admin in the channel)", e)

    # Initialize Platform State Trackers
    platforms: Dict[str, Tuple[PlatformState, callable]] = {
        "myntra": (PlatformState("Myntra", config.MYNTRA_INTERVAL_MIN), scrape_myntra),
        "flipkart": (PlatformState("Flipkart", config.FLIPKART_INTERVAL_MIN), scrape_flipkart),
        "amazon": (PlatformState("Amazon", config.AMAZON_INTERVAL_MIN), scrape_amazon),
    }

    # Setup Graceful Shutdown via SIGINT / SIGTERM
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, shutdown_event.set)
    loop.add_signal_handler(signal.SIGTERM, shutdown_event.set)

    logger.info("🔄 Bot scheduler running. Intervals: Myntra %dm | Flipkart %dm | Amazon %dm",
                config.MYNTRA_INTERVAL_MIN, config.FLIPKART_INTERVAL_MIN, config.AMAZON_INTERVAL_MIN)

    # Continuous Daemon Loop
    while not shutdown_event.is_set():
        try:
            for p_key, (p_state, p_func) in platforms.items():
                if shutdown_event.is_set():
                    break

                is_ready, reason = p_state.is_due()
                if not is_ready:
                    logger.debug("   [Skip %s]: %s", p_state.name, reason)
                    continue

                # Pick a small subset of keywords per scan for the specific platform
                platform_kw_map = {
                    "myntra": config.KEYWORDS_MYNTRA,
                    "flipkart": config.KEYWORDS_FLIPKART,
                    "amazon": config.KEYWORDS_AMAZON,
                }
                platform_keywords = platform_kw_map.get(p_key, config.KEYWORDS)
                k_count = min(config.MAX_KEYWORDS_PER_SCAN, len(platform_keywords))
                scan_keywords = random.sample(platform_keywords, k_count) if k_count > 0 else platform_keywords

                # Execute platform scan independently in its own try/except
                try:
                    await scan_platform(
                        platform_state=p_state,
                        scraper_func=p_func,
                        bot_client=bot_client,
                        channel_chat_id=review_channel,
                        keywords_batch=scan_keywords,
                        shutdown_event=shutdown_event
                    )
                except Exception as plat_err:
                    logger.exception("❌ Error while scanning %s: %s", p_state.name, plat_err)

        except Exception as loop_err:
            logger.exception("❌ Unexpected loop error: %s", loop_err)

        # Sleep briefly between schedule checks (30s) while checking shutdown_event
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            pass

    logger.info("👋 Deal Bot daemon shutdown cleanly.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except SystemExit as e:
        sys.exit(e.code)
