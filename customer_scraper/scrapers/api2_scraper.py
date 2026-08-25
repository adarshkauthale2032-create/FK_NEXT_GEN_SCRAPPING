"""
API #2 Scraper: Brand & Listing Details.

Fetches up to 20 active listings for a customer/seller, extracts product titles,
and applies the 12-of-20 frequency rule to classify as Possibly a Brand or Possibly a Seller.
"""

from collections import Counter
import logging
from typing import Any, Dict, List, Optional, Tuple
from api.api_client import APIClient
from config.settings import API2_ENDPOINT, BRAND_THRESHOLD, LISTING_BATCH_SIZE

logger = logging.getLogger("customer_scraper")


class API2Scraper:
    """
    Scraper module for API #2 (listingsDataForStates).
    """

    def __init__(self, api_client: APIClient):
        self.api_client = api_client

    def _extract_listings_list(self, response_data: Any) -> List[Dict[str, Any]]:
        """
        Safely extracts the listings list from various possible response structures.
        """
        if not response_data:
            return []

        # Case 1: Directly a list
        if isinstance(response_data, list):
            return response_data

        if isinstance(response_data, dict):
            # Case 2: Under 'listing_data_response'
            if "listing_data_response" in response_data:
                val = response_data["listing_data_response"]
                if isinstance(val, list):
                    return val

            # Case 3: Under 'result' -> 'listing_data_response' or 'result' -> 'listings'
            result = response_data.get("result")
            if isinstance(result, dict):
                for key in ("listing_data_response", "listings", "data", "listingData"):
                    val = result.get(key)
                    if isinstance(val, list):
                        return val
            elif isinstance(result, list):
                return result

            # Case 4: Under 'response' -> 'listing_data_response'
            resp_node = response_data.get("response")
            if isinstance(resp_node, dict):
                for key in ("listing_data_response", "listings", "data"):
                    val = resp_node.get(key)
                    if isinstance(val, list):
                        return val

            # Case 5: Under 'data'
            data_node = response_data.get("data")
            if isinstance(data_node, list):
                return data_node
            if isinstance(data_node, dict) and "listing_data_response" in data_node:
                val = data_node["listing_data_response"]
                if isinstance(val, list):
                    return val

        return []

    def _evaluate_brand_rule(self, brands: List[str]) -> Tuple[str, str]:
        """
        Evaluates the 12-of-20 brand frequency rule.

        Rule:
        If the SAME non-empty brand appears MORE THAN 12 times (> 12) among returned listings:
            is_brand = "Possibly a Brand"
            brand_name = that brand
        Otherwise:
            is_brand = "Possibly a Seller"
            brand_name = ""
        """
        # Filter out empty/null/none brand values
        valid_brands = [
            b.strip() for b in brands
            if b and str(b).strip().lower() not in ("null", "none", "")
        ]

        if not valid_brands:
            return "Possibly a Seller", ""

        counts = Counter(valid_brands)
        for brand, count in counts.items():
            if count > BRAND_THRESHOLD:
                return "Possibly a Brand", brand

        return "Possibly a Seller", ""

    def get_listings_and_brand(self, customer_id: str) -> Dict[str, Any]:
        """
        Fetches up to 20 listings and determines brand classification.

        Returns:
            Dict containing:
                customer_id: str
                listing_titles: List[str]
        """
        endpoint = API2_ENDPOINT.format(customer_id=customer_id)

        # Exact payload from the working browser cURL
        payload_candidates = [
            {
                "search_text": "",
                "search_filters": {
                    "internal_state": "ACTIVE"
                },
                "column": {
                    "sort": {
                        "column_name": "demand_weight",
                        "sort_by": "DESC"
                    }
                },
                "pagination": {
                    "batch_no": 0,
                    "batch_size": LISTING_BATCH_SIZE
                }
            },
            {
                "search_text": "",
                "search_filters": {
                    "internal_state": "ACTIVE"
                }
            },
            {
                "search_text": "",
                "search_filters": {}
            }
        ]

        # Ensure CSRF token, seller-view-context, and specific Referer are supplied for API #2 POST request
        csrf_token = (
            self.api_client.auth_manager.headers.get("FK-CSRF-TOKEN")
            or self.api_client.auth_manager.cookies.get("XyZ7pQ9rS2T1uV8wA3bC6dE4fG0h")
            or ""
        )
        api2_headers = {
            "Content-Type": "application/json",
            "Origin": "https://suv-flipkart.seller-support.fkcloud.it",
            "Referer": f"https://suv-flipkart.seller-support.fkcloud.it/sellerDashboard/index.html?sellerId={customer_id}",
            "Accept": "*/*",
            "seller-view-context": "ALL",
        }
        if csrf_token:
            api2_headers["FK-CSRF-TOKEN"] = csrf_token

        logger.info("API #2 started for customer ID: %s (Requesting up to %d listings)", customer_id, LISTING_BATCH_SIZE)
        
        response_data = None
        for p_idx, payload in enumerate(payload_candidates):
            try:
                response_data = self.api_client.post(endpoint, json_data=payload, headers=api2_headers)
                if response_data:
                    break
            except Exception as e:
                logger.debug("API #2 payload variant %d failed for %s: %s", p_idx + 1, customer_id, str(e))
                if p_idx == len(payload_candidates) - 1:
                    logger.warning(
                        "API #2 encountered an error for customer %s (%s). Proceeding with empty listing details.",
                        customer_id,
                        str(e)
                    )
                    return {
                        "customer_id": str(customer_id).strip(),
                        "listings": [],
                        "listing_titles": [],
                        "listing_brands": [],
                        "is_brand": "Possibly a Seller",
                        "brand_name": "",
                        "listing_count": 0,
                    }

        raw_listings = self._extract_listings_list(response_data)
        logger.info("API #2 received %d raw listing items for customer ID: %s", len(raw_listings), customer_id)

        # Process up to LISTING_BATCH_SIZE items
        listings: List[Dict[str, str]] = []
        titles: List[str] = []
        brands: List[str] = []

        for item in raw_listings[:LISTING_BATCH_SIZE]:
            if not isinstance(item, dict):
                continue
            title_val = (
                item.get("title")
                or item.get("product_title")
                or item.get("productTitle")
                or item.get("listing_title")
                or item.get("listingTitle")
            )
            brand_val = (
                item.get("brand")
                or item.get("brand_name")
                or item.get("brandName")
            )

            title_str = str(title_val).strip() if title_val is not None else ""
            if title_str.lower() in ("null", "none"):
                title_str = ""

            brand_str = str(brand_val).strip() if brand_val is not None else ""
            if brand_str.lower() in ("null", "none"):
                brand_str = ""

            if title_str or brand_str:
                titles.append(title_str)
                brands.append(brand_str)
                listings.append({"title": title_str, "brand": brand_str})

        # Print entire raw JSON response to console for complete visibility
        import json
        print("\n" + "=" * 70)
        print(f"[API #2 RAW RESPONSE] Customer ID: {customer_id}")
        try:
            print(json.dumps(response_data, indent=2))
        except Exception:
            print(str(response_data))
        print("=" * 70 + "\n")

        is_brand, brand_name = self._evaluate_brand_rule(brands)
        logger.info(
            "API #2 completed for %s: %d titles extracted, Classification: %s (Brand: '%s')",
            customer_id,
            len(titles),
            is_brand,
            brand_name
        )

        return {
            "customer_id": str(customer_id).strip(),
            "listings": listings,
            "listing_titles": titles,
            "listing_brands": brands,
            "is_brand": is_brand,
            "brand_name": brand_name,
            "listing_count": len(titles),
        }
