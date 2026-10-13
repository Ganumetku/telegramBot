"""
earnkaro_converter.py
---------------------
Automatic affiliate link converter for EarnKaro using Playwright.
Uses saved session (earnkaro_session.json) to convert product URLs to
profit/affiliate links (e.g. fktr.in, myntr.it, ekaro.in).
"""

import asyncio
import concurrent.futures
import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger("DealBot")

BASE_DIR = Path(__file__).resolve().parent
SESSION_FILE = BASE_DIR / "earnkaro_session.json"
CACHE_FILE = BASE_DIR / "earnkaro_links_cache.json"
CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# Load persistent link cache
_LINK_CACHE = {}
if CACHE_FILE.exists():
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            _LINK_CACHE = json.load(f)
    except Exception:
        _LINK_CACHE = {}


def _save_cache():
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_LINK_CACHE, f, indent=2)
    except Exception:
        pass


def is_earnkaro_configured() -> bool:
    """Checks if the saved Playwright session file exists."""
    return SESSION_FILE.exists()


def _convert_browser_worker(product_url: str) -> str:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path=CHROME_PATH if os.path.exists(CHROME_PATH) else None,
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
            )
            context = browser.new_context(storage_state=str(SESSION_FILE))
            page = context.new_page()

            page.goto("https://earnkaro.com/create-earn-link", timeout=25000)
            page.wait_for_timeout(1500)

            inp = page.locator("#deallink")
            if inp.count() == 0:
                logger.warning("EarnKaro #deallink input not found. Session may need re-login.")
                browser.close()
                return product_url

            inp.fill(product_url)
            btn = page.locator('button:has-text("MAKE PROFIT LINK")')
            btn.click()

            # The converted link is populated in #deallinkshorturl
            converted_link = None
            short_locator = page.locator("#deallinkshorturl")

            for _ in range(8):  # check up to 4 seconds
                page.wait_for_timeout(500)
                if short_locator.count() > 0:
                    val = short_locator.input_value().strip()
                    if val and ("http://" in val or "https://" in val):
                        converted_link = val
                        break

            browser.close()

            if converted_link:
                logger.info("🔗 [EarnKaro] Converted: %s -> %s", product_url, converted_link)
                _LINK_CACHE[product_url] = converted_link
                _save_cache()
                return converted_link
            else:
                logger.info("ℹ️ [EarnKaro] No converted link returned for %s (Partner status may be pending)", product_url)
                return product_url

    except Exception as e:
        logger.error("❌ [EarnKaro] Error converting link %s: %s", product_url, e)
        return product_url


def convert_to_earnkaro_link(product_url: str) -> str:
    """
    Converts a retailer product URL (Myntra, Flipkart, Amazon) to an EarnKaro profit link.
    Returns the affiliate link if successful; falls back to the original URL otherwise.
    Safely dispatches to a background thread if an asyncio event loop is detected.
    """
    if not product_url or not product_url.startswith("http"):
        return product_url

    # Check cache
    if product_url in _LINK_CACHE:
        return _LINK_CACHE[product_url]

    if not is_earnkaro_configured():
        logger.debug("EarnKaro session not found (%s). Returning original URL.", SESSION_FILE)
        return product_url

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(_convert_browser_worker, product_url).result()
    else:
        return _convert_browser_worker(product_url)


if __name__ == "__main__":
    test_links = [
        "https://www.myntra.com/1364628",
        "https://www.flipkart.com/puma-smash-v2-sneakers-men/p/itm2881a2da38e21"
    ]
    for tl in test_links:
        print(f"Testing {tl}...")
        res = convert_to_earnkaro_link(tl)
        print(f" -> Result: {res}\n")
