"""
API #4 Scraper: Seller Copilot GraphQL SSE (Monthly GMV Metrics).

Fetches monthly aggregated GMV metrics for the preceding 3 calendar months (excluding current month)
via GraphQL SSE stream (/sellerDashboard/napi/graphql-sse), parses the structured Table component,
Markdown tables, and tool function responses, and extracts:
- Month
- Gross Amount (GMV)
- Gross Units
- Net Amount
- Cancelled Amount
"""

import datetime
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple
import uuid

from api.api_client import APIClient, NetworkConnectionError
from auth.auth_manager import AuthExpiredError
from config.settings import API4_ENDPOINT, API4_GRAPHQL_ENDPOINT, COPILOT_SESSION_ID

logger = logging.getLogger("customer_scraper")


def get_last_three_months(base_date: Optional[datetime.date] = None) -> List[Tuple[str, int, str]]:
    """
    Computes the 3 preceding calendar months excluding the current month.

    Example: If base_date is in September 2026:
    Returns: [('June', 2026, 'June 2026'), ('July', 2026, 'July 2026'), ('August', 2026, 'August 2026')]
    """
    if base_date is None:
        base_date = datetime.date.today()

    first_of_curr = base_date.replace(day=1)
    months = []
    for i in range(3, 0, -1):
        y = first_of_curr.year
        m = first_of_curr.month - i
        while m <= 0:
            m += 12
            y -= 1
        d = datetime.date(y, m, 1)
        m_name = d.strftime("%B")
        months.append((m_name, y, f"{m_name} {y}"))
    return months


