"""
Unit tests for API 1, API 2, and API 3 scrapers and business rules.
"""

import unittest
from unittest.mock import MagicMock

from api.api_client import APIClient
from scrapers.api1_scraper import API1Scraper
from scrapers.api2_scraper import API2Scraper
from scrapers.api3_scraper import API3Scraper


class TestAPI1Scraper(unittest.TestCase):
    def setUp(self):
        self.mock_client = MagicMock(spec=APIClient)
        self.scraper = API1Scraper(self.mock_client)

    def test_support_manager_no_when_all_fields_null(self):
        self.mock_client.get.return_value = {
            "result": {
                "displayName": "Seller One",
                "supportRole": {
                    "tier_type": "SUPPORT",
                    "role_name": None,
                    "user_id": None,
                    "email_id": None,
                    "name": None,
                    "phone_num": None,
                    "manager_email_id": None,
                },
                "gmv": {
                    "response": {
                        "details": {
                            "darwin_tier_v2": {
                                "tier_name": "Silver"
                            }
                        }
                    }
                },
                "profileInfo": {
                    "created_at": "2022-01-15T10:00:00Z"
                },
                "liveDate": "2022-02-01"
            }
        }

        res = self.scraper.get_seller_details("ID001")
        self.assertEqual(res["customer_id"], "ID001")
        self.assertEqual(res["account_name"], "Seller One")
        self.assertEqual(res["support_manager"], "No")
        self.assertEqual(res["seller_tier"], "Silver")
        self.assertEqual(res["signed_up_date"], "2022-01-15")
        self.assertEqual(res["live_date"], "2022-02-01")

    def test_support_manager_yes_when_manager_field_present(self):
        self.mock_client.get.return_value = {
            "result": {
                "displayName": "Seller Two",
                "supportRole": {
                    "tier_type": "SUPPORT",
                    "role_name": "Account Manager",
                    "user_id": "mgr123",
                    "email_id": "mgr@fk.com",
                    "name": "John Doe",
                    "phone_num": "9999999999",
                    "manager_email_id": "lead@fk.com",
                },
                "gmv": {
                    "response": {
                        "details": {
                            "darwin_tier_v2": {
                                "tier_name": "Gold"
                            }
                        }
                    }
                },
                "profileInfo": {
                    "created_at": "2021-05-10"
                },
                "liveDate": "2021-06-01"
            }
        }

        res = self.scraper.get_seller_details("ID002")
        self.assertEqual(res["support_manager"], "Yes")
        self.assertEqual(res["account_name"], "Seller Two")
        self.assertEqual(res["seller_tier"], "Gold")

    def test_support_manager_yes_when_single_non_null_field(self):
        self.mock_client.get.return_value = {
            "result": {
                "displayName": "Seller Three",
                "supportRole": {
                    "tier_type": "SUPPORT",
                    "role_name": None,
                    "user_id": None,
                    "email_id": "support_agent@example.com",
                    "name": None,
                    "phone_num": None,
                    "manager_email_id": None,
                }
            }
        }
        res = self.scraper.get_seller_details("ID003")
        self.assertEqual(res["support_manager"], "Yes")

    def test_null_and_missing_handling(self):
        self.mock_client.get.return_value = {"result": None}
        res = self.scraper.get_seller_details("ID004")
        self.assertEqual(res["customer_id"], "ID004")
        self.assertEqual(res["account_name"], "")
        self.assertEqual(res["support_manager"], "No")
        self.assertEqual(res["seller_tier"], "")
        self.assertEqual(res["signed_up_date"], "")
        self.assertEqual(res["live_date"], "")


