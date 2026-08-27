# Flipkart Seller Support Customer Scraping Automation

A production-ready, modular, and resilient Python automation system to extract seller details, listing data, and contact info from Flipkart Seller Support APIs (`suv-flipkart.seller-support.fkcloud.it`), apply custom business rules, and continuously write clean tabular data into **CSV** (`output/scraped_data.csv`).

---

## 1. Project Structure

```text
customer_scraper/
│
├── main.py                     # CLI entry point & sequential orchestrator
├── requirements.txt            # Python dependencies (requests, openpyxl, playwright)
├── README.md                   # Comprehensive documentation & guide
│
├── auth/                       # Authentication & session layer
│   ├── __init__.py
│   ├── auth_manager.py         # Cookie management, auth verification & token refresh
│   ├── playwright_session.py   # Playwright CDP session scraper & 10-min keepalive refresher
│   └── chrome_session.py       # cURL parser helper
│
├── browser_profile/            # Dedicated browser profile directory
│
├── api/                        # Shared network layer
│   ├── __init__.py
│   └── api_client.py           # HTTP GET/POST with retries, status checks & auth handling
│
├── scrapers/                   # Isolated API scraper modules
│   ├── __init__.py
│   ├── api1_scraper.py         # API #1: Seller Details & Support Manager logic
│   ├── api2_scraper.py         # API #2: Active Listings & 12-of-20 Brand Rule
│   └── api3_scraper.py         # API #3: Seller Contact Details
│
├── excel/                      # Persistence layer (CSV / Excel compatibility)
│   ├── __init__.py
│   └── excel_writer.py         # CSVWriter with locked-file detection & pending buffer
│
├── config/                     # Configuration & credentials
│   ├── __init__.py
│   ├── settings.py             # URLs, timeouts, retry limits, thresholds, paths
│   └── session.json            # Active cookie and header configuration
│
├── input/
│   └── customer_id_input.txt   # Line-separated customer/seller IDs
│
├── output/
│   ├── scraped_data.csv        # Output CSV dataset (utf-8-sig)
│   ├── progress.json           # Progress tracker of completed IDs for resume capability
│   └── pending_results.json    # Durable pending queue when CSV is locked
│
└── logs/
    └── scraper.log             # Timestamped application logs
```

---

## 2. Python Version & Requirements

* **Python Version:** Python 3.8+ (Tested on Python 3.10 / 3.11 / 3.12)
* **Dependencies:**
  * `requests>=2.31.0` (HTTP networking)
  * `openpyxl>=3.1.2` (Workbook support)
  * `urllib3>=2.0.0` (Connection pooling)
  * `playwright>=1.40.0` (CDP browser connection & session scraping)

---

## 3. Installation & Setup (Windows PowerShell)

1. Open PowerShell and navigate to the project directory:
   ```powershell
   cd "c:\Users\Adarsh Kauthale\Documents\FK Next-Gen FlipKart Scrapping\customer_scraper"
   ```

2. Create a virtual environment:
   ```powershell
   python -m venv .venv
   ```

3. Activate the virtual environment:
   ```powershell
   .venv\Scripts\activate
   ```

4. Install required dependencies:
   ```powershell
   pip install -r requirements.txt
   ```

5. Install Playwright browser binaries (one-time setup):
   ```powershell
   playwright install chromium
   ```

---

## 4. Input Configuration

Place your target customer/seller IDs into:
```text
input/customer_id_input.txt
```
Format: One customer ID per line (empty lines and `#` comments are automatically ignored).

Example:
```text
218598a2b41c4bcd
123456789abcdef
987654321abcdef
```

---

## 5. Authentication & Browser Session Scraping (CDP)

The scraper connects directly to an **opened Chrome browser** over the Chrome DevTools Protocol (`http://127.0.0.1:9222`) without requiring hardcoded Chrome installation paths or SQLite cookie decryption:

### 1. Launching Chrome with CDP Enabled
Start Google Chrome from the command line with remote debugging:
```powershell
chrome.exe --remote-debugging-port=9222 "https://suv-flipkart.seller-support.fkcloud.it/#app/seller/218598a2b41c4bcd/info"
```
Log in to Flipkart Seller Support in this browser window.

### 2. Routine 10-Minute Keep-Alive Tab Refresh
* While the scraper is active, a background thread **refreshes the open Flipkart tab every 10 minutes (600s)** solely to keep the session alive and prevent timeout/logout.
* During these routine 10-minute refreshes, the scraper **does NOT overwrite `config/session.json`**.

