"""
Unit tests for API #4 (Setu Copilot GraphQL SSE - 3-Month GMV Metrics Scraper).
"""

import datetime
import json
import unittest
from unittest.mock import MagicMock

from api.api_client import APIClient
from scrapers.api4_scraper import API4Scraper, get_last_three_months


class TestAPI4Scraper(unittest.TestCase):
    def setUp(self):
        self.mock_client = MagicMock(spec=APIClient)
        self.scraper = API4Scraper(self.mock_client)

    def test_get_last_three_months_september(self):
        """Test calculation of 3 preceding months for September 2026."""
        sept = datetime.date(2026, 9, 17)
        months = get_last_three_months(sept)
        self.assertEqual(len(months), 3)
        self.assertEqual(months[0], ("June", 2026, "June 2026"))
        self.assertEqual(months[1], ("July", 2026, "July 2026"))
        self.assertEqual(months[2], ("August", 2026, "August 2026"))

    def test_get_last_three_months_january_year_wrap(self):
        """Test calculation of 3 preceding months across year boundary (Jan 2026 -> Oct, Nov, Dec 2025)."""
        jan = datetime.date(2026, 1, 15)
        months = get_last_three_months(jan)
        self.assertEqual(len(months), 3)
        self.assertEqual(months[0], ("October", 2025, "October 2025"))
        self.assertEqual(months[1], ("November", 2025, "November 2025"))
        self.assertEqual(months[2], ("December", 2025, "December 2025"))

    def test_parse_copilot_response_ui_json_table(self):
        """Test parsing ```ui-json Table block from SSE response stream."""
        table_component = {
            "component": "Table",
            "columns": ["Month", "Gross Amount (GMV)", "Gross Units", "Net Amount", "Cancelled Amount"],
            "cells": [
                "June 2026", "₹915", "1", "₹0", "₹915",
                "July 2026", "₹1,603", "1", "₹1,603", "₹0",
                "August 2026", "₹6,313", "4", "₹3,082", "₹4,692"
            ]
        }
        ui_json_text = f"Here is the data:\n```ui-json\n{json.dumps(table_component)}\n```\nHope this helps!"

        sse_data = {
            "data": {
                "sellerCopilot_runSseStream": {
                    "data": {
                        "content": {
                            "parts": [
                                {"text": ui_json_text}
                            ]
                        }
                    }
                }
            }
        }

        raw_sse = f"event: next\ndata: {json.dumps(sse_data)}\n\nevent: complete\ndata: [DONE]\n"

        metrics = self.scraper.parse_copilot_response(raw_sse)

        self.assertEqual(metrics["month"], "June 2026 | July 2026 | August 2026")
        self.assertEqual(metrics["gross_amount"], "₹915 | ₹1,603 | ₹6,313")
        self.assertEqual(metrics["gross_units"], "1 | 1 | 4")
        self.assertEqual(metrics["net_amount"], "₹0 | ₹1,603 | ₹3,082")
        self.assertEqual(metrics["cancelled_amount"], "₹915 | ₹0 | ₹4,692")

    def test_parse_copilot_response_empty_or_no_table(self):
        """Test fallback when SSE output is empty or missing ui-json block."""
        metrics_empty = self.scraper.parse_copilot_response("")
        self.assertEqual(metrics_empty["month"], "")
        self.assertEqual(metrics_empty["gross_amount"], "")
        self.assertEqual(metrics_empty["gross_units"], "")
        self.assertEqual(metrics_empty["net_amount"], "")
        self.assertEqual(metrics_empty["cancelled_amount"], "")

        # SSE without ui-json
        sse_data = {
            "data": {
                "sellerCopilot_runSseStream": {
                    "data": {
                        "content": {
                            "parts": [
                                {"text": "I could not find any GMV data for the requested seller."}
                            ]
                        }
                    }
                }
            }
        }
        raw_sse = f"data: {json.dumps(sse_data)}\n"
        metrics_no_table = self.scraper.parse_copilot_response(raw_sse)
        self.assertEqual(metrics_no_table["month"], "")
        self.assertEqual(metrics_no_table["gross_amount"], "")

    def test_get_seller_gmv_metrics_payload_and_call(self):
        """Test get_seller_gmv_metrics constructs correct prompt and headers."""
        table_component = {
            "component": "Table",
            "columns": ["Month", "Gross Amount (GMV)", "Gross Units", "Net Amount", "Cancelled Amount"],
            "cells": [
                "June 2026", "₹5,000", "5", "₹4,000", "₹1,000",
            ]
        }
        ui_json_text = f"```ui-json\n{json.dumps(table_component)}\n```"
        sse_data = {
            "data": {
                "sellerCopilot_runSseStream": {
                    "data": {
                        "content": {
                            "parts": [{"text": ui_json_text}]
                        }
                    }
                }
            }
        }
        self.mock_client.post_sse_stream.return_value = f"data: {json.dumps(sse_data)}\n"

        base_date = datetime.date(2026, 9, 1)
        res = self.scraper.get_seller_gmv_metrics("40384a72b90d496c", base_date=base_date)

        self.assertEqual(res["month"], "June 2026")
        self.assertEqual(res["gross_amount"], "₹5,000")
        self.assertEqual(res["gross_units"], "5")
        self.assertEqual(res["net_amount"], "₹4,000")
        self.assertEqual(res["cancelled_amount"], "₹1,000")

        # Verify call arguments
        self.mock_client.post_sse_stream.assert_called_once()
        args, kwargs = self.mock_client.post_sse_stream.call_args
        self.assertIn("40384a72b90d496c", kwargs["endpoint_or_url"])
        self.assertEqual(
            kwargs["json_data"]["variables"]["input"]["newMessage"]["parts"][0]["text"],
            "June, July and August GMV Data"
        )


if __name__ == "__main__":
    unittest.main()
