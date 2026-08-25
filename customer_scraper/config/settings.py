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
INPUT_FILE_PATH = INPUT_DIR / "customer_id_input.xlsx"
INPUT_SHEET_NAME = "Input Sheet"
INPUT_COLUMN_NAME = "seller_id"

OUTPUT_EXCEL_PATH = OUTPUT_DIR / "scraped_data.xlsx"
PROGRESS_FILE_PATH = OUTPUT_DIR / "progress.json"
PENDING_FILE_PATH = OUTPUT_DIR / "pending_results.json"
LOG_FILE_PATH = LOGS_DIR / "scraper.log"
SESSION_CONFIG_PATH = CONFIG_DIR / "session.json"

# Custom Chrome / Browser Path Configuration
# If your Chrome is installed in a custom location, replace "Enter_YOUR_PATH" with your actual path.
# Supported formats:
#   1. Chrome executable: r"C:\Program Files\Google\Chrome\Application\chrome.exe"
#   2. Chrome installation folder: r"C:\Program Files\Google\Chrome\Application"
#   3. Chrome User Data / Profile folder: r"C:\Users\<YourUser>\AppData\Local\Google\Chrome\User Data"
CHROME_INSTALLED_PATH = "Enter_YOUR_PATH"

# Ensure runtime directories exist
INPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)
BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

# API Configuration
BASE_URL = "https://suv-flipkart.seller-support.fkcloud.it"
CDP_PORT = 9222
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"

API1_ENDPOINT = "/getSellerDetails?sellerId={customer_id}"
API2_ENDPOINT = "/sellerDashboard/orchestrator/graphql?sellerId={customer_id}"
API3_ENDPOINT = "/getSellerContacts?sellerId={customer_id}"

# Scraping & Business Rules
BRAND_THRESHOLD = 12  # Must be strictly > 12 to be considered "Possibly a Brand"
LISTING_BATCH_SIZE = 20  # Fetch up to 20 listings in API #2

# HTTP & Retry Settings
CONNECT_TIMEOUT = 10  # seconds to establish TCP connection
READ_TIMEOUT = 25  # seconds to wait for server response
REQUEST_TIMEOUT = (CONNECT_TIMEOUT, READ_TIMEOUT)  # (connect, read) tuple
MAX_REQUEST_RETRIES = 3  # retry attempts for transient network/API errors
BACKOFF_FACTOR = 2  # exponential backoff multiplier in seconds
MAX_AUTH_RETRIES = 2  # retry attempts after session refresh

# Excel Lock & Retry Settings
EXCEL_RETRY_INTERVAL = 3  # seconds between retries when file is locked
MAX_EXCEL_LOCK_RETRIES = 60  # total retries before pausing/raising (approx 3 minutes)

# Excel Column Definitions (Preserving strict order)
EXCEL_COLUMNS = [
    "Sr No",
    "Customer ID",
    "Account Name",
    "Support Manager",
    "Seller Tier",
    "Signed Up Date",
    "Live Date",
    "Brand List",
    "Listing Brand",
    "Is Brand",
    "Brand Name",
    "Mobile Number",
    "Registered Mobile Number",
    "Email ID",
    "Registered Email ID",
]
