"""
Unit tests for QnA API (questionsV2) parsing, brand D2C determination,
short-circuiting logic, and 20-column Excel/CSV formatting.
"""

import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock

from api.api_client import APIClient
from scrapers.api2_scraper import API2Scraper
from scrapers.api3_scraper import API3Scraper, determine_unique_email
from excel.excel_writer import CSVWriter
from config.settings import CSV_COLUMNS


class TestQnAParser(unittest.TestCase):
    def setUp(self):
        self.mock_client = MagicMock(spec=APIClient)
        self.mock_client.auth_manager = MagicMock()
        self.mock_client.auth_manager.get_csrf_token.return_value = "mock_csrf_token"
        self.scraper = API2Scraper(self.mock_client)

    def test_parse_real_response_file(self):
        """Tests parsing questions and answers directly from Response.txt."""
        response_file = Path(__file__).resolve().parent.parent / "Response.txt"
        self.assertTrue(response_file.exists(), "Response.txt must exist")

        with open(response_file, "r", encoding="utf-8") as f:
            sample_data = json.load(f)

        self.mock_client.get.return_value = sample_data

        result = self.scraper.get_question_answers("aaa30e788efa4f6c", "460248034")

        # Verify parsed answers from Response.txt
        self.assertEqual(result["brand_owner"], "No")
        self.assertEqual(result["document_type"], "BAL")
        self.assertEqual(result["brand_website_link"], "")

    def test_brand_approval_details_short_circuit(self):
        """
        Tests that when a seller has 2 unique brands with multiple request IDs,
        the first request yielding BAL/TM short-circuits further API calls.
        """
        # Mock requestsV2-count
        self.mock_client.get.side_effect = [
            # 1. requestsV2-count
            {"APPROVED": 3},
            # 2. questionsV2 for first request (yielding BAL)
            {
                "sections": {
                    "MANDATORY_APPROVED": [
                        {
                            "question": {"text": "Select the document type"},
                            "answer": {"answer_text": "BAL"},
                        }
                    ],
                    "OPTIONAL": [
                        {
                            "question": {"text": "Are you the brand owner?"},
                            "answer": {"answer_text": "No"},
                        }
                    ]
                }
            }
        ]

        # Mock requestsV2 (3 approved records: 2 for BRAND_A, 1 for BRAND_B)
        self.mock_client.post.return_value = [
            {"request_id": "REQ101", "brand_name": "BRAND_A", "request_status": "Approved"},
            {"request_id": "REQ102", "brand_name": "BRAND_A", "request_status": "Approved"},
            {"request_id": "REQ201", "brand_name": "BRAND_B", "request_status": "Approved"},
        ]

        res = self.scraper.get_brand_approval_details("seller_test_1")

        self.assertEqual(res["approved_brand"], 3)
        self.assertEqual(res["actual_brand_count"], 2)
        self.assertEqual(res["request_id"], "REQ101")
        self.assertEqual(res["brand_name"], "BRAND_A")
        self.assertEqual(res["document_type"], "BAL")
        self.assertEqual(res["brand_owner"], "No")
        self.assertTrue(res["brand_is_d2c"])

        # questionsV2 should have only been called once due to short-circuiting!
        # Total get calls = 1 for count + 1 for REQ101 = 2
        self.assertEqual(self.mock_client.get.call_count, 2)

    def test_brand_website_link_d2c_trigger(self):
        """Tests that a valid Brand Website Link triggers brand_is_d2c = True."""
        self.mock_client.get.side_effect = [
            {"APPROVED": 1},
            {
                "sections": {
                    "MANDATORY_APPROVED": [
                        {
                            "question": {"text": "Select the document type"},
                            "answer": {"answer_text": "OTHER"},
                        }
                    ],
                    "OPTIONAL": [
                        {
                            "question": {"text": "Brand Website Link"},
                            "answer": {"answer_text": "https://www.mybrandstore.in"},
                        }
                    ]
                }
            }
        ]

        self.mock_client.post.return_value = [
            {"request_id": "REQ301", "brand_name": "COOL_BRAND", "request_status": "Approved"},
        ]

        res = self.scraper.get_brand_approval_details("seller_test_2")
        self.assertEqual(res["request_id"], "REQ301")
        self.assertEqual(res["brand_name"], "COOL_BRAND")
        self.assertEqual(res["document_type"], "OTHER")
        self.assertEqual(res["brand_website_link"], "https://www.mybrandstore.in")
        self.assertTrue(res["brand_is_d2c"])


