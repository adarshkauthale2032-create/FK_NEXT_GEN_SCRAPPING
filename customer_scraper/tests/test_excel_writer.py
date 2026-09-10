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
        self.excel_path = Path(self.test_dir) / "test_scraped_data.xlsx"
        self.pending_path = Path(self.test_dir) / "test_pending.json"
        self.writer = CSVWriter(
            csv_path=self.csv_path,
            excel_path=self.excel_path,
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
            "account_status": "ACTIVE",
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
        self.assertEqual(row_vals[3], "ACTIVE")  # Account Status
        self.assertEqual(row_vals[4], "Yes")  # Support Manager
        self.assertEqual(row_vals[5], "Gold")  # Seller Tier
        self.assertEqual(row_vals[6], "2021-01-01")
        self.assertEqual(row_vals[7], "2021-01-15")

    def test_append_customer_support_manager_no_with_brands(self):
        cust_data = {
            "customer_id": "CUST_002",
            "account_name": "Retailer Plus",
            "account_status": "ACTIVE",
            "approved_brand": 26,
            "actual_brand_count": 15,
            "support_manager": "No",
            "seller_tier": "Silver",
            "signed_up_date": "2022-03-10",
            "live_date": "2022-03-20",
            "mobile_number": "9876543210",
            "registered_mobile_number": "9876543211",
            "email_id": "contact@retail.com",
            "registered_email_id": "reg@retail.com",
        }

        success = self.writer.append_customer(cust_data, sr_no=2)
        self.assertTrue(success)

        with open(self.csv_path, "r", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))

        self.assertEqual(len(rows), 2)  # Header + 1 data row
        row_vals = rows[1]
        self.assertEqual(row_vals[0], "2")  # Sr No
        self.assertEqual(row_vals[1], "CUST_002")
        self.assertEqual(row_vals[2], "Retailer Plus")
        self.assertEqual(row_vals[3], "ACTIVE")
        self.assertEqual(row_vals[4], "No")
        self.assertEqual(row_vals[5], "Silver")
        self.assertEqual(row_vals[6], "2022-03-10")
        self.assertEqual(row_vals[7], "2022-03-20")
        self.assertEqual(row_vals[8], "26")
        self.assertEqual(row_vals[9], "15")
        self.assertEqual(row_vals[10], "")            # Request ID
        self.assertEqual(row_vals[11], "")            # Brand Name
        self.assertEqual(row_vals[15], "")            # Instagram URL
        self.assertEqual(row_vals[16], "")            # Instagram Followers
        self.assertEqual(row_vals[17], "9876543210")  # Mobile Number
        self.assertEqual(row_vals[18], "9876543211")  # Registered Mobile Number
        self.assertEqual(row_vals[19], "contact@retail.com")
        self.assertEqual(row_vals[20], "reg@retail.com")
        self.assertEqual(row_vals[21], "Yes")         # Unique Email
        self.assertEqual(row_vals[22], "Yes")         # retail.com is custom domain -> isD2C = Yes

    def test_get_completed_customer_ids(self):
        cust1 = {"customer_id": "ID_AAA", "support_manager": "Yes"}
        cust2 = {"customer_id": "ID_BBB", "support_manager": "No", "approved_brand": 5, "actual_brand_count": 3}

        self.writer.append_customer(cust1, sr_no=1)
        self.writer.append_customer(cust2, sr_no=2)

        completed = self.writer.get_completed_customer_ids()
        self.assertEqual(completed, {"ID_AAA", "ID_BBB"})
        self.assertEqual(self.writer.get_current_customer_count(), 2)

    def test_chunking_and_csv_generation(self):
        output_dir = Path(self.test_dir) / "chunk_output"
        chunk_writer = ExcelWriter(output_dir=output_dir, chunk_size=2)

        # Customer 1 (Sr 1) -> Batch 1 (1 to 2)
        c1 = {"customer_id": "C1", "account_name": "Seller 1", "support_manager": "Yes"}
        chunk_writer.append_customer(c1, sr_no=1)

        # Customer 2 (Sr 2) -> Batch 1 (1 to 2)
        c2 = {"customer_id": "C2", "account_name": "Seller 2", "support_manager": "Yes"}
        chunk_writer.append_customer(c2, sr_no=2)

        # Customer 3 (Sr 3) -> Batch 2 (3 to 4)
        c3 = {"customer_id": "C3", "account_name": "Seller 3", "support_manager": "Yes"}
        chunk_writer.append_customer(c3, sr_no=3)

        batch1_excel = output_dir / "scraped_data_1_to_2.xlsx"
        batch1_csv = output_dir / "scraped_data_1_to_2.csv"
        batch2_excel = output_dir / "scraped_data_3_to_4.xlsx"
        batch2_csv = output_dir / "scraped_data_3_to_4.csv"

        self.assertTrue(batch1_excel.exists())
        self.assertTrue(batch1_csv.exists())
        self.assertTrue(batch2_excel.exists())
        self.assertTrue(batch2_csv.exists())

        # Verify CSV content for batch 1 has headers and 2 sellers
        csv_lines = batch1_csv.read_text(encoding="utf-8-sig").splitlines()
        self.assertEqual(len(csv_lines), 3)  # Header + 2 data rows

        # Verify completed IDs across all batches
        completed = chunk_writer.get_completed_customer_ids()
        self.assertEqual(completed, {"C1", "C2", "C3"})
        self.assertEqual(chunk_writer.get_current_customer_count(), 3)

    def test_corrupted_workbook_auto_recovery(self):
        # Deliberately corrupt the excel file with garbage bytes
        self.excel_path.write_bytes(b"CORRUPTED_GARBAGE_BYTES_NOT_ZIP")
        
        # Next append should detect corruption, backup corrupt file, reinitialize fresh and succeed
        cust = {"customer_id": "RECOVERED_ID", "account_name": "Recovered", "support_manager": "Yes"}
        success = self.writer.append_customer(cust, sr_no=1)
        self.assertTrue(success)

        # File is now a valid zip / Excel file
        import zipfile
        self.assertTrue(zipfile.is_zipfile(self.excel_path))
        completed = self.writer.get_completed_customer_ids()
        self.assertIn("RECOVERED_ID", completed)


if __name__ == "__main__":
    unittest.main()
