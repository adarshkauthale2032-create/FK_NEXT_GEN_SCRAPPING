"""
Scrapers package containing isolated scraper modules for API 1, 2, and 3.
"""

from .api1_scraper import API1Scraper
from .api2_scraper import API2Scraper
from .api3_scraper import API3Scraper

__all__ = ["API1Scraper", "API2Scraper", "API3Scraper"]
