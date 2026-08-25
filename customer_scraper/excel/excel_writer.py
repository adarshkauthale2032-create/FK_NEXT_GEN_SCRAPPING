"""
Excel Writer module.

Handles creation, continuous appending, styling, locked file detection,
and retry recovery for the scraped output Excel workbook.
"""

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from config.settings import (
    EXCEL_COLUMNS,
    EXCEL_RETRY_INTERVAL,
    MAX_EXCEL_LOCK_RETRIES,
    OUTPUT_EXCEL_PATH,
    PENDING_FILE_PATH,
)

logger = logging.getLogger("customer_scraper")


class ExcelWriter:
    """
    Manages writing scraped customer records to Excel with file-lock recovery.
    """

    def __init__(
        self,
        excel_path: Optional[Path] = None,
        pending_path: Optional[Path] = None,
    ):
        self.excel_path = excel_path or OUTPUT_EXCEL_PATH
        self.pending_path = pending_path or PENDING_FILE_PATH
        self._ensure_workbook_exists()

    def _ensure_workbook_exists(self) -> None:
        """
        Creates the target workbook with formatted headers if it doesn't exist.
        """
        if self.excel_path.exists():
            return

        logger.info("Initializing new Excel workbook at %s", self.excel_path)
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Scraped Sellers"

        # Define Header Styling
        header_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
        header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        thin_border = Border(
            left=Side(style="thin", color="D9D9D9"),
            right=Side(style="thin", color="D9D9D9"),
            top=Side(style="thin", color="D9D9D9"),
            bottom=Side(style="thin", color="D9D9D9"),
        )

        ws.append(EXCEL_COLUMNS)

        for col_num, _ in enumerate(EXCEL_COLUMNS, start=1):
            cell = ws.cell(row=1, column=col_num)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = header_alignment
            cell.border = thin_border

        ws.row_dimensions[1].height = 28
        ws.freeze_panes = "A2"

        self._save_workbook_with_retry(wb)

    def _save_workbook_with_retry(self, wb: openpyxl.Workbook) -> bool:
        """
        Saves the workbook with automatic retries if locked by another application (e.g. MS Excel).
        """
        retries = 0
        while retries < MAX_EXCEL_LOCK_RETRIES:
            try:
                wb.save(self.excel_path)
                logger.debug("Excel workbook saved successfully.")
                return True
            except (PermissionError, OSError) as lock_err:
                retries += 1
                logger.warning(
                    "Excel file '%s' is locked (likely open in Excel). Save attempt %d/%d failed. Retrying in %ds... Please close the file if open.",
                    self.excel_path.name,
                    retries,
                    MAX_EXCEL_LOCK_RETRIES,
                    EXCEL_RETRY_INTERVAL,
                )
                time.sleep(EXCEL_RETRY_INTERVAL)

        logger.error(
            "Failed to save Excel file after %d attempts. Workbook lock could not be released.",
            MAX_EXCEL_LOCK_RETRIES
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
            with open(self.pending_path, "w", encoding="utf-8") as f:
                json.dump(pending_list, f, indent=2)
        except Exception as e:
            logger.error("Failed to write to pending file (%s): %s", self.pending_path, str(e))

    def clear_pending_records(self) -> None:
        """
        Clears pending records file upon successful persistence to Excel.
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
        Formats customer scraped data into one or more Excel row lists.
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
            current_sr = sr_no if idx == 0 else ""
            brand_for_listing = listing_brands[idx] if idx < len(listing_brands) else ""
            rows.append([
                current_sr,
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
        return rows

    def append_customer(self, customer_data: Dict[str, Any], sr_no: Any) -> bool:
        """
        Appends a single customer's scraped data to Excel.
        If Excel is locked, buffers record to pending_results.json and retries.

        Returns:
            True if written and saved to Excel successfully, False otherwise.
        """
        self._ensure_workbook_exists()

        # Add this record with its sr_no to pending queue first for safety
        pending = self.load_pending_records()
        pending.append({"sr_no": sr_no, "data": customer_data})
        self.save_pending_records(pending)

        # Attempt to flush all pending records to Excel
        success = self.flush_pending()
        if success:
            logger.info("Excel saved successfully for customer ID: %s", customer_data.get("customer_id"))
        else:
            logger.warning("Pending save queued for customer ID: %s", customer_data.get("customer_id"))

        return success

    def flush_pending(self) -> bool:
        """
        Attempts to write all buffered pending records to Excel and save the workbook.
        """
        pending = self.load_pending_records()
        if not pending:
            return True

        try:
            wb = openpyxl.load_workbook(self.excel_path)
            ws = wb.active
        except (PermissionError, OSError) as e:
            logger.warning("Cannot open Excel workbook to flush pending data (File locked): %s", str(e))
            return False
        except Exception as e:
            logger.error("Failed to load Excel workbook: %s", str(e))
            return False

        cell_font = Font(name="Calibri", size=10)
        center_align = Alignment(horizontal="center", vertical="center")
        left_align = Alignment(horizontal="left", vertical="center")
        thin_border = Border(
            left=Side(style="thin", color="E0E0E0"),
            right=Side(style="thin", color="E0E0E0"),
            top=Side(style="thin", color="E0E0E0"),
            bottom=Side(style="thin", color="E0E0E0"),
        )

        for item in pending:
            item_sr = item.get("sr_no")
            item_data = item.get("data", {})
            rows_to_add = self._format_customer_rows(item_data, item_sr)

            for row_vals in rows_to_add:
                ws.append(row_vals)
                row_idx = ws.max_row
                ws.row_dimensions[row_idx].height = 20

                for col_idx in range(1, len(row_vals) + 1):
                    cell = ws.cell(row=row_idx, column=col_idx)
                    cell.font = cell_font
                    cell.border = thin_border
                    # Align center for short meta, left for titles/names/emails
                    if col_idx in (1, 2, 4, 5, 6, 7, 9, 10, 11, 12, 13):
                        cell.alignment = center_align
                    else:
                        cell.alignment = left_align

        # Adjust column widths automatically
        for col in ws.columns:
            col_letter = get_column_letter(col[0].column)
            max_len = 0
            for cell in col:
                val_str = str(cell.value or "")
                if len(val_str) > max_len:
                    max_len = len(val_str)
            ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

        saved = self._save_workbook_with_retry(wb)
        if saved:
            self.clear_pending_records()
            return True

        return False

    def get_completed_customer_ids(self) -> Set[str]:
        """
        Extracts all unique customer IDs that have already been persisted to the Excel file.
        """
        if not self.excel_path.exists():
            return set()

        completed_ids: Set[str] = set()
        try:
            wb = openpyxl.load_workbook(self.excel_path, read_only=True)
            ws = wb.active
            # Iterate rows starting from row 2 (skipping header)
            for row in ws.iter_rows(min_row=2, values_only=True):
                if row and len(row) > 1 and row[1]:
                    c_id = str(row[1]).strip()
                    if c_id and c_id.lower() not in ("customer id", "none", ""):
                        completed_ids.add(c_id)
            wb.close()
        except Exception as e:
            logger.error("Could not read completed IDs from Excel (%s): %s", self.excel_path, str(e))

        return completed_ids

    def get_current_customer_count(self) -> int:
        """
        Returns the number of unique completed customers already present in the Excel file.
        """
        return len(self.get_completed_customer_ids())