### 3. Session Expiration & Automatic Refresh
* When an API call returns a session expired error (`401`, `403`, or auth redirect):
  1. The scraper automatically connects to Chrome via CDP.
  2. Reloads the dynamic seller info URL (`https://suv-flipkart.seller-support.fkcloud.it/#app/seller/${seller_id}/info`).
  3. Intercepts the automatic API calls (such as `getSellerDetails` and `requestsV2`) to capture `FK-CSRF-TOKEN`, `User-Agent`, etc.
  4. Extracts all active cookies (`connect.sid`, `XyZ7pQ9rS2T1uV8wA3bC6dE4fG0h`, `_gcl_au`, `_fbp`, `_gid`, `is_login`, `AMCV_...`, etc.).
  5. Updates `config/session.json` in the exact standard schema.
  6. Resumes scraping seamlessly.

### 4. Standalone CLI Commands
* **Refresh session on-demand:**
  ```powershell
  python main.py --refresh-session --seller-id 218598a2b41c4bcd
  ```
* **Run 10-minute keep-alive monitor in standalone mode:**
  ```powershell
  python main.py --monitor-session --seller-id 218598a2b41c4bcd
  ```
* **Import from copied cURL string:**
  ```powershell
  python main.py --import-curl "<pasted_curl_command>"
  ```

---

## 6. How to Start the Scraper

Ensure you are connected to the internal Flipkart Network/VPN and your browser is logged in:
```powershell
python main.py
```

---

## 7. Business Logic & Processing Flow

For each Customer ID:
1. **API #1 (`getSellerDetails`):**
   * Extracts Account Name, Support Manager status, Seller Tier, Signed Up Date, Live Date.
   * **Support Manager Rule:**
     * If `role_name`, `user_id`, `email_id`, `name`, `phone_num`, or `manager_email_id` contains any non-null/non-empty value $\to$ `Support Manager = Yes`.
     * If all are null/empty $\to$ `Support Manager = No`.
   * **Support Manager = Yes Branch:**
     * Immediately saves API #1 data to CSV.
     * **Skips API #2 and API #3**.
     * Marks customer completed and moves to next ID.
2. **API #2 (`listingsDataForStates`):**
   * Requests up to 20 active listings.
   * Extracts all product titles.
   * **12-of-20 Brand Rule:**
     * If the SAME brand appears **strictly more than 12 times** (`count > 12`):
       * `Is Brand = Possibly a Brand`
       * `Brand Name = <brand>`
     * Otherwise:
       * `Is Brand = Possibly a Seller`
       * `Brand Name = ""`
3. **API #3 (`getSellerContacts`):**
   * Extracts Mobile Number, Registered Mobile, Email ID, Registered Email.
4. **CSV Persistence:**
   * Combines all data.
   * Each product listing title is written as a separate row linked under that customer.
   * Confirms write to CSV $\to$ marks customer as completed in `output/progress.json`.

---

## 8. CSV Output Format
Saved at: `output/scraped_data.csv` (encoded as `utf-8-sig` for immediate opening in Excel).

| Sr No | Customer ID | Account Name | Support Manager | Seller Tier | Signed Up Date | Live Date | Brand List (Title) | Listing Brand | Is Brand | Brand Name | Mobile Number | Registered Mobile Number | Email ID | Registered Email ID |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | ID001 | Seller ABC | No | Silver | 2021-01-10 | 2021-02-15 | Product Title 1 | BRAND_X | Possibly a Brand | BRAND_X | 9876543210 | 9876543210 | abc@mail.com | abc@mail.com |
| | ID001 | Seller ABC | No | Silver | 2021-01-10 | 2021-02-15 | Product Title 2 | BRAND_X | Possibly a Brand | BRAND_X | 9876543210 | 9876543210 | abc@mail.com | abc@mail.com |
| 2 | ID002 | Seller XYZ | Yes | Gold | 2020-05-12 | 2020-06-01 | | | | | | | | |

---

## 9. Fault Tolerance & Data Safety

### Resumability After Crash / Restart
* Progress is tracked in real-time in `output/progress.json`.
* Additionally, `main.py` scans `output/scraped_data.csv` upon startup.
* Any already-completed customer ID is skipped automatically.
* Customers are never duplicated, and partially completed customers are cleanly retried.

### Locked CSV File Recovery
* If `output/scraped_data.csv` is open in Microsoft Excel on Windows when a save occurs:
  1. The scraper catches the file lock exception (`PermissionError`).
  2. Scraped data is safely buffered into `output/pending_results.json`.
  3. The system waits and retries every `CSV_RETRY_INTERVAL` (default 3 seconds).
  4. Once the file is closed or unlocked, all pending data is flushed to CSV, verified, and cleared from the buffer.
  5. **No data is lost**.

---

## 10. Logging & Monitoring

* Logs are written simultaneously to the terminal console and to `logs/scraper.log`.
* Log format: `[YYYY-MM-DD HH:MM:SS] [LEVEL] Message`
* Structured events: `START`, `Customer ID started`, `API #1 started`, `Support Manager detected`, `API #2 started`, `API #3 started`, `CSV saved`, `Customer completed`, `Session expired`, `Session refreshed`, `Retry`, `CSV locked`, `Pending save`, `END`.
* **Zero Secret Leakage:** Authentication cookies and tokens are strictly stripped and never logged.
