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
                is_brand: "Possibly a Brand" | "Possibly a Seller"
                brand_name: str
                listing_count: int
        """
        endpoint = API2_ENDPOINT.format(customer_id=customer_id)
        payload = {
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
        }

        logger.info("API #2 started for customer ID: %s (Requesting up to %d listings)", customer_id, LISTING_BATCH_SIZE)
        response_data = self.api_client.post(endpoint, json_data=payload)

        raw_listings = self._extract_listings_list(response_data)
        logger.info("API #2 received %d raw listing items for customer ID: %s", len(raw_listings), customer_id)

        titles: List[str] = []
        brands: List[str] = []

        # Process up to LISTING_BATCH_SIZE items
        for item in raw_listings[:LISTING_BATCH_SIZE]:
            if not isinstance(item, dict):
                continue
            title_val = item.get("title")
            brand_val = item.get("brand")

            title_str = str(title_val).strip() if title_val is not None else ""
            if title_str.lower() in ("null", "none"):
                title_str = ""

            brand_str = str(brand_val).strip() if brand_val is not None else ""
            if brand_str.lower() in ("null", "none"):
                brand_str = ""

            if title_str:
                titles.append(title_str)
            brands.append(brand_str)

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
            "listing_titles": titles,
            "is_brand": is_brand,
            "brand_name": brand_name,
            "listing_count": len(titles),
        }
