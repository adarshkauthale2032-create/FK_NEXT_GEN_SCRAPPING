"""
Session and Authentication Manager.

Manages HTTP sessions, headers, cookies, token refresh workflows,
and auth expiry detection with Playwright CDP session recovery.
"""

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional
import requests

from config.settings import BASE_URL, DEFAULT_SELLER_ID, REFRESH_INTERVAL, SESSION_CONFIG_PATH
from auth.playwright_session import PlaywrightSessionHandler

logger = logging.getLogger("customer_scraper")


class AuthExpiredError(Exception):
    """Raised when an active session has expired and cannot automatically proceed."""
    pass


class AuthManager:
    """
    Handles authentication state, cookies, headers, and session lifecycles
    using requests.Session and Playwright CDP browser session extraction.
    """

    def __init__(
        self,
        session_path: Optional[Path] = None,
        base_url: Optional[str] = None,
        refresh_interval: int = REFRESH_INTERVAL,
    ):
        self.session_path = Path(session_path or SESSION_CONFIG_PATH)
        self.base_url = base_url or BASE_URL
        self.session = requests.Session()
        self.cookies: Dict[str, str] = {}
        self.headers: Dict[str, str] = {
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
        self.playwright_handler = PlaywrightSessionHandler(
            session_file=self.session_path,
            refresh_interval=refresh_interval,
        )
        self.load_session()

    def load_session(self) -> bool:
        """
        Loads cookies and headers from the session configuration file.
        If file is missing, attempts extracting from opened Chrome browser via CDP.
        """
        loaded = False

        # 1. Try loading from cached session JSON file
        if self.session_path.exists():
            try:
                with open(self.session_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    file_cookies = data.get("cookies", {})
                    file_headers = data.get("headers", {})

                    if file_cookies and isinstance(file_cookies, dict) and len(file_cookies) > 0:
                        self.cookies.update(file_cookies)
                        self.session.cookies.update(file_cookies)
                        loaded = True

                    if file_headers and isinstance(file_headers, dict):
                        self.headers.update(file_headers)
                        self.session.headers.update(file_headers)

                    # Ensure FK-CSRF-TOKEN header is set if present in cookies or headers
                    csrf_val = self.get_csrf_token()
                    if csrf_val:
                        self.headers["FK-CSRF-TOKEN"] = csrf_val
                        self.headers["fk-csrf-token"] = csrf_val
                        self.session.headers["FK-CSRF-TOKEN"] = csrf_val
                        self.session.headers["fk-csrf-token"] = csrf_val

                    if loaded:
                        logger.info("Session configuration successfully loaded from %s (%d cookies, %d headers)", self.session_path.name, len(self.cookies), len(self.headers))
            except Exception as e:
                logger.debug("Failed to read session file (%s): %s", self.session_path, str(e))

        # 2. Check environment variables as fallback
        if not loaded:
            env_cookie = os.environ.get("FLIPKART_COOKIE")
            if env_cookie:
                self.set_cookie_string(env_cookie)
                loaded = True
                logger.info("Session cookies loaded from environment variable FLIPKART_COOKIE")

        return loaded

    def get_csrf_token(self) -> Optional[str]:
        """
        Finds and returns the active CSRF token from headers or cookies.
        Checks:
        1. Header 'FK-CSRF-TOKEN' or 'fk-csrf-token'
        2. Cookie 'XyZ7pQ9rS2T1uV8wA3bC6dE4fG0h'
        3. Case-insensitive cookie search for xyz7... or csrf
        """
        # 1. Check in headers
        for k, v in self.headers.items():
            if k.lower() == "fk-csrf-token" and v and str(v).strip():
                return str(v).strip()

        # 2. Check direct cookie key
        if "XyZ7pQ9rS2T1uV8wA3bC6dE4fG0h" in self.cookies:
            val = self.cookies["XyZ7pQ9rS2T1uV8wA3bC6dE4fG0h"]
            if val and str(val).strip():
                return str(val).strip()

        # 3. Check case-insensitive cookie search
        for k, v in self.cookies.items():
            k_lower = k.lower()
            if (k_lower == "xyz7pq9rs2t1uv8wa3bc6de4fg0h" or "csrf" in k_lower) and v and str(v).strip():
                return str(v).strip()

        return None

    def get_cookie_header_string(self) -> str:
        """
        Returns all active cookies formatted as a single 'Cookie' header string.
        Guarantees that subdomains and endpoints receive all captured session cookies.
        """
        if not self.cookies:
            return ""
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items() if v is not None and str(v).strip())

    def clear_session(self) -> None:
        """Clears stale session cookies and headers from memory and session file."""
        self.cookies = {}
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/152.0.0.0 Safari/537.36"
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
            "sec-ch-ua": '"Not=A?Brand";v="99", "Google Chrome";v="152", "Chromium";v="152"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        }
        self.session = requests.Session()
        if self.session_path.exists():
            try:
                with open(self.session_path, "w", encoding="utf-8") as f:
                    json.dump({"cookies": {}, "headers": self.headers}, f, indent=4)
                logger.info("Cleared stale session from %s", self.session_path.name)
            except Exception as e:
                logger.debug("Failed to reset session file: %s", str(e))

    def validate_session_alive(self, test_seller_id: str = DEFAULT_SELLER_ID) -> bool:
        """
        Quickly tests whether current session credentials are live and authorized.
        Returns True if authenticated, False if expired or invalid.
        """
        if not self.cookies:
            return False

        csrf = self.get_csrf_token()
        if not csrf:
            return False

        test_url = f"{self.base_url.rstrip('/')}/getSellerDetails?sellerId={test_seller_id}"
        req_headers = dict(self.headers)
        req_headers["FK-CSRF-TOKEN"] = csrf
        req_headers["fk-csrf-token"] = csrf

        cookie_str = self.get_cookie_header_string()
        if cookie_str:
            req_headers["Cookie"] = cookie_str

        try:
            resp = self.session.get(test_url, headers=req_headers, timeout=(5, 8), allow_redirects=True)
            if self.is_session_expired(resp):
                return False

            if resp.status_code == 200:
                ct = resp.headers.get("Content-Type", "").lower()
                if "application/json" in ct or resp.text.strip().startswith("{"):
                    try:
                        data = resp.json()
                        if isinstance(data, dict):
                            if "result" in data or "displayName" in data or "profileInfo" in data or "status" in data:
                                return True
                    except Exception:
                        pass
            return False
        except Exception:
            return False

    def ensure_valid_session(self, seller_id: Optional[str] = None, force_refresh: bool = False) -> bool:
        """
        Verifies session health. If expired or missing, clears stale cookies and
        automatically re-extracts a fresh session from Chrome.
        """
        target_seller = str(seller_id).strip() if seller_id else DEFAULT_SELLER_ID

        if not force_refresh and self.validate_session_alive(target_seller):
            logger.info("✅ Session verified: Active and authenticated (%d cookies, %d headers)", len(self.cookies), len(self.headers))
            return True

        logger.warning("⚠️ [AUTH] Session is expired, invalid, or missing in %s.", self.session_path.name)
        print("\n" + "=" * 75)
        print("⚠️ [SESSION EXPIRED DETECTED]")
        print("   Current session in session.json is expired or invalid.")
        print("🔄 Clearing expired session and extracting fresh session from Chrome...")
        print("=" * 75 + "\n")

        # 1. Clear stale session
        self.clear_session()

        # 2. Extract fresh session from Chrome via CDP
        try:
            refreshed = self.refresh_session(seller_id=target_seller, target_api="all")
            if refreshed and self.cookies:
                print("\n" + "=" * 75)
                print("✅ [AUTH READY] Fresh session successfully captured from Chrome!")
                print(f"   Cookies: {len(self.cookies)} | CSRF Token: {self.get_csrf_token() or 'N/A'}")
                print("=" * 75 + "\n")
                return True
        except Exception as e:
            logger.error("Session refresh failed: %s", str(e))

        return False

    def refresh_session(self, seller_id: Optional[str] = None, target_api: str = "all") -> bool:
        """
        Refreshes session automatically when expired using Chrome DevTools Protocol (CDP).
        Tries 3 times on the existing tab. If it still fails after 3 attempts, it closes the
        existing portal tabs in Chrome and reopens a brand new clean tab to extract fresh session details.
        """
        target_seller = str(seller_id).strip() if seller_id else DEFAULT_SELLER_ID
        logger.info("[AUTH] Automatic session refresh initiated for seller ID %s (Target: %s)...", target_seller, target_api.upper())

        # Phase 1: Try extracting / refreshing on existing tab up to 3 times
        max_refresh_attempts = 3
        for attempt in range(1, max_refresh_attempts + 1):
            try:
                session_data = self.playwright_handler.refresh_and_extract_session(
                    seller_id=target_seller,
                    target_api=target_api,
                    force_new_tab=False,
                )
                if session_data and session_data.get("cookies"):
                    new_cookies = session_data.get("cookies", {})
                    new_headers = session_data.get("headers", {})

                    # 1. Update in-memory state
                    self.cookies.update(new_cookies)
                    self.headers.update(new_headers)

                    # 2. Fresh requests.Session instance
                    self.session = requests.Session()
                    self.session.cookies.update(new_cookies)
                    self.session.headers.update(new_headers)

                    # 3. Ensure CSRF token header
                    if "XyZ7pQ9rS2T1uV8wA3bC6dE4fG0h" in self.cookies:
                        csrf_val = self.cookies["XyZ7pQ9rS2T1uV8wA3bC6dE4fG0h"]
                        self.headers["FK-CSRF-TOKEN"] = csrf_val
                        self.headers["fk-csrf-token"] = csrf_val
                        self.session.headers["FK-CSRF-TOKEN"] = csrf_val
                        self.session.headers["fk-csrf-token"] = csrf_val

                    # 4. Save and force overwrite session.json on disk
                    self._save_to_file()

                    logger.info(
                        "[AUTH] Session successfully refreshed and persisted (%d cookies, %d headers). Automatically continuing scraping.",
                        len(self.cookies),
                        len(self.headers),
                    )
                    return True
                else:
                    logger.warning("[AUTH] Attempt %d/%d: Session extraction returned empty cookies. Retrying in 1s...", attempt, max_refresh_attempts)
            except Exception as e:
                logger.error("[AUTH] Attempt %d/%d: CDP session refresh error: %s", attempt, max_refresh_attempts, str(e))

            if attempt < max_refresh_attempts:
                time.sleep(1)

        # Phase 2: If still not retrieved after 3 attempts, close existing tabs and reopen a fresh tab!
        logger.warning("⚠️ [AUTH FALLBACK] Session could not be retrieved after %d attempts. Closing existing tabs and opening a fresh tab in Chrome...", max_refresh_attempts)
        print("\n" + "=" * 75)
        print("⚠️ [AUTH RECOVERY] Session extraction failed on existing tab after 3 attempts.")
        print("🔄 [CLEAN TAB RESTART] Closing existing Flipkart tabs & reopening a new tab in Chrome...")
        print("=" * 75 + "\n")

        try:
            session_data = self.playwright_handler.refresh_and_extract_session(
                seller_id=target_seller,
                target_api=target_api,
                force_new_tab=True,
            )
            if session_data and session_data.get("cookies"):
                new_cookies = session_data.get("cookies", {})
                new_headers = session_data.get("headers", {})

                self.cookies.update(new_cookies)
                self.headers.update(new_headers)

                self.session = requests.Session()
                self.session.cookies.update(new_cookies)
                self.session.headers.update(new_headers)

                if "XyZ7pQ9rS2T1uV8wA3bC6dE4fG0h" in self.cookies:
                    csrf_val = self.cookies["XyZ7pQ9rS2T1uV8wA3bC6dE4fG0h"]
                    self.headers["FK-CSRF-TOKEN"] = csrf_val
                    self.headers["fk-csrf-token"] = csrf_val
                    self.session.headers["FK-CSRF-TOKEN"] = csrf_val
                    self.session.headers["fk-csrf-token"] = csrf_val

                self._save_to_file()
                print("\n" + "=" * 75)
                print("✅ [AUTH RECOVERED] Fresh session captured from newly opened Chrome tab!")
                print(f"   Cookies: {len(self.cookies)} | CSRF Token: {self.get_csrf_token() or 'N/A'}")
                print("=" * 75 + "\n")
                return True
        except Exception as e_new:
            logger.error("Fresh tab session extraction error: %s", str(e_new))

        raise AuthExpiredError(
            f"Automated session refresh failed after {max_refresh_attempts} attempts and fresh tab reopening. Please ensure Chrome is logged into Flipkart Seller Portal on {self.playwright_handler.cdp_url}."
        )

    def _save_to_file(self) -> None:
        """Saves current cookies and headers to session.json with forced overwrite."""
        try:
            data = {"cookies": self.cookies, "headers": self.headers}
            self.session_path.parent.mkdir(parents=True, exist_ok=True)
            # Try atomic replace with temp file, fallback to direct write
            tmp_path = self.session_path.with_suffix(f".tmp_{os.getpid()}")
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=4, ensure_ascii=False)
                os.replace(tmp_path, self.session_path)
            except Exception:
                with open(self.session_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=4, ensure_ascii=False)
            logger.info("Saved active session configuration to %s", self.session_path.name)
        except Exception as e:
            logger.error("Failed to save session configuration: %s", str(e))

    def import_curl(self, curl_command: str) -> bool:
        """Parses a cURL command and updates session headers and cookies."""
        from auth.chrome_session import parse_curl_command
        parsed = parse_curl_command(curl_command)
        if parsed.get("cookies") or parsed.get("headers"):
            if parsed.get("cookies"):
                self.cookies.update(parsed["cookies"])
                self.session.cookies.update(parsed["cookies"])
            if parsed.get("headers"):
                self.headers.update(parsed["headers"])
                self.session.headers.update(parsed["headers"])
            self._save_to_file()
            logger.info("Successfully imported session from cURL command.")
            return True
        return False

    def set_cookie_string(self, cookie_string: str) -> None:
        """Parses a standard Cookie header string into the session."""
        cookies_dict = {}
        for item in cookie_string.split(";"):
            if "=" in item:
                key, val = item.strip().split("=", 1)
                cookies_dict[key.strip()] = val.strip()
        self.cookies.update(cookies_dict)
        self.session.cookies.update(cookies_dict)

    def set_headers(self, headers_dict: Dict[str, str]) -> None:
        """Updates session headers."""
        self.headers.update(headers_dict)
        self.session.headers.update(headers_dict)

    def get_session(self) -> requests.Session:
        """Returns the active, configured requests Session."""
        return self.session

    def is_session_expired(self, response: requests.Response) -> bool:
        """
        Determines whether the given HTTP response indicates an unauthenticated/expired session.
        """
        # Status code 401 Unauthorized, 403 Forbidden, 419 Auth Timeout, 440 Login Timeout
        if response.status_code in (401, 403, 419, 440):
            return True

        # Check if redirected to login / SSO / auth page
        curr_url = str(response.url).lower()
        if any(term in curr_url for term in ("/login", "login.", "sso.", "/signin", "auth.")):
            return True

        # Check for redirection in history
        if response.history:
            for resp in response.history:
                if resp.status_code in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("Location", "").lower()
                    if any(term in loc for term in ("login", "auth", "sso", "signin")):
                        return True

        # Check content type: if an API returned text/html instead of JSON, it's a login page
        content_type = response.headers.get("Content-Type", "").lower()
        if "text/html" in content_type:
            text = response.text.lower()
            if any(term in text for term in ("login", "signin", "unauthorized", "sign in", "password", "sso", "redirect")):
                return True
            if "<html" in text or "<!doctype" in text:
                return True

        # Check JSON error payloads
        if "application/json" in content_type or response.text.strip().startswith("{"):
            try:
                data = response.json()
                if isinstance(data, dict):
                    error_msg = str(data.get("error", "")).lower()
                    status_val = str(data.get("status", "")).lower()
                    message_val = str(data.get("message", "")).lower()
                    code_val = str(data.get("code", "")).lower()
                    error_code = str(data.get("errorCode", "")).lower()

                    expired_terms = (
                        "unauthorized", "expired", "invalid session", "forbidden", "session",
                        "auth", "not logged in", "session_invalid", "token expired", "invalid_token"
                    )
                    if any(term in error_msg for term in expired_terms):
                        return True
                    if any(term in message_val for term in expired_terms):
                        return True
                    if any(term in error_code for term in expired_terms):
                        return True
                    if code_val in ("401", "403", "419"):
                        return True
                    if status_val in ("401", "403", "419", "unauthorized") and not data.get("result"):
                        return True
            except Exception:
                pass

        return False

