"""
Unit tests for CSVWriter module and lock recovery.
"""

import csv
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config.settings import CSV_COLUMNS
from excel.excel_writer import CSVWriter, ExcelWriter


class TestCSVWriter(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.csv_path = Path(self.test_dir) / "test_scraped_data.csv"
        self.pending_path = Path(self.test_dir) / "test_pending.json"
        self.writer = CSVWriter(
            csv_path=self.csv_path,
            pending_path=self.pending_path
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_csv_initialization_and_headers(self):
        self.assertTrue(self.csv_path.exists())
        with open(self.csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            headers = next(reader)
            self.assertEqual(headers, CSV_COLUMNS)

    def test_append_support_manager_yes_customer(self):
        cust_data = {
            "customer_id": "CUST_001",
            "account_name": "Acme Corp",
            "support_manager": "Yes",
            "seller_tier": "Gold",
            "signed_up_date": "2021-01-01",
            "live_date": "2021-01-15",
        }

        success = self.writer.append_customer(cust_data, sr_no=1)
        self.assertTrue(success)

        with open(self.csv_path, "r", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))

        self.assertEqual(len(rows), 2)  # Header + 1 data row
        row_vals = rows[1]
        self.assertEqual(row_vals[0], "1")  # Sr No
        self.assertEqual(row_vals[1], "CUST_001")
        self.assertEqual(row_vals[2], "Acme Corp")
        self.assertEqual(row_vals[3], "Yes")
        self.assertEqual(row_vals[4], "Gold")
        self.assertEqual(row_vals[5], "2021-01-01")
        self.assertEqual(row_vals[6], "2021-01-15")

    def test_append_customer_with_listings_multi_row(self):
        cust_data = {
            "customer_id": "CUST_002",
            "account_name": "Retailer Plus",
            "support_manager": "No",
            "seller_tier": "Silver",
            "signed_up_date": "2022-03-10",
            "live_date": "2022-03-20",
            "listing_titles": ["Product Title 1", "Product Title 2", "Product Title 3"],
            "listing_brands": ["BRAND_A", "BRAND_B", "BRAND_A"],
            "is_brand": "Possibly a Brand",
            "brand_name": "RETAIL_BRAND",
            "mobile_number": "9876543210",
            "registered_mobile_number": "9876543211",
            "email_id": "contact@retail.com",
            "registered_email_id": "reg@retail.com",
        }

        success = self.writer.append_customer(cust_data, sr_no=2)
        self.assertTrue(success)

        with open(self.csv_path, "r", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))

        # Header (1) + 3 listing rows = 4 rows
        self.assertEqual(len(rows), 4)

        # Row 1 (first listing row)
        row1 = rows[1]
        self.assertEqual(row1[0], "2")  # Sr No on first row
        self.assertEqual(row1[1], "CUST_002")
        self.assertEqual(row1[7], "Product Title 1")
        self.assertEqual(row1[8], "BRAND_A")  # Listing Brand
        self.assertEqual(row1[9], "Possibly a Brand")
        self.assertEqual(row1[10], "RETAIL_BRAND")
        self.assertEqual(row1[11], "9876543210")

        # Row 2 (second listing row)
        row2 = rows[2]
        self.assertEqual(row2[0], "")  # Sr No empty on secondary rows
        self.assertEqual(row2[1], "")  # Customer ID empty on secondary rows
        self.assertEqual(row2[7], "Product Title 2")
        self.assertEqual(row2[8], "BRAND_B")  # Listing Brand

        # Row 3 (third listing row)
        row3 = rows[3]
        self.assertEqual(row3[0], "")
        self.assertEqual(row3[1], "")
        self.assertEqual(row3[7], "Product Title 3")
        self.assertEqual(row3[8], "BRAND_A")

    def test_get_completed_customer_ids(self):
        cust1 = {"customer_id": "ID_AAA", "support_manager": "Yes"}
        cust2 = {"customer_id": "ID_BBB", "support_manager": "No", "listing_titles": ["Item 1", "Item 2"]}

        self.writer.append_customer(cust1, sr_no=1)
        self.writer.append_customer(cust2, sr_no=2)

        completed = self.writer.get_completed_customer_ids()
        self.assertEqual(completed, {"ID_AAA", "ID_BBB"})
        self.assertEqual(self.writer.get_current_customer_count(), 2)

    def test_csv_lock_buffers_to_pending_and_recovers(self):
        cust_data = {
            "customer_id": "ID_LOCKED",
            "account_name": "Locked Seller",
            "support_manager": "Yes",
        }

        # Simulate lock on first save attempt by mocking _append_rows_with_retry
        with patch.object(self.writer, "_append_rows_with_retry", return_value=False):
            success = self.writer.append_customer(cust_data, sr_no=1)
            self.assertFalse(success)

            # Check that pending file exists and has the data
            self.assertTrue(self.pending_path.exists())
            pending = self.writer.load_pending_records()
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["data"]["customer_id"], "ID_LOCKED")

        # Now simulate unlocking and flushing
        flush_success = self.writer.flush_pending()
        self.assertTrue(flush_success)
        self.assertFalse(self.pending_path.exists())  # pending cleared

        # Verify data is now in CSV
        completed = self.writer.get_completed_customer_ids()
        self.assertIn("ID_LOCKED", completed)


if __name__ == "__main__":
    unittest.main()
