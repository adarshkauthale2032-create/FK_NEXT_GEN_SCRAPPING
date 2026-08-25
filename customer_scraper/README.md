# Flipkart Seller Support Customer Scraping Automation

A production-ready, modular, and resilient Python automation system to extract seller details, listing data, and contact info from Flipkart Seller Support APIs (`suv-flipkart.seller-support.fkcloud.it`), apply custom business rules, and continuously write clean tabular data into Microsoft Excel.

---

## 1. Project Structure

```text
customer_scraper/
│
├── main.py                     # CLI entry point & sequential orchestrator
├── requirements.txt            # Python dependencies (requests, openpyxl)
├── README.md                   # Comprehensive documentation & guide
│
├── auth/                       # Authentication & session layer
│   ├── __init__.py
│   └── auth_manager.py         # Cookie management, auth verification & token refresh
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
├── excel/                      # Excel persistence layer
│   ├── __init__.py
│   └── excel_writer.py         # openpyxl writer with locked-file detection & pending buffer
│
├── config/                     # Configuration & credentials
│   ├── __init__.py
│   ├── settings.py             # URLs, timeouts, retry limits, thresholds, paths
│   └── session.json.example    # Template for cookie/header session configuration
│
├── input/
│   └── customer_id_input.txt   # Line-separated customer/seller IDs
│
├── output/
│   ├── scraped_data.xlsx       # Output Excel sheet
│   ├── progress.json           # Progress tracker of completed IDs for resume capability
│   └── pending_results.json    # Durable pending queue when Excel is locked
│
└── logs/
    └── scraper.log             # Timestamped application logs
```

---

## 2. Python Version & Requirements

* **Python Version:** Python 3.8+ (Tested on Python 3.10 / 3.11 / 3.12)
* **Dependencies:**
  * `requests>=2.31.0` (HTTP networking)
  * `openpyxl>=3.1.2` (Excel generation & styling)
  * `urllib3>=2.0.0` (Connection pooling)

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

## 5. Authentication & Session Setup

Python `requests` runs independently from Google Chrome. To authenticate with Flipkart Seller Support:

### Option A: Import Directly from Chrome (Easiest)
1. Open Chrome and log in to [Flipkart Seller Support](https://suv-flipkart.seller-support.fkcloud.it).
2. Open DevTools (`F12`), go to the **Network** tab.
3. Click any request (e.g. `getSellerDetails` or any `suv-flipkart` call).
4. Right-click the request $\to$ **Copy** $\to$ **Copy as cURL (bash / PowerShell / cmd)**.
5. Run:
   ```powershell
   python main.py --import-curl "PASTE_YOUR_CURL_HERE"
   ```
   This automatically parses cookies and headers and saves them to `config/session.json`.

### Option B: Set Cookie String Directly
```powershell
python main.py --set-cookie "SESSION_COOKIE_1=val1; SESSION_COOKIE_2=val2"
```

### Option C: Manual `config/session.json` Configuration
Copy `config/session.json.example` to `config/session.json`:
```powershell
Copy-Item config\session.json.example config\session.json
```
And populate your cookies and headers.

### Option D: Automatic Chrome Cookie Extraction (Windows)
If Google Chrome is installed and you are logged in, `AuthManager` will automatically attempt to decrypt and load active cookies from your local Chrome profile.

---

## 6. How to Start the Scraper

Ensure you are connected to the internal Flipkart Network/VPN (required for `*.fkcloud.it` endpoints):
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
     * Immediately saves API #1 data to Excel.
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
4. **Excel Persistence:**
   * Combines all data.
   * Each product listing title is written as a separate row linked under that customer.
   * Confirms write to Excel $\to$ marks customer as completed in `output/progress.json`.

---

## 8. Excel Output Format
Saved at: `output/scraped_data.xlsx`

| Sr No | Customer ID | Account Name | Support Manager | Seller Tier | Signed Up Date | Live Date | Brand List (Title) | Listing Brand | Is Brand | Brand Name | Mobile Number | Registered Mobile Number | Email ID | Registered Email ID |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | ID001 | Seller ABC | No | Silver | 2021-01-10 | 2021-02-15 | Product Title 1 | BRAND_X | Possibly a Brand | BRAND_X | 9876543210 | 9876543210 | abc@mail.com | abc@mail.com |
| | ID001 | Seller ABC | No | Silver | 2021-01-10 | 2021-02-15 | Product Title 2 | BRAND_X | Possibly a Brand | BRAND_X | 9876543210 | 9876543210 | abc@mail.com | abc@mail.com |
| 2 | ID002 | Seller XYZ | Yes | Gold | 2020-05-12 | 2020-06-01 | | | | | | | | |

---

## 9. Fault Tolerance & Data Safety

### Resumability After Crash / Restart
* Progress is tracked in real-time in `output/progress.json`.
* Additionally, `main.py` scans `output/scraped_data.xlsx` upon startup.
* Any already-completed customer ID is skipped automatically.
* Customers are never duplicated, and partially completed customers are cleanly retried.

### Locked Excel File Recovery
* If `output/scraped_data.xlsx` is open in Microsoft Excel on Windows when a save occurs:
  1. The scraper catches the file lock exception.
  2. Scraped data is safely buffered into `output/pending_results.json`.
  3. The system waits and retries every `EXCEL_RETRY_INTERVAL` (default 3 seconds).
  4. Once Excel is closed or unlocked, all pending data is flushed to Excel, verified, and cleared from the buffer.
  5. **No data is lost**.

---

## 10. Logging & Monitoring

* Logs are written simultaneously to the terminal console and to `logs/scraper.log`.
* Log format: `[YYYY-MM-DD HH:MM:SS] [LEVEL] Message`
* Structured events: `START`, `Customer ID started`, `API #1 started`, `Support Manager detected`, `API #2 started`, `API #3 started`, `Excel saved`, `Customer completed`, `Session expired`, `Session refreshed`, `Retry`, `Excel locked`, `Pending save`, `END`.
* **Zero Secret Leakage:** Authentication cookies and tokens are strictly stripped and never logged.

---

## 11. Troubleshooting

1. **Authentication Error / 401 / 403:**
   * Your session cookie in `config/session.json` has expired.
   * Log in to Flipkart Seller Support in your browser, copy the active cookies from Developer Tools (Network tab), and update `config/session.json`.
2. **PermissionError: [Errno 13] Permission denied:**
   * `scraped_data.xlsx` is open in Microsoft Excel.
   * Close the file in Excel; the scraper will automatically detect the release and save buffered records.
3. **No customer IDs processed:**
   * Check `input/customer_id_input.txt` to ensure valid customer IDs are entered.
   * Check `output/progress.json` to see if those IDs have already been marked completed. Delete `progress.json` and `scraped_data.xlsx` if you want a complete fresh re-run.

