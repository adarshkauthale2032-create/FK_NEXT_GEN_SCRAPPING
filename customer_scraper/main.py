"""
Main Orchestration Script for Customer Scraping Automation.

Processes customer/seller IDs sequentially, calls API #1, #2, #3, evaluates
business rules, handles authentication refresh via Playwright CDP, maintains a
10-minute browser tab keepalive refresh, persists output to CSV, and guarantees
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
from typing import List, Set

# Ensure customer_scraper root is in sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config.settings import (
    INPUT_FILE_PATH,
    LOG_FILE_PATH,
    OUTPUT_CSV_PATH,
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
        Synchronizes state with completed IDs found in the CSV dataset.
        """
        if csv_ids:
            before_count = len(self.completed_ids)
            self.completed_ids.update(csv_ids)
            if len(self.completed_ids) > before_count:
                logger.info("Synchronized progress: found %d completed IDs in CSV.", len(self.completed_ids))
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


def read_customer_ids(file_path: Path) -> List[str]:
    """
    Reads customer IDs from the text file.
    - Strips whitespace
    - Ignores empty lines
    - Preserves ordering
    """
    if not file_path.exists():
        logger.error("Input file not found at %s", file_path)
        return []

    customer_ids = []
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
    args = parser.parse_args()

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

    # 4. Load Customer IDs
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

    # Start background 10-minute tab keepalive refresher
    auth_manager.start_keepalive_refresher(seller_id=first_seller_id)

    api_client = APIClient(auth_manager)
    api1 = API1Scraper(api_client)
    api2 = API2Scraper(api_client)
    api3 = API3Scraper(api_client)
    
    csv_writer = CSVWriter()
    progress_tracker = ProgressTracker()

    # 2. Synchronize progress with existing CSV dataset
    csv_completed = csv_writer.get_completed_customer_ids()
    progress_tracker.sync_with_csv(csv_completed)

    # 3. Flush any pending unpersisted data if present
    csv_writer.flush_pending()

    # Calculate current sequential Sr No counter
    current_sr_no = csv_writer.get_current_customer_count() + 1

    total_ids = len(customer_ids)
    processed_in_session = 0
    skipped_count = 0
    failed_count = 0

    try:
        for index, customer_id in enumerate(customer_ids, start=1):
            # Check if customer was already completed
            if progress_tracker.is_completed(customer_id):
                logger.info("[Progress %d/%d] ID: %s | Status: SKIPPED (Already completed)", index, total_ids, customer_id)
                skipped_count += 1
                continue

            try:
                # Step 1: Execute API #1 (Customer & Seller Details)
                api1_data = api1.get_seller_details(customer_id)
                account_name = api1_data.get("account_name", "")
                support_mgr = api1_data.get("support_manager", "No")
                tier = api1_data.get("seller_tier", "")

                # Check Support Manager Condition
                if support_mgr == "Yes":
                    save_success = csv_writer.append_customer(api1_data, sr_no=current_sr_no)
                    if save_success:
                        progress_tracker.mark_completed(customer_id)
                        current_sr_no += 1
                        processed_in_session += 1
                        logger.info(
                            "[Progress %d/%d] ID: %s | Account: %s | Tier: %s | Support Manager: Yes -> SAVED (Skipped API #2/#3)",
                            index, total_ids, customer_id, account_name, tier
                        )
                    else:
                        logger.error("Failed to persist data to CSV for customer ID: %s", customer_id)
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

                # Step 5: Save to CSV
                save_success = csv_writer.append_customer(combined_record, sr_no=current_sr_no)
                if save_success:
                    progress_tracker.mark_completed(customer_id)
                    current_sr_no += 1
                    processed_in_session += 1
                    brand_info = f"{is_brand} ({brand_name})" if brand_name else is_brand
                    logger.info(
                        "[Progress %d/%d] ID: %s | Account: %s | Tier: %s | Listings: %d | Brand: %s -> SAVED",
                        index, total_ids, customer_id, account_name, tier, listings_cnt, brand_info
                    )
                else:
                    logger.error("Failed to persist data to CSV for customer ID: %s", customer_id)

            except AuthExpiredError as auth_err:
                logger.critical("[ALERT] Authentication expired while processing %s: %s", customer_id, str(auth_err))
                print(f"\n[!] Scraping paused due to authentication expiry on {customer_id}. Please refresh session.")
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
        csv_writer.flush_pending()
        
        # Stop background keepalive refresher
        auth_manager.stop_keepalive_refresher()
        
        logger.info("==========================================")
        logger.info("Scraping Summary:")
        logger.info("  Total input IDs:             %d", total_ids)
        logger.info("  Processed in this session:   %d", processed_in_session)
        logger.info("  Skipped (already completed): %d", skipped_count)
        logger.info("  Failed:                      %d", failed_count)
        logger.info("  Total completed in CSV:      %d", len(progress_tracker.completed_ids))
        logger.info("  CSV Output:                  %s", OUTPUT_CSV_PATH)
        logger.info("==========================================")


if __name__ == "__main__":
    main()
