"""
Authentication package for customer scraper.
"""

from .auth_manager import AuthManager, AuthExpiredError
from .playwright_session import PlaywrightSessionHandler

__all__ = ["AuthManager", "AuthExpiredError", "PlaywrightSessionHandler"]
