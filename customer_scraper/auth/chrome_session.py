"""
Chrome Session & Cookie Extractor Helper.

Allows automatic extraction of active cookies and session details from Google Chrome on Windows:
1. Direct extraction & decryption from a custom Chrome installation or User Data path.
2. Auto-extraction from default Windows Chrome profile SQLite database.
3. Connecting to a running Chrome instance via CDP/Remote Debugging.
4. Parsing raw cURL commands into session.json.
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
from typing import Any, Dict, Optional, Union
import urllib.request

try:
    import win32crypt  # type: ignore
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

from config.settings import BASE_URL, CHROME_INSTALLED_PATH, SESSION_CONFIG_PATH

logger = logging.getLogger("customer_scraper")


def resolve_chrome_paths(custom_path: Optional[Union[str, Path]] = None) -> Dict[str, Optional[Path]]:
    """
    Intelligently resolves the Chrome executable path and User Data directory
    from a custom path, settings configuration, or default system locations.

    Args:
        custom_path: Path string or Path object pointing to:
                     - Chrome executable (e.g. chrome.exe)
                     - Chrome installation directory (e.g. C:\\Program Files\\Google\\Chrome\\Application)
                     - Chrome User Data / Profile directory (e.g. AppData\\Local\\Google\\Chrome\\User Data)

    Returns:
        Dict with keys:
            'executable': Path to chrome.exe or None
            'user_data': Path to User Data directory or None
    """
    target_raw = custom_path or CHROME_INSTALLED_PATH or os.environ.get("CHROME_PATH") or os.environ.get("CHROME_USER_DATA_PATH")
    
    # Ignore unset placeholders
    if isinstance(target_raw, str):
        target_raw = target_raw.strip()
        if not target_raw or target_raw in ("Enter_YOUR_PATH", "YOUR_PATH", "ENTER_YOUR_PATH"):
            target_raw = None

    resolved_exe: Optional[Path] = None
    resolved_user_data: Optional[Path] = None

    if target_raw:
        candidate = Path(target_raw).expanduser().resolve()
        
        # 1. If candidate is directly an executable file
        if candidate.is_file():
            resolved_exe = candidate
            # Check if User Data is located in parent or sibling structure
            if (candidate.parent / "User Data").is_dir():
                resolved_user_data = candidate.parent / "User Data"
        
        # 2. If candidate is a directory
        elif candidate.is_dir():
            # Check if it contains chrome.exe
            if (candidate / "chrome.exe").is_file():
                resolved_exe = candidate / "chrome.exe"
            elif (candidate / "Application" / "chrome.exe").is_file():
                resolved_exe = candidate / "Application" / "chrome.exe"

            # Check if it is a User Data directory
            if (candidate / "Local State").is_file() or (candidate / "Default").is_dir():
                resolved_user_data = candidate
            elif (candidate / "User Data").is_dir():
                resolved_user_data = candidate / "User Data"

    # Default fallback for executable if not yet found
    if not resolved_exe:
        program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        program_files_x86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        local_app_data = os.environ.get("LOCALAPPDATA", "")

        default_exe_candidates = [
            Path(program_files) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(program_files_x86) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(local_app_data) / "Google" / "Chrome" / "Application" / "chrome.exe" if local_app_data else None,
            Path(program_files) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            Path(program_files_x86) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        ]
        for c in default_exe_candidates:
            if c and c.is_file():
                resolved_exe = c
                break

    # Default fallback for User Data directory if not yet found
    if not resolved_user_data:
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        if local_app_data:
            resolved_user_data = Path(local_app_data) / "Google" / "Chrome" / "User Data"
        else:
            resolved_user_data = Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "User Data"

    return {
        "executable": resolved_exe,
        "user_data": resolved_user_data,
    }


def get_chrome_user_data_path(custom_path: Optional[Union[str, Path]] = None) -> Path:
    """Returns the resolved Chrome User Data directory on Windows."""
    paths = resolve_chrome_paths(custom_path)
    if paths.get("user_data") and paths["user_data"].exists():
        return paths["user_data"]
    
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        return Path(local_app_data) / "Google" / "Chrome" / "User Data"
    return Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "User Data"


def get_chrome_encryption_key(user_data_path: Optional[Path] = None) -> Optional[bytes]:
    """Retrieves and decrypts the Chrome Master Key from Local State."""
    if not HAS_CRYPTO:
        return None
    
    base_user_data = user_data_path or get_chrome_user_data_path()
    local_state_path = base_user_data / "Local State"
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
        logger.debug("Could not decrypt Chrome key from %s: %s", local_state_path, str(e))
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


def extract_cookies_from_chrome_db(
    custom_path: Optional[Union[str, Path]] = None,
    domain_filter: str = "fkcloud.it",
) -> Dict[str, str]:
    """
    Attempts to read cookies for the given domain from Chrome's SQLite database.
    Supports a custom Chrome installation / User Data directory path.
    Copies database to a temporary location to avoid locked file errors.
    """
    cookies: Dict[str, str] = {}
    user_data_dir = get_chrome_user_data_path(custom_path)
    if not user_data_dir.exists():
        logger.debug("Chrome User Data directory does not exist at: %s", user_data_dir)
        return cookies

    # Check Default and Profile directories
    profile_dirs = [user_data_dir / "Default"] + list(user_data_dir.glob("Profile *"))
    key = get_chrome_encryption_key(user_data_dir)

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
                    "SELECT name, encrypted_value, value, host_key FROM cookies WHERE host_key LIKE ? OR host_key LIKE ?",
                    (f"%{domain_filter}%", "%flipkart%")
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


def extract_full_session_from_chrome(
    custom_path: Optional[Union[str, Path]] = None,
    save_to_file: bool = True,
) -> Optional[Dict[str, Any]]:
    """
    Extracts session cookies and generates matching headers directly from the
    specified Chrome path (or default installation/profile).
    
    If cookies are found and save_to_file is True, updates config/session.json.

    Returns:
        Dict with 'cookies' and 'headers' if successful, or None.
    """
    resolved = resolve_chrome_paths(custom_path)
    logger.info("Resolving Chrome paths (Exe: %s, UserData: %s)...", resolved.get("executable"), resolved.get("user_data"))

    # 1. Attempt direct SQLite database decryption from Chrome User Data
    cookies = extract_cookies_from_chrome_db(custom_path=custom_path)
    
    # 2. If SQLite was locked or empty, attempt CDP/Playwright extraction if browser is running
    if not cookies or "connect.sid" not in cookies:
        try:
            from auth.playwright_session import PlaywrightSessionHandler
            playwright_handler = PlaywrightSessionHandler(
                profile_dir=resolved.get("user_data"),
                base_url=BASE_URL,
            )
            if playwright_handler.is_browser_open():
                cdp_session = playwright_handler.extract_session_from_opened_browser()
                if cdp_session and cdp_session.get("cookies"):
                    cookies.update(cdp_session["cookies"])
        except Exception as e:
            logger.debug("CDP session fallback check: %s", str(e))

    if not cookies:
        logger.warning("No Flipkart seller cookies were found at the specified Chrome path.")
        return None

    # Determine CSRF Token from cookies if present
    csrf_token = cookies.get("XyZ7pQ9rS2T1uV8wA3bC6dE4fG0h", "")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
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
        "sec-ch-ua": "\"Not=A?Brand\";v=\"99\", \"Google Chrome\";v=\"151\", \"Chromium\";v=\"151\"",
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": "\"Windows\"",
    }

    if csrf_token:
        headers["FK-CSRF-TOKEN"] = csrf_token
        headers["fk-csrf-token"] = csrf_token

    session_payload = {
        "cookies": cookies,
        "headers": headers,
    }

    if save_to_file:
        try:
            SESSION_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(SESSION_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(session_payload, f, indent=4)
            logger.info("Successfully saved %d extracted cookies to %s", len(cookies), SESSION_CONFIG_PATH.name)
        except Exception as e:
            logger.error("Failed to save extracted session to %s: %s", SESSION_CONFIG_PATH, str(e))

    return session_payload


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
