"""
API #2 Scraper: Brand Approval Store Requests & Unique Brand Analysis.

Fetches approval request metrics via requestsV2-count, retrieves all approval request
records via requestsV2 with full pagination, and computes unique case-insensitive brand counts.
"""

import json
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from api.api_client import APIClient
from config.settings import API2_COUNT_ENDPOINT, API2_REQUESTS_ENDPOINT

logger = logging.getLogger("customer_scraper")


class API2Scraper:
    """
    Scraper module for API #2 (Approval Store requestsV2-count & requestsV2).
    """

    def __init__(self, api_client: APIClient):
        self.api_client = api_client

    def get_approval_counts(self, customer_id: str) -> Dict[str, int]:
        """
        Fetches approval request status counts for a seller.
        Endpoint: /sellerDashboard/napi/approval-store/requestsV2-count?sellerId={customer_id}

        Response format example:
            {
                "ALL": 43,
                "RESUBMISSION_REQUIRED": 17,
                "DISAPPROVED": 16,
                "APPROVAL_PENDING": 0,
                "APPROVED": 26
            }
        """
        endpoint = API2_COUNT_ENDPOINT.format(customer_id=customer_id)
        headers = {
            "Accept": "*/*",
            "Referer": f"https://suv-flipkart.seller-support.fkcloud.it/sellerDashboard/index.html?sellerId={customer_id}",
        }

        logger.info("API #2 (requestsV2-count) started for customer ID: %s", customer_id)

        try:
            response_data = self.api_client.get(endpoint, headers=headers)
        except Exception as e:
            logger.warning(
                "API #2 (requestsV2-count) error for customer %s (%s). Proceeding with 0 counts.",
                customer_id,
                str(e),
            )
            return {"APPROVED": 0, "ALL": 0}

        if not isinstance(response_data, dict):
            logger.warning("API #2 (requestsV2-count) returned non-dict response for %s", customer_id)
            return {"APPROVED": 0, "ALL": 0}

        # Check for nested result dict if present
        data_node = response_data.get("result") if isinstance(response_data.get("result"), dict) else response_data

        counts: Dict[str, int] = {}
        for k, v in data_node.items():
            if isinstance(v, (int, float)):
                counts[str(k).upper()] = int(v)
            elif isinstance(v, str) and v.isdigit():
                counts[str(k).upper()] = int(v)

        approved = counts.get("APPROVED", 0)
        all_cnt = counts.get("ALL", 0)
        logger.info("API #2 (requestsV2-count) for %s -> APPROVED: %d, ALL: %d", customer_id, approved, all_cnt)
        return counts

    def get_approved_brands(self, customer_id: str, approved_count: int = 0) -> Tuple[int, Set[str]]:
        """
        Fetches all approval request records via requestsV2 with full pagination,
        filters for approved requests, and calculates unique case-insensitive brand names.

        Endpoint: /sellerDashboard/napi/approval-store/requestsV2?sellerId={customer_id}
        Payload: {"page": 1, "pageSize": N, "status": [null]}
        """
        if approved_count <= 0:
            return 0, set()

        endpoint = API2_REQUESTS_ENDPOINT.format(customer_id=customer_id)

        # Retrieve CSRF token
        csrf_token = (
            self.api_client.auth_manager.headers.get("FK-CSRF-TOKEN")
            or self.api_client.auth_manager.headers.get("fk-csrf-token")
            or self.api_client.auth_manager.cookies.get("XyZ7pQ9rS2T1uV8wA3bC6dE4fG0h")
            or ""
        )

        headers = {
            "Accept": "*/*",
            "Content-Type": "application/json",
            "Origin": "https://suv-flipkart.seller-support.fkcloud.it",
            "Referer": f"https://suv-flipkart.seller-support.fkcloud.it/sellerDashboard/index.html?sellerId={customer_id}",
        }
        if csrf_token:
            headers["FK-CSRF-TOKEN"] = csrf_token
            headers["fk-csrf-token"] = csrf_token

        # Determine optimal page size (requesting full approved count or at least 10)
        requested_page_size = max(approved_count, 10)
        # Cap single page request to 500 to prevent server timeouts, then paginate if more
        page_size = min(requested_page_size, 500)

        all_records: List[Dict[str, Any]] = []
        page = 1
        max_pages = 20  # Safeguard upper limit

        while page <= max_pages:
            payload = {
                "page": page,
                "pageSize": page_size,
                "status": [None],
            }

            try:
                response_data = self.api_client.post(endpoint, json_data=payload, headers=headers)
            except Exception as e:
                logger.warning(
                    "API #2 (requestsV2) error for customer %s on page %d (%s). Proceeding with records collected so far.",
                    customer_id,
                    page,
                    str(e),
                )
                break

            records_page: List[Dict[str, Any]] = []
            if isinstance(response_data, list):
                records_page = response_data
            elif isinstance(response_data, dict):
                for k in ("result", "data", "requests", "items", "records"):
                    if isinstance(response_data.get(k), list):
                        records_page = response_data[k]
                        break

            if not records_page:
                break

            all_records.extend(records_page)

            # If we retrieved all or the page was not full, no need for further pages
            if len(records_page) < page_size or len(all_records) >= approved_count:
                break

            page += 1

        # Extract unique brand names with case-insensitive normalization
        unique_brands: Set[str] = set()
        approved_found_count = 0

        for item in all_records:
            if not isinstance(item, dict):
                continue

            req_status = str(item.get("request_status", "")).strip().lower()
            reg_status = str(item.get("regulation_action_status", "")).strip().lower()

            # Check if this item is approved
            is_approved = (
                req_status == "approved"
                or reg_status == "approved"
                or (not req_status and not reg_status)  # fallback if status not present
            )

            if is_approved:
                approved_found_count += 1
                brand_name = item.get("brand_name") or item.get("brand") or item.get("brandName")
                if brand_name is not None:
                    brand_clean = str(brand_name).strip()
                    if brand_clean and brand_clean.lower() not in ("null", "none", ""):
                        unique_brands.add(brand_clean.lower())

        logger.info(
            "API #2 (requestsV2) for %s -> Total fetched: %d, Approved records: %d, Unique brands: %d",
            customer_id,
            len(all_records),
            approved_found_count or approved_count,
            len(unique_brands),
        )

        return len(unique_brands), unique_brands

    def get_brand_approval_details(self, customer_id: str) -> Dict[str, Any]:
        """
        Main entry point for API #2:
        1. Gets counts via requestsV2-count (extracts APPROVED count).
        2. Gets unique case-insensitive brand names via requestsV2.
        
        Returns:
            Dict containing:
                customer_id: str
                approved_brand: int (count from requestsV2-count)
                actual_brand_count: int (unique case-insensitive count)
                unique_brands: List[str]
        """
        counts = self.get_approval_counts(customer_id)
        approved_count = counts.get("APPROVED", 0)

        actual_brand_count, unique_brands = self.get_approved_brands(
            customer_id=customer_id,
            approved_count=approved_count,
        )

        return {
            "customer_id": str(customer_id).strip(),
            "approved_brand": approved_count,
            "actual_brand_count": actual_brand_count,
            "unique_brands": sorted(list(unique_brands)),
        }

    # Backward compatibility alias
    def get_listings_and_brand(self, customer_id: str) -> Dict[str, Any]:
        """Alias for get_brand_approval_details."""
        return self.get_brand_approval_details(customer_id)
