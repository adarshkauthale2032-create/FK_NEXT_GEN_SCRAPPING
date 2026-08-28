"""
Main Orchestration Script for Customer Scraping Automation.

Processes customer/seller IDs sequentially, calls API #1, #2, #3, evaluates
business rules, handles authentication refresh via Playwright CDP, maintains a
10-minute browser tab keepalive refresh, persists output to Excel and CSV, and guarantees
resumability and data integrity.
"""

import argparse
from datetime import datetime
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional, Set, Union

# Ensure customer_scraper root is in sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config.settings import (
    CHUNK_SIZE,
    DEFAULT_SCRAPE_LIMIT,
    INPUT_FILE_PATH,
    INPUT_SHEET_NAME,
    INPUT_COLUMN_NAME,
    LOG_FILE_PATH,
    OUTPUT_CSV_PATH,
    OUTPUT_DIR,
    OUTPUT_EXCEL_PATH,
    PROGRESS_FILE_PATH,
    SESSION_CONFIG_PATH,
)
from auth.auth_manager import AuthManager, AuthExpiredError
from api.api_client import APIClient, APIError
from scrapers.api1_scraper import API1Scraper
from scrapers.api2_scraper import API2Scraper
from scrapers.api3_scraper import API3Scraper
from excel.excel_writer import CSVWriter, ExcelWriter


def setup_logger() -> logging.Logger:
    """
    Configures unified logging to both file and console with formatted timestamps.
    """
    logger = logging.getLogger("customer_scraper")
    logger.setLevel(logging.INFO)

    # Avoid duplicate handlers if re-initialized
    if logger.handlers:
        return logger

    # Log format: [YYYY-MM-DD HH:MM:SS] [LEVEL] message
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # File Handler (Rotating max 10MB, 5 backups)
    LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        LOG_FILE_PATH,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8"
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger


logger = setup_logger()