class TestExcelWriter22Columns(unittest.TestCase):
    def setUp(self):
        self.writer = CSVWriter()

    def test_format_customer_rows_22_columns(self):
        """Tests that formatted row has exactly 22 columns matching CSV_COLUMNS."""
        self.assertEqual(len(CSV_COLUMNS), 22)

        data = {
            "customer_id": "c1234567890",
            "account_name": "Test Enterprise",
            "account_status": "ACTIVE",
            "support_manager": "Yes",
            "seller_tier": "Platinum",
            "signed_up_date": "2023-01-01T00:00:00",
            "live_date": "2023-01-15",
            "approved_brand": 5,
            "actual_brand_count": 2,
            "request_id": "REQ999",
            "brand_name": "Test Brand",
            "brand_owner": "Yes",
            "document_type": "TM",
            "brand_website_link": "https://brand.com",
            "instagram_url": "https://www.instagram.com/testenterprise/",
            "mobile_number": "9876543210",
            "registered_mobile_number": "9876543210",
            "email_id": "info@brand.com",
            "registered_email_id": "contact@brand.com",
            "unique_email": "Yes",
            "isD2C": "Yes",
        }

        rows = self.writer._format_customer_rows(data, sr_no=1)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(len(row), 23)

        # Check column values in order
        self.assertEqual(row[0], 1)                   # Sr No
        self.assertEqual(row[1], "c1234567890")       # Customer ID
        self.assertEqual(row[2], "Test Enterprise")   # Account Name
        self.assertEqual(row[3], "ACTIVE")            # Account Status
        self.assertEqual(row[4], "Yes")               # Support Manager
        self.assertEqual(row[5], "Platinum")          # Seller Tier
        self.assertEqual(row[6], "2023-01-01")        # Signed Up Date
        self.assertEqual(row[7], "2023-01-15")        # Live Date
        self.assertEqual(row[8], 5)                   # Approved Brand
        self.assertEqual(row[9], 2)                   # Actual Brand Count
        self.assertEqual(row[10], "REQ999")           # Request ID
        self.assertEqual(row[11], "Test Brand")       # Brand Name
        self.assertEqual(row[12], "Yes")              # Brand Owner
        self.assertEqual(row[13], "TM")               # Document Type
        self.assertEqual(row[14], "https://brand.com")# Brand Website Link
        self.assertEqual(row[15], "https://www.instagram.com/testenterprise/") # Instagram URL
        self.assertEqual(row[16], "")                 # Instagram Followers
        self.assertEqual(row[17], "9876543210")       # Mobile Number
        self.assertEqual(row[18], "9876543210")       # Registered Mobile Number
        self.assertEqual(row[19], "info@brand.com")   # Email ID
        self.assertEqual(row[20], "contact@brand.com")# Registered Email ID
        self.assertEqual(row[21], "Yes")              # Unique Email
        self.assertEqual(row[22], "Yes")              # isD2C

    def test_format_customer_rows_non_d2c_record(self):
        """Tests that non-D2C records are properly formatted with isD2C = 'No' and saved."""
        data = {
            "customer_id": "c9876543210",
            "account_name": "Generic Seller",
            "account_status": "ACTIVE",
            "support_manager": "No",
            "seller_tier": "Bronze",
            "signed_up_date": "2023-05-10",
            "live_date": "2023-05-20",
            "approved_brand": 0,
            "actual_brand_count": 0,
            "request_id": "",
            "brand_name": "",
            "brand_owner": "",
            "document_type": "",
            "brand_website_link": "",
            "instagram_url": "",
            "instagram_followers": "",
            "mobile_number": "9123456780",
            "registered_mobile_number": "9123456780",
            "email_id": "seller@gmail.com",
            "registered_email_id": "seller@gmail.com",
            "unique_email": "No",
            "isD2C": "No",
        }

        rows = self.writer._format_customer_rows(data, sr_no=2)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(len(row), 23)
        self.assertEqual(row[0], 2)
        self.assertEqual(row[1], "c9876543210")
        self.assertEqual(row[11], "")     # Brand Name
        self.assertEqual(row[15], "")     # Instagram URL
        self.assertEqual(row[16], "")     # Instagram Followers
        self.assertEqual(row[21], "No")   # Unique Email
        self.assertEqual(row[22], "No")   # isD2C


if __name__ == "__main__":
    unittest.main()

