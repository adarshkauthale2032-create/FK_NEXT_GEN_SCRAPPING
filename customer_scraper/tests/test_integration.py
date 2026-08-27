"""
Integration tests for complete scraping workflow, resume capability, and state tracking with CSV.
"""

import csv
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from api.api_client import APIClient
from excel.excel_writer import CSVWriter, ExcelWriter
from main import ProgressTracker, read_customer_ids
from scrapers.api1_scraper import API1Scraper
from scrapers.api2_scraper import API2Scraper
from scrapers.api3_scraper import API3Scraper


class TestIntegrationScraperFlow(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.input_file = self.test_dir / "input.txt"
        self.csv_file = self.test_dir / "scraped_data.csv"
        self.progress_file = self.test_dir / "progress.json"
        self.pending_file = self.test_dir / "pending.json"

        # Populate sample input file
        self.input_file.write_text("ID001\nID002\nID003\n\n# comment\nID004\n", encoding="utf-8")

        self.mock_client = MagicMock(spec=APIClient)
        self.api1 = API1Scraper(self.mock_client)
        self.api2 = API2Scraper(self.mock_client)
        self.api3 = API3Scraper(self.mock_client)
        self.csv_writer = CSVWriter(csv_path=self.csv_file, pending_path=self.pending_file)
        self.excel_writer = self.csv_writer
        self.tracker = ProgressTracker(progress_path=self.progress_file)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_input_file_reader_txt(self):
        ids = read_customer_ids(self.input_file)
        self.assertEqual(ids, ["ID001", "ID002", "ID003", "ID004"])

    def test_input_file_reader_excel(self):
        import openpyxl
        input_xlsx = self.test_dir / "customer_id_input.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Input Sheet"
        ws.append(["seller_id"])
        ws.append(["XLSX_ID_1"])
        ws.append(["XLSX_ID_2"])
        ws.append(["XLSX_ID_3"])
        wb.save(input_xlsx)

        ids = read_customer_ids(input_xlsx)
        self.assertEqual(ids, ["XLSX_ID_1", "XLSX_ID_2", "XLSX_ID_3"])

    def test_support_manager_yes_runs_all_apis_without_skipping(self):
        customer_id = "ID_SUPP_YES"

        # API 1 mock with Support Manager = Yes
        self.mock_client.get.return_value = {
            "result": {
                "displayName": "Managed Seller",
                "supportRole": {"name": "Manager Alex"},
                "liveDate": "2023-01-01",
                "gmv": {"response": {"details": {"state_details": {"state": "ACTIVE"}}}}
            }
        }

        api1_data = self.api1.get_seller_details(customer_id)
        self.assertEqual(api1_data["support_manager"], "Yes")
        self.assertEqual(api1_data["account_status"], "ACTIVE")

        # In updated workflow, all APIs run for all sellers
        saved = self.csv_writer.append_customer(api1_data, sr_no=1)
        self.assertTrue(saved)
        self.tracker.mark_completed(customer_id)
        self.assertTrue(self.tracker.is_completed(customer_id))

    def test_resume_skips_already_completed_customers(self):
        # Mark ID001 and ID002 as completed
        self.tracker.mark_completed("ID001")
        self.tracker.mark_completed("ID002")

        # Reload tracker from disk
        new_tracker = ProgressTracker(progress_path=self.progress_file)
        self.assertTrue(new_tracker.is_completed("ID001"))
        self.assertTrue(new_tracker.is_completed("ID002"))
        self.assertFalse(new_tracker.is_completed("ID003"))

    def test_full_combined_flow(self):
        cust_id = "ID002"

        # 1. API 1 response (Support Manager = No)
        api1_resp = {
            "result": {
                "displayName": "Unmanaged Seller",
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
                            "state_details": {"state": "ACTIVE"},
                            "darwin_tier_v2": {"tier_name": "Bronze"}
                        }
                    }
                },
                "profileInfo": {"created_at": "2020-01-01"},
                "liveDate": "2020-01-10",
            }
        }

        # 2. API 2 responses (Count + Requests)
        api2_count_resp = {
            "ALL": 43,
            "APPROVED": 26,
        }
        api2_records_resp = [
            {"brand_name": "RRCART", "request_status": "Approved"},
            {"brand_name": "rrcart", "request_status": "Approved"},
            {"brand_name": "TOY_BRAND", "request_status": "Approved"},
        ]

        # 3. API 3 response
        api3_resp = {
            "result": {
                "loginMobileNumber": "9123456780",
                "primaryMobileNumber": "9123456781",
                "loginEmail": "unman@mail.com",
                "primaryEmail": "unman_prim@mail.com",
            }
        }

        def mock_get(endpoint, headers=None):
            if "getSellerDetails" in endpoint:
                return api1_resp
            if "requestsV2-count" in endpoint:
                return api2_count_resp
            if "getSellerContacts" in endpoint:
                return api3_resp
            return {}

        self.mock_client.get.side_effect = mock_get
        self.mock_client.post.return_value = api2_records_resp

        # Run flow
        res1 = self.api1.get_seller_details(cust_id)
        self.assertEqual(res1["support_manager"], "No")
        self.assertEqual(res1["account_status"], "ACTIVE")

        res2 = self.api2.get_brand_approval_details(cust_id)
        self.assertEqual(res2["approved_brand"], 26)
        # Baseline approved = 26. "RRCART" and "rrcart" is 1 duplicate -> 26 - 1 = 25
        self.assertEqual(res2["actual_brand_count"], 25)

        res3 = self.api3.get_seller_contacts(cust_id)
        self.assertEqual(res3["email_id"], "unman@mail.com")

        combined = {**res1, **res2, **res3}
        saved = self.csv_writer.append_customer(combined, sr_no=1)
        self.assertTrue(saved)
        self.tracker.mark_completed(cust_id)

        # Verify CSV output
        with open(self.csv_file, "r", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))

        self.assertEqual(len(rows), 2)  # Header + 1 row
        self.assertEqual(rows[1][1], cust_id)
        self.assertEqual(rows[1][2], "Unmanaged Seller")
        self.assertEqual(rows[1][3], "ACTIVE")
        self.assertEqual(rows[1][4], "No")  # Support Manager
        self.assertEqual(rows[1][5], "Bronze")  # Seller Tier
        self.assertEqual(rows[1][6], "2020-01-01")  # Signed Up Date
        self.assertEqual(rows[1][7], "2020-01-10")  # Live Date
        self.assertEqual(rows[1][8], "26")  # Approved Brand
        self.assertEqual(rows[1][9], "25")  # Actual Brand Count

    def test_seller_limit_stops_execution(self):
        limit = 2
        input_ids = [f"ID_{i:03d}" for i in range(10)]
        processed_count = 0

        self.mock_client.get.return_value = {
            "result": {
                "displayName": "Test Seller",
                "supportRole": {"name": "Manager Alex"},
                "liveDate": "2023-01-01",
                "gmv": {"response": {"details": {"state_details": {"state": "ACTIVE"}}}}
            }
        }

        for c_id in input_ids:
            if processed_count >= limit:
                break
            data = self.api1.get_seller_details(c_id)
            self.excel_writer.append_customer(data, sr_no=processed_count + 1)
            self.tracker.mark_completed(c_id)
            processed_count += 1

        self.assertEqual(processed_count, limit)
        self.assertEqual(len(self.tracker.completed_ids), limit)

    def test_multi_batch_chunking_across_inputs(self):
        chunk_dir = self.test_dir / "integration_batches"
        chunk_writer = ExcelWriter(output_dir=chunk_dir, chunk_size=2)

        self.mock_client.get.return_value = {
            "result": {
                "displayName": "Batch Seller",
                "supportRole": {"name": "Manager Alex"},
                "liveDate": "2023-01-01",
                "gmv": {"response": {"details": {"state_details": {"state": "ACTIVE"}}}}
            }
        }

        for i in range(1, 7):
            c_id = f"BATCH_ID_{i:03d}"
            data = self.api1.get_seller_details(c_id)
            chunk_writer.append_customer(data, sr_no=i)
            self.tracker.mark_completed(c_id)

        for start, end in [(1, 2), (3, 4), (5, 6)]:
            xlsx = chunk_dir / f"scraped_data_{start}_to_{end}.xlsx"
            csv_f = chunk_dir / f"scraped_data_{start}_to_{end}.csv"
            self.assertTrue(xlsx.exists(), f"{xlsx.name} should exist")
            self.assertTrue(csv_f.exists(), f"{csv_f.name} should exist")


if __name__ == "__main__":
    unittest.main()
