"""
earnkaro_login.py
-----------------
Automated login helper using Playwright with persistent Chrome context.
Opens a visible Chrome window so the user can log in to EarnKaro.
Once logged in, it saves the session state for headless 24/7 background automation.
"""

import os
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent
PROFILE_DIR = BASE_DIR / "earnkaro_profile"
SESSION_FILE = BASE_DIR / "earnkaro_session.json"
CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

def main():
    print("=" * 60)
    print("🌐 EARNKARO PLAYWRIGHT LOGIN SETUP")
    print("=" * 60)
    print("👉 Opening Chrome window in front... Please log in to EarnKaro.")
    print("   (Enter Mobile Number & OTP in the opened window)")
    print("=" * 60)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            executable_path=CHROME_PATH if os.path.exists(CHROME_PATH) else None,
            headless=False,
            viewport={"width": 1280, "height": 850},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--start-maximized"
            ]
        )

        page = context.new_page() if not context.pages else context.pages[0]
        page.goto("https://earnkaro.com")

        # Bring window to front
        try:
            page.bring_to_front()
        except Exception:
            pass

        print("\n⏳ Chrome window is now OPEN in front of you!")
        print("👉 Please log in to EarnKaro. The script will automatically detect your login.\n")

        logged_in = False
        start_time = time.time()
        timeout_seconds = 900  # 15 minutes

        last_status_time = time.time()

        while time.time() - start_time < timeout_seconds:
            try:
                # Check login state
                is_logged = page.evaluate("""() => {
                    const text = document.body ? document.body.innerText : '';
                    const hasProfile = text.includes('My Profile') || text.includes('Profit Rates') || text.includes('Make Links');
                    const hasProfileIcon = !!document.querySelector('a[href*="profile"], a[href*="make-link"], [class*="profile"]');
                    const hasUser = !!localStorage.getItem('token') || !!localStorage.getItem('user_data') || !!localStorage.getItem('user');
                    return hasProfile || hasProfileIcon || hasUser;
                }""")

                current_url = page.url
                # If redirected to dashboard or create-earn-link
                if "create-earn-link" in current_url or "dashboard" in current_url or is_logged:
                    # Double check if it's really logged in by seeing if 'My Profile' or 'Make Links' is on page
                    text = page.inner_text("body")
                    if "My Profile" in text or "Make Links" in text or "create-earn-link" in current_url:
                        logged_in = True
                        break

            except Exception:
                pass

            if time.time() - last_status_time > 15:
                elapsed = int(time.time() - start_time)
                print(f"⏳ Still waiting for login... ({elapsed}s elapsed)")
                last_status_time = time.time()

            time.sleep(2)

        if logged_in:
            print("\n🎉 SUCCESS: LOGIN DETECTED!")
            # Ensure we visit make link page to warm up cookies
            try:
                page.goto("https://earnkaro.com/create-earn-link")
                page.wait_for_timeout(3000)
            except Exception:
                pass

            context.storage_state(path=str(SESSION_FILE))
            print(f"✅ Session successfully saved to {SESSION_FILE}!")
            print("🤖 Playwright is now completely configured to auto-generate links!")
        else:
            print("\n❌ Login timed out after 15 minutes.")

        context.close()

if __name__ == "__main__":
    main()