class TestAPI2Scraper(unittest.TestCase):
    def setUp(self):
        self.mock_client = MagicMock(spec=APIClient)
        self.scraper = API2Scraper(self.mock_client)

    def test_brand_rule_more_than_12_is_possibly_a_brand(self):
        # 13 listings of "IGRIM", 7 listings of "OTHER" = 20 total
        mock_listings = [
            {"title": f"Product {i}", "brand": "IGRIM"} for i in range(13)
        ] + [
            {"title": f"Other Product {i}", "brand": "OTHER"} for i in range(7)
        ]

        self.mock_client.post.return_value = {
            "listing_data_response": mock_listings
        }

        res = self.scraper.get_listings_and_brand("ID001")
        self.assertEqual(len(res["listing_titles"]), 20)
        self.assertEqual(res["is_brand"], "Possibly a Brand")
        self.assertEqual(res["brand_name"], "IGRIM")

    def test_brand_rule_exactly_12_is_possibly_a_seller(self):
        # Exactly 12 of "IGRIM" (does NOT satisfy strictly > 12)
        mock_listings = [
            {"title": f"Product {i}", "brand": "IGRIM"} for i in range(12)
        ] + [
            {"title": f"Other Product {i}", "brand": "OTHER"} for i in range(8)
        ]

        self.mock_client.post.return_value = {
            "listing_data_response": mock_listings
        }

        res = self.scraper.get_listings_and_brand("ID002")
        self.assertEqual(len(res["listing_titles"]), 20)
        self.assertEqual(res["is_brand"], "Possibly a Seller")
        self.assertEqual(res["brand_name"], "")

    def test_brand_rule_distributed_brands_is_possibly_a_seller(self):
        # 20 different brands
        mock_listings = [
            {"title": f"Product {i}", "brand": f"BRAND_{i}"} for i in range(20)
        ]
        self.mock_client.post.return_value = {
            "listing_data_response": mock_listings
        }
        res = self.scraper.get_listings_and_brand("ID003")
        self.assertEqual(res["is_brand"], "Possibly a Seller")
        self.assertEqual(res["brand_name"], "")

    def test_fewer_than_20_listings(self):
        # Only 5 listings returned
        mock_listings = [
            {"title": f"Item {i}", "brand": "TEST_BRAND"} for i in range(5)
        ]
        self.mock_client.post.return_value = {
            "result": {"listing_data_response": mock_listings}
        }
        res = self.scraper.get_listings_and_brand("ID004")
        self.assertEqual(len(res["listing_titles"]), 5)
        # 5 is not > 12 -> Possibly a Seller
        self.assertEqual(res["is_brand"], "Possibly a Seller")
        self.assertEqual(res["brand_name"], "")

    def test_graphql_response_parsing(self):
        mock_data = {
            "data": {
                "listingsManagementMetrics": {
                    "listingRows": {
                        "count": 100,
                        "listingDataResponse": [
                            {
                                "title": f"Shoe {i}",
                                "brand": "NIKE",
                                "view": {"title": f"Shoe {i}", "brand": "NIKE"}
                            }
                            for i in range(15)
                        ] + [
                            {
                                "title": f"Puma {i}",
                                "brand": "PUMA",
                                "view": {"title": f"Puma {i}", "brand": "PUMA"}
                            }
                            for i in range(5)
                        ]
                    }
                }
            }
        }
        self.mock_client.post.return_value = mock_data
        res = self.scraper.get_listings_and_brand("ID_GQL")
        self.assertEqual(len(res["listing_titles"]), 20)
        self.assertEqual(len(res["listing_brands"]), 20)
        self.assertEqual(res["is_brand"], "Possibly a Brand")
        self.assertEqual(res["brand_name"], "NIKE")


class TestAPI3Scraper(unittest.TestCase):
    def setUp(self):
        self.mock_client = MagicMock(spec=APIClient)
        self.scraper = API3Scraper(self.mock_client)

    def test_get_seller_contacts_from_profile_info(self):
        self.mock_client.get.return_value = {
            "result": {
                "loginMobileNumber": "+919717321982",
                "primaryMobileNumber": "+919717321982",
                "loginEmail": "slowlorisstore@gmail.com",
                "primaryEmail": "slowlorisstore@gmail.com",
                "profileInfo": {
                    "email_id": "profile_email@example.com",
                    "mobile_number": "+919999354199",
                }
            }
        }
        res = self.scraper.get_seller_contacts("ID001")
        self.assertEqual(res["customer_id"], "ID001")
        self.assertEqual(res["mobile_number"], "+919717321982")
        self.assertEqual(res["registered_mobile_number"], "+919999354199")  # from profileInfo.mobile_number
        self.assertEqual(res["email_id"], "slowlorisstore@gmail.com")
        self.assertEqual(res["registered_email_id"], "profile_email@example.com")  # from profileInfo.email_id

    def test_get_seller_contacts_null_values(self):
        self.mock_client.get.return_value = {
            "result": {
                "loginMobileNumber": None,
                "primaryMobileNumber": "null",
                "loginEmail": "None",
                "primaryEmail": None,
            }
        }
        res = self.scraper.get_seller_contacts("ID002")
        self.assertEqual(res["mobile_number"], "")
        self.assertEqual(res["registered_mobile_number"], "")
        self.assertEqual(res["email_id"], "")
        self.assertEqual(res["registered_email_id"], "")


if __name__ == "__main__":
    unittest.main()
