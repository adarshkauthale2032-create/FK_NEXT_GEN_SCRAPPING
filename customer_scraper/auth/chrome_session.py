"""
Chrome Session & Cookie Extractor Helper.

Allows automatic extraction of active cookies from Google Chrome on Windows,
connecting to a running Chrome instance via CDP/Remote Debugging, or
parsing raw cURL commands into session.json.
"""

import base64
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
from typing import Dict, Optional
import urllib.request

try:
    import win32crypt  # type: ignore
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

logger = logging.getLogger("customer_scraper")


def get_chrome_user_data_path() -> Path:
    """Returns the default Chrome User Data directory on Windows."""
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        return Path(local_app_data) / "Google" / "Chrome" / "User Data"
    return Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "User Data"


def get_chrome_encryption_key() -> Optional[bytes]:
    """Retrieves and decrypts the Chrome Master Key from Local State."""
    if not HAS_CRYPTO:
        return None
    local_state_path = get_chrome_user_data_path() / "Local State"
    if not local_state_path.exists():
        return None

    try:
        with open(local_state_path, "r", encoding="utf-8") as f:
            local_state = json.load(f)
        encrypted_key = base64.b64decode(local_state["os_crypt"]["encrypted_key"])
        # Remove 'DPAPI' prefix (first 5 bytes)
        encrypted_key = encrypted_key[5:]
        decrypted_key = win32crypt.CryptUnprotectData(encrypted_key, None, None, None, 0)[1]
        return decrypted_key
    except Exception as e:
        logger.debug("Could not decrypt Chrome key: %s", str(e))
        return None


def decrypt_chrome_cookie_value(encrypted_val: bytes, key: Optional[bytes]) -> str:
    """Decrypts a Chrome cookie value from the SQLite Cookies database."""
    if not encrypted_val:
        return ""
    try:
        # Chrome 80+ AES-GCM (starts with 'v10' or 'v11')
        if encrypted_val[:3] in (b"v10", b"v11"):
            if not key or not HAS_CRYPTO:
                return ""
            nonce = encrypted_val[3:15]
            ciphertext = encrypted_val[15:]
            aesgcm = AESGCM(key)
            decrypted = aesgcm.decrypt(nonce, ciphertext, None)
            return decrypted.decode("utf-8", errors="ignore")
        # Legacy DPAPI decryption
        elif HAS_CRYPTO:
            decrypted = win32crypt.CryptUnprotectData(encrypted_val, None, None, None, 0)[1]
            return decrypted.decode("utf-8", errors="ignore")
    except Exception:
        pass
    return ""


def extract_cookies_from_chrome_db(domain_filter: str = "seller-support.fkcloud.it") -> Dict[str, str]:
    """
    Attempts to read cookies for the given domain from Chrome's SQLite database.
    Copies database to a temporary location to avoid locked file errors.
    """
    cookies: Dict[str, str] = {}
    user_data_dir = get_chrome_user_data_path()
    if not user_data_dir.exists():
        return cookies

    # Check Default and Profile directories
    profile_dirs = [user_data_dir / "Default"] + list(user_data_dir.glob("Profile *"))
    key = get_chrome_encryption_key()

    for p_dir in profile_dirs:
        # Chrome stores cookies in 'Network/Cookies' or 'Cookies'
        cookie_db_paths = [p_dir / "Network" / "Cookies", p_dir / "Cookies"]
        for db_path in cookie_db_paths:
            if not db_path.exists():
                continue

            temp_copy = db_path.parent / "Cookies_temp_scraper.db"
            try:
                shutil.copy2(db_path, temp_copy)
                conn = sqlite3.connect(str(temp_copy))
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT name, encrypted_value, value, host_key FROM cookies WHERE host_key LIKE ?",
                    (f"%{domain_filter}%",)
                )
                for name, enc_val, plain_val, host in cursor.fetchall():
                    val = plain_val
                    if not val and enc_val:
                        val = decrypt_chrome_cookie_value(enc_val, key)
                    if val:
                        cookies[name] = val
                conn.close()
            except Exception as e:
                logger.debug("Error reading cookie db %s: %s", db_path, str(e))
            finally:
                if temp_copy.exists():
                    try:
                        temp_copy.unlink()
                    except Exception:
                        pass

    return cookies


def extract_cookies_from_running_chrome_cdp(port: int = 9222, target_url: str = "seller-support.fkcloud.it") -> Dict[str, str]:
    """
    Connects to an already running Chrome started with remote debugging port:
    chrome.exe --remote-debugging-port=9222
    """
    cookies = {}
    try:
        json_url = f"http://127.0.0.1:{port}/json"
        req = urllib.request.Request(json_url, headers={"User-Agent": "CustomerScraper"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            tabs = json.loads(resp.read().decode("utf-8"))
            logger.info("Found %d open tabs in running Chrome instance.", len(tabs))
    except Exception:
        # Chrome is not running in remote debugging mode
        return cookies
    return cookies


def parse_curl_command(curl_text: str) -> Dict[str, Any]:
    """
    Parses a copied cURL command to extract cookies, headers, and endpoints.
    """
    headers = {}
    cookies = {}

    # Extract headers (-H 'Key: Value' or -H "Key: Value")
    header_matches = re.findall(r"-H\s+['\"]([^'\"]+)['\"]", curl_text)
    for h in header_matches:
        if ":" in h:
            k, v = h.split(":", 1)
            k = k.strip()
            v = v.strip()
            if k.lower() == "cookie":
                for item in v.split(";"):
                    if "=" in item:
                        ck, cv = item.strip().split("=", 1)
                        cookies[ck.strip()] = cv.strip()
            else:
                headers[k] = v

    # Extract --cookie or -b flag
    cookie_flag_matches = re.findall(r"(?:--cookie|-b)\s+['\"]([^'\"]+)['\"]", curl_text)
    for c_str in cookie_flag_matches:
        for item in c_str.split(";"):
            if "=" in item:
                ck, cv = item.strip().split("=", 1)
                cookies[ck.strip()] = cv.strip()

    return {"cookies": cookies, "headers": headers}
