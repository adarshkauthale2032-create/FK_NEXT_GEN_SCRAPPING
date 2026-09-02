"""
Configuration settings for the Customer Scraping Automation project.
"""

import os
from pathlib import Path

# Base Paths
BASE_DIR = Path(__file__).resolve().parent.parent
INPUT_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"
LOGS_DIR = BASE_DIR / "logs"
CONFIG_DIR = BASE_DIR / "config"
BROWSER_PROFILE_DIR = BASE_DIR / "browser_profile"

# File Paths
INPUT_EXCEL_CANDIDATES = [
    INPUT_DIR / "input.xlsx",
    INPUT_DIR / "customer_id_input.xlsx",
    INPUT_DIR / "input.txt",
    INPUT_DIR / "customer_id_input.txt",
]

def resolve_input_file() -> Path:
    """Finds the existing input file path among configured candidates."""
    for p in INPUT_EXCEL_CANDIDATES:
        if p.exists():
            return p
    return INPUT_DIR / "input.xlsx"

INPUT_FILE_PATH = resolve_input_file()
INPUT_EXCEL_PATH = INPUT_DIR / "input.xlsx"
INPUT_TXT_PATH = INPUT_DIR / "input.txt"
INPUT_SHEET_NAMES = ["Merged Data 1", "Merged Data 2", "Merged Data 3"]
INPUT_SHEET_NAME = "Merged Data 1"
INPUT_COLUMN_NAMES = ["Seller ID", "seller_id", "SellerID", "Customer ID", "customer_id", "seller_account_id"]
INPUT_COLUMN_NAME = "Seller ID"
OUTPUT_CSV_PATH = OUTPUT_DIR / "scraped_data.csv"
OUTPUT_EXCEL_PATH = OUTPUT_DIR / "scraped_data.xlsx"
PROGRESS_FILE_PATH = OUTPUT_DIR / "progress.json"
PENDING_FILE_PATH = OUTPUT_DIR / "pending_results.json"
LOG_FILE_PATH = LOGS_DIR / "scraper.log"
SESSION_CONFIG_PATH = CONFIG_DIR / "session.json"

# Ensure runtime directories exist
INPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)
BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

# API & Browser CDP Configuration
BASE_URL = "https://suv-flipkart.seller-support.fkcloud.it"
CDP_PORT = 9222
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"

# Browser Tab Keep-Alive Refresh Interval (10 minutes = 600 seconds)
REFRESH_INTERVAL = 600

# Dynamic Portal URLs
DEFAULT_SELLER_ID = "218598a2b41c4bcd"
DEFAULT_FALLBACK_SELLER_ID = DEFAULT_SELLER_ID
SELLER_INFO_URL = "https://suv-flipkart.seller-support.fkcloud.it/#app/seller/{seller_id}/info"
SELLER_SETTINGS_URL = "https://suv-flipkart.seller-support.fkcloud.it/sellerDashboard/index.html?sellerId={seller_id}#dashboard/settings"
SELLER_APPROVALS_URL = SELLER_SETTINGS_URL

# API Endpoints
API1_ENDPOINT = "/getSellerDetails?sellerId={customer_id}"
API2_COUNT_ENDPOINT = "/sellerDashboard/napi/approval-store/requestsV2-count?sellerId={customer_id}"
API2_REQUESTS_ENDPOINT = "/sellerDashboard/napi/approval-store/requestsV2?sellerId={customer_id}"
API2_ENDPOINT = API2_REQUESTS_ENDPOINT  # Compatibility alias
API3_ENDPOINT = "/getSellerContacts?sellerId={customer_id}"
API_APPROVALS_ENDPOINT = "/sellerDashboard/napi/approval-store/requestsV2?sellerId={customer_id}"
API_QUESTIONS_ENDPOINT = "/sellerDashboard/napi/qnaStore/questionsV2?processId={request_id}&sellerId={customer_id}"

# Scraping & Business Rules
CHUNK_SIZE = 10000  # Number of sellers per output CSV batch file (e.g. 1-10000, 10001-20000, etc.)
DEFAULT_SCRAPE_LIMIT = 10000  # Default number of sellers to scrape in this run

# HTTP & Retry Settings
CONNECT_TIMEOUT = 10  # seconds to establish TCP connection
READ_TIMEOUT = 25  # seconds to wait for server response
REQUEST_TIMEOUT = (CONNECT_TIMEOUT, READ_TIMEOUT)  # (connect, read) tuple
MAX_REQUEST_RETRIES = 3  # retry attempts for transient network/API errors
BACKOFF_FACTOR = 2  # exponential backoff multiplier in seconds
MAX_AUTH_RETRIES = 3  # retry attempts after session refresh (max 3 times)

# CSV / File Lock & Retry Settings
CSV_RETRY_INTERVAL = 3  # seconds between retries when file is locked
MAX_CSV_LOCK_RETRIES = 60  # total retries before pausing/raising (approx 3 minutes)

# Generic Email Domains (not considered unique/D2C)
GENERIC_EMAIL_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "yahoo.com",
    "yahoo.co.in",
    "yahoo.in",
    "rediffmail.com",
    "outlook.com",
    "hotmail.com",
    "live.com",
    "icloud.com",
    "zoho.com",
    "protonmail.com",
    "aol.com",
    "ymail.com",
    "mail.com",
    "gmx.com",
}

# CSV / Excel Column Definitions (Preserving strict order)
CSV_COLUMNS = [
    "Sr No",
    "Customer ID",
    "Account Name",
    "Account Status",
    "Support Manager",
    "Seller Tier",
    "Signed Up Date",
    "Live Date",
    "Approved Brand",
    "Actual Brand Count",
    "Request ID",
    "Brand Name",
    "Brand Owner",
    "Document Type",
    "Brand Website Link",
    "Instagram URL",
    "Mobile Number",
    "Registered Mobile Number",
    "Email ID",
    "Registered Email ID",
    "Unique Email",
    "isD2C",
]
EXCEL_COLUMNS = CSV_COLUMNS  # Backward compatibility alias
