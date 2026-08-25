"""
Integration tests for complete scraping workflow, resume capability, and state tracking.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from api.api_client import APIClient
from excel.excel_writer import ExcelWriter
from main import ProgressTracker, read_customer_ids
from scrapers.api1_scraper import API1Scraper
from scrapers.api2_scraper import API2Scraper
from scrapers.api3_scraper import API3Scraper


class TestIntegrationScraperFlow(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.input_file = self.test_dir / "input.txt"
        self.excel_file = self.test_dir / "scraped_data.xlsx"
        self.progress_file = self.test_dir / "progress.json"
        self.pending_file = self.test_dir / "pending.json"

        # Populate sample input file
        self.input_file.write_text("ID001\nID002\nID003\n\n# comment\nID004\n", encoding="utf-8")

        self.mock_client = MagicMock(spec=APIClient)
        self.api1 = API1Scraper(self.mock_client)
        self.api2 = API2Scraper(self.mock_client)
        self.api3 = API3Scraper(self.mock_client)
        self.excel_writer = ExcelWriter(excel_path=self.excel_file, pending_path=self.pending_file)
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

    def test_support_manager_yes_skips_api2_and_api3(self):
        customer_id = "ID_SUPP_YES"

        # API 1 mock with Support Manager = Yes
        self.mock_client.get.return_value = {
            "result": {
                "displayName": "Managed Seller",
                "supportRole": {"name": "Manager Alex"},
                "liveDate": "2023-01-01"
            }
        }

        api1_data = self.api1.get_seller_details(customer_id)
        self.assertEqual(api1_data["support_manager"], "Yes")

        # In main workflow, when Yes: save and mark completed without calling api2/api3
        saved = self.excel_writer.append_customer(api1_data, sr_no=1)
        self.assertTrue(saved)
        self.tracker.mark_completed(customer_id)

        # Verify API #2 and #3 were not called
        self.assertEqual(self.mock_client.post.call_count, 0)
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
                "gmv": {"response": {"details": {"darwin_tier_v2": {"tier_name": "Bronze"}}}},
                "profileInfo": {"created_at": "2020-01-01"},
                "liveDate": "2020-01-10",
            }
        }

        # 2. API 2 response (20 listings, 15 brand matches -> Brand)
        api2_resp = {
            "listing_data_response": [{"title": f"Shoe {i}", "brand": "NIKE"} for i in range(15)] + [
                {"title": f"Socks {i}", "brand": "GENERIC"} for i in range(5)
            ]
        }

        # 3. API 3 response
        api3_resp = {
            "result": {
                "loginMobileNumber": "9123456780",
                "primaryMobileNumber": "9123456781",
                "loginEmail": "unman@mail.com",
                "primaryEmail": "unman_prim@mail.com",
            }
        }

        def mock_get(endpoint):
            if "getSellerDetails" in endpoint:
                return api1_resp
            if "getSellerContacts" in endpoint:
                return api3_resp
            return {}

        self.mock_client.get.side_effect = mock_get
        self.mock_client.post.return_value = api2_resp

        # Run flow
        res1 = self.api1.get_seller_details(cust_id)
        self.assertEqual(res1["support_manager"], "No")

        res2 = self.api2.get_listings_and_brand(cust_id)
        self.assertEqual(res2["is_brand"], "Possibly a Brand")
        self.assertEqual(res2["brand_name"], "NIKE")
        self.assertEqual(len(res2["listing_titles"]), 20)

        res3 = self.api3.get_seller_contacts(cust_id)
        self.assertEqual(res3["email_id"], "unman@mail.com")

        combined = {**res1, **res2, **res3}
        saved = self.excel_writer.append_customer(combined, sr_no=1)
        self.assertTrue(saved)
        self.tracker.mark_completed(cust_id)

        # Verify Excel output has 20 listing rows + header = 21 rows
        import openpyxl
        wb = openpyxl.load_workbook(self.excel_file)
        ws = wb.active
        self.assertEqual(ws.max_row, 21)
        self.assertEqual(ws.cell(row=2, column=8).value, "Shoe 0")
        self.assertEqual(ws.cell(row=21, column=8).value, "Socks 4")

    def test_seller_limit_stops_execution(self):
        # Test that processing stops when max limit (e.g. 2) is reached even if input has 10 IDs
        limit = 2
        input_ids = [f"ID_{i:03d}" for i in range(10)]
        processed_count = 0

        self.mock_client.get.return_value = {
            "result": {
                "displayName": "Test Seller",
                "supportRole": {"name": "Manager Alex"},
                "liveDate": "2023-01-01"
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
        # Test simulated 6 sellers with chunk_size = 2 -> 3 batch files created
        chunk_dir = self.test_dir / "integration_batches"
        chunk_writer = ExcelWriter(output_dir=chunk_dir, chunk_size=2)

        self.mock_client.get.return_value = {
            "result": {
                "displayName": "Batch Seller",
                "supportRole": {"name": "Manager Alex"},
                "liveDate": "2023-01-01"
            }
        }

        for i in range(1, 7):
            c_id = f"BATCH_ID_{i:03d}"
            data = self.api1.get_seller_details(c_id)
            chunk_writer.append_customer(data, sr_no=i)
            self.tracker.mark_completed(c_id)

        # Expect 3 xlsx and 3 csv files:
        # 1 to 2, 3 to 4, 5 to 6
        for start, end in [(1, 2), (3, 4), (5, 6)]:
            xlsx = chunk_dir / f"scraped_data_{start}_to_{end}.xlsx"
            csv_f = chunk_dir / f"scraped_data_{start}_to_{end}.csv"
            self.assertTrue(xlsx.exists(), f"{xlsx.name} should exist")
            self.assertTrue(csv_f.exists(), f"{csv_f.name} should exist")


if __name__ == "__main__":
    unittest.main()
