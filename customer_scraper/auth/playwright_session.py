"""
Playwright CDP Session Handler for Flipkart Seller Support.

Features:
1. Connects to running Chrome via Chrome DevTools Protocol (CDP: http://127.0.0.1:9222).
2. Dynamic page navigation & reload (e.g. /#app/seller/{seller_id}/info).
3. Session capture on expiry: captures cookies and request headers (including FK-CSRF-TOKEN,
   connect.sid, XyZ7pQ9rS2T1uV8wA3bC6dE4fG0h) from automatic API triggers and saves to session.json.
4. Keep-Alive loop: reloads Flipkart tab every 10 minutes without overwriting session.json.
"""

import asyncio
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any, Dict, Optional, Tuple
import urllib.request

from config.settings import (
    BASE_URL,
    CDP_PORT,
    CDP_URL,
    DEFAULT_SELLER_ID,
    REFRESH_INTERVAL,
    SELLER_APPROVALS_URL,
    SELLER_INFO_URL,
    SESSION_CONFIG_PATH,
)

logger = logging.getLogger("customer_scraper")

DEFAULT_FALLBACK_SELLER_ID = DEFAULT_SELLER_ID or "218598a2b41c4bcd"


def is_cdp_available(cdp_url: str = CDP_URL) -> bool:
    """Checks whether Chrome is actively listening on the CDP debugging port."""
    try:
        req = urllib.request.Request(
            f"{cdp_url}/json/version",
            headers={"User-Agent": "FlipkartSessionHandler"},
        )
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            return resp.status == 200
    except Exception:
        return False


def get_default_headers() -> Dict[str, str]:
    """Returns baseline default headers matching Flipkart portal requests."""
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/151.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
        "Content-Type": "application/json",
        "Origin": "https://suv-flipkart.seller-support.fkcloud.it",
        "Referer": "https://suv-flipkart.seller-support.fkcloud.it/sellerDashboard/index.html",
        "operation": "query",
        "operation-name": "GetListingRows",
        "x-internal-env-type": "WEB",
        "x-marketplace-context": "ALL",
        "x-requested-with": "XMLHttpRequest",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "sec-ch-ua": '"Not=A?Brand";v="99", "Google Chrome";v="151", "Chromium";v="151"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }


