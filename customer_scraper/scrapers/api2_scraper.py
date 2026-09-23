"""
API #2 Scraper: Brand Approval Store Requests & Unique Brand Analysis.

Fetches approval request metrics via requestsV2-count, retrieves all approval request
records via requestsV2 with full pagination, and computes unique case-insensitive brand counts.
"""

import json
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from api.api_client import APIClient, NetworkConnectionError
from auth.auth_manager import AuthExpiredError
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
        """
        endpoint = API2_COUNT_ENDPOINT.format(customer_id=customer_id)
        headers = {
            "Accept": "*/*",
            "Origin": "https://suv-flipkart.seller-support.fkcloud.it",
            "Referer": f"https://suv-flipkart.seller-support.fkcloud.it/sellerDashboard/index.html?sellerId={customer_id}#dashboard/listings/trackApprovalRequestsV2?requestState=APPROVED",
        }

        logger.info("API #2 (requestsV2-count) started for customer ID: %s", customer_id)

        try:
            response_data = self.api_client.get(endpoint, headers=headers)
        except (AuthExpiredError, NetworkConnectionError):
            raise
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

    def get_approved_brands(
        self, customer_id: str, approved_count: int = 0
    ) -> Tuple[int, Dict[str, List[str]]]:
        """
        Fetches all approval request records via requestsV2 with full pagination,
        filters for approved requests, and groups request IDs by unique brand names.

        Returns:
            Tuple of (actual_brand_count: int, brand_requests_map: Dict[str, List[str]])
        """
        # Always clear / initialize per-run mapping dictionaries
        self._last_brand_display_names = {}
        self._last_req_to_brand_map = {}
        self._last_req_to_vertical_map = {}

        if approved_count <= 0:
            return 0, {}

        endpoint = API2_REQUESTS_ENDPOINT.format(customer_id=customer_id)

        headers = {
            "Accept": "*/*",
            "Content-Type": "application/json",
            "Origin": "https://suv-flipkart.seller-support.fkcloud.it",
            "Referer": f"https://suv-flipkart.seller-support.fkcloud.it/sellerDashboard/index.html?sellerId={customer_id}#dashboard/listings/trackApprovalRequestsV2?requestState=APPROVED",
            "x-internal-env-type": "WEB",
            "x-requested-with": "XMLHttpRequest",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }

        csrf_token = self.api_client.auth_manager.get_csrf_token()
        if csrf_token:
            headers["FK-CSRF-TOKEN"] = csrf_token
            headers["fk-csrf-token"] = csrf_token

        page_size = max(approved_count, 1000)
        all_records: List[Dict[str, Any]] = []
        page = 1
        max_pages = 20

        while page <= max_pages:
            payload = {
                "page": page,
                "pageSize": page_size,
                "status": ["APPROVED"],
            }

            logger.debug("Calling requestsV2 for Seller %s (Page: %d, PageSize: %d)", customer_id, page, page_size)

            try:
                response_data = self.api_client.post(endpoint, json_data=payload, headers=headers)
            except (AuthExpiredError, NetworkConnectionError):
                raise
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

            if len(records_page) < page_size or len(all_records) >= approved_count:
                break

            page += 1

        # Group request IDs by unique brand name (case-insensitive)
        brand_requests_map: Dict[str, List[str]] = {}
        brand_display_names: Dict[str, str] = {}
        brand_to_verticals_map: Dict[str, List[str]] = {}
        req_to_brand_map: Dict[str, str] = {}
        req_to_vertical_map: Dict[str, str] = {}

        for item in all_records:
            if not isinstance(item, dict):
                continue

            req_status = str(item.get("request_status", "")).strip().lower()
            reg_status = str(item.get("regulation_action_status", "")).strip().lower()

            is_approved = (
                req_status == "approved"
                or reg_status == "approved"
                or (not req_status and not reg_status)
            )

            if is_approved:
                raw_brand = (
                    item.get("brand_name")
                    or item.get("brand")
                    or item.get("brandName")
                    or item.get("internal_brand_id")
                    or f"BRAND_{len(brand_requests_map) + 1}"
                )
                brand_clean = str(raw_brand).strip()
                if not brand_clean or brand_clean.lower() in ("null", "none"):
                    brand_clean = f"BRAND_{len(brand_requests_map) + 1}"

                brand_key = brand_clean.upper()
                req_id = str(item.get("request_id") or "").strip()

                # Extract Vertical name from item
                raw_vertical = (
                    item.get("vertical")
                    or item.get("vertical_name")
                    or item.get("verticalName")
                    or item.get("business_vertical")
                    or ""
                )
                if isinstance(raw_vertical, dict):
                    vertical_clean = str(
                        raw_vertical.get("name")
                        or raw_vertical.get("vertical_name")
                        or raw_vertical.get("vertical")
                        or ""
                    ).strip()
                else:
                    vertical_clean = str(raw_vertical).strip()
                if vertical_clean.lower() in ("null", "none"):
                    vertical_clean = ""

                if brand_key not in brand_requests_map:
                    brand_requests_map[brand_key] = []
                    brand_display_names[brand_key] = brand_clean
                    brand_to_verticals_map[brand_key] = []

                if vertical_clean and vertical_clean not in brand_to_verticals_map[brand_key]:
                    brand_to_verticals_map[brand_key].append(vertical_clean)

                if req_id and req_id.lower() not in ("null", "none"):
                    req_to_brand_map[req_id] = brand_clean
                    req_to_vertical_map[req_id] = vertical_clean
                    if req_id not in brand_requests_map[brand_key]:
                        brand_requests_map[brand_key].append(req_id)

        self._last_brand_display_names = brand_display_names
        self._last_brand_to_verticals_map = brand_to_verticals_map
        self._last_req_to_brand_map = req_to_brand_map
        self._last_req_to_vertical_map = req_to_vertical_map

        actual_brand_count = len(brand_requests_map) if brand_requests_map else (approved_count if approved_count > 0 else 0)

        logger.info(
            "API #2 (requestsV2) for %s -> Base Approved: %d, Actual Brand Count (Unique): %d",
            customer_id,
            approved_count,
            actual_brand_count,
        )

        return actual_brand_count, brand_requests_map

    def get_brand_approval_details(self, customer_id: str) -> Dict[str, Any]:
        """
        Main entry point for API #2:
        1. Gets counts via requestsV2-count (extracts APPROVED count).
        2. Gets unique case-insensitive brands and request IDs via requestsV2.

        Returns:
            Dict containing:
                customer_id: str
                approved_brand: int
                actual_brand_count: int
                unique_brands: List[str]
        """
        # Reset instance variables explicitly per seller call
        self._last_brand_display_names = {}
        self._last_brand_to_verticals_map = {}
        self._last_req_to_brand_map = {}
        self._last_req_to_vertical_map = {}

        counts = self.get_approval_counts(customer_id)
        approved_count = counts.get("APPROVED", 0)

        if approved_count <= 0:
            return {
                "customer_id": str(customer_id).strip(),
                "approved_brand": 0,
                "actual_brand_count": 0,
                "unique_brands": [],
            }

        actual_brand_count, brand_requests_map = self.get_approved_brands(
            customer_id=customer_id,
            approved_count=approved_count,
        )

        return {
            "customer_id": str(customer_id).strip(),
            "approved_brand": approved_count,
            "actual_brand_count": actual_brand_count,
            "unique_brands": sorted(list(brand_requests_map.keys())),
        }

    # Backward compatibility alias
    def get_listings_and_brand(self, customer_id: str) -> Dict[str, Any]:
        """Alias for get_brand_approval_details."""
        return self.get_brand_approval_details(customer_id)