class API4Scraper:
    """
    Scraper module for API #4 (GraphQL SSE - sellerCopilot_runSseStream GMV Metrics).
    """

    def __init__(self, api_client: APIClient):
        self.api_client = api_client

    def parse_copilot_response(self, sse_text: str) -> Dict[str, str]:
        """
        Parses the raw SSE text output from sellerCopilot_runSseStream to extract GMV metrics.
        Supports:
        1. ui-json Table components (Horizontal Multi-Month and Vertical Key-Value)
        2. Markdown Table blocks (| Month | GMV | ...)
        3. Intermediate tool functionResponse CSV data (get_sellmore_aggregated_sales)
        4. Text summary patterns

        Returns:
            Dict containing:
                month: str
                gross_amount: str
                gross_units: str
                net_amount: str
                cancelled_amount: str
        """
        if not sse_text or not sse_text.strip():
            return {
                "month": "",
                "gross_amount": "",
                "gross_units": "",
                "net_amount": "",
                "cancelled_amount": "",
            }

        accumulated_text = ""
        raw_function_results: List[str] = []

        # Check if entire sse_text is already a raw JSON response
        clean_text = sse_text.strip()
        if clean_text.startswith("{") and clean_text.endswith("}"):
            try:
                raw_json = json.loads(clean_text)
                events = raw_json.get("data", {}).get("sellerCopilot_getSessionById", {}).get("events", [])
                for ev in events:
                    parts = ev.get("content", {}).get("parts", []) if isinstance(ev.get("content"), dict) else []
                    for part in parts:
                        if isinstance(part, dict):
                            t = part.get("text", "")
                            if t:
                                accumulated_text += t + "\n"
                            fn_resp = part.get("functionResponse", {}).get("response", {}).get("result", "")
                            if fn_resp:
                                raw_function_results.append(str(fn_resp))
            except Exception:
                accumulated_text += clean_text + "\n"

        # Collect text and function responses from SSE data stream lines
        for line in sse_text.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue

            data_str = line[5:].strip()
            if not data_str or data_str == "[DONE]":
                continue

            try:
                data_obj = json.loads(data_str)
            except Exception:
                continue

            stream_data = data_obj.get("data", {}).get("sellerCopilot_runSseStream", {}).get("data", {})
            content = stream_data.get("content", {})
            parts = content.get("parts", [])
            for part in parts:
                if isinstance(part, dict):
                    txt = part.get("text", "")
                    if txt:
                        accumulated_text += txt
                    fn_resp = part.get("functionResponse", {}).get("response", {}).get("result", "")
                    if fn_resp:
                        raw_function_results.append(str(fn_resp))

        # Helper formatting functions
        def format_currency(val_str: str) -> str:
            if not val_str:
                return ""
            clean = str(val_str).strip()
            if any(curr in clean for curr in ("₹", "Cr", "Lakh", "k")):
                return clean if clean.startswith("₹") else f"₹{clean}"
            try:
                num = float(clean.replace(",", ""))
                if num >= 10000000:
                    return f"₹{num / 10000000:.2f} Cr"
                elif num >= 100000:
                    return f"₹{num / 100000:.2f} Lakh"
                return f"₹{int(num):,}"
            except Exception:
                return clean

        def format_units(val_str: str) -> str:
            if not val_str:
                return ""
            clean = str(val_str).strip()
            try:
                return f"{int(float(clean.replace(',', ''))):,}"
            except Exception:
                return clean

        # 1. Parse raw functionResponse CSV metrics (get_sellmore_aggregated_sales)
        fn_metrics_by_month: Dict[str, Dict[str, str]] = {}
        if raw_function_results:
            for raw_res in raw_function_results:
                csv_lines = [l.strip() for l in raw_res.splitlines() if l.strip() and not l.startswith("=")]
                if len(csv_lines) >= 2 and "gross_amount" in csv_lines[0]:
                    header = [h.strip() for h in csv_lines[0].split(",")]
                    h_map = {h: i for i, h in enumerate(header)}

                    for r_line in csv_lines[1:]:
                        if r_line.startswith("==="):
                            break
                        parts = [p.strip() for p in r_line.split(",")]
                        if len(parts) >= len(header):
                            ts = parts[h_map.get("timestamp", -1)] if "timestamp" in h_map else ""
                            g_amt = parts[h_map.get("gross_amount", -1)] if "gross_amount" in h_map else ""
                            g_u = parts[h_map.get("gross_units", -1)] if "gross_units" in h_map else ""
                            n_amt = parts[h_map.get("net_amount", -1)] if "net_amount" in h_map else ""
                            c_amt = parts[h_map.get("cancellation_amount", -1)] if "cancellation_amount" in h_map else ""

                            m_label = ts
                            try:
                                dt = datetime.datetime.strptime(ts, "%Y-%m-%d")
                                m_label = dt.strftime("%B %Y")
                            except Exception:
                                pass

                            if m_label:
                                fn_metrics_by_month[m_label.lower()] = {
                                    "month": m_label,
                                    "gross_amount": format_currency(g_amt),
                                    "gross_units": format_units(g_u),
                                    "net_amount": format_currency(n_amt),
                                    "cancelled_amount": format_currency(c_amt),
                                }

        # 2. Parse ```ui-json Table block
        table_matches = re.findall(r"```ui-json\s*(\{[\s\S]*?\})\s*```", accumulated_text)
        for table_json_str in table_matches:
            try:
                table_obj = json.loads(table_json_str)
                if table_obj.get("component") == "Table":
                    columns = [str(c).strip() for c in table_obj.get("columns", [])]
                    cells = [str(c).strip() for c in table_obj.get("cells", [])]
                    num_cols = len(columns)

                    # Format A: Vertical Key-Value Table (e.g. columns = ["Metric", "Value"])
                    if num_cols == 2 and any("metric" in c.lower() for c in columns):
                        metric_map = {}
                        for r_start in range(0, len(cells), 2):
                            if r_start + 1 < len(cells):
                                k = cells[r_start].strip().lower()
                                v = cells[r_start + 1].strip()
                                metric_map[k] = v

                        g_amt = metric_map.get("gross gmv") or metric_map.get("gross amount") or metric_map.get("gross amount (gmv)") or metric_map.get("gmv (gross amount)") or ""
                        g_u = metric_map.get("gross units") or ""
                        n_amt = metric_map.get("net amount") or ""
                        c_amt = metric_map.get("cancellation amount") or metric_map.get("cancelled amount") or ""
                        m_val = metric_map.get("month") or ""

                        if g_amt or g_u or n_amt:
                            return {
                                "month": m_val,
                                "gross_amount": format_currency(g_amt),
                                "gross_units": format_units(g_u),
                                "net_amount": format_currency(n_amt),
                                "cancelled_amount": format_currency(c_amt),
                            }

                    # Format B: Multi-Month Horizontal Table (columns = ["Month", "GMV (Gross Amount)", ...])
                    if num_cols > 0 and len(cells) >= num_cols:
                        col_indices: Dict[str, int] = {}
                        for idx, col in enumerate(columns):
                            c_low = col.lower()
                            if "month" in c_low:
                                col_indices["month"] = idx
                            elif "gross amount" in c_low or "gmv" in c_low:
                                col_indices["gross_amount"] = idx
                            elif "gross unit" in c_low or "unit" in c_low:
                                col_indices["gross_units"] = idx
                            elif "net" in c_low:
                                col_indices["net_amount"] = idx
                            elif "cancel" in c_low:
                                col_indices["cancelled_amount"] = idx

                        months_list = []
                        gross_amt_list = []
                        gross_units_list = []
                        net_amt_list = []
                        cancelled_amt_list = []

                        for row_start in range(0, len(cells), num_cols):
                            row_cells = cells[row_start : row_start + num_cols]
                            if len(row_cells) < num_cols:
                                break

                            m_val = row_cells[col_indices["month"]] if "month" in col_indices and col_indices["month"] < len(row_cells) else ""
                            g_amt = row_cells[col_indices["gross_amount"]] if "gross_amount" in col_indices and col_indices["gross_amount"] < len(row_cells) else ""
                            g_u = row_cells[col_indices["gross_units"]] if "gross_units" in col_indices and col_indices["gross_units"] < len(row_cells) else ""
                            n_amt = row_cells[col_indices["net_amount"]] if "net_amount" in col_indices and col_indices["net_amount"] < len(row_cells) else ""
                            c_amt = row_cells[col_indices["cancelled_amount"]] if "cancelled_amount" in col_indices and col_indices["cancelled_amount"] < len(row_cells) else ""

                            # Supplement missing Net / Cancelled metrics from functionResponse if needed
                            if not n_amt or not c_amt:
                                m_key = m_val.lower()
                                for fn_key, fn_vals in fn_metrics_by_month.items():
                                    if fn_key in m_key or m_key in fn_key:
                                        if not n_amt and fn_vals.get("net_amount"):
                                            n_amt = fn_vals["net_amount"]
                                        if not c_amt and fn_vals.get("cancelled_amount"):
                                            c_amt = fn_vals["cancelled_amount"]
                                        break

                            months_list.append(m_val)
                            gross_amt_list.append(format_currency(g_amt))
                            gross_units_list.append(format_units(g_u))
                            net_amt_list.append(format_currency(n_amt))
                            cancelled_amt_list.append(format_currency(c_amt))

                        if gross_amt_list:
                            return {
                                "month": " | ".join(months_list),
                                "gross_amount": " | ".join(gross_amt_list),
                                "gross_units": " | ".join(gross_units_list),
                                "net_amount": " | ".join(net_amt_list),
                                "cancelled_amount": " | ".join(cancelled_amt_list),
                            }
            except Exception as e:
                logger.debug("Failed parsing ui-json Table block: %s", str(e))

        # 3. Parse Markdown Table block (| Month | Gross Amount | ...)
        md_table_lines = [l.strip() for l in accumulated_text.splitlines() if l.strip().startswith("|") and l.strip().endswith("|")]
        if len(md_table_lines) >= 2:
            try:
                header_parts = [p.strip() for p in md_table_lines[0].split("|")[1:-1]]
                if any("month" in h.lower() for h in header_parts) and any("gross" in h.lower() or "gmv" in h.lower() for h in header_parts):
                    h_indices: Dict[str, int] = {}
                    for idx, h in enumerate(header_parts):
                        h_low = h.lower()
                        if "month" in h_low:
                            h_indices["month"] = idx
                        elif "gross" in h_low or "gmv" in h_low:
                            h_indices["gross_amount"] = idx
                        elif "unit" in h_low:
                            h_indices["gross_units"] = idx
                        elif "net" in h_low:
                            h_indices["net_amount"] = idx
                        elif "cancel" in h_low:
                            h_indices["cancelled_amount"] = idx

                    m_list, g_list, u_list, n_list, c_list = [], [], [], [], []
                    for row_line in md_table_lines[1:]:
                        if "---" in row_line:
                            continue
                        row_parts = [p.strip() for p in row_line.split("|")[1:-1]]
                        if len(row_parts) >= len(header_parts):
                            m_val = row_parts[h_indices["month"]] if "month" in h_indices else ""
                            g_amt = row_parts[h_indices["gross_amount"]] if "gross_amount" in h_indices else ""
                            g_u = row_parts[h_indices["gross_units"]] if "gross_units" in h_indices else ""
                            n_amt = row_parts[h_indices["net_amount"]] if "net_amount" in h_indices else ""
                            c_amt = row_parts[h_indices["cancelled_amount"]] if "cancelled_amount" in h_indices else ""

                            if m_val and g_amt:
                                m_list.append(m_val)
                                g_list.append(format_currency(g_amt))
                                u_list.append(format_units(g_u))
                                n_list.append(format_currency(n_amt))
                                c_list.append(format_currency(c_amt))

                    if g_list:
                        return {
                            "month": " | ".join(m_list),
                            "gross_amount": " | ".join(g_list),
                            "gross_units": " | ".join(u_list),
                            "net_amount": " | ".join(n_list),
                            "cancelled_amount": " | ".join(c_list),
                        }
            except Exception as md_err:
                logger.debug("Failed parsing markdown table: %s", str(md_err))

        # 4. Fallback: Directly use extracted functionResponse metrics from API
        if fn_metrics_by_month:
            months_list = [v["month"] for v in fn_metrics_by_month.values()]
            gross_amt_list = [v["gross_amount"] for v in fn_metrics_by_month.values()]
            gross_units_list = [v["gross_units"] for v in fn_metrics_by_month.values()]
            net_amt_list = [v["net_amount"] for v in fn_metrics_by_month.values()]
            cancelled_amt_list = [v["cancelled_amount"] for v in fn_metrics_by_month.values()]

            return {
                "month": " | ".join(months_list),
                "gross_amount": " | ".join(gross_amt_list),
                "gross_units": " | ".join(gross_units_list),
                "net_amount": " | ".join(net_amt_list),
                "cancelled_amount": " | ".join(cancelled_amt_list),
            }

        return {
            "month": "",
            "gross_amount": "",
            "gross_units": "",
            "net_amount": "",
            "cancelled_amount": "",
        }

    def get_seller_gmv_metrics(self, customer_id: str, base_date: Optional[datetime.date] = None) -> Dict[str, str]:
        """
        Executes API #4 (Setu Copilot: CreateSession -> RunSseStream) for a seller and returns the 5 GMV metrics.
        Follows the exact 2-step sequence from Flipkart portal:
        1. SellerCopilotCreateSession: Creates and registers a new session on Flipkart backend.
        2. sellerCopilot_runSseStream: Streams the GMV query via GraphQL SSE using the created session ID.

        Returns:
            Dict containing:
                month: str
                gross_amount: str
                gross_units: str
                net_amount: str
                cancelled_amount: str
        """
        months = get_last_three_months(base_date=base_date)
        m_names = [m[0] for m in months]
        prompt_text = f"GMV for {m_names[0]} {m_names[1]} and {m_names[2]} month"
        display_name = f"GMV Data for last {m_names[0]} {m_names[1]} and {m_names[2]} month"

        endpoint = API4_ENDPOINT.format(customer_id=customer_id)
        graphql_endpoint = API4_GRAPHQL_ENDPOINT.format(customer_id=customer_id)
        logger.info("API #4 (Setu Copilot) started for %s (Prompt: '%s')", customer_id, prompt_text)

        print("\n" + "=" * 30 + f" [DEBUGGING 4TH API START: {customer_id}] " + "=" * 30)
        print(f"🔗 [API #4 Endpoint] {endpoint}")
        print(f"📝 [API #4 Prompt]   \"{prompt_text}\"")

        # Step 1: Call SellerCopilotCreateSession on Flipkart backend
        create_session_payload = {
            "operationName": "SellerCopilotCreateSession",
            "variables": {
                "appName": "setu_orchestrator_suv",
                "displayName": display_name,
            },
            "query": "query SellerCopilotCreateSession($appName: String!, $displayName: String) {\n  sellerCopilot_createSession(appName: $appName, displayName: $displayName) {\n    id\n    appName\n    userId\n    __typename\n  }\n}\n",
        }
        create_session_headers = {
            "accept": "*/*",
            "content-type": "application/json",
            "operation": "query",
            "operation-name": "SellerCopilotCreateSession",
            "Origin": "https://suv-flipkart.seller-support.fkcloud.it",
            "Referer": f"https://suv-flipkart.seller-support.fkcloud.it/sellerDashboard/index.html?sellerId={customer_id}#dashboard/settings",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "sec-ch-ua": '"Google Chrome";v="153", "Not_A Brand";v="8", "Chromium";v="153"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "x-internal-env-type": "WEB",
            "x-requested-with": "XMLHttpRequest",
        }

        created_session_id = ""
        try:
            print(f"🔄 [API #4 Step 1] Calling SellerCopilotCreateSession on {graphql_endpoint}...")
            session_resp = self.api_client.post(
                endpoint_or_url=graphql_endpoint,
                json_data=create_session_payload,
                headers=create_session_headers,
                timeout=(8, 15),
            )
            if isinstance(session_resp, dict):
                created_session_id = session_resp.get("data", {}).get("sellerCopilot_createSession", {}).get("id", "")
                if created_session_id:
                    print(f"✅ [API #4 Step 1] Created valid backend session ID: {created_session_id}")
                else:
                    print(f"ℹ️ [API #4 Step 1] CreateSession response: {session_resp}")
        except Exception as cs_err:
            print(f"⚠️ [API #4 Step 1 Warning] CreateSession error: {str(cs_err)}")

        if not created_session_id:
            created_session_id = COPILOT_SESSION_ID or str(uuid.uuid4())
            print(f"ℹ️ [API #4 Step 1 Fallback] Using fallback session ID: {created_session_id}")

        # Step 2: Stream prompt through GraphQL SSE (sellerCopilot_runSseStream)
        payload = {
            "query": "subscription sellerCopilot_runSseStream($input: RunSseInput!) {\n  sellerCopilot_runSseStream(input: $input) {\n    data\n  }\n}\n",
            "variables": {
                "input": {
                    "appName": "setu_orchestrator_suv",
                    "sessionId": created_session_id,
                    "newMessage": {
                        "role": "user",
                        "parts": [{"text": prompt_text}],
                    },
                    "stateDelta": {"client_surface": "web"},
                }
            },
            "operationName": "sellerCopilot_runSseStream",
        }

        headers = {
            "Accept": "text/event-stream",
            "Content-Type": "application/json; charset=utf-8",
            "Origin": "https://suv-flipkart.seller-support.fkcloud.it",
            "Referer": f"https://suv-flipkart.seller-support.fkcloud.it/sellerDashboard/index.html?sellerId={customer_id}#dashboard/settings",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "sec-ch-ua": '"Google Chrome";v="153", "Not_A Brand";v="8", "Chromium";v="153"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "x-internal-env-type": "WEB",
            "x-requested-with": "XMLHttpRequest",
            "operation": "subscription",
            "operation-name": "sellerCopilot_runSseStream",
        }

        print(f"📦 [API #4 Variables] {json.dumps(payload.get('variables', {}), indent=2)}")

        sse_response_text = ""
        try:
            sse_response_text = self.api_client.post_sse_stream(
                endpoint_or_url=endpoint,
                json_data=payload,
                headers=headers,
                timeout=(10, 60),
            )
        except Exception as e:
            print(f"⚠️ [API #4 ERROR] Encountered issue for seller {customer_id}: {str(e)}")
            logger.warning("API #4 encountered an error for customer %s (%s).", customer_id, str(e))
            sse_response_text = ""

        if sse_response_text:
            print(f"📋 [API #4 Raw Stream Response Preview ({len(sse_response_text)} chars)]:\n{sse_response_text[:400]}...")
        else:
            print("ℹ️ [API #4 Response] Empty response or no SSE stream data received.")

        metrics = self.parse_copilot_response(sse_response_text)
        print(f"📊 [API #4 Parsed Metrics]:")
        print(f"   • Month:            {metrics.get('month') or '(empty)'}")
        print(f"   • Gross Amount GMV: {metrics.get('gross_amount') or '(empty)'}")
        print(f"   • Gross Units:      {metrics.get('gross_units') or '(empty)'}")
        print(f"   • Net Amount:       {metrics.get('net_amount') or '(empty)'}")
        print(f"   • Cancelled Amount: {metrics.get('cancelled_amount') or '(empty)'}")
        print("=" * 30 + f" [DEBUGGING 4TH API END: {customer_id}] " + "=" * 30 + "\n")

        logger.info(
            "API #4 parsed for %s -> Month: '%s', GMV: '%s', Net: '%s'",
            customer_id,
            metrics.get("month") or "-",
            metrics.get("gross_amount") or "-",
            metrics.get("net_amount") or "-",
        )
        return metrics

    def parse_brand_listing_response(self, sse_text: str, brand_names: Optional[List[str]] = None) -> Dict[str, Dict[str, str]]:
        """
        Parses the SSE stream response from sellerCopilot_runSseStream for the prompt:
        "What is the active listing count for the <brands> along with variation"

        Extracts per-brand metrics:
        - active_listings (e.g. "70+", "Active", "0")
        - suppressed_listings (e.g. "30", "0")
        - variants_available (e.g. "WeldingMachine, PowerDrill, HandToolKit, Paint Sprayer, Heat Gun")

        Returns:
            Dict mapping brand_key (or brand_name) -> {
                "active_listings": str,
                "suppressed_listings": str,
                "variants_available": str,
            }
        """
        if not sse_text or not sse_text.strip():
            return {}

        accumulated_text = ""
        raw_function_results: List[str] = []

        # Check if entire sse_text is a raw JSON response
        clean_text = sse_text.strip()
        if clean_text.startswith("{") and clean_text.endswith("}"):
            try:
                raw_json = json.loads(clean_text)
                events = raw_json.get("data", {}).get("sellerCopilot_getSessionById", {}).get("events", [])
                for ev in events:
                    parts = ev.get("content", {}).get("parts", []) if isinstance(ev.get("content"), dict) else []
                    for part in parts:
                        if isinstance(part, dict):
                            t = part.get("text", "")
                            if t:
                                accumulated_text += t + "\n"
                            fn_resp = part.get("functionResponse", {}).get("response", {}).get("result", "")
                            if fn_resp:
                                raw_function_results.append(str(fn_resp))
            except Exception:
                accumulated_text += clean_text + "\n"

        # Collect text from SSE data stream lines
        for line in sse_text.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue

            data_str = line[5:].strip()
            if not data_str or data_str == "[DONE]":
                continue

            try:
                data_obj = json.loads(data_str)
            except Exception:
                continue

            stream_data = data_obj.get("data", {}).get("sellerCopilot_runSseStream", {}).get("data", {})
            content = stream_data.get("content", {})
            parts = content.get("parts", [])
            for part in parts:
                if isinstance(part, dict):
                    txt = part.get("text", "")
                    if txt:
                        accumulated_text += txt
                    fn_resp = part.get("functionResponse", {}).get("response", {}).get("result", "")
                    if fn_resp:
                        raw_function_results.append(str(fn_resp))

        results: Dict[str, Dict[str, str]] = {}

        # 1. Parse ```ui-json Table block
        table_matches = re.findall(r"```ui-json\s*(\{[\s\S]*?\})\s*```", accumulated_text)
        for table_json_str in table_matches:
            try:
                table_obj = json.loads(table_json_str)
                if table_obj.get("component") == "Table":
                    columns = [str(c).strip() for c in table_obj.get("columns", [])]
                    cells = [str(c).strip() for c in table_obj.get("cells", [])]
                    num_cols = len(columns)

                    col_indices: Dict[str, int] = {}
                    for idx, col in enumerate(columns):
                        c_low = col.lower()
                        if "brand" in c_low:
                            col_indices["brand"] = idx
                        elif "active" in c_low:
                            col_indices["active_listings"] = idx
                        elif "suppress" in c_low:
                            col_indices["suppressed_listings"] = idx
                        elif any(k in c_low for k in ("variant", "vertical", "key", "available")):
                            col_indices["variants_available"] = idx

                    if "brand" in col_indices and num_cols > 0:
                        for row_start in range(0, len(cells), num_cols):
                            row_cells = cells[row_start : row_start + num_cols]
                            if len(row_cells) < num_cols:
                                break

                            b_val = row_cells[col_indices["brand"]] if col_indices["brand"] < len(row_cells) else ""
                            act_val = row_cells[col_indices["active_listings"]] if "active_listings" in col_indices and col_indices["active_listings"] < len(row_cells) else ""
                            sup_val = row_cells[col_indices["suppressed_listings"]] if "suppressed_listings" in col_indices and col_indices["suppressed_listings"] < len(row_cells) else ""
                            var_val = row_cells[col_indices["variants_available"]] if "variants_available" in col_indices and col_indices["variants_available"] < len(row_cells) else ""

                            if b_val:
                                results[b_val.strip()] = {
                                    "active_listings": act_val.strip(),
                                    "suppressed_listings": sup_val.strip(),
                                    "variants_available": var_val.strip(),
                                }
            except Exception as e:
                logger.debug("Failed parsing ui-json Table in brand listing response: %s", str(e))

        # 2. Parse Markdown Table block (| Brand | Active Listings | Suppressed Listings | Key Verticals/Variants Available |)
        if not results:
            md_table_lines = [l.strip() for l in accumulated_text.splitlines() if l.strip().startswith("|") and l.strip().endswith("|")]
            if len(md_table_lines) >= 2:
                try:
                    header_parts = [p.strip() for p in md_table_lines[0].split("|")[1:-1]]
                    col_indices = {}
                    for idx, h in enumerate(header_parts):
                        h_low = h.lower()
                        if "brand" in h_low:
                            col_indices["brand"] = idx
                        elif "active" in h_low:
                            col_indices["active_listings"] = idx
                        elif "suppress" in h_low:
                            col_indices["suppressed_listings"] = idx
                        elif any(k in h_low for k in ("variant", "vertical", "key", "available")):
                            col_indices["variants_available"] = idx

                    if "brand" in col_indices:
                        for row_line in md_table_lines[1:]:
                            if "---" in row_line:
                                continue
                            row_parts = [p.strip() for p in row_line.split("|")[1:-1]]
                            if len(row_parts) >= len(header_parts):
                                b_val = row_parts[col_indices["brand"]] if col_indices["brand"] < len(row_parts) else ""
                                act_val = row_parts[col_indices["active_listings"]] if "active_listings" in col_indices and col_indices["active_listings"] < len(row_parts) else ""
                                sup_val = row_parts[col_indices["suppressed_listings"]] if "suppressed_listings" in col_indices and col_indices["suppressed_listings"] < len(row_parts) else ""
                                var_val = row_parts[col_indices["variants_available"]] if "variants_available" in col_indices and col_indices["variants_available"] < len(row_parts) else ""

                                if b_val:
                                    results[b_val.strip()] = {
                                        "active_listings": act_val.strip(),
                                        "suppressed_listings": sup_val.strip(),
                                        "variants_available": var_val.strip(),
                                    }
                except Exception as md_err:
                    logger.debug("Failed parsing markdown table in brand listing response: %s", str(md_err))

        # 3. Fallback: Parse bullet/paragraph text summaries if table was not found
        if not results and brand_names:
            for b_name in brand_names:
                b_clean = b_name.strip()
                if not b_clean:
                    continue
                pattern = rf"{re.escape(b_clean)}[\s\S]*?(?=(?:[A-Z][a-zA-Z0-9_\s]+ Brand|\Z))"
                match = re.search(pattern, accumulated_text, re.IGNORECASE)
                if match:
                    snippet = match.group(0)
                    act_m = re.search(r"(\d+\+?|\bactive\b)\s+active listings?", snippet, re.IGNORECASE)
                    sup_m = re.search(r"(\d+)\s+listings?[\s\w]*?suppressed", snippet, re.IGNORECASE)

                    act_val = act_m.group(1) if act_m else ("0" if "zero active" in snippet.lower() or "no active" in snippet.lower() else "")
                    sup_val = sup_m.group(1) if sup_m else ("0" if "0" in snippet or "zero" in snippet.lower() else "")

                    results[b_clean] = {
                        "active_listings": act_val,
                        "suppressed_listings": sup_val,
                        "variants_available": snippet.strip()[:200],
                    }

        return results

    def get_brand_listing_metrics(self, customer_id: str, brand_names: List[str]) -> Dict[str, Dict[str, str]]:
        """
        Calls Setu Copilot AI chatbot for the prompt:
        "What is the active listing count for the <brand names> along with variation"

        Returns:
            Dict mapping brand name -> {"active_listings": str, "suppressed_listings": str, "variants_available": str}
        """
        valid_brands = [b.strip() for b in brand_names if b and str(b).strip() and str(b).strip().lower() not in ("null", "none")]
        if not valid_brands:
            return {}

        brand_query_str = ", ".join(valid_brands)
        prompt_text = f"What is the active listing count for the {brand_query_str} along with variation"
        display_name = f"Brand Listing Count for {brand_query_str}"

        endpoint = API4_ENDPOINT.format(customer_id=customer_id)
        graphql_endpoint = API4_GRAPHQL_ENDPOINT.format(customer_id=customer_id)
        logger.info("API #4 (Brand Listing Count) started for %s (Prompt: '%s')", customer_id, prompt_text)

        print("\n" + "=" * 30 + f" [DEBUGGING BRAND LISTING AI START: {customer_id}] " + "=" * 30)
        print(f"🔗 [API #4 Endpoint] {endpoint}")
        print(f"📝 [AI Brand Prompt] \"{prompt_text}\"")

        # Step 1: Create Session
        create_session_payload = {
            "operationName": "SellerCopilotCreateSession",
            "variables": {
                "appName": "setu_orchestrator_suv",
                "displayName": display_name,
            },
            "query": "query SellerCopilotCreateSession($appName: String!, $displayName: String) {\n  sellerCopilot_createSession(appName: $appName, displayName: $displayName) {\n    id\n    appName\n    userId\n    __typename\n  }\n}\n",
        }
        create_session_headers = {
            "accept": "*/*",
            "content-type": "application/json",
            "operation": "query",
            "operation-name": "SellerCopilotCreateSession",
            "Origin": "https://suv-flipkart.seller-support.fkcloud.it",
            "Referer": f"https://suv-flipkart.seller-support.fkcloud.it/sellerDashboard/index.html?sellerId={customer_id}#dashboard/settings",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "sec-ch-ua": '"Google Chrome";v="153", "Not_A Brand";v="8", "Chromium";v="153"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "x-internal-env-type": "WEB",
            "x-requested-with": "XMLHttpRequest",
        }

        created_session_id = ""
        try:
            session_resp = self.api_client.post(
                endpoint_or_url=graphql_endpoint,
                json_data=create_session_payload,
                headers=create_session_headers,
                timeout=(8, 15),
            )
            if isinstance(session_resp, dict):
                created_session_id = session_resp.get("data", {}).get("sellerCopilot_createSession", {}).get("id", "")
        except Exception as cs_err:
            logger.debug("CreateSession error: %s", str(cs_err))

        if not created_session_id:
            created_session_id = COPILOT_SESSION_ID or str(uuid.uuid4())

        # Step 2: Stream prompt through GraphQL SSE (sellerCopilot_runSseStream)
        payload = {
            "query": "subscription sellerCopilot_runSseStream($input: RunSseInput!) {\n  sellerCopilot_runSseStream(input: $input) {\n    data\n  }\n}\n",
            "variables": {
                "input": {
                    "appName": "setu_orchestrator_suv",
                    "sessionId": created_session_id,
                    "newMessage": {
                        "role": "user",
                        "parts": [{"text": prompt_text}],
                    },
                    "stateDelta": {"client_surface": "web"},
                }
            },
            "operationName": "sellerCopilot_runSseStream",
        }

        headers = {
            "Accept": "text/event-stream",
            "Content-Type": "application/json; charset=utf-8",
            "Origin": "https://suv-flipkart.seller-support.fkcloud.it",
            "Referer": f"https://suv-flipkart.seller-support.fkcloud.it/sellerDashboard/index.html?sellerId={customer_id}#dashboard/settings",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "sec-ch-ua": '"Google Chrome";v="153", "Not_A Brand";v="8", "Chromium";v="153"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "x-internal-env-type": "WEB",
            "x-requested-with": "XMLHttpRequest",
            "operation": "subscription",
            "operation-name": "sellerCopilot_runSseStream",
        }

        sse_response_text = ""
        try:
            sse_response_text = self.api_client.post_sse_stream(
                endpoint_or_url=endpoint,
                json_data=payload,
                headers=headers,
                timeout=(10, 60),
            )
        except Exception as e:
            logger.warning("API #4 (Brand Listing Count) error for customer %s (%s).", customer_id, str(e))
            sse_response_text = ""

        parsed_brand_metrics = self.parse_brand_listing_response(sse_response_text, valid_brands)
        print(f"📊 [AI Brand Metrics Parsed]:")
        for b, m in parsed_brand_metrics.items():
            print(f"   • {b}: Active={m.get('active_listings')}, Suppressed={m.get('suppressed_listings')}, Variants={m.get('variants_available')}")
        print("=" * 30 + f" [DEBUGGING BRAND LISTING AI END: {customer_id}] " + "=" * 30 + "\n")

        return parsed_brand_metrics


def match_brand_listing_metrics(brand_name: str, brand_metrics_map: Dict[str, Dict[str, str]]) -> Dict[str, str]:
    """
    Finds the matching listing metrics for a given brand name from brand_metrics_map
    using exact, case-insensitive, or substring matching.
    """
    if not brand_name or not brand_metrics_map:
        return {"active_listings": "", "suppressed_listings": "", "variants_available": ""}

    b_clean = brand_name.strip().lower()

    # Exact case-insensitive match
    for k, v in brand_metrics_map.items():
        if k.strip().lower() == b_clean:
            return v

    # Substring / containment match (e.g. 'Vormir (Vormar)' matches 'Vormar' or 'Vormir')
    for k, v in brand_metrics_map.items():
        k_clean = k.strip().lower()
        if b_clean in k_clean or k_clean in b_clean:
            return v

    # Token match
    b_tokens = set(re.findall(r"\w+", b_clean))
    for k, v in brand_metrics_map.items():
        k_tokens = set(re.findall(r"\w+", k.strip().lower()))
        if b_tokens and k_tokens and (b_tokens.intersection(k_tokens)):
            return v

    return {"active_listings": "", "suppressed_listings": "", "variants_available": ""}

