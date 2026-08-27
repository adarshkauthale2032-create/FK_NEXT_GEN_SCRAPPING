"""
CSV Writer module (formerly Excel Writer).

Handles creation, continuous appending, locked file detection,
retry recovery, and durable persistence for the scraped output CSV dataset.
"""

import csv
import json
import logging
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Set

from config.settings import (
    CSV_COLUMNS,
    CSV_RETRY_INTERVAL,
    MAX_CSV_LOCK_RETRIES,
    OUTPUT_CSV_PATH,
    PENDING_FILE_PATH,
)

logger = logging.getLogger("customer_scraper")


class CSVWriter:
    """
    Manages writing scraped customer records to CSV with file-lock recovery.
    """

    def __init__(
        self,
        csv_path: Optional[Path] = None,
        pending_path: Optional[Path] = None,
    ):
        self.csv_path = Path(csv_path or OUTPUT_CSV_PATH)
        self.pending_path = Path(pending_path or PENDING_FILE_PATH)
        self._ensure_file_exists()

    def _ensure_file_exists(self) -> None:
        """
        Creates the target CSV file with formatted headers if it doesn't exist.
        """
        if self.csv_path.exists():
            return

        logger.info("Initializing new CSV file at %s", self.csv_path)
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.csv_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(CSV_COLUMNS)
        except Exception as e:
            logger.error("Failed to initialize CSV file (%s): %s", self.csv_path, str(e))

    def _append_rows_with_retry(self, rows: List[List[Any]]) -> bool:
        """
        Appends rows to the CSV file with automatic retries if locked (e.g., opened in Excel).
        """
        if not rows:
            return True

        retries = 0
        while retries < MAX_CSV_LOCK_RETRIES:
            try:
                # Ensure parent directory and header exist
                self._ensure_file_exists()

                with open(self.csv_path, "a", newline="", encoding="utf-8-sig") as f:
                    writer = csv.writer(f)
                    for row in rows:
                        writer.writerow(row)
                logger.debug("Successfully appended %d row(s) to %s", len(rows), self.csv_path.name)
                return True
            except (PermissionError, OSError) as lock_err:
                retries += 1
                logger.warning(
                    "CSV file '%s' is locked (likely open in Excel). Save attempt %d/%d failed. Retrying in %ds... Please close the file if open.",
                    self.csv_path.name,
                    retries,
                    MAX_CSV_LOCK_RETRIES,
                    CSV_RETRY_INTERVAL,
                )
                time.sleep(CSV_RETRY_INTERVAL)

        logger.error(
            "Failed to save CSV file after %d attempts. File lock could not be released.",
            MAX_CSV_LOCK_RETRIES,
        )
        return False

    def load_pending_records(self) -> List[Dict[str, Any]]:
        """
        Loads unsaved records from the durable pending JSON file.
        """
        if not self.pending_path.exists():
            return []
        try:
            with open(self.pending_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception as e:
            logger.error("Error reading pending results file (%s): %s", self.pending_path, str(e))
        return []

    def save_pending_records(self, pending_list: List[Dict[str, Any]]) -> None:
        """
        Persists pending customer records to disk.
        """
        try:
            self.pending_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.pending_path, "w", encoding="utf-8") as f:
                json.dump(pending_list, f, indent=2)
        except Exception as e:
            logger.error("Failed to write to pending file (%s): %s", self.pending_path, str(e))

    def clear_pending_records(self) -> None:
        """
        Clears pending records file upon successful persistence to CSV.
        """
        if self.pending_path.exists():
            try:
                self.pending_path.unlink()
            except Exception as e:
                logger.warning("Could not delete pending file: %s", str(e))

    def _clean_date_str(self, val: Any) -> str:
        """Strips time component from date string if present."""
        if val is None:
            return ""
        s = str(val).strip()
        if not s or s.lower() in ("null", "none"):
            return ""
        if "T" in s:
            return s.split("T")[0].strip()
        if " " in s:
            return s.split(" ")[0].strip()
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            return s[:10]
        return s

    def _format_customer_rows(self, data: Dict[str, Any], sr_no: Any) -> List[List[Any]]:
        """
        Formats customer scraped data into one or more CSV row lists.
        """
        customer_id = data.get("customer_id", "")
        account_name = data.get("account_name", "")
        support_manager = data.get("support_manager", "")
        seller_tier = data.get("seller_tier", "")
        signed_up_date = self._clean_date_str(data.get("signed_up_date", ""))
        live_date = self._clean_date_str(data.get("live_date", ""))

        # Case 1: Support Manager is Yes -> Only API #1 info
        if support_manager == "Yes":
            return [[
                sr_no,
                customer_id,
                account_name,
                "Yes",
                seller_tier,
                signed_up_date,
                live_date,
                "",  # Brand List
                "",  # Listing Brand
                "",  # Is Brand
                "",  # Brand Name
                "",  # Mobile Number
                "",  # Registered Mobile
                "",  # Email ID
                "",  # Registered Email
            ]]

        # Case 2: Support Manager is No -> Include API #2 and API #3
        is_brand = data.get("is_brand", "")
        brand_name = data.get("brand_name", "")
        mobile_number = data.get("mobile_number", "")
        registered_mobile = data.get("registered_mobile_number", "")
        email_id = data.get("email_id", "")
        registered_email = data.get("registered_email_id", "")
        listing_titles = data.get("listing_titles", [])
        listing_brands = data.get("listing_brands", [])

        # If no listings were returned, write single row with listing fields blank
        if not listing_titles:
            return [[
                sr_no,
                customer_id,
                account_name,
                "No",
                seller_tier,
                signed_up_date,
                live_date,
                "",
                "",
                is_brand,
                brand_name,
                mobile_number,
                registered_mobile,
                email_id,
                registered_email,
            ]]

        # If listings exist (up to 20), output one row per listing with its title and brand
        rows = []
        for idx, title in enumerate(listing_titles):
            brand_for_listing = listing_brands[idx] if idx < len(listing_brands) else ""
            if idx == 0:
                rows.append([
                    sr_no,
                    customer_id,
                    account_name,
                    "No",
                    seller_tier,
                    signed_up_date,
                    live_date,
                    title,
                    brand_for_listing,
                    is_brand,
                    brand_name,
                    mobile_number,
                    registered_mobile,
                    email_id,
                    registered_email,
                ])
            else:
                rows.append([
                    "",  # Sr No
                    "",  # Customer ID
                    "",  # Account Name
                    "",  # Support Manager
                    "",  # Seller Tier
                    "",  # Signed Up Date
                    "",  # Live Date
                    title,  # Brand List (Product Title)
                    brand_for_listing,  # Listing Brand
                    "",  # Is Brand
                    "",  # Brand Name
                    "",  # Mobile Number
                    "",  # Registered Mobile
                    "",  # Email ID
                    "",  # Registered Email
                ])
        return rows

    def append_customer(self, customer_data: Dict[str, Any], sr_no: Any) -> bool:
        """
        Appends a single customer's scraped data to CSV.
        If CSV is locked, buffers record to pending_results.json and retries.

        Returns:
            True if written and saved to CSV successfully, False otherwise.
        """
        self._ensure_file_exists()

        # Add this record with its sr_no to pending queue first for safety
        pending = self.load_pending_records()
        pending.append({"sr_no": sr_no, "data": customer_data})
        self.save_pending_records(pending)

        # Attempt to flush all pending records to CSV
        success = self.flush_pending()
        if success:
            logger.info("CSV saved successfully for customer ID: %s", customer_data.get("customer_id"))
        else:
            logger.warning("Pending save queued for customer ID: %s", customer_data.get("customer_id"))

        return success

    def flush_pending(self) -> bool:
        """
        Attempts to write all buffered pending records to CSV.
        """
        pending = self.load_pending_records()
        if not pending:
            return True

        all_rows_to_append = []
        for item in pending:
            item_sr = item.get("sr_no")
            item_data = item.get("data", {})
            rows_to_add = self._format_customer_rows(item_data, item_sr)
            all_rows_to_append.extend(rows_to_add)

        saved = self._append_rows_with_retry(all_rows_to_append)
        if saved:
            self.clear_pending_records()
            return True

        return False

    def get_completed_customer_ids(self) -> Set[str]:
        """
        Extracts all unique customer IDs that have already been persisted to the CSV file.
        """
        if not self.csv_path.exists():
            return set()

        completed_ids: Set[str] = set()
        try:
            with open(self.csv_path, "r", newline="", encoding="utf-8-sig") as f:
                reader = csv.reader(f)
                header = next(reader, None)
                cust_col_idx = 1
                if header:
                    for i, col in enumerate(header):
                        if col.strip().lower() in ("customer id", "customer_id"):
                            cust_col_idx = i
                            break

                for row in reader:
                    if row and len(row) > cust_col_idx:
                        c_id = str(row[cust_col_idx]).strip()
                        if c_id and c_id.lower() not in ("customer id", "customer_id", "none", ""):
                            completed_ids.add(c_id)
        except Exception as e:
            logger.error("Could not read completed IDs from CSV (%s): %s", self.csv_path, str(e))

        return completed_ids

    def get_current_customer_count(self) -> int:
        """
        Returns the number of unique completed customers already present in the CSV file.
        """
        return len(self.get_completed_customer_ids())


# Backward-compatible alias
ExcelWriter = CSVWriter
