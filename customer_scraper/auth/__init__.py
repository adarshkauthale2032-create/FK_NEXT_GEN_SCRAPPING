"""
Authentication package for customer scraper.
"""

from .auth_manager import AuthManager, AuthExpiredError
from .playwright_session import PlaywrightSessionHandler
from .chrome_session import (
    parse_curl_command,
    extract_full_session_from_chrome,
)

__all__ = [
    "AuthManager",
    "AuthExpiredError",
    "PlaywrightSessionHandler",
    "parse_curl_command",
    "extract_full_session_from_chrome",
]

