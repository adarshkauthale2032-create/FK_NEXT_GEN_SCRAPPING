"""
Session and Authentication Manager.

Manages HTTP sessions, headers, cookies, token refresh workflows,
and auth expiry detection without hard-coding sensitive credentials.
"""

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional
import requests

from config.settings import SESSION_CONFIG_PATH

logger = logging.getLogger("customer_scraper")


class AuthExpiredError(Exception):
    """Raised when an active session has expired and cannot automatically proceed."""
    pass


class AuthManager:
    """
    Handles authentication state, cookies, headers, and session lifecycles.
    """

    def __init__(self, session_path: Optional[Path] = None):
        self.session_path = session_path or SESSION_CONFIG_PATH
        self.session = requests.Session()
        self.cookies: Dict[str, str] = {}
        self.headers: Dict[str, str] = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
        }
        self.load_session()

    def load_session(self) -> bool:
        """
        Loads cookies and headers from the configuration file or environment.
        """
        loaded = False

        # Try loading from session JSON file
        if self.session_path.exists():
            try:
                with open(self.session_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    file_cookies = data.get("cookies", {})
                    file_headers = data.get("headers", {})

                    if file_cookies:
                        self.cookies.update(file_cookies)
                        self.session.cookies.update(file_cookies)
                    if file_headers:
                        self.headers.update(file_headers)
                        self.session.headers.update(file_headers)

                    loaded = True
                    logger.info("Session configuration successfully loaded from %s", self.session_path.name)
            except Exception as e:
                logger.error("Failed to read session file (%s): %s", self.session_path, str(e))

        # Check environment variables as fallback
        env_cookie = os.environ.get("FLIPKART_COOKIE")
        if env_cookie:
            self.set_cookie_string(env_cookie)
            loaded = True
            logger.info("Session cookies loaded from environment variable FLIPKART_COOKIE")

        # Fallback: Attempt automatic extraction from local Chrome profile on Windows
        if not loaded:
            try:
                from auth.chrome_session import extract_cookies_from_chrome_db
                auto_cookies = extract_cookies_from_chrome_db()
                if auto_cookies:
                    self.cookies.update(auto_cookies)
                    self.session.cookies.update(auto_cookies)
                    loaded = True
                    logger.info("Auto-detected %d session cookies directly from Google Chrome profile!", len(auto_cookies))
                    # Save to session.json so future runs have it cached
                    self._save_to_file()
            except Exception as e:
                logger.debug("Chrome cookie auto-extraction skipped: %s", str(e))

        if not loaded:
            logger.warning(
                "No session configuration found at %s. Please populate it with valid browser session credentials.",
                self.session_path
            )

        return loaded

    def _save_to_file(self) -> None:
        """Saves current cookies and headers to session.json."""
        try:
            data = {"cookies": self.cookies, "headers": self.headers}
            self.session_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.session_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
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
        """
        Parses a standard Cookie header string into the session.
        """
        cookies_dict = {}
        for item in cookie_string.split(";"):
            if "=" in item:
                key, val = item.strip().split("=", 1)
                cookies_dict[key.strip()] = val.strip()
        self.cookies.update(cookies_dict)
        self.session.cookies.update(cookies_dict)

    def set_headers(self, headers_dict: Dict[str, str]) -> None:
        """
        Updates session headers.
        """
        self.headers.update(headers_dict)
        self.session.headers.update(headers_dict)

    def get_session(self) -> requests.Session:
        """
        Returns the active, configured requests Session.
        """
        return self.session

    def is_session_expired(self, response: requests.Response) -> bool:
        """
        Determines whether the given HTTP response indicates an unauthenticated/expired session.
        """
        # Status code 401 Unauthorized or 403 Forbidden
        if response.status_code in (401, 403):
            return True

        # Check for redirection to login/SSO URL
        if response.history:
            for resp in response.history:
                if resp.status_code in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("Location", "").lower()
                    if "login" in loc or "auth" in loc or "sso" in loc:
                        return True

        # Check content type and content for login page indicators
        content_type = response.headers.get("Content-Type", "").lower()
        if "text/html" in content_type:
            text = response.text.lower()
            if "<html" in text and ("login" in text or "signin" in text or "unauthorized" in text):
                return True

        # Check JSON error payloads
        if "application/json" in content_type:
            try:
                data = response.json()
                if isinstance(data, dict):
                    error_msg = str(data.get("error", "")).lower()
                    status_val = str(data.get("status", "")).lower()
                    message_val = str(data.get("message", "")).lower()

                    if any(term in error_msg for term in ("unauthorized", "expired", "invalid session", "forbidden", "auth")):
                        return True
                    if any(term in message_val for term in ("unauthorized", "expired", "invalid session", "forbidden", "session")):
                        return True
                    if status_val in ("401", "403", "unauthorized"):
                        return True
            except Exception:
                pass

        return False

    def refresh_session(self) -> bool:
        """
        Attempts to reload and re-establish the session.
        If running interactively, prompts the user to refresh session.json.
        """
        logger.info("Session expired or invalid. Attempting session refresh...")

        # Re-read session file from disk
        if self.session_path.exists():
            success = self.load_session()
            if success:
                logger.info("Session refreshed from disk configuration.")
                return True

        # In interactive terminal mode, offer user to update credentials
        if sys.stdin.isatty():
            print("\n" + "=" * 60)
            print("AUTHENTICATION REQUIRED:")
            print(f"Please update your active session cookies in: {self.session_path}")
            print("Or paste updated 'Cookie' header string below (Press Enter to continue):")
            print("=" * 60)
            try:
                user_input = input("Cookie Header / Press Enter to reload from file: ").strip()
                if user_input:
                    self.set_cookie_string(user_input)
                    # Save to file
                    data = {"cookies": self.cookies, "headers": self.headers}
                    with open(self.session_path, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2)
                    logger.info("Session updated and saved to %s", self.session_path.name)
                    return True
                else:
                    return self.load_session()
            except (EOFError, KeyboardInterrupt):
                raise AuthExpiredError("User aborted session authentication prompt.")

        raise AuthExpiredError(
            f"Authentication failed. Please update valid session cookies in {self.session_path} and restart."
        )
