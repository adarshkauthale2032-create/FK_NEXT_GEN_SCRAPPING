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

    def test_brand_approval_details_multi_brand_search(self):
        """
        Tests that when a seller has 2 unique brands, QnA is queried for each unique brand
        and all brand details are returned in brands_details list.
        """
        # Mock requestsV2-count
        self.mock_client.get.side_effect = [
            # 1. requestsV2-count
            {"APPROVED": 3},
            # 2. questionsV2 for first brand (BRAND_A -> BAL)
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
            },
            # 3. questionsV2 for second brand (BRAND_B -> TM)
            {
                "sections": {
                    "MANDATORY_APPROVED": [
                        {
                            "question": {"text": "Select the document type"},
                            "answer": {"answer_text": "TM"},
                        }
                    ],
                    "OPTIONAL": [
                        {
                            "question": {"text": "Are you the brand owner?"},
                            "answer": {"answer_text": "Yes"},
                        }
                    ]
                }
            }
        ]

        # Mock requestsV2 (3 approved records: 2 for BRAND_A, 1 for BRAND_B)
        self.mock_client.post.return_value = [
            {"request_id": "REQ101", "brand_name": "BRAND_A", "vertical": "Clothing", "request_status": "Approved"},
            {"request_id": "REQ102", "brand_name": "BRAND_A", "vertical": "Clothing", "request_status": "Approved"},
            {"request_id": "REQ201", "brand_name": "BRAND_B", "vertical": "Footwear", "request_status": "Approved"},
        ]

        res = self.scraper.get_brand_approval_details("seller_test_1")

        self.assertEqual(res["approved_brand"], 3)
        self.assertEqual(res["actual_brand_count"], 2)
        self.assertEqual(res["request_id"], "REQ101")
        self.assertEqual(res["brand_name"], "BRAND_A")
        self.assertEqual(res["document_type"], "BAL")
        self.assertEqual(res["brand_owner"], "No")
        self.assertTrue(res["brand_is_d2c"])

        # Verify all unique brands details captured
        self.assertIn("brands_details", res)
        self.assertEqual(len(res["brands_details"]), 2)
        self.assertEqual(res["brands_details"][0]["brand_name"], "BRAND_A")
        self.assertEqual(res["brands_details"][0]["request_id"], "REQ101")
        self.assertEqual(res["brands_details"][0]["document_type"], "BAL")
        self.assertEqual(res["brands_details"][0]["vertical_name"], "Clothing")

        self.assertEqual(res["brands_details"][1]["brand_name"], "BRAND_B")
        self.assertEqual(res["brands_details"][1]["request_id"], "REQ201")
        self.assertEqual(res["brands_details"][1]["document_type"], "TM")
        self.assertEqual(res["brands_details"][1]["vertical_name"], "Footwear")

        # Total get calls = 1 for count + 1 for BRAND_A + 1 for BRAND_B = 3
        self.assertEqual(self.mock_client.get.call_count, 3)

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
        self.assertEqual(len(res["brands_details"]), 1)


