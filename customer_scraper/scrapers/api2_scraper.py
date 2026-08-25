"""
API #2 Scraper: Brand & Listing Details via GraphQL Orchestrator.

Fetches up to 20 active listings for a customer/seller using GetListingRows GraphQL query,
extracts listing product titles and listing brands, and applies the 12-of-20 frequency rule.
"""

from collections import Counter
import json
import logging
from typing import Any, Dict, List, Optional, Tuple
from api.api_client import APIClient
from config.settings import API2_ENDPOINT, BRAND_THRESHOLD, LISTING_BATCH_SIZE

logger = logging.getLogger("customer_scraper")

GET_LISTING_ROWS_QUERY = """query GetListingRows($input: ListingsManagementMetricInput) {
  listingsManagementMetrics(input: $input) {
    listingRows {
      count
      listingDataResponse {
        listingId
        skuId
        productId
        vertical
        hsn
        brand
        view {
          title
          imageUrl
        }
        sellerId
        attributes {
          internalState
          fkReleaseDate
          verticalDisplayName
          serviceProfile
          listingTier
          shippingDays
          procurementType
          ssp
          esp
          mrp
          shippingProvider
          localShippingFeeFromBuyer
          zonalShippingFeeFromBuyer
          nationalShippingFeeFromBuyer
          subsidizedShipping
          potentialRfaLoss
          potentialRfaUnit
          minimumOrderQuantity
          recommendedMinimumOrderQuantity
          recommendedMinoqFsp
          recommendedMinoqMaxFsp
          potentialTag
          reasonForDeactivation
          reasonForArchival
          zuluChartStatus
          visibilityStatePostQc
        }
      }
    }
  }
}"""


class API2Scraper:
    """
    Scraper module for API #2 (GraphQL GetListingRows).
    """

    def __init__(self, api_client: APIClient):
        self.api_client = api_client

    def _extract_listings_list(self, response_data: Any) -> List[Dict[str, Any]]:
        """
        Safely extracts the listings list from the GraphQL response structure.
        """
        if not isinstance(response_data, dict):
            return []

        # 1. Primary path: data.listingsManagementMetrics.listingRows.listingDataResponse
        try:
            data_node = response_data.get("data")
            if isinstance(data_node, dict):
                metrics = data_node.get("listingsManagementMetrics")
                if isinstance(metrics, dict):
                    rows = metrics.get("listingRows")
                    if isinstance(rows, dict):
                        resp_list = rows.get("listingDataResponse")
                        if isinstance(resp_list, list):
                            return resp_list
        except Exception:
            pass

        # 2. General fallbacks
        for path in [
            ["listingDataResponse"],
            ["data", "listingDataResponse"],
            ["result", "listing_data_response"],
            ["listing_data_response"],
            ["result", "listings"],
            ["listings"],
        ]:
            curr = response_data
            for p in path:
                if isinstance(curr, dict):
                    curr = curr.get(p)
                else:
                    curr = None
                    break
            if isinstance(curr, list):
                return curr

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
        Fetches up to 20 listings via GraphQL and determines brand classification.

        Returns:
            Dict containing:
                customer_id: str
                listings: List[Dict[str, str]]
                listing_titles: List[str]
                listing_brands: List[str]
                is_brand: "Possibly a Brand" | "Possibly a Seller"
                brand_name: str
                listing_count: int
        """
        endpoint = API2_ENDPOINT.format(customer_id=customer_id)

        # GraphQL Request Body
        graphql_payload = {
            "operationName": "GetListingRows",
            "variables": {
                "input": {
                    "listingRowsInput": {
                        "internalState": "ACTIVE",
                        "searchText": "",
                        "pagination": {
                            "pageNumber": 0,
                            "pageSize": LISTING_BATCH_SIZE,
                        },
                        "sort": {
                            "columnName": "demand_weight",
                            "sortBy": "DESC",
                        },
                    }
                }
            },
            "query": GET_LISTING_ROWS_QUERY,
        }

        # Authentication and GraphQL headers
        csrf_token = (
            self.api_client.auth_manager.headers.get("fk-csrf-token")
            or self.api_client.auth_manager.headers.get("FK-CSRF-TOKEN")
            or self.api_client.auth_manager.cookies.get("XyZ7pQ9rS2T1uV8wA3bC6dE4fG0h")
            or ""
        )

        api2_headers = {
            "accept": "*/*",
            "content-type": "application/json",
            "Origin": "https://suv-flipkart.seller-support.fkcloud.it",
            "Referer": f"https://suv-flipkart.seller-support.fkcloud.it/sellerDashboard/index.html?sellerId={customer_id}",
            "operation": "query",
            "operation-name": "GetListingRows",
            "x-internal-env-type": "WEB",
            "x-marketplace-context": "ALL",
            "x-requested-with": "XMLHttpRequest",
        }
        if csrf_token:
            api2_headers["fk-csrf-token"] = csrf_token

        logger.info("API #2 (GraphQL) started for customer ID: %s (Requesting up to %d listings)", customer_id, LISTING_BATCH_SIZE)

        try:
            response_data = self.api_client.post(endpoint, json_data=graphql_payload, headers=api2_headers)
        except Exception as e:
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

        listings: List[Dict[str, str]] = []
        titles: List[str] = []
        brands: List[str] = []

        for item in raw_listings[:LISTING_BATCH_SIZE]:
            if not isinstance(item, dict):
                continue

            # 1. Extract Title
            title_val = item.get("title")
            if not title_val and isinstance(item.get("view"), dict):
                title_val = item["view"].get("clean_title") or item["view"].get("title")
            if not title_val:
                title_val = item.get("clean_title") or item.get("product_title") or item.get("productTitle")

            title_str = str(title_val).strip() if title_val is not None else ""

            # If title is nested serialized JSON (e.g. {"w3_title": "..."})
            if title_str.startswith("{") and "w3_title" in title_str:
                try:
                    t_obj = json.loads(title_str)
                    title_str = t_obj.get("w3_title") or t_obj.get("clean_title") or title_str
                except Exception:
                    pass

            if title_str.lower() in ("null", "none"):
                title_str = ""

            # 2. Extract Brand
            brand_val = item.get("brand")
            if not brand_val and isinstance(item.get("view"), dict):
                brand_val = item["view"].get("brand")
            if not brand_val:
                brand_val = item.get("brand_name") or item.get("brandName")

            brand_str = str(brand_val).strip() if brand_val is not None else ""
            if brand_str.lower() in ("null", "none"):
                brand_str = ""

            if title_str or brand_str:
                titles.append(title_str)
                brands.append(brand_str)
                listings.append({"title": title_str, "brand": brand_str})

        # Apply 12-of-20 brand frequency rule
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
