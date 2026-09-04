"""
Unit tests for Instagram scraper module, URL validation, followers count extraction,
match scoring, and 23-column CSV/Excel row formatting.
"""

import unittest
from unittest.mock import MagicMock, patch

from scrapers.instagram_scraper import (
    InstagramScraper,
    brand_key,
    calculate_match_score,
    clean_instagram_url,
    extract_instagram_followers_from_text,
    get_instagram_username,
    is_brand_in_instagram_url,
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

    def test_extract_instagram_followers_from_text(self):
        self.assertEqual(extract_instagram_followers_from_text("125K Followers, 450 Following, 1,200 Posts"), "125K")
        self.assertEqual(extract_instagram_followers_from_text("50.2k followers • 1,234 posts"), "50.2K")
        self.assertEqual(extract_instagram_followers_from_text("1,234 Followers"), "1,234")
        self.assertEqual(extract_instagram_followers_from_text("1.5M Followers on Instagram"), "1.5M")
        self.assertEqual(extract_instagram_followers_from_text("Followers: 45K"), "45K")
        self.assertEqual(extract_instagram_followers_from_text('<meta property="og:description" content="350K Followers, 100 Following">'), "350K")
        self.assertEqual(extract_instagram_followers_from_text("No stats here"), "")

    def test_calculate_match_score(self):
        # Exact match
        score_exact = calculate_match_score("Woostro", "woostro", "Woostro Official", "Official store")
        self.assertGreaterEqual(score_exact, 60)

        # Penalize fan / meme accounts
        score_fan = calculate_match_score("Woostro", "woostro_fanpage", "Woostro Fan Club", "Fan page")
        self.assertLess(score_fan, 40)

    def test_is_brand_in_instagram_url(self):
        # 1. Exact / variations of lenskart
        self.assertTrue(is_brand_in_instagram_url("lenskart", "https://www.instagram.com/lenskart/"))
        self.assertTrue(is_brand_in_instagram_url("lenskart", "https://www.instagram.com/lens.kart/"))
        self.assertTrue(is_brand_in_instagram_url("lenskart", "https://www.instagram.com/lens_kart/"))
        self.assertTrue(is_brand_in_instagram_url("lenskart", "https://www.instagram.com/lens_kart_official/"))
        self.assertTrue(is_brand_in_instagram_url("lenskart", "https://www.instagram.com/lenskartofficial/"))
        self.assertTrue(is_brand_in_instagram_url("lens.kart", "https://www.instagram.com/lenskart/"))
        self.assertTrue(is_brand_in_instagram_url("LENS_KART", "https://www.instagram.com/lens.kart.india/"))
        self.assertTrue(is_brand_in_instagram_url("lens-kart", "https://www.instagram.com/lens_kart/"))

        # 2. Multi-word brand names
        self.assertTrue(is_brand_in_instagram_url("IBELL POWER TOOLS", "https://www.instagram.com/ibell_tools/"))
        self.assertTrue(is_brand_in_instagram_url("The Derma Co", "https://www.instagram.com/thedermacoindia/"))
        self.assertTrue(is_brand_in_instagram_url("Kalivera Healthcare", "https://www.instagram.com/kaliverahealthcare/"))

        # 3. Mismatched brands (should fail validation)
        self.assertFalse(is_brand_in_instagram_url("lenskart", "https://www.instagram.com/specsmakers/"))
        self.assertFalse(is_brand_in_instagram_url("lenskart", "https://www.instagram.com/titaneyeplus/"))
        self.assertFalse(is_brand_in_instagram_url("IBELL", "https://www.instagram.com/boschtools/"))
        self.assertFalse(is_brand_in_instagram_url("BRAND_1", "https://www.instagram.com/somebrand/"))
        self.assertFalse(is_brand_in_instagram_url("", "https://www.instagram.com/lenskart/"))
        self.assertFalse(is_brand_in_instagram_url("lenskart", ""))


class TestInstagramScraperMocked(unittest.TestCase):
    def test_search_instagram_mocked(self):
        scraper = InstagramScraper(request_delay=0, min_score=30)
        scraper._is_cdp_available = MagicMock(return_value=False)

        mock_ddgs = MagicMock()
        mock_ddgs.text.return_value = [
            {
                "href": "https://www.instagram.com/woostro_official/",
                "title": "Woostro Official (@woostro_official)",
                "body": "Welcome to the official Woostro page. 25K Followers, 100 Following.",
            }
        ]

        with patch("scrapers.instagram_scraper.DDGS", return_value=mock_ddgs):
            details = scraper.search_instagram_with_details("Woostro")
            self.assertEqual(details["instagram_url"], "https://www.instagram.com/woostro_official/")
            self.assertEqual(details["instagram_followers"], "25K")

            # Verify in-memory cache hit
            cached_url = scraper.search_instagram("Woostro")
            self.assertEqual(cached_url, "https://www.instagram.com/woostro_official/")


class TestExcelWriter23Columns(unittest.TestCase):
    def setUp(self):
        self.writer = CSVWriter()

    def test_format_customer_rows_23_columns_with_instagram_followers(self):
        """Tests that formatted row has exactly 23 columns and Instagram Followers is at index 16."""
        self.assertEqual(len(CSV_COLUMNS), 23)
        self.assertEqual(CSV_COLUMNS[15], "Instagram URL")
        self.assertEqual(CSV_COLUMNS[16], "Instagram Followers")

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
            "brand_name": "Insta Brand",
            "brand_owner": "No",
            "document_type": "OTHER",  # Not BAL/TM
            "brand_website_link": "",   # No website link
            "instagram_url": "https://www.instagram.com/instaseller_official/",  # Instagram found!
            "instagram_followers": "125K",  # Followers count!
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
        self.assertEqual(len(row), 23)

        self.assertEqual(row[0], 5)                                         # Sr No
        self.assertEqual(row[1], "c111222333444")                           # Customer ID
        self.assertEqual(row[10], "REQ123")                                 # Request ID
        self.assertEqual(row[11], "Insta Brand")                            # Brand Name
        self.assertEqual(row[15], "https://www.instagram.com/instaseller_official/") # Instagram URL
        self.assertEqual(row[16], "125K")                                   # Instagram Followers
        self.assertEqual(row[21], "No")                                     # Unique Email
        self.assertEqual(row[22], "Yes")                                    # isD2C (Triggered by Instagram!)


if __name__ == "__main__":
    unittest.main()
