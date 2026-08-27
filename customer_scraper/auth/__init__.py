"""
Authentication package for customer scraper.
"""

from .auth_manager import AuthManager, AuthExpiredError
from .playwright_session import PlaywrightSessionHandler
from .chrome_session import (
    resolve_chrome_paths,
    extract_cookies_from_chrome_db,
    extract_full_session_from_chrome,
)

__all__ = [
    "AuthManager",
    "AuthExpiredError",
    "PlaywrightSessionHandler",
    "resolve_chrome_paths",
    "extract_cookies_from_chrome_db",
    "extract_full_session_from_chrome",
]
