"""
API #4 Scraper: Seller Copilot GraphQL SSE (Monthly GMV Metrics).

Fetches monthly aggregated GMV metrics for the preceding 3 calendar months (excluding current month)
via GraphQL SSE stream (/sellerDashboard/napi/graphql-sse), parses the structured Table component
from the streaming response, and extracts:
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
from typing import Any, Dict, List, Optional, Tuple
import uuid

from api.api_client import APIClient, NetworkConnectionError
from auth.auth_manager import AuthExpiredError
from config.settings import API4_ENDPOINT

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
        Parses the raw SSE text output from sellerCopilot_runSseStream to extract GMV table metrics.

        Returns:
            Dict containing:
                month: str (e.g. "June 2026 | July 2026 | August 2026")
                gross_amount: str (e.g. "₹915 | ₹1,603 | ₹6,313")
                gross_units: str (e.g. "1 | 1 | 4")
                net_amount: str (e.g. "₹0 | ₹1,603 | ₹3,082")
                cancelled_amount: str (e.g. "₹915 | ₹0 | ₹4,692")
        """
        if not sse_text or not sse_text.strip():
            return {
                "month": "",
                "gross_amount": "",
                "gross_units": "",
                "net_amount": "",
                "cancelled_amount": "",
            }

        # 1. Collect all complete model message texts from SSE data lines
        accumulated_text = ""
        raw_function_results: List[str] = []

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
                    # Check for direct text
                    txt = part.get("text", "")
                    if txt:
                        accumulated_text += txt
                    # Check for tool function response
                    fn_resp = part.get("functionResponse", {}).get("response", {}).get("result", "")
                    if fn_resp:
                        raw_function_results.append(str(fn_resp))

        # 2. Primary parsing: Extract ```ui-json Table block
        table_match = re.search(r"```ui-json\s*(\{[\s\S]*?\})\s*```", accumulated_text)
        if table_match:
            try:
                table_json_str = table_match.group(1)
                table_obj = json.loads(table_json_str)
                if table_obj.get("component") == "Table":
                    columns = [str(c).strip() for c in table_obj.get("columns", [])]
                    cells = [str(c).strip() for c in table_obj.get("cells", [])]
                    num_cols = len(columns)
                    if num_cols > 0 and len(cells) >= num_cols:
                        # Map columns by lowercase name
                        col_indices: Dict[str, int] = {}
                        for idx, col in enumerate(columns):
                            c_low = col.lower()
                            if "month" in c_low:
                                col_indices["month"] = idx
                            elif "gross amount" in c_low or "gmv" in c_low:
                                col_indices["gross_amount"] = idx
                            elif "gross unit" in c_low:
                                col_indices["gross_units"] = idx
                            elif "net amount" in c_low:
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
                            g_unit = row_cells[col_indices["gross_units"]] if "gross_units" in col_indices and col_indices["gross_units"] < len(row_cells) else ""
                            n_amt = row_cells[col_indices["net_amount"]] if "net_amount" in col_indices and col_indices["net_amount"] < len(row_cells) else ""
                            c_amt = row_cells[col_indices["cancelled_amount"]] if "cancelled_amount" in col_indices and col_indices["cancelled_amount"] < len(row_cells) else ""

                            months_list.append(m_val)
                            gross_amt_list.append(g_amt)
                            gross_units_list.append(g_unit)
                            net_amt_list.append(n_amt)
                            cancelled_amt_list.append(c_amt)

                        print(f"✨ [API #4] Successfully parsed Table with {len(months_list)} month(s) of GMV data.")
                        return {
                            "month": " | ".join(months_list),
                            "gross_amount": " | ".join(gross_amt_list),
                            "gross_units": " | ".join(gross_units_list),
                            "net_amount": " | ".join(net_amt_list),
                            "cancelled_amount": " | ".join(cancelled_amt_list),
                        }
            except Exception as e:
                logger.debug("Failed parsing ui-json Table block: %s", str(e))

        # 3. Fallback parsing: If ui-json block wasn't found, check raw functionResponse CSV data
        if raw_function_results:
            months_list = []
            gross_amt_list = []
            gross_units_list = []
            net_amt_list = []
            cancelled_amt_list = []

            for raw_res in raw_function_results:
                csv_lines = [l.strip() for l in raw_res.splitlines() if l.strip() and not l.startswith("=")]
                if len(csv_lines) >= 2 and "gross_amount" in csv_lines[0]:
                    header = [h.strip() for h in csv_lines[0].split(",")]
                    h_map = {h: i for i, h in enumerate(header)}

                    # If monthly interval
                    if "MONTH" in raw_res or "monthly" in raw_res.lower():
                        for r_line in csv_lines[1:]:
                            if r_line.startswith("==="):
                                break
                            parts = [p.strip() for p in r_line.split(",")]
                            if len(parts) >= len(header):
                                ts = parts[h_map.get("timestamp", -1)] if "timestamp" in h_map else ""
                                g_amt = parts[h_map.get("gross_amount", -1)] if "gross_amount" in h_map else "0"
                                g_u = parts[h_map.get("gross_units", -1)] if "gross_units" in h_map else "0"
                                n_amt = parts[h_map.get("net_amount", -1)] if "net_amount" in h_map else "0"
                                c_amt = parts[h_map.get("cancellation_amount", -1)] if "cancellation_amount" in h_map else "0"

                                m_label = ts
                                try:
                                    dt = datetime.datetime.strptime(ts, "%Y-%m-%d")
                                    m_label = dt.strftime("%B %Y")
                                except Exception:
                                    pass

                                months_list.append(m_label)
                                gross_amt_list.append(f"₹{int(float(g_amt)):,}" if g_amt.replace(".", "", 1).isdigit() else g_amt)
                                gross_units_list.append(str(int(float(g_u))) if g_u.replace(".", "", 1).isdigit() else g_u)
                                net_amt_list.append(f"₹{int(float(n_amt)):,}" if n_amt.replace(".", "", 1).isdigit() else n_amt)
                                cancelled_amt_list.append(f"₹{int(float(c_amt)):,}" if c_amt.replace(".", "", 1).isdigit() else c_amt)

            if months_list:
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
        Executes API #4 (Setu Copilot SSE stream) for a seller and returns the 5 GMV metrics.

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
        prompt_text = f"{m_names[0]}, {m_names[1]} and {m_names[2]} GMV Data"

        endpoint = API4_ENDPOINT.format(customer_id=customer_id)
        logger.info("API #4 (Setu Copilot SSE) started for %s (Prompt: '%s')", customer_id, prompt_text)

        payload = {
            "query": "subscription sellerCopilot_runSseStream($input: RunSseInput!) {\n  sellerCopilot_runSseStream(input: $input) {\n    data\n  }\n}",
            "variables": {
                "input": {
                    "appName": "setu_orchestrator_suv",
                    "sessionId": str(uuid.uuid4()),
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
            "x-internal-env-type": "WEB",
            "x-requested-with": "XMLHttpRequest",
            "operation": "subscription",
            "operation-name": "sellerCopilot_runSseStream",
        }

        print(f"🤖 [API #4] Prompt: '{prompt_text}' for seller '{customer_id}'")
        try:
            sse_response_text = self.api_client.post_sse_stream(
                endpoint_or_url=endpoint,
                json_data=payload,
                headers=headers,
            )
        except (AuthExpiredError, NetworkConnectionError):
            raise
        except Exception as e:
            print(f"⚠️ [API #4] Error fetching SSE for {customer_id}: {str(e)}")
            logger.warning("API #4 encountered an error for customer %s (%s). Proceeding with empty metrics.", customer_id, str(e))
            sse_response_text = ""

        metrics = self.parse_copilot_response(sse_response_text)
        print(f"📊 [API #4 Result] Month: '{metrics.get('month') or '-'}' | GMV: '{metrics.get('gross_amount') or '-'}' | Net: '{metrics.get('net_amount') or '-'}'")
        logger.info(
            "API #4 parsed for %s -> Month: '%s', GMV: '%s', Net: '%s'",
            customer_id,
            metrics.get("month") or "-",
            metrics.get("gross_amount") or "-",
            metrics.get("net_amount") or "-",
        )
        return metrics
