"""
Playwright Persistent Context Session Manager.

Manages persistent browser contexts, interactive manual logins,
and automatic extraction/persistence of cookies, local storage,
and session credentials across scraper runs.
"""

import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from config.settings import BASE_URL, BROWSER_PROFILE_DIR

logger = logging.getLogger("customer_scraper")


class PlaywrightSessionHandler:
    """
    Handles browser automation and authentication using Playwright Persistent Context.
    """

    def __init__(
        self,
        profile_dir: Optional[Path] = None,
        base_url: Optional[str] = None,
    ):
        self.profile_dir = Path(profile_dir or BROWSER_PROFILE_DIR)
        self.base_url = base_url or BASE_URL
        self.profile_dir.mkdir(parents=True, exist_ok=True)

    def launch_login_session(self, timeout_seconds: int = 300) -> Dict[str, Any]:
        """
        Launches a persistent browser window to allow the user to manually log in.
        Once the user finishes logging in, extracts all cookies and headers,
        and saves them for API scraping.

        Args:
            timeout_seconds: Maximum time to wait for manual login if non-interactive.

        Returns:
            Dict containing 'cookies' (Dict[str, str]) and 'headers' (Dict[str, str]).
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.error(
                "Playwright is not installed. Please install it using: "
                "pip install playwright && playwright install chromium"
            )
            raise RuntimeError(
                "Playwright dependency missing. Run: pip install -r requirements.txt && playwright install chromium"
            )

        logger.info(
            "Launching Playwright Persistent Context browser at %s", self.profile_dir
        )

        extracted_cookies: Dict[str, str] = {}
        extracted_headers: Dict[str, str] = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/",
        }

        try:
            with sync_playwright() as p:
                # Launch persistent browser context using dedicated profile directory
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(self.profile_dir),
                    headless=False,
                    viewport={"width": 1280, "height": 850},
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--start-maximized",
                    ],
                    ignore_https_errors=True,
                )

                # Use existing page or create a new one
                page = context.pages[0] if context.pages else context.new_page()

                # Navigate to the target portal
                logger.info("Navigating to %s for authentication...", self.base_url)
                try:
                    page.goto(self.base_url, timeout=45000, wait_until="domcontentloaded")
                except Exception as nav_err:
                    logger.debug("Initial page navigation notice: %s", str(nav_err))

                try:
                    user_agent = page.evaluate("() => navigator.userAgent")
                    if user_agent:
                        extracted_headers["User-Agent"] = user_agent
                except Exception:
                    pass

                # Interactive prompt for manual user login
                print("\n" + "=" * 72)
                print("  PLAYWRIGHT PERSISTENT CONTEXT - AUTHENTICATION REQUIRED")
                print("=" * 72)
                print(f"  Target URL: {self.base_url}")
                print(f"  Profile Location: {self.profile_dir}")
                print("  1. A browser window has opened.")
                print("  2. Please log in to Flipkart Seller Support in the opened window.")
                print("  3. Complete any required 2FA / OTP verification.")
                print("  4. Once logged in and dashboard/portal is visible:")
                print("     -> Press [ENTER] in this terminal to continue scraping.")
                print("=" * 72 + "\n")

                if sys.stdin.isatty():
                    try:
                        input("Press [ENTER] once you have logged in to continue... ")
                    except (KeyboardInterrupt, EOFError):
                        logger.warning("Authentication login prompt interrupted by user.")
                else:
                    # Non-interactive fallback: wait for timeout or check for cookies
                    logger.info("Non-interactive terminal detected. Waiting up to %d seconds for session...", timeout_seconds)
                    start_wait = time.time()
                    while time.time() - start_wait < timeout_seconds:
                        cookies_list = context.cookies()
                        if any(c.get("name") == "XyZ7pQ9rS2T1uV8wA3bC6dE4fG0h" for c in cookies_list) or len(cookies_list) > 3:
                            logger.info("Detected active login session cookies automatically.")
                            break
                        time.sleep(3)

                # Wait 2 seconds for any background requests/storage to settle
                time.sleep(2)

                # Extract cookies from persistent context
                all_cookies = context.cookies()
                for c in all_cookies:
                    name = c.get("name")
                    value = c.get("value")
                    if name and value is not None:
                        extracted_cookies[name] = value

                # Check for CSRF token if present
                if "XyZ7pQ9rS2T1uV8wA3bC6dE4fG0h" in extracted_cookies:
                    extracted_headers["FK-CSRF-TOKEN"] = extracted_cookies["XyZ7pQ9rS2T1uV8wA3bC6dE4fG0h"]

                logger.info(
                    "Captured %d cookies from Playwright persistent session.",
                    len(extracted_cookies),
                )

                # Cleanly close context so all cookies/storage are flushed to profile directory
                context.close()

        except Exception as e:
            logger.error("Error during Playwright persistent session: %s", str(e))
            if "Executable doesn't exist" in str(e) or "playwright install" in str(e):
                print("\n[!] Playwright browser binary missing.")
                print("    Please run: playwright install chromium\n")
            raise

        return {
            "cookies": extracted_cookies,
            "headers": extracted_headers,
        }

    def extract_existing_profile_cookies(self) -> Dict[str, str]:
        """
        Attempts to read existing cookies from the persistent profile in headless mode
        without requiring user interaction if valid cookies are already stored.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return {}

        extracted: Dict[str, str] = {}
        try:
            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(self.profile_dir),
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled"],
                    ignore_https_errors=True,
                )
                cookies_list = context.cookies()
                for c in cookies_list:
                    name = c.get("name")
                    val = c.get("value")
                    if name and val is not None:
                        extracted[name] = val
                context.close()
        except Exception as e:
            logger.debug("Silent persistent profile cookie extraction skipped: %s", str(e))

        return extracted
