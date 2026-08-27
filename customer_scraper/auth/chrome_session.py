"""
Chrome Session & cURL Helper.

Provides lightweight utility functions such as cURL command parsing
and CDP delegation, without hardcoded paths or SQLite crypto dependencies.
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional

from config.settings import SESSION_CONFIG_PATH
from auth.playwright_session import PlaywrightSessionHandler

logger = logging.getLogger("customer_scraper")


def parse_curl_command(curl_text: str) -> Dict[str, Any]:
    """
    Parses a copied cURL command to extract cookies, headers, and tokens.
    """
    headers: Dict[str, str] = {}
    cookies: Dict[str, str] = {}

    # Extract headers (-H 'Key: Value' or -H "Key: Value" or -H ^"Key: Value^")
    # Clean cmd escape carats ^
    cleaned_curl = curl_text.replace("^", "")

    header_matches = re.findall(r"-H\s+[\"']([^\"']+)[\"']", cleaned_curl)
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
    cookie_flag_matches = re.findall(r"(?:--cookie|-b)\s+[\"']([^\"']+)[\"']", cleaned_curl)
    for c_str in cookie_flag_matches:
        for item in c_str.split(";"):
            if "=" in item:
                ck, cv = item.strip().split("=", 1)
                cookies[ck.strip()] = cv.strip()

    # If FK-CSRF-TOKEN is in headers, also mirror lowercase
    for k, v in list(headers.items()):
        if k.lower() == "fk-csrf-token":
            headers["FK-CSRF-TOKEN"] = v
            headers["fk-csrf-token"] = v

    return {"cookies": cookies, "headers": headers}


def extract_full_session_from_chrome(
    seller_id: Optional[str] = None,
    save_to_file: bool = True,
) -> Optional[Dict[str, Any]]:
    """
    Delegates session extraction to the CDP-based PlaywrightSessionHandler.
    """
    handler = PlaywrightSessionHandler(session_file=SESSION_CONFIG_PATH)
    return handler.refresh_and_extract_session(seller_id=seller_id)