class TestExcelWriter30Columns(unittest.TestCase):
    def setUp(self):
        self.writer = CSVWriter()

    def test_format_customer_rows_30_columns(self):
        """Tests that formatted row has exactly 30 columns matching CSV_COLUMNS."""
        self.assertEqual(len(CSV_COLUMNS), 30)

        data = {
            "customer_id": "c1234567890",
            "account_name": "Test Enterprise",
            "account_status": "ACTIVE",
            "support_manager": "Yes",
            "seller_tier": "Platinum",
            "address": "123 MG Road",
            "signed_up_date": "2023-01-01T00:00:00",
            "live_date": "2023-01-15",
            "approved_brand": 5,
            "actual_brand_count": 2,
            "request_id": "REQ999",
            "brand_name": "Test Brand",
            "vertical_name": "Clothing",
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
            "month": "June 2026",
            "gross_amount": "₹10,000",
            "gross_units": "5",
            "net_amount": "₹8,000",
            "cancelled_amount": "₹2,000",
        }

        rows = self.writer._format_customer_rows(data, sr_no=1)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(len(row), 30)

        # Check column values in order
        self.assertEqual(row[0], 1)                   # Sr No
        self.assertEqual(row[1], "c1234567890")       # Customer ID
        self.assertEqual(row[2], "Test Enterprise")   # Account Name
        self.assertEqual(row[3], "ACTIVE")            # Account Status
        self.assertEqual(row[4], "Yes")               # Support Manager
        self.assertEqual(row[5], "Platinum")          # Seller Tier
        self.assertEqual(row[6], "123 MG Road")       # Address
        self.assertEqual(row[7], "2023-01-01")        # Signed Up Date
        self.assertEqual(row[8], "2023-01-15")        # Live Date
        self.assertEqual(row[9], 5)                   # Approved Brand
        self.assertEqual(row[10], 2)                  # Actual Brand Count
        self.assertEqual(row[11], "REQ999")           # Request ID
        self.assertEqual(row[12], "Test Brand")       # Brand Name
        self.assertEqual(row[13], "Clothing")         # Vertical Name
        self.assertEqual(row[14], "Yes")              # Brand Owner
        self.assertEqual(row[15], "TM")               # Document Type
        self.assertEqual(row[16], "https://brand.com")# Brand Website Link
        self.assertEqual(row[17], "https://www.instagram.com/testenterprise/") # Instagram URL
        self.assertEqual(row[18], "")                 # Instagram Followers
        self.assertEqual(row[19], "9876543210")       # Mobile Number
        self.assertEqual(row[20], "9876543210")       # Registered Mobile Number
        self.assertEqual(row[21], "info@brand.com")   # Email ID
        self.assertEqual(row[22], "contact@brand.com")# Registered Email ID
        self.assertEqual(row[23], "Yes")              # Unique Email
        self.assertEqual(row[24], "Yes")              # isD2C
        self.assertEqual(row[25], "June 2026")        # Month
        self.assertEqual(row[26], "₹10,000")          # Gross Amount (GMV)
        self.assertEqual(row[27], "5")                # Gross Units
        self.assertEqual(row[28], "₹8,000")           # Net Amount
        self.assertEqual(row[29], "₹2,000")           # Cancelled Amount

    def test_format_customer_rows_non_d2c_record(self):
        """Tests that non-D2C records are properly formatted with isD2C = 'No' and saved."""
        data = {
            "customer_id": "c9876543210",
            "account_name": "Generic Seller",
            "account_status": "ACTIVE",
            "support_manager": "No",
            "seller_tier": "Bronze",
            "address": "",
            "signed_up_date": "2023-05-10",
            "live_date": "2023-05-20",
            "approved_brand": 0,
            "actual_brand_count": 0,
            "request_id": "",
            "brand_name": "",
            "vertical_name": "",
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
        self.assertEqual(len(row), 30)
        self.assertEqual(row[0], 2)
        self.assertEqual(row[1], "c9876543210")
        self.assertEqual(row[6], "")      # Address
        self.assertEqual(row[12], "")     # Brand Name
        self.assertEqual(row[13], "")     # Vertical Name
        self.assertEqual(row[17], "")     # Instagram URL
        self.assertEqual(row[18], "")     # Instagram Followers
        self.assertEqual(row[23], "No")   # Unique Email
        self.assertEqual(row[24], "No")   # isD2C
        self.assertEqual(row[25], "")     # Month
        self.assertEqual(row[26], "")     # Gross Amount

    def test_format_customer_rows_multiple_brands_3_rows(self):
        """
        Tests that when a seller has 3 brands in brands_details:
        - Row 1: Fully filled with seller details and Brand 1 info.
        - Rows 2 and 3: Blank except for the 5 brand columns (Request ID, Brand Name, Vertical Name, Brand Owner, Document Type).
        """
        data = {
            "customer_id": "c111222333",
            "account_name": "Multi Brand Seller",
            "account_status": "ACTIVE",
            "support_manager": "No",
            "seller_tier": "Gold",
            "address": "Bangalore",
            "signed_up_date": "2022-01-01",
            "live_date": "2022-01-10",
            "approved_brand": 5,
            "actual_brand_count": 3,
            "unique_brands": ["BRAND_1", "BRAND_2", "BRAND_3"],
            "brands_details": [
                {
                    "request_id": "REQ_001",
                    "brand_name": "Brand One",
                    "vertical_name": "Footwear",
                    "brand_owner": "Yes",
                    "document_type": "TM",
                    "brand_website_link": "https://brandone.com",
                },
                {
                    "request_id": "REQ_002",
                    "brand_name": "Brand Two",
                    "vertical_name": "Apparel",
                    "brand_owner": "No",
                    "document_type": "BAL",
                    "brand_website_link": "",
                },
                {
                    "request_id": "REQ_003",
                    "brand_name": "Brand Three",
                    "vertical_name": "Accessories",
                    "brand_owner": "No",
                    "document_type": "OTHER",
                    "brand_website_link": "",
                },
            ],
            "instagram_url": "https://instagram.com/brandone",
            "instagram_followers": "50K",
            "mobile_number": "9998887770",
            "registered_mobile_number": "9998887770",
            "email_id": "info@brandone.com",
            "registered_email_id": "info@brandone.com",
            "unique_email": "Yes",
            "isD2C": "Yes",
            "month": "July 2026",
            "gross_amount": "₹50,000",
            "gross_units": "20",
            "net_amount": "₹45,000",
            "cancelled_amount": "₹5,000",
        }

        rows = self.writer._format_customer_rows(data, sr_no=1)
        self.assertEqual(len(rows), 3)

        # Row 1 (Full Row)
        row1 = rows[0]
        self.assertEqual(len(row1), 30)
        self.assertEqual(row1[0], 1)                   # Sr No
        self.assertEqual(row1[1], "c111222333")        # Customer ID
        self.assertEqual(row1[2], "Multi Brand Seller")# Account Name
        self.assertEqual(row1[10], 3)                  # Actual Brand Count
        self.assertEqual(row1[11], "REQ_001")          # Request ID
        self.assertEqual(row1[12], "Brand One")        # Brand Name
        self.assertEqual(row1[13], "Footwear")         # Vertical Name
        self.assertEqual(row1[14], "Yes")              # Brand Owner
        self.assertEqual(row1[15], "TM")               # Document Type
        self.assertEqual(row1[16], "https://brandone.com") # Brand Website Link
        self.assertEqual(row1[17], "https://instagram.com/brandone")
        self.assertEqual(row1[24], "Yes")              # isD2C
        self.assertEqual(row1[26], "₹50,000")          # Gross Amount (GMV)

        # Row 2 (Brand Two: only 5 columns filled, rest 25 empty)
        row2 = rows[1]
        self.assertEqual(len(row2), 30)
        self.assertEqual(row2[0], "")                  # Sr No is empty
        self.assertEqual(row2[1], "")                  # Customer ID is empty
        self.assertEqual(row2[2], "")                  # Account Name is empty
        self.assertEqual(row2[10], "")                 # Actual Brand Count is empty
        self.assertEqual(row2[11], "REQ_002")          # Request ID
        self.assertEqual(row2[12], "Brand Two")        # Brand Name
        self.assertEqual(row2[13], "Apparel")          # Vertical Name
        self.assertEqual(row2[14], "No")               # Brand Owner
        self.assertEqual(row2[15], "BAL")              # Document Type
        self.assertEqual(row2[16], "")                 # Website Link is empty
        self.assertEqual(row2[17], "")                 # Instagram URL is empty
        self.assertEqual(row2[24], "")                 # isD2C is empty
        self.assertEqual(row2[26], "")                 # GMV is empty

        # Row 3 (Brand Three: only 5 columns filled, rest 25 empty)
        row3 = rows[2]
        self.assertEqual(len(row3), 30)
        self.assertEqual(row3[0], "")                  # Sr No is empty
        self.assertEqual(row3[1], "")                  # Customer ID is empty
        self.assertEqual(row3[11], "REQ_003")          # Request ID
        self.assertEqual(row3[12], "Brand Three")      # Brand Name
        self.assertEqual(row3[13], "Accessories")      # Vertical Name
        self.assertEqual(row3[14], "No")               # Brand Owner
        self.assertEqual(row3[15], "OTHER")            # Document Type
        self.assertEqual(row3[16], "")
        self.assertEqual(row3[24], "")


if __name__ == "__main__":
    unittest.main()


