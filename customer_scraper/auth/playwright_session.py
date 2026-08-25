"""
Playwright Persistent Context & Opened Browser Session Manager.

Connects directly to an already-opened browser instance via CDP (Chrome DevTools Protocol),
or launches a persistent browser window with remote debugging enabled.
Extracts active cookies, local storage, and headers directly from the opened browser
WITHOUT closing the browser window, allowing continuous reuse and zero re-logins.
"""

import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Dict, Optional
import urllib.request

from config.settings import BASE_URL, BROWSER_PROFILE_DIR, CDP_PORT, CDP_URL

logger = logging.getLogger("customer_scraper")


def is_browser_running_on_cdp(cdp_url: str = CDP_URL) -> bool:
    """
    Checks if a browser instance is actively running and accessible on the CDP port.
    """
    try:
        req = urllib.request.Request(f"{cdp_url}/json/version", headers={"User-Agent": "CustomerScraper"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            return resp.status == 200
    except Exception:
        return False


def find_browser_executable() -> Optional[str]:
    """
    Finds the Chrome or Chromium executable on the Windows system.
    """
    # 1. Check common Google Chrome paths on Windows
    program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    program_files_x86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
    local_app_data = os.environ.get("LOCALAPPDATA", "")

    potential_paths = [
        Path(program_files) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(program_files_x86) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(local_app_data) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(program_files) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(program_files_x86) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
    ]

    for p in potential_paths:
        if p.exists():
            return str(p)

    return None


class PlaywrightSessionHandler:
    """
    Handles session extraction from an opened browser via CDP or Persistent Context,
    ensuring the browser remains open and alive across scraper runs.
    """

    def __init__(
        self,
        profile_dir: Optional[Path] = None,
        base_url: Optional[str] = None,
        cdp_port: int = CDP_PORT,
    ):
        self.profile_dir = Path(profile_dir or BROWSER_PROFILE_DIR)
        self.base_url = base_url or BASE_URL
        self.cdp_port = cdp_port
        self.cdp_url = f"http://127.0.0.1:{self.cdp_port}"
        self.profile_dir.mkdir(parents=True, exist_ok=True)

    def is_browser_open(self) -> bool:
        """Returns True if a browser is currently running and listening on the CDP port."""
        return is_browser_running_on_cdp(self.cdp_url)

    def extract_session_from_opened_browser(self) -> Optional[Dict[str, Any]]:
        """
        Connects to the already-opened browser over CDP, reads active cookies
        and headers, and disconnects WITHOUT closing the browser window.
        """
        if not self.is_browser_open():
            return None

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.error("Playwright is not installed. Please run: pip install playwright")
            return None

        extracted_cookies: Dict[str, str] = {}
        extracted_headers: Dict[str, str] = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/",
        }

        try:
            with sync_playwright() as p:
                logger.info("Connecting to already opened browser at %s via CDP...", self.cdp_url)
                browser = p.chromium.connect_over_cdp(self.cdp_url)

                # Iterate through all browser contexts
                for context in browser.contexts:
                    # Extract cookies from context
                    for c in context.cookies():
                        name = c.get("name")
                        val = c.get("value")
                        if name and val is not None:
                            extracted_cookies[name] = val

                    # Check open pages for user agent or CSRF tokens
                    for page in context.pages:
                        try:
                            page_url = page.url
                            if "seller-support.fkcloud.it" in page_url or "suv-flipkart" in page_url:
                                ua = page.evaluate("() => navigator.userAgent")
                                if ua:
                                    extracted_headers["User-Agent"] = ua
                        except Exception:
                            pass

                # Extract CSRF token if present
                if "XyZ7pQ9rS2T1uV8wA3bC6dE4fG0h" in extracted_cookies:
                    extracted_headers["FK-CSRF-TOKEN"] = extracted_cookies["XyZ7pQ9rS2T1uV8wA3bC6dE4fG0h"]

                # Note: We do NOT call browser.close() or context.close()!
                # Exiting the sync_playwright context simply disconnects the CDP client.
                # The user's opened browser window remains running and completely open.

        except Exception as e:
            logger.debug("Could not extract session from running CDP browser: %s", str(e))
            return None

        if extracted_cookies:
            logger.info(
                "Successfully extracted %d cookies directly from opened browser.",
                len(extracted_cookies),
            )
            return {
                "cookies": extracted_cookies,
                "headers": extracted_headers,
            }

        return None

    def launch_browser_and_keep_open(self) -> None:
        """
        Launches the browser with the persistent profile and remote debugging enabled
        in an independent, detached background process so it stays open indefinitely.
        """
        if self.is_browser_open():
            logger.info("Browser is already running on port %d.", self.cdp_port)
            return

        browser_exe = find_browser_executable()
        if not browser_exe:
            # Fallback to python playwright CLI or default
            browser_exe = "chrome"

        cmd = [
            browser_exe,
            f"--remote-debugging-port={self.cdp_port}",
            f"--user-data-dir={str(self.profile_dir)}",
            "--no-first-run",
            "--no-default-browser-check",
            "--start-maximized",
            self.base_url,
        ]

        logger.info("Launching persistent browser: %s", " ".join(cmd[:3]))
        try:
            # Spawn detached process so it never closes when scraper finishes
            if sys.platform == "win32":
                subprocess.Popen(cmd, creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
            else:
                subprocess.Popen(cmd, start_new_session=True)
            
            # Wait for CDP endpoint to become ready
            for _ in range(15):
                time.sleep(0.5)
                if self.is_browser_open():
                    logger.info("Browser is now ready on CDP port %d.", self.cdp_port)
                    return
        except Exception as e:
            logger.error("Failed to launch detached browser process: %s", str(e))

    def get_or_prompt_session(self, force_login_prompt: bool = False) -> Dict[str, Any]:
        """
        Gets the session directly from the opened browser.
        If the browser is not running, launches it and keeps it open.
        Prompts the user to log in if cookies are missing or if force_login_prompt is True.
        """
        # Step 1: Check if already open and has valid session cookies
        if not force_login_prompt and self.is_browser_open():
            session = self.extract_session_from_opened_browser()
            if session and session.get("cookies"):
                return session

        # Step 2: Ensure browser is launched and kept open
        if not self.is_browser_open():
            self.launch_browser_and_keep_open()

        # Step 3: Prompt user in terminal to complete login in the opened browser
        print("\n" + "=" * 72)
        print("  SESSION REQUIRED - USE OPENED BROWSER")
        print("=" * 72)
        print(f"  Target Portal: {self.base_url}")
        print("  1. The browser window is OPEN (it will remain open).")
        print("  2. Please log in to Flipkart Seller Support in the opened window.")
        print("  3. Complete any required 2FA, OTP, or SSO verification.")
        print("  4. Once you are logged in and see the dashboard:")
        print("     -> Press [ENTER] in this terminal to continue scraping.")
        print("=" * 72 + "\n")

        if sys.stdin.isatty():
            try:
                input("Press [ENTER] after logging in to the opened browser... ")
            except (KeyboardInterrupt, EOFError):
                logger.warning("Session capture prompt interrupted by user.")
        else:
            # Non-interactive wait loop
            logger.info("Waiting for login in opened browser...")
            for _ in range(60):
                time.sleep(2)
                session = self.extract_session_from_opened_browser()
                if session and len(session.get("cookies", {})) > 2:
                    return session

        # Step 4: Extract the session from the opened browser without closing it
        session = self.extract_session_from_opened_browser()
        if session and session.get("cookies"):
            return session

        # Return empty structure if no cookies found
        return {
            "cookies": {},
            "headers": {
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Origin": self.base_url,
                "Referer": f"{self.base_url}/",
            },
        }

    def extract_existing_profile_cookies(self) -> Dict[str, str]:
        """
        Extracts cookies from the opened browser or persistent profile.
        """
        if self.is_browser_open():
            session = self.extract_session_from_opened_browser()
            if session and session.get("cookies"):
                return session["cookies"]

        return {}