class ProgressTracker:
    """
    Tracks completed customer IDs in progress.json to enable deterministic resume.
    """

    def __init__(self, progress_path: Path = PROGRESS_FILE_PATH):
        self.progress_path = progress_path
        self.completed_ids: Set[str] = set()
        self.last_completed_id: str = ""
        self.load()

    def load(self) -> None:
        """Loads progress from disk if file exists."""
        if self.progress_path.exists():
            try:
                with open(self.progress_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.completed_ids = set(data.get("completed_customer_ids", []))
                    self.last_completed_id = data.get("last_completed_customer_id", "")
                    logger.info("Loaded %d previously completed customer IDs from %s", len(self.completed_ids), self.progress_path.name)
            except Exception as e:
                logger.error("Error reading progress file (%s): %s", self.progress_path, str(e))

    def mark_completed(self, customer_id: str) -> None:
        """
        Marks a customer as completed and immediately writes to progress.json.
        """
        clean_id = str(customer_id).strip()
        self.completed_ids.add(clean_id)
        self.last_completed_id = clean_id
        self._save()

    def sync_with_csv(self, csv_ids: Set[str]) -> None:
        """
        Synchronizes state with completed IDs found in the CSV/Excel dataset.
        """
        if csv_ids:
            before_count = len(self.completed_ids)
            self.completed_ids.update(csv_ids)
            if len(self.completed_ids) > before_count:
                logger.info("Synchronized progress: found %d completed IDs in output datasets.", len(self.completed_ids))
                self._save()

    # Backward compatibility alias
    sync_with_excel = sync_with_csv

    def is_completed(self, customer_id: str) -> bool:
        """Checks whether customer ID has already been completed."""
        return str(customer_id).strip() in self.completed_ids

    def _save(self) -> None:
        """Atomic write to progress JSON."""
        try:
            payload = {
                "completed_customer_ids": sorted(list(self.completed_ids)),
                "last_completed_customer_id": self.last_completed_id,
                "total_completed": len(self.completed_ids),
                "updated_at": datetime.now().isoformat(),
            }
            tmp_path = self.progress_path.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            tmp_path.replace(self.progress_path)
        except Exception as e:
            logger.error("Failed to save progress marker: %s", str(e))


def ensure_input_excel_exists(excel_path: Path, txt_path: Optional[Path] = None) -> None:
    """
    Ensures customer_id_input.xlsx exists.
    If not, automatically populates it from customer_id_input.txt or default sample IDs.
    """
    if excel_path.exists():
        return

    try:
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = INPUT_SHEET_NAME
        ws.append([INPUT_COLUMN_NAME])

        # Try reading from text file if available
        ids_to_write = []
        if txt_path and txt_path.exists():
            with open(txt_path, "r", encoding="utf-8") as f:
                for line in f:
                    c_id = line.strip()
                    if c_id and not c_id.startswith("#"):
                        ids_to_write.append(c_id)

        if not ids_to_write:
            ids_to_write = ["d519f67b462d4e10", "218598a2b41c4bcd", "aaa30e788efa4f6c", "1111218ddd74492b"]

        for c_id in ids_to_write:
            ws.append([str(c_id)])

        excel_path.parent.mkdir(parents=True, exist_ok=True)
        wb.save(excel_path)
        logger.info(
            "Created input template at %s with %d seller IDs in '%s' sheet under '%s' column.",
            excel_path.name,
            len(ids_to_write),
            INPUT_SHEET_NAME,
            INPUT_COLUMN_NAME,
        )
    except Exception as e:
        logger.warning("Could not auto-create %s: %s", excel_path.name, str(e))


def read_customer_ids(
    file_path: Path,
    sheet_name: str = INPUT_SHEET_NAME,
    column_name: str = INPUT_COLUMN_NAME,
) -> List[str]:
    """
    Reads customer / seller IDs from an Excel workbook (.xlsx) or text file (.txt).
    For .xlsx files:
      - Reads the sheet named `sheet_name` (default: 'Input Sheet')
      - Finds the column with heading `column_name` (default: 'seller_id')
      - Extracts all non-empty IDs in exact sequential order
    """
    customer_ids: List[str] = []

    # If the configured .xlsx file does not exist, attempt auto-creation from .txt if present
    if not file_path.exists() and file_path.suffix.lower() in (".xlsx", ".xlsm", ".xltx"):
        txt_fallback = file_path.with_suffix(".txt")
        ensure_input_excel_exists(file_path, txt_path=txt_fallback)

    if not file_path.exists():
        logger.error("Input file not found at %s", file_path)
        return []

    # 1. Handle Excel (.xlsx / .xlsm / .xltx) input
    if file_path.suffix.lower() in (".xlsx", ".xlsm", ".xltx"):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(file_path, data_only=True)

            # Find matching sheet (case-insensitive & whitespace trimmed)
            target_sheet = None
            for s_name in wb.sheetnames:
                if s_name.strip().lower() == sheet_name.strip().lower():
                    target_sheet = wb[s_name]
                    break
            if target_sheet is None:
                for s_name in wb.sheetnames:
                    if "input" in s_name.strip().lower():
                        target_sheet = wb[s_name]
                        break
            if target_sheet is None:
                target_sheet = wb.active
                logger.warning("Sheet '%s' not found. Using active sheet '%s'.", sheet_name, target_sheet.title)

            # Find target column heading in top rows (row 1 or row 2)
            target_col_idx = 1
            header_row = 1
            col_found = False

            for r in range(1, min(5, target_sheet.max_row + 1)):
                for c in range(1, target_sheet.max_column + 1):
                    val = target_sheet.cell(row=r, column=c).value
                    if val is not None:
                        val_str = str(val).strip().lower().replace(" ", "_").replace("-", "_")
                        col_clean = column_name.strip().lower().replace(" ", "_").replace("-", "_")
                        if val_str == col_clean or val_str in ("seller_id", "sellerid", "customer_id", "customerid", "id"):
                            target_col_idx = c
                            header_row = r
                            col_found = True
                            break
                if col_found:
                    break

            # Read all IDs below header row
            for r in range(header_row + 1, target_sheet.max_row + 1):
                val = target_sheet.cell(row=r, column=target_col_idx).value
                if val is not None:
                    if isinstance(val, float) and val.is_integer():
                        clean_id = str(int(val))
                    else:
                        clean_id = str(val).strip()

                    if clean_id and clean_id.lower() not in ("none", "null", ""):
                        customer_ids.append(clean_id)

            wb.close()
            logger.info(
                "Read %d customer IDs from Excel file '%s' (Sheet: '%s', Column: '%s')",
                len(customer_ids),
                file_path.name,
                target_sheet.title,
                column_name,
            )
            return customer_ids

        except Exception as e:
            logger.error("Failed to read Excel input file %s: %s", file_path, str(e))
            txt_fallback = file_path.with_suffix(".txt")
            if txt_fallback.exists():
                logger.info("Falling back to reading from %s...", txt_fallback.name)
                return read_customer_ids(txt_fallback)
            return []

    # 2. Handle Plain Text (.txt) input
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            clean_line = line.strip()
            if clean_line and not clean_line.startswith("#"):
                customer_ids.append(clean_line)

    logger.info("Read %d customer IDs from input file: %s", len(customer_ids), file_path.name)
    return customer_ids


def main():
    parser = argparse.ArgumentParser(description="Flipkart Customer Scraping Automation")
    parser.add_argument("--refresh-session", action="store_true", help="Refresh Flipkart tab in Chrome and update session.json")
    parser.add_argument("--monitor-session", action="store_true", help="Run 10-minute browser tab keepalive refresh loop")
    parser.add_argument("--seller-id", type=str, default=None, help="Specific seller ID for session refresh / keepalive")
    parser.add_argument("--import-curl", type=str, help="Import session headers and cookies from a copied cURL command")
    parser.add_argument("--set-cookie", type=str, help="Set cookie string directly")
    parser.add_argument("--chunk-size", type=int, default=CHUNK_SIZE, help=f"Number of sellers per output batch file (default: {CHUNK_SIZE})")
    parser.add_argument("--limit", type=int, default=DEFAULT_SCRAPE_LIMIT, help=f"Number of new seller records to scrape in this session (default: {DEFAULT_SCRAPE_LIMIT})")
    args = parser.parse_args()

    chunk_size = args.chunk_size or CHUNK_SIZE
    max_limit = args.limit

    # 1. Initialize Authentication Manager
    auth_manager = AuthManager(SESSION_CONFIG_PATH)

    # Handle standalone CLI commands
    if args.refresh_session:
        logger.info("Manual session refresh requested via --refresh-session.")
        success = auth_manager.refresh_session(seller_id=args.seller_id)
        if success:
            print(f"\n[+] Successfully refreshed and saved {len(auth_manager.cookies)} cookies to {SESSION_CONFIG_PATH.name}")
        else:
            print("\n[-] Session refresh could not be completed.")
        return

    if args.monitor_session:
        logger.info("Keep-alive monitoring requested via --monitor-session.")
        import asyncio
        asyncio.run(auth_manager.playwright_handler._async_keepalive_loop(seller_id=args.seller_id))
        return

    if args.import_curl:
        success = auth_manager.import_curl(args.import_curl)
        if success:
            print(f"[+] Successfully imported session into {SESSION_CONFIG_PATH.name}")
        else:
            print("[-] Could not parse cookies from provided cURL string.")
        return

    if args.set_cookie:
        auth_manager.set_cookie_string(args.set_cookie)
        auth_manager._save_to_file()
        print(f"[+] Cookie string saved to {SESSION_CONFIG_PATH.name}")
        return

    logger.info("==========================================")
    logger.info("START - Customer Scraping Automation")
    logger.info("==========================================")

    # 2. Load Customer IDs
    customer_ids = read_customer_ids(INPUT_FILE_PATH)
    if not customer_ids:
        logger.warning("No customer IDs to process. Please check %s", INPUT_FILE_PATH)
        logger.info("END - Scraper finished (No input).")
        return

    first_seller_id = customer_ids[0] if customer_ids else None

    # If no session cookies found, attempt automatic refresh/extraction from browser
    if not auth_manager.cookies:
        logger.info("No active session cookies found in %s. Connecting to browser via CDP...", SESSION_CONFIG_PATH.name)
        try:
            auth_success = auth_manager.refresh_session(seller_id=first_seller_id)
            if not auth_success or not auth_manager.cookies:
                logger.error("Authentication required to proceed. Please ensure Chrome is open with CDP and logged in.")
                return
        except Exception as e:
            logger.error("Authentication error: %s", str(e))
            return

    api_client = APIClient(auth_manager)
    api1 = API1Scraper(api_client)
    api2 = API2Scraper(api_client)
    api3 = API3Scraper(api_client)
    
    csv_writer = CSVWriter(output_dir=OUTPUT_DIR, chunk_size=chunk_size)
    excel_writer = csv_writer
    progress_tracker = ProgressTracker()

    # 3. Synchronize progress with existing CSV datasets
    completed_ids = csv_writer.get_completed_customer_ids()
    progress_tracker.sync_with_csv(completed_ids)

    # 4. Flush any pending unpersisted data if present
    csv_writer.flush_pending()

    # Calculate current sequential Sr No counter
    current_sr_no = csv_writer.get_current_customer_count() + 1

    total_ids = len(customer_ids)
    processed_in_session = 0
    skipped_count = 0
    failed_count = 0

    # Pick up latest seller ID from progress.json and determine start index
    last_completed_id = progress_tracker.last_completed_id
    start_idx = 0
    if last_completed_id and last_completed_id in customer_ids:
        start_idx = customer_ids.index(last_completed_id) + 1
        logger.info(
            "📍 [RESUME] Picked up latest seller ID '%s' from progress.json (Index %d/%d). Resuming next seller from input file at position %d...",
            last_completed_id, start_idx, total_ids, start_idx + 1
        )
    elif progress_tracker.completed_ids:
        logger.info(
            "📍 [RESUME] Progress loaded with %d completed sellers. Scanning input file for remaining IDs...",
            len(progress_tracker.completed_ids)
        )

    logger.info("Total inputs in file: %d", total_ids)
    logger.info("Already completed sellers (will be skipped): %d", len(progress_tracker.completed_ids))
    logger.info("Batch file size: %d sellers / CSV file", chunk_size)
    if max_limit:
        logger.info("Target for this session: Scraping next %d new seller records.", max_limit)

    try:
        for index in range(start_idx, total_ids):
            customer_id = customer_ids[index]
            display_pos = index + 1

            if max_limit and processed_in_session >= max_limit:
                logger.info("🎉 [SESSION COMPLETE] Successfully scraped target of %d sellers in this run. Stopping script cleanly.", max_limit)
                break

            # Check if customer was already completed
            if progress_tracker.is_completed(customer_id):
                logger.info("[Progress %d/%d] ID: %s | Status: SKIPPED (Already completed)", display_pos, total_ids, customer_id)
                skipped_count += 1
                continue

            try:
                # Calculate current batch metrics and target paths
                batch_num = ((current_sr_no - 1) // chunk_size) + 1
                batch_pos = ((current_sr_no - 1) % chunk_size) + 1
                target_csv = csv_writer.get_csv_path_for_sr(current_sr_no)

                # Step 1: Execute API #1 (Customer & Seller Details)
                api1_data = api1.get_seller_details(customer_id)
                account_name = api1_data.get("account_name", "")
                account_status = api1_data.get("account_status", "")
                support_mgr = api1_data.get("support_manager", "No")
                tier = api1_data.get("seller_tier", "")

                # Step 2: Execute API #2 (Approval Store & Brand Count Analysis)
                api2_data = api2.get_brand_approval_details(customer_id)
                approved_brand_cnt = api2_data.get("approved_brand", 0)
                actual_brand_cnt = api2_data.get("actual_brand_count", 0)

                # Step 3: Execute API #3 (Seller Contact Details)
                api3_data = api3.get_seller_contacts(customer_id)

                # Step 4: Combine Results
                combined_record = {
                    **api1_data,
                    **api2_data,
                    **api3_data,
                }

                # Step 5: Save directly to CSV
                save_success = csv_writer.append_customer(combined_record, sr_no=current_sr_no)
                if save_success:
                    progress_tracker.mark_completed(customer_id)
                    is_d2c = combined_record.get("isD2C") or combined_record.get("is_d2c", "No")
                    logger.info(
                        "[Progress %d/%d | Batch #%d (%d/%d)] ID: %s | Account: %s | Status: %s | Support Mgr: %s | Approved Brands: %s | Actual Brands: %s | Tier: %s | isD2C: %s -> SAVED (%s)",
                        display_pos, total_ids, batch_num, batch_pos, chunk_size, customer_id, account_name, account_status, support_mgr, approved_brand_cnt, actual_brand_cnt, tier, is_d2c, target_csv.name
                    )
                    if current_sr_no % chunk_size == 0:
                        logger.info(
                            "🎉 [BATCH COMPLETED] Batch #%d (%d sellers) fully saved to %s!",
                            batch_num, chunk_size, target_csv.name
                        )
                    current_sr_no += 1
                    processed_in_session += 1
                    if max_limit and processed_in_session >= max_limit:
                        logger.info("🎉 [SESSION COMPLETE] Successfully scraped target of %d sellers in this run. Stopping script cleanly.", max_limit)
                        break
                else:
                    logger.error("Failed to persist data for customer ID: %s", customer_id)

            except AuthExpiredError as auth_err:
                logger.critical("[CRITICAL] Authentication error on customer ID %s: %s", customer_id, str(auth_err))
                print("\n" + "=" * 75)
                print("❌ [SCRIPT STOPPED] Failed to retrieve valid Flipkart session.")
                print(f"   Reason: {str(auth_err)}")
                print("   Action: Please open Chrome, make sure you are logged into the Flipkart")
                print("           Seller Portal, and rerun the script:")
                print("           python main.py")
                print("=" * 75 + "\n")
                break

            except APIError as api_err:
                failed_count += 1
                logger.warning("[WARNING] API error for customer ID %s: %s. Customer skipped.", customer_id, str(api_err))

            except Exception as unexp_err:
                failed_count += 1
                logger.error("[ERROR] Unexpected error for customer ID %s: %s", customer_id, str(unexp_err))

    except KeyboardInterrupt:
        logger.warning("Scraper execution interrupted by user (KeyboardInterrupt). Shutting down cleanly...")
    finally:
        # Attempt final flush of any pending records
        excel_writer.flush_pending()
        
        logger.info("==========================================")
        logger.info("Scraping Summary:")
        logger.info("  Total input IDs:             %d", total_ids)
        logger.info("  Batch chunk size:            %d sellers / file", chunk_size)
        logger.info("  Processed in this session:   %d", processed_in_session)
        logger.info("  Skipped (already completed): %d", skipped_count)
        logger.info("  Failed:                      %d", failed_count)
        logger.info("  Total completed sellers:     %d", len(progress_tracker.completed_ids))
        logger.info("  Output Directory:            %s", OUTPUT_DIR)
        logger.info("==========================================")


if __name__ == "__main__":
    main()