class PlaywrightSessionHandler:
    """
    Manages Playwright CDP interaction, 10-minute keepalive tab refresh,
    and automatic session cookie/header extraction upon expiration.
    """

    def __init__(
        self,
        cdp_url: str = CDP_URL,
        session_file: Optional[Path] = None,
        refresh_interval: int = REFRESH_INTERVAL,
    ):
        self.cdp_url = cdp_url
        self.session_file = Path(session_file or SESSION_CONFIG_PATH)
        self.refresh_interval = refresh_interval
        self._keepalive_thread: Optional[threading.Thread] = None
        self._stop_keepalive = threading.Event()

    def is_browser_open(self) -> bool:
        """Returns True if Chrome is reachable via CDP."""
        return is_cdp_available(self.cdp_url)

    def launch_chrome_if_needed(self, initial_url: Optional[str] = None) -> bool:
        """
        If Chrome is not currently open with CDP, launches Chrome with --remote-debugging-port.
        """
        if self.is_browser_open():
            return True

        target_url = initial_url or SELLER_INFO_URL.format(seller_id=DEFAULT_FALLBACK_SELLER_ID)
        logger.info("Chrome CDP not detected on %s. Attempting to launch Chrome...", self.cdp_url)

        # Standard Chrome launch candidates on Windows and POSIX
        possible_chrome_paths = [
            "chrome.exe",
            "chrome",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"),
        ]

        launched = False
        for chrome_bin in possible_chrome_paths:
            if not chrome_bin:
                continue
            cmd = [
                chrome_bin,
                f"--remote-debugging-port={CDP_PORT}",
                "--no-first-run",
                "--no-default-browser-check",
                "--start-maximized",
                target_url,
            ]
            try:
                if sys.platform == "win32":
                    subprocess.Popen(
                        cmd,
                        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                    )
                else:
                    subprocess.Popen(cmd, start_new_session=True)
                launched = True
                break
            except Exception as e:
                logger.debug("Failed to launch Chrome via %s: %s", chrome_bin, str(e))

        if not launched:
            logger.warning("Could not automatically launch Chrome binary.")

        # Wait up to 6 seconds for CDP endpoint to respond
        for _ in range(12):
            time.sleep(0.5)
            if self.is_browser_open():
                logger.info("Chrome successfully connected and listening on CDP %s", self.cdp_url)
                return True

        return False

    async def _async_refresh_and_extract_session(
        self,
        seller_id: Optional[str] = None,
        target_api: str = "all",
        force_new_tab: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        Connects over CDP, refreshes the dynamic seller page, intercepts
        network requests for API headers, extracts cookies, and updates session.json.
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.error("Playwright is not installed. Please run: pip install playwright")
            return None

        active_seller_id = str(seller_id).strip() if seller_id else DEFAULT_FALLBACK_SELLER_ID
        target_info_url = SELLER_INFO_URL.format(seller_id=active_seller_id)
        target_approvals_url = SELLER_APPROVALS_URL.format(seller_id=active_seller_id)

        # If refreshing only API 2, preserve existing valid Tab 1 session data
        existing_session = self.read_current_session_file() if target_api == "api2" else {"cookies": {}, "headers": {}}
        existing_cookies = existing_session.get("cookies", {})
        existing_headers = existing_session.get("headers", {})

        captured_headers: Dict[str, str] = {**get_default_headers(), **existing_headers}
        captured_cookies: Dict[str, str] = {**existing_cookies}
        csrf_token_found: Optional[str] = None

        logger.info("[SESSION] Connecting to Chrome over CDP (%s) for target: %s...", self.cdp_url, target_api.upper())

        async with async_playwright() as p:
            try:
                browser = await asyncio.wait_for(p.chromium.connect_over_cdp(self.cdp_url), timeout=8.0)
            except asyncio.TimeoutError:
                logger.error("[SESSION] Timeout connecting to Chrome CDP on %s (8s)", self.cdp_url)
                return None
            except Exception as e:
                logger.error("[SESSION] Could not connect to Chrome CDP (%s): %s", self.cdp_url, str(e))
                return None

            if not browser.contexts:
                logger.error("[SESSION] No browser contexts found in Chrome instance.")
                return None

            context = browser.contexts[0]

            # ------------------------------------------------------------
            # Setup Request Interception to Capture API Headers & CSRF Tokens
            # ------------------------------------------------------------
            async def handle_request(request):
                nonlocal csrf_token_found
                try:
                    url = request.url
                    # Intercept relevant Flipkart seller API requests
                    if any(key in url for key in (
                        "getSellerDetails",
                        "approval-store",
                        "requestsV2",
                        "orchestrator",
                        "graphql",
                        "sellerDashboard",
                        "seller-support.fkcloud.it",
                    )):
                        req_headers = await request.all_headers()

                        # Extract CSRF token
                        for header_k, header_v in req_headers.items():
                            if header_k.lower() == "fk-csrf-token":
                                csrf_token_found = header_v
                                captured_headers["FK-CSRF-TOKEN"] = header_v
                                captured_headers["fk-csrf-token"] = header_v
                            elif header_k.lower() in (
                                "user-agent",
                                "sec-ch-ua",
                                "sec-ch-ua-mobile",
                                "sec-ch-ua-platform",
                                "origin",
                                "referer",
                                "x-requested-with",
                                "x-internal-env-type",
                                "x-marketplace-context",
                            ):
                                captured_headers[header_k] = header_v

                except Exception as ex:
                    logger.debug("[SESSION] Request intercept handler notice: %s", str(ex))

            context.on("request", handle_request)

            tab_info = None

            # ------------------------------------------------------------
            # Step 1: Manage Flipkart Seller Portal Tab (or force reopen clean tab)
            # ------------------------------------------------------------
            if force_new_tab:
                logger.info("[SESSION] Force new tab requested: Closing existing Flipkart tabs and opening a fresh tab...")
                old_portal_tabs = [
                    p for p in context.pages
                    if "seller-support.fkcloud.it" in p.url.lower() or "fkcloud.it" in p.url.lower()
                ]

                # 1. Open new tab first so browser context always remains active
                try:
                    tab_info = await context.new_page()
                except Exception as ex_np:
                    logger.debug("[SESSION] Error creating new page: %s", str(ex_np))
                    tab_info = context.pages[0] if context.pages else None

                # 2. Close old portal tabs
                for old_p in old_portal_tabs:
                    if old_p != tab_info:
                        try:
                            await old_p.close()
                        except Exception:
                            pass

                # 3. Navigate new tab to portal URL
                if tab_info:
                    logger.info("[SESSION] Navigating fresh tab to %s...", target_info_url)
                    try:
                        await asyncio.wait_for(tab_info.goto(target_info_url, wait_until="domcontentloaded"), timeout=12.0)
                    except Exception as ex_goto:
                        logger.debug("[SESSION] Fresh tab goto notice: %s", str(ex_goto))
            else:
                # Normal mode: find and reload existing tab or open if not found
                for page in context.pages:
                    try:
                        p_url = page.url.lower()
                        if "seller-support.fkcloud.it" in p_url or "fkcloud.it" in p_url:
                            tab_info = page
                            break
                    except Exception:
                        pass

                if tab_info is None:
                    logger.info("[SESSION] Tab 1 (Seller Info) not open. Opening: %s", target_info_url)
                    try:
                        tab_info = await context.new_page()
                        await asyncio.wait_for(tab_info.goto(target_info_url, wait_until="domcontentloaded"), timeout=10.0)
                    except Exception as ex1:
                        logger.debug("[SESSION] Tab 1 open notice: %s", str(ex1))
                else:
                    logger.info("[SESSION] Found Tab 1 (Seller Info): %s. Reloading...", tab_info.url)
                    try:
                        await asyncio.wait_for(tab_info.goto(target_info_url, wait_until="domcontentloaded"), timeout=8.0)
                    except Exception:
                        try:
                            await asyncio.wait_for(tab_info.reload(wait_until="domcontentloaded"), timeout=8.0)
                        except Exception as ex1:
                            logger.debug("[SESSION] Tab 1 reload notice: %s", str(ex1))

            # Wait up to 3.5 seconds for background SPA API calls to fire or until CSRF is captured
            for _ in range(18):
                if csrf_token_found and "connect.sid" in captured_cookies:
                    break
                await asyncio.sleep(0.2)

            # ------------------------------------------------------------
            # Step 2: Extract All Live Cookies for fkcloud.it domain
            # ------------------------------------------------------------
            try:
                browser_cookies = await context.cookies([
                    "https://suv-flipkart.seller-support.fkcloud.it",
                    "https://fkcloud.it",
                    "https://seller.flipkart.com",
                ])

                for c in browser_cookies:
                    c_name = c.get("name")
                    c_val = c.get("value")
                    if c_name and c_val is not None:
                        captured_cookies[c_name] = c_val
            except Exception as ex_ck:
                logger.debug("[SESSION] Context cookies error: %s", str(ex_ck))

            # Also extract document.cookie directly from active page DOM
            if tab_info:
                try:
                    dom_cookies = await tab_info.evaluate("() => document.cookie")
                    if dom_cookies:
                        for item in dom_cookies.split(";"):
                            if "=" in item:
                                ck, cv = item.strip().split("=", 1)
                                if ck.strip() and cv.strip():
                                    captured_cookies[ck.strip()] = cv.strip()
                except Exception as ex_dom:
                    logger.debug("[SESSION] DOM cookies error: %s", str(ex_dom))

            # Resolve CSRF token from cookies or headers (ensuring both are populated)
            csrf_token_val = None
            if "XyZ7pQ9rS2T1uV8wA3bC6dE4fG0h" in captured_cookies:
                csrf_token_val = captured_cookies["XyZ7pQ9rS2T1uV8wA3bC6dE4fG0h"]
            else:
                for ck, cv in captured_cookies.items():
                    if ck.lower() == "xyz7pq9rs2t1uv8wa3bc6de4fg0h" or "csrf" in ck.lower():
                        csrf_token_val = cv
                        break

            if not csrf_token_val:
                csrf_token_val = captured_headers.get("FK-CSRF-TOKEN") or captured_headers.get("fk-csrf-token")

            if csrf_token_val:
                captured_headers["FK-CSRF-TOKEN"] = csrf_token_val
                captured_headers["fk-csrf-token"] = csrf_token_val
                captured_cookies["XyZ7pQ9rS2T1uV8wA3bC6dE4fG0h"] = csrf_token_val

            # Read user-agent from page evaluation if not intercepted
            active_tab = tab_info
            if active_tab:
                try:
                    ua = await active_tab.evaluate("() => navigator.userAgent")
                    if ua:
                        captured_headers["User-Agent"] = ua
                except Exception:
                    pass

        # ------------------------------------------------------------
        # Save to session.json with fresh extracted session data
        # ------------------------------------------------------------
        if captured_cookies:
            # Construct fresh session data
            session_data = {
                "cookies": captured_cookies,
                "headers": captured_headers,
            }

            self.session_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.session_file, "w", encoding="utf-8") as f:
                json.dump(session_data, f, indent=4, ensure_ascii=False)

            print()
            print("=" * 70)
            print(f"[SESSION] session.json UPDATED (Target: {target_api.upper()})")
            print(f"[SESSION] File:    {self.session_file.resolve()}")
            print(f"[SESSION] Seller:  {active_seller_id}")
            print(f"[SESSION] Cookies: {len(captured_cookies)} captured")
            print(f"[SESSION] Headers: {len(captured_headers)} captured")
            if "connect.sid" in captured_cookies:
                print(f"[SESSION] connect.sid: {captured_cookies['connect.sid'][:25]}...")
            if "FK-CSRF-TOKEN" in captured_headers:
                print(f"[SESSION] CSRF Token:  {captured_headers['FK-CSRF-TOKEN']}")
            print("=" * 70)
            print()

            return session_data
        else:
            logger.warning("[SESSION] No cookies were extracted from Chrome context.")
            return None

    def read_current_session_file(self) -> Dict[str, Any]:
        """Safely reads the current session.json file if present."""
        if self.session_file.exists():
            try:
                with open(self.session_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.debug("Failed to read %s: %s", self.session_file, str(e))
        return {"cookies": {}, "headers": {}}

    def refresh_and_extract_session(
        self,
        seller_id: Optional[str] = None,
        target_api: str = "all",
        force_new_tab: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        Synchronous entry point to refresh Flipkart browser page and extract updated session.
        If force_new_tab is True, closes existing Flipkart portal tabs and opens a fresh tab.
        """
        if not self.is_browser_open():
            launched = self.launch_chrome_if_needed(
                initial_url=SELLER_INFO_URL.format(seller_id=seller_id or DEFAULT_FALLBACK_SELLER_ID)
            )
            if not launched:
                logger.error("Chrome is not running on port %d and could not be started.", CDP_PORT)
                return None

        return asyncio.run(
            self._async_refresh_and_extract_session(
                seller_id=seller_id,
                target_api=target_api,
                force_new_tab=force_new_tab,
            )
        )


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Flipkart Browser Session Scraper")
    parser.add_argument("--seller-id", type=str, default=DEFAULT_FALLBACK_SELLER_ID, help="Dynamic seller ID for portal URLs")
    parser.add_argument("--refresh-now", action="store_true", help="Immediately refresh page and extract session to session.json")
    args = parser.parse_args()

    handler = PlaywrightSessionHandler()
    res = handler.refresh_and_extract_session(seller_id=args.seller_id)
    if res:
        print("[+] Session successfully refreshed and extracted into session.json!")
    else:
        print("[-] Failed to refresh session from browser.")
