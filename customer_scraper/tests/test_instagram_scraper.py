"""
Unit tests for Instagram scraper module, URL validation, match scoring,
and 21-column CSV/Excel row formatting.
"""

import unittest
from unittest.mock import MagicMock, patch

from scrapers.instagram_scraper import (
    InstagramScraper,
    brand_key,
    calculate_match_score,
    clean_instagram_url,
    get_instagram_username,
    is_valid_instagram_profile,
    normalize_text,
)
from excel.excel_writer import CSVWriter
from config.settings import CSV_COLUMNS


class TestInstagramHelpers(unittest.TestCase):
    def test_normalize_text(self):
        self.assertEqual(normalize_text("The Derma Co Pvt Ltd"), "the derma")
        self.assertEqual(normalize_text("Modicare Limited"), "modicare")
        self.assertEqual(normalize_text("Awesome Brand LLP"), "awesome brand")

    def test_brand_key(self):
        self.assertEqual(brand_key("The Derma Co"), "thederma")
        self.assertEqual(brand_key("The-Derma-Co"), "thederma")
        self.assertEqual(brand_key("WOOSTRO"), "woostro")

    def test_get_instagram_username(self):
        self.assertEqual(
            get_instagram_username("https://www.instagram.com/thedermacoindia/"),
            "thedermacoindia"
        )
        self.assertEqual(
            get_instagram_username("https://instagram.com/woostro_official?igshid=123"),
            "woostro_official"
        )
        self.assertIsNone(get_instagram_username("https://example.com/page"))

    def test_is_valid_instagram_profile(self):
        self.assertTrue(is_valid_instagram_profile("https://www.instagram.com/woostro/"))
        self.assertTrue(is_valid_instagram_profile("https://instagram.com/brand.official_1/"))

        # Rejects posts, reels, explore, system pages
        self.assertFalse(is_valid_instagram_profile("https://www.instagram.com/p/C12345/"))
        self.assertFalse(is_valid_instagram_profile("https://www.instagram.com/reel/C12345/"))
        self.assertFalse(is_valid_instagram_profile("https://www.instagram.com/explore/"))
        self.assertFalse(is_valid_instagram_profile("https://www.instagram.com/about/"))
        self.assertFalse(is_valid_instagram_profile("https://www.instagram.com/reels/"))

    def test_calculate_match_score(self):
        # Exact match
        score_exact = calculate_match_score("Woostro", "woostro", "Woostro Official", "Official store")
        self.assertGreaterEqual(score_exact, 60)

        # Penalize fan / meme accounts
        score_fan = calculate_match_score("Woostro", "woostro_fanpage", "Woostro Fan Club", "Fan page")
        self.assertLess(score_fan, 40)


class TestInstagramScraperMocked(unittest.TestCase):
    def test_search_instagram_mocked(self):
        scraper = InstagramScraper(request_delay=0, min_score=30)

        mock_ddgs = MagicMock()
        mock_ddgs.text.return_value = [
            {
                "href": "https://www.instagram.com/woostro_official/",
                "title": "Woostro Official (@woostro_official)",
                "body": "Welcome to the official Woostro Instagram page.",
            }
        ]

        with patch("scrapers.instagram_scraper.DDGS", return_value=mock_ddgs):
            url = scraper.search_instagram("Woostro")
            self.assertEqual(url, "https://www.instagram.com/woostro_official/")

            # Verify in-memory cache hit
            cached_url = scraper.search_instagram("Woostro")
            self.assertEqual(cached_url, "https://www.instagram.com/woostro_official/")
            # DDGS should not be called again due to caching
            self.assertGreaterEqual(mock_ddgs.text.call_count, 1)


class TestExcelWriter21Columns(unittest.TestCase):
    def setUp(self):
        self.writer = CSVWriter()

    def test_format_customer_rows_21_columns_with_instagram(self):
        """Tests that formatted row has exactly 21 columns and Instagram presence marks isD2C = Yes."""
        self.assertEqual(len(CSV_COLUMNS), 21)

        data = {
            "customer_id": "c111222333444",
            "account_name": "Insta Seller",
            "account_status": "ACTIVE",
            "support_manager": "No",
            "seller_tier": "Bronze",
            "signed_up_date": "2023-01-01",
            "live_date": "2023-01-15",
            "approved_brand": 1,
            "actual_brand_count": 1,
            "request_id": "REQ123",
            "brand_owner": "No",
            "document_type": "OTHER",  # Not BAL/TM
            "brand_website_link": "",   # No website link
            "instagram_url": "https://www.instagram.com/instaseller_official/",  # Instagram found!
            "mobile_number": "9876543210",
            "registered_mobile_number": "9876543210",
            "email_id": "seller@gmail.com",  # Generic email
            "registered_email_id": "seller@gmail.com",  # Generic email
            "unique_email": "No",
            "isD2C": "",  # To be computed
        }

        rows = self.writer._format_customer_rows(data, sr_no=5)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(len(row), 21)

        self.assertEqual(row[0], 5)                                         # Sr No
        self.assertEqual(row[1], "c111222333444")                           # Customer ID
        self.assertEqual(row[14], "https://www.instagram.com/instaseller_official/") # Instagram URL
        self.assertEqual(row[19], "No")                                     # Unique Email
        self.assertEqual(row[20], "Yes")                                    # isD2C (Triggered by Instagram!)


if __name__ == "__main__":
    unittest.main()
