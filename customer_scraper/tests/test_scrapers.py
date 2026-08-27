"""
Unit tests for API 1, API 2 (Approval Store), and API 3 scrapers.
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

    def test_account_status_and_support_manager_no(self):
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
                            "state_details": {
                                "state": "ACTIVE"
                            },
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
        self.assertEqual(res["account_status"], "ACTIVE")
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
                            "state_details": {
                                "state": "BLOCKED"
                            },
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
        self.assertEqual(res["account_status"], "BLOCKED")
        self.assertEqual(res["seller_tier"], "Gold")

    def test_null_and_missing_handling(self):
        self.mock_client.get.return_value = {"result": None}
        res = self.scraper.get_seller_details("ID004")
        self.assertEqual(res["customer_id"], "ID004")
        self.assertEqual(res["account_name"], "")
        self.assertEqual(res["account_status"], "")
        self.assertEqual(res["support_manager"], "No")
        self.assertEqual(res["seller_tier"], "")
        self.assertEqual(res["signed_up_date"], "")
        self.assertEqual(res["live_date"], "")


class TestAPI2Scraper(unittest.TestCase):
    def setUp(self):
        self.mock_client = MagicMock(spec=APIClient)
        self.scraper = API2Scraper(self.mock_client)

    def test_requests_v2_counts(self):
        self.mock_client.get.return_value = {
            "ALL": 43,
            "RESUBMISSION_REQUIRED": 17,
            "DISAPPROVED": 16,
            "APPROVAL_PENDING": 0,
            "APPROVED": 26,
        }
        counts = self.scraper.get_approval_counts("00396fe5ddcb4956")
        self.assertEqual(counts.get("APPROVED"), 26)
        self.assertEqual(counts.get("ALL"), 43)

    def test_unique_brand_count_case_insensitive_deduplication(self):
        self.mock_client.get.return_value = {
            "ALL": 5,
            "APPROVED": 4,
        }
        mock_records = [
            {"brand_name": "BRAND", "request_status": "Approved"},
            {"brand_name": "Brand", "request_status": "Approved"},
            {"brand_name": "bRAND", "request_status": "Approved"},
            {"brand_name": "OTHER_BRAND", "request_status": "Approved"},
            {"brand_name": "DISAPPROVED_BRAND", "request_status": "Disapproved"},
        ]
        self.mock_client.post.return_value = mock_records

        res = self.scraper.get_brand_approval_details("00396fe5ddcb4956")
        self.assertEqual(res["approved_brand"], 4)
        # "BRAND", "Brand", "bRAND" deduplicate to 1, plus "OTHER_BRAND" = 2 unique approved brands
        self.assertEqual(res["actual_brand_count"], 2)
        self.assertIn("brand", res["unique_brands"])
        self.assertIn("other_brand", res["unique_brands"])

    def test_approved_7_with_all_unique_brands_gives_7(self):
        self.mock_client.get.return_value = {"ALL": 10, "APPROVED": 7}
        mock_records = [
            {"brand_name": f"UNIQUE_BRAND_{i}", "request_status": "Approved"}
            for i in range(1, 8)
        ]
        self.mock_client.post.return_value = mock_records
        res = self.scraper.get_brand_approval_details("SELLER_7")
        self.assertEqual(res["approved_brand"], 7)
        self.assertEqual(res["actual_brand_count"], 7)

    def test_approved_7_with_empty_requests_gives_7(self):
        # If requestsV2 returns empty/fails, approved count (7) is preserved as actual count
        self.mock_client.get.return_value = {"ALL": 7, "APPROVED": 7}
        self.mock_client.post.return_value = []
        res = self.scraper.get_brand_approval_details("SELLER_EMPTY")
        self.assertEqual(res["approved_brand"], 7)
        self.assertEqual(res["actual_brand_count"], 7)

    def test_zero_approved_brands(self):
        self.mock_client.get.return_value = {
            "ALL": 0,
            "APPROVED": 0,
        }
        res = self.scraper.get_brand_approval_details("ID_ZERO")
        self.assertEqual(res["approved_brand"], 0)
        self.assertEqual(res["actual_brand_count"], 0)


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
        self.assertEqual(res["registered_mobile_number"], "+919999354199")
        self.assertEqual(res["email_id"], "slowlorisstore@gmail.com")
        self.assertEqual(res["registered_email_id"], "profile_email@example.com")

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
