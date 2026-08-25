"""
Main Orchestration Script for Customer Scraping Automation.

Processes customer/seller IDs sequentially, calls API #1, #2, #3, evaluates
business rules, handles authentication refresh, automatically splits output into
1,000-record Excel (.xlsx) and CSV (.csv) batch files, and guarantees resumability
and data integrity.
"""

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
    INPUT_FILE_PATH,
    INPUT_SHEET_NAME,
    INPUT_COLUMN_NAME,
    LOG_FILE_PATH,
    OUTPUT_DIR,
    PROGRESS_FILE_PATH,
    SESSION_CONFIG_PATH,
)
from auth.auth_manager import AuthManager, AuthExpiredError
from api.api_client import APIClient, APIError
from scrapers.api1_scraper import API1Scraper
from scrapers.api2_scraper import API2Scraper
from scrapers.api3_scraper import API3Scraper
from excel.excel_writer import ExcelWriter


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

    def sync_with_excel(self, excel_ids: Set[str]) -> None:
        """
        Synchronizes state with completed IDs found across all batch files.
        """
        if excel_ids:
            before_count = len(self.completed_ids)
            self.completed_ids.update(excel_ids)
            if len(self.completed_ids) > before_count:
                logger.info("Synchronized progress: found %d completed IDs in output files.", len(self.completed_ids))
                self._save()

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
    import argparse
    parser = argparse.ArgumentParser(description="Customer Scraping Automation")
    parser.add_argument("--import-curl", type=str, help="Import session headers and cookies from a copied cURL command")
    parser.add_argument("--set-cookie", type=str, help="Set cookie string directly")
    parser.add_argument("--chunk-size", type=int, default=CHUNK_SIZE, help=f"Number of sellers per output batch file (default: {CHUNK_SIZE})")
    parser.add_argument("--limit", type=int, default=None, help="Optional max limit of sellers to scrape across all batches")
    args = parser.parse_args()

    # 1. Initialize Components
    auth_manager = AuthManager(SESSION_CONFIG_PATH)

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

    if not auth_manager.cookies:
        print("\n" + "=" * 70)
        print("  AUTHENTICATION REQUIRED")
        print("=" * 70)
        print("No active session cookies were detected.")
        print("Please paste ANY of the following:")
        print("  1. A copied cURL command from Chrome DevTools (Network tab)")
        print("  2. The raw 'Cookie' header string from your browser")
        print("=" * 70)

        if sys.stdin.isatty():
            try:
                user_input = input("Paste cURL or Cookie here (or Press Enter to exit): ").strip()
                if user_input:
                    if user_input.startswith("curl") or "-H" in user_input or "--cookie" in user_input:
                        auth_manager.import_curl(user_input)
                    else:
                        auth_manager.set_cookie_string(user_input)
                        auth_manager._save_to_file()
                    print(f"[+] Session configuration saved to {SESSION_CONFIG_PATH.name}!")
                else:
                    print("[-] No session provided. Please configure config/session.json and restart.")
                    return
            except (KeyboardInterrupt, EOFError):
                print("\n[-] Aborted.")
                return
        else:
            logger.error("No active session cookies found in %s. Please populate it and restart.", SESSION_CONFIG_PATH)
            return

    api_client = APIClient(auth_manager)
    
    api1 = API1Scraper(api_client)
    api2 = API2Scraper(api_client)
    api3 = API3Scraper(api_client)
    
    chunk_size = args.chunk_size or CHUNK_SIZE
    excel_writer = ExcelWriter(chunk_size=chunk_size)
    progress_tracker = ProgressTracker()

    # 2. Synchronize progress with existing output files
    excel_completed = excel_writer.get_completed_customer_ids()
    progress_tracker.sync_with_excel(excel_completed)

    # 3. Flush any pending unpersisted data if present
    excel_writer.flush_pending()

    # 4. Load Customer IDs
    customer_ids = read_customer_ids(INPUT_FILE_PATH)
    if not customer_ids:
        logger.warning("No customer IDs to process. Please check %s", INPUT_FILE_PATH)
        logger.info("END - Scraper finished (No input).")
        return

    # Calculate current sequential Sr No counter across all batches
    current_sr_no = excel_writer.get_current_customer_count() + 1

    total_ids = len(customer_ids)
    max_limit = args.limit
    processed_in_session = 0
    skipped_count = 0
    failed_count = 0

    logger.info("Total inputs available: %d | Batch chunk size: %d sellers/file", total_ids, chunk_size)
    if max_limit:
        logger.info("Configured global scrape limit: %d sellers", max_limit)

    try:
        for index, customer_id in enumerate(customer_ids, start=1):
            if max_limit and processed_in_session >= max_limit:
                logger.info("Reached global target limit of %d scraped sellers. Stopping script.", max_limit)
                break

            # Check if customer was already completed
            if progress_tracker.is_completed(customer_id):
                logger.info("[Progress %d/%d] ID: %s | Status: SKIPPED (Already completed)", index, total_ids, customer_id)
                skipped_count += 1
                continue

            try:
                # Calculate current batch metrics and target paths
                batch_num = ((current_sr_no - 1) // chunk_size) + 1
                batch_pos = ((current_sr_no - 1) % chunk_size) + 1
                target_excel = excel_writer.get_excel_path_for_sr(current_sr_no)
                target_csv = excel_writer.get_csv_path_for_sr(current_sr_no)

                # Step 1: Execute API #1 (Customer & Seller Details)
                api1_data = api1.get_seller_details(customer_id)
                account_name = api1_data.get("account_name", "")
                support_mgr = api1_data.get("support_manager", "No")
                tier = api1_data.get("seller_tier", "")

                # Check Support Manager Condition
                if support_mgr == "Yes":
                    save_success = excel_writer.append_customer(api1_data, sr_no=current_sr_no)
                    if save_success:
                        progress_tracker.mark_completed(customer_id)
                        logger.info(
                            "[Progress %d/%d | Batch #%d (%d/%d)] ID: %s | Account: %s | Tier: %s | Support Manager: Yes -> SAVED (%s & %s)",
                            index, total_ids, batch_num, batch_pos, chunk_size, customer_id, account_name, tier, target_excel.name, target_csv.name
                        )
                        if current_sr_no % chunk_size == 0:
                            logger.info(
                                "🎉 [BATCH COMPLETED] Batch #%d (%d sellers) fully saved to %s and %s!",
                                batch_num, chunk_size, target_excel.name, target_csv.name
                            )
                        current_sr_no += 1
                        processed_in_session += 1
                        if max_limit and processed_in_session >= max_limit:
                            logger.info("Reached global target limit of %d scraped sellers. Stopping script.", max_limit)
                            break
                    else:
                        logger.error("Failed to persist data for customer ID: %s", customer_id)
                    continue

                # Step 2: Execute API #2 (GraphQL Listings & Brand Analysis)
                api2_data = api2.get_listings_and_brand(customer_id)
                listings_cnt = api2_data.get("listing_count", 0)
                is_brand = api2_data.get("is_brand", "")
                brand_name = api2_data.get("brand_name", "")

                # Step 3: Execute API #3 (Seller Contact Details)
                api3_data = api3.get_seller_contacts(customer_id)

                # Step 4: Combine Results
                combined_record = {
                    **api1_data,
                    **api2_data,
                    **api3_data,
                }

                # Step 5: Save to Excel and CSV
                save_success = excel_writer.append_customer(combined_record, sr_no=current_sr_no)
                if save_success:
                    progress_tracker.mark_completed(customer_id)
                    brand_info = f"{is_brand} ({brand_name})" if brand_name else is_brand
                    logger.info(
                        "[Progress %d/%d | Batch #%d (%d/%d)] ID: %s | Account: %s | Tier: %s | Listings: %d | Brand: %s -> SAVED (%s & %s)",
                        index, total_ids, batch_num, batch_pos, chunk_size, customer_id, account_name, tier, listings_cnt, brand_info, target_excel.name, target_csv.name
                    )
                    if current_sr_no % chunk_size == 0:
                        logger.info(
                            "🎉 [BATCH COMPLETED] Batch #%d (%d sellers) fully saved to %s and %s!",
                            batch_num, chunk_size, target_excel.name, target_csv.name
                        )
                    current_sr_no += 1
                    processed_in_session += 1
                    if max_limit and processed_in_session >= max_limit:
                        logger.info("Reached global target limit of %d scraped sellers. Stopping script.", max_limit)
                        break
                else:
                    logger.error("Failed to persist data for customer ID: %s", customer_id)

            except AuthExpiredError as auth_err:
                logger.critical("[ALERT] Authentication expired while processing %s: %s", customer_id, str(auth_err))
                print("\n[!] Scraping paused due to authentication expiry. Please update config/session.json and restart.")
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
