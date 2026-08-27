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
    REFRESH_INTERVAL,
    SELLER_APPROVALS_URL,
    SELLER_INFO_URL,
    SESSION_CONFIG_PATH,
)

logger = logging.getLogger("customer_scraper")

DEFAULT_FALLBACK_SELLER_ID = "218598a2b41c4bcd"


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

        # Standard Chrome launch commands
        launch_cmds = [
            [
                "chrome.exe",
                f"--remote-debugging-port={CDP_PORT}",
                "--no-first-run",
                "--no-default-browser-check",
                "--start-maximized",
                target_url,
            ],
            [
                "chrome",
                f"--remote-debugging-port={CDP_PORT}",
                "--no-first-run",
                "--no-default-browser-check",
                "--start-maximized",
                target_url,
            ],
        ]

        for cmd in launch_cmds:
            try:
                if sys.platform == "win32":
                    subprocess.Popen(
                        cmd,
                        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                    )
                else:
                    subprocess.Popen(cmd, start_new_session=True)
                break
            except Exception as e:
                logger.debug("Failed to launch Chrome with %s: %s", cmd[0], str(e))

        # Wait up to 10 seconds for CDP endpoint to respond
        for _ in range(20):
            time.sleep(0.5)
            if self.is_browser_open():
                logger.info("Chrome successfully launched and listening on CDP %s", self.cdp_url)
                return True

        return False

    async def _async_refresh_and_extract_session(
        self,
        seller_id: Optional[str] = None,
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

        captured_headers: Dict[str, str] = get_default_headers()
        captured_cookies: Dict[str, str] = {}
        csrf_token_found: Optional[str] = None

        logger.info("[SESSION] Connecting to Chrome over CDP (%s)...", self.cdp_url)

        async with async_playwright() as p:
            try:
                browser = await p.chromium.connect_over_cdp(self.cdp_url)
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

            # ------------------------------------------------------------
            # Step 1: Detect Open Tabs for Tab 1 (Seller Info) & Tab 2 (Track Approvals)
            # ------------------------------------------------------------
            tab_info = None
            tab_approvals = None

            for page in context.pages:
                try:
                    p_url = page.url.lower()
                    if "trackapprovalrequest" in p_url or "approval-store" in p_url or "requeststate=" in p_url:
                        if tab_approvals is None:
                            tab_approvals = page
                    elif "seller-support.fkcloud.it" in p_url or "fkcloud.it" in p_url or "sellerdashboard" in p_url:
                        if tab_info is None:
                            tab_info = page
                except Exception:
                    pass

            # ------------------------------------------------------------
            # Step 2: Refresh / Open Tab 1 (Seller Info)
            # ------------------------------------------------------------
            if tab_info is None:
                logger.info("[SESSION] Tab 1 (Seller Info) not open. Opening new tab: %s", target_info_url)
                tab_info = await context.new_page()
                await tab_info.goto(target_info_url, wait_until="domcontentloaded")
            else:
                logger.info("[SESSION] Found Tab 1 (Seller Info): %s. Refreshing...", tab_info.url)
                try:
                    if active_seller_id not in tab_info.url or "#app/seller" not in tab_info.url:
                        await tab_info.goto(target_info_url, wait_until="domcontentloaded")
                    else:
                        await tab_info.reload(wait_until="domcontentloaded")
                except Exception as ex1:
                    logger.warning("[SESSION] Tab 1 reload failed (%s). Navigating to target URL...", str(ex1))
                    await tab_info.goto(target_info_url, wait_until="domcontentloaded")

            await asyncio.sleep(1.5)

            # ------------------------------------------------------------
            # Step 3: Refresh / Open Tab 2 (Track Approvals)
            # ------------------------------------------------------------
            if tab_approvals is None or tab_approvals == tab_info:
                logger.info("[SESSION] Tab 2 (Track Approvals) not open. Opening new tab: %s", target_approvals_url)
                tab_approvals = await context.new_page()
                await tab_approvals.goto(target_approvals_url, wait_until="domcontentloaded")
            else:
                logger.info("[SESSION] Found Tab 2 (Track Approvals): %s. Refreshing...", tab_approvals.url)
                try:
                    if active_seller_id not in tab_approvals.url or "trackApprovalRequestsV2" not in tab_approvals.url:
                        await tab_approvals.goto(target_approvals_url, wait_until="domcontentloaded")
                    else:
                        await tab_approvals.reload(wait_until="domcontentloaded")
                except Exception as ex2:
                    logger.warning("[SESSION] Tab 2 reload failed (%s). Navigating to target URL...", str(ex2))
                    await tab_approvals.goto(target_approvals_url, wait_until="domcontentloaded")

            # Wait for automatic portal API requests to trigger on both tabs
            await asyncio.sleep(2.5)

            # ------------------------------------------------------------
            # Step 4: Extract All Cookies for fkcloud.it domain
            # ------------------------------------------------------------
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

            # If CSRF token is in cookies (XyZ7pQ9rS2T1uV8wA3bC6dE4fG0h), ensure header is updated
            if "XyZ7pQ9rS2T1uV8wA3bC6dE4fG0h" in captured_cookies:
                csrf_val = captured_cookies["XyZ7pQ9rS2T1uV8wA3bC6dE4fG0h"]
                captured_headers["FK-CSRF-TOKEN"] = csrf_val
                captured_headers["fk-csrf-token"] = csrf_val

            # Read user-agent from page evaluation if not intercepted
            active_tab = tab_approvals or tab_info
            if active_tab:
                try:
                    ua = await active_tab.evaluate("() => navigator.userAgent")
                    if ua:
                        captured_headers["User-Agent"] = ua
                except Exception:
                    pass

        # ------------------------------------------------------------
        # Save to session.json in identical schema
        # ------------------------------------------------------------
        if captured_cookies:
            # Preserve existing session values if new capture missed any
            existing_session = self.read_current_session_file()
            existing_cookies = existing_session.get("cookies", {})
            existing_headers = existing_session.get("headers", {})

            merged_cookies = {**existing_cookies, **captured_cookies}
            merged_headers = {**existing_headers, **captured_headers}

            session_data = {
                "cookies": merged_cookies,
                "headers": merged_headers,
            }

            self.session_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.session_file, "w", encoding="utf-8") as f:
                json.dump(session_data, f, indent=4, ensure_ascii=False)

            print()
            print("=" * 70)
            print("[SESSION] session.json CREATED / UPDATED")
            print(f"[SESSION] File:    {self.session_file.resolve()}")
            print(f"[SESSION] Seller:  {active_seller_id}")
            print(f"[SESSION] Cookies: {len(merged_cookies)} captured")
            print(f"[SESSION] Headers: {len(merged_headers)} captured")
            if "connect.sid" in merged_cookies:
                print(f"[SESSION] connect.sid: {merged_cookies['connect.sid'][:25]}...")
            if "FK-CSRF-TOKEN" in merged_headers:
                print(f"[SESSION] CSRF Token:  {merged_headers['FK-CSRF-TOKEN']}")
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

    def refresh_and_extract_session(self, seller_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Synchronous entry point to refresh Flipkart browser page and extract updated session.
        """
        if not self.is_browser_open():
            launched = self.launch_chrome_if_needed(
                initial_url=SELLER_INFO_URL.format(seller_id=seller_id or DEFAULT_FALLBACK_SELLER_ID)
            )
            if not launched:
                logger.error("Chrome is not running on port %d and could not be started.", CDP_PORT)
                return None

        return asyncio.run(self._async_refresh_and_extract_session(seller_id=seller_id))

    async def _async_keepalive_loop(self, seller_id: Optional[str] = None):
        """
        Background keepalive loop: refreshes both Flipkart tabs every 10 minutes (600s)
        WITHOUT extracting or overwriting session.json.
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.error("Playwright is not installed.")
            return

        active_seller_id = str(seller_id).strip() if seller_id else DEFAULT_FALLBACK_SELLER_ID
        target_info_url = SELLER_INFO_URL.format(seller_id=active_seller_id)
        target_approvals_url = SELLER_APPROVALS_URL.format(seller_id=active_seller_id)

        print()
        print("=" * 70)
        print("[KEEPALIVE] Flipkart Dual-Tab Browser Keep-Alive Monitor Started")
        print(f"[KEEPALIVE] Tab 1 (Seller Info):        {target_info_url}")
        print(f"[KEEPALIVE] Tab 2 (Track Approvals):    {target_approvals_url}")
        print(f"[KEEPALIVE] Refresh Interval:           {self.refresh_interval} seconds (10 minutes)")
        print("[KEEPALIVE] Note: Only reloads pages; does not overwrite session.json")
        print("=" * 70)
        print()

        async with async_playwright() as p:
            while not self._stop_keepalive.is_set():
                try:
                    if not is_cdp_available(self.cdp_url):
                        logger.debug("[KEEPALIVE] Chrome CDP not available. Waiting...")
                        await asyncio.sleep(10)
                        continue

                    browser = await p.chromium.connect_over_cdp(self.cdp_url)
                    if not browser.contexts:
                        await asyncio.sleep(10)
                        continue

                    context = browser.contexts[0]
                    tab_info = None
                    tab_approvals = None

                    # Detect open tabs
                    for page in context.pages:
                        try:
                            p_url = page.url.lower()
                            if "trackapprovalrequest" in p_url or "approval-store" in p_url:
                                if tab_approvals is None:
                                    tab_approvals = page
                            elif "seller-support.fkcloud.it" in p_url or "fkcloud.it" in p_url:
                                if tab_info is None:
                                    tab_info = page
                        except Exception:
                            pass

                    # Refresh or open Tab 1
                    if tab_info is None or tab_info.is_closed():
                        logger.info("[KEEPALIVE] Opening Tab 1 (Seller Info) at %s...", target_info_url)
                        tab_info = await context.new_page()
                        await tab_info.goto(target_info_url, wait_until="domcontentloaded")
                    else:
                        logger.info("[KEEPALIVE] 10-minute refresh on Tab 1: %s", tab_info.url)
                        await tab_info.reload(wait_until="domcontentloaded")

                    await asyncio.sleep(1.5)

                    # Refresh or open Tab 2
                    if tab_approvals is None or tab_approvals.is_closed() or tab_approvals == tab_info:
                        logger.info("[KEEPALIVE] Opening Tab 2 (Track Approvals) at %s...", target_approvals_url)
                        tab_approvals = await context.new_page()
                        await tab_approvals.goto(target_approvals_url, wait_until="domcontentloaded")
                    else:
                        logger.info("[KEEPALIVE] 10-minute refresh on Tab 2: %s", tab_approvals.url)
                        await tab_approvals.reload(wait_until="domcontentloaded")

                    # Wait for next refresh interval, checking stop flag periodically
                    for _ in range(int(self.refresh_interval / 5)):
                        if self._stop_keepalive.is_set():
                            break
                        await asyncio.sleep(5)

                except Exception as e:
                    logger.debug("[KEEPALIVE] Refresh loop notice: %s", str(e))
                    await asyncio.sleep(10)

    def start_keepalive_thread(self, seller_id: Optional[str] = None) -> None:
        """
        Starts the 10-minute keep-alive tab refresh loop in a background daemon thread.
        """
        if self._keepalive_thread and self._keepalive_thread.is_alive():
            logger.debug("Keepalive thread is already running.")
            return

        self._stop_keepalive.clear()

        def _runner():
            asyncio.run(self._async_keepalive_loop(seller_id=seller_id))

        self._keepalive_thread = threading.Thread(
            target=_runner,
            name="FlipkartKeepAliveThread",
            daemon=True,
        )
        self._keepalive_thread.start()
        logger.info("Started background 10-minute Flipkart browser keepalive refresher.")

    def stop_keepalive_thread(self) -> None:
        """Stops the background keepalive thread."""
        self._stop_keepalive.set()
        if self._keepalive_thread and self._keepalive_thread.is_alive():
            self._keepalive_thread.join(timeout=2.0)
            logger.info("Stopped background Flipkart browser keepalive refresher.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Flipkart Browser Session Scraper & Keep-Alive Monitor")
    parser.add_argument("--seller-id", type=str, default=DEFAULT_FALLBACK_SELLER_ID, help="Dynamic seller ID for info URL")
    parser.add_argument("--refresh-now", action="store_true", help="Immediately refresh page and extract session to session.json")
    parser.add_argument("--interval", type=int, default=REFRESH_INTERVAL, help="Refresh interval in seconds (default: 600)")
    args = parser.parse_args()

    handler = PlaywrightSessionHandler(refresh_interval=args.interval)

    if args.refresh_now:
        res = handler.refresh_and_extract_session(seller_id=args.seller_id)
        if res:
            print("[+] Session successfully refreshed and extracted!")
        else:
            print("[-] Failed to refresh session from browser.")
    else:
        try:
            asyncio.run(handler._async_keepalive_loop(seller_id=args.seller_id))
        except KeyboardInterrupt:
            print("\n[INFO] Keep-alive monitoring stopped.")
