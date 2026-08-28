"""
API #2 Scraper: Brand Approval Store Requests & Unique Brand Analysis.

Fetches approval request metrics via requestsV2-count, retrieves all approval request
records via requestsV2 with full pagination, and computes unique case-insensitive brand counts.
"""

import json
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from api.api_client import APIClient
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
        except AuthExpiredError:
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

        headers = {
            "Accept": "*/*",
            "Content-Type": "application/json",
            "Origin": "https://suv-flipkart.seller-support.fkcloud.it",
            "Referer": f"https://suv-flipkart.seller-support.fkcloud.it/sellerDashboard/index.html?sellerId={customer_id}",
            "x-internal-env-type": "WEB",
            "x-requested-with": "XMLHttpRequest",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }

        # Determine page size: send max(approved_count, 1000) to retrieve all records in 1 shot
        page_size = max(approved_count, 1000)

        all_records: List[Dict[str, Any]] = []
        page = 1
        max_pages = 20  # Safeguard upper limit

        while page <= max_pages:
            payload = {
                "page": page,
                "pageSize": page_size,
                "status": [None],
            }

            print(f"\n[API #2] Calling requestsV2 for Seller: {customer_id} (Page: {page}, PageSize: {page_size})")
            print(f"  URL:     {endpoint}")
            print(f"  Payload: {json.dumps(payload)}")

            try:
                response_data = self.api_client.post(endpoint, json_data=payload, headers=headers)
            except AuthExpiredError:
                raise
            except Exception as e:
                print(f"❌ [API #2 ERROR] requestsV2 call failed for seller {customer_id}: {str(e)}")
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

            print(f"✅ [API #2] Received {len(records_page)} records for seller {customer_id} (Page: {page})")

            if not records_page:
                break

            all_records.extend(records_page)

            # If we retrieved all or the page was not full, no need for further pages
            if len(records_page) < page_size or len(all_records) >= approved_count:
                break

            page += 1

        # Extract brand names and calculate duplicate occurrences
        from collections import Counter
        brand_counter: Counter = Counter()
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
                brand_name = (
                    item.get("brand_name")
                    or item.get("brand")
                    or item.get("brandName")
                    or item.get("internal_brand_id")
                )
                if brand_name is not None:
                    brand_clean = str(brand_name).strip()
                    if brand_clean and brand_clean.lower() not in ("null", "none", ""):
                        brand_counter[brand_clean.lower()] += 1

        # Calculate number of duplicate instances (e.g. if "BRAND" appears 3 times, duplicates = 3 - 1 = 2)
        duplicates = sum(cnt - 1 for cnt in brand_counter.values() if cnt > 1)

        # Baseline count is approved_count (e.g. 7)
        base_count = approved_count if approved_count > 0 else approved_found_count

        if base_count > 0:
            # Reduce baseline count ONLY by duplicates of same-name brands; treat all other records as unique
            if duplicates > 0:
                actual_brand_count = max(1, base_count - duplicates)
            else:
                actual_brand_count = base_count
        else:
            actual_brand_count = 0

        unique_brands = set(brand_counter.keys())

        logger.info(
            "API #2 (requestsV2) for %s -> Base Approved: %d, Duplicates found: %d, Actual Brand Count: %d, Unique brand names: %d",
            customer_id,
            base_count,
            duplicates,
            actual_brand_count,
            len(unique_brands),
        )

        return actual_brand_count, unique_brands

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
