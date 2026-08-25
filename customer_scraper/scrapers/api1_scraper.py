"""
API #1 Scraper: Customer & Seller Details.

Fetches seller details, determines Account Name, Support Manager status,
Seller Tier, Signed Up Date, and Live Date.
"""

import logging
from typing import Any, Dict, Optional
from api.api_client import APIClient
from config.settings import API1_ENDPOINT

logger = logging.getLogger("customer_scraper")


class API1Scraper:
    """
    Scraper module for API #1 (getSellerDetails).
    """

    def __init__(self, api_client: APIClient):
        self.api_client = api_client

    def _determine_support_manager(self, support_role_data: Any) -> str:
        """
        Determines Support Manager status ("Yes" or "No").

        Business Rule:
        If supportRole is a dict or list of dicts where all fields other than
        'tier_type' (role_name, user_id, email_id, name, phone_num, manager_email_id)
        are null or empty, then "No".
        If any of these fields contains a meaningful non-null, non-empty value, then "Yes".
        """
        if not support_role_data:
            return "No"

        # Normalize to list of dictionaries
        roles = support_role_data if isinstance(support_role_data, list) else [support_role_data]

        check_fields = [
            "role_name",
            "user_id",
            "email_id",
            "name",
            "phone_num",
            "manager_email_id",
        ]

        for role in roles:
            if not isinstance(role, dict):
                continue
            for field in check_fields:
                val = role.get(field)
                if val is not None:
                    str_val = str(val).strip()
                    # Ignore 'null', 'none', empty string
                    if str_val and str_val.lower() not in ("null", "none", ""):
                        return "Yes"

        return "No"

    def _safe_get(self, dictionary: Any, *keys, default: str = "") -> str:
        """
        Safely navigates nested dictionaries to retrieve a string value.
        """
        curr = dictionary
        for key in keys:
            if not isinstance(curr, dict):
                return default
            curr = curr.get(key)
            if curr is None:
                return default

        if curr is None:
            return default
        val_str = str(curr).strip()
        return "" if val_str.lower() in ("null", "none") else val_str

    def get_seller_details(self, customer_id: str) -> Dict[str, Any]:
        """
        Fetches and extracts API #1 seller details.

        Returns:
            Dict containing:
                customer_id: str
                account_name: str
                support_manager: "Yes" | "No"
                seller_tier: str
                signed_up_date: str
                live_date: str
        """
        endpoint = API1_ENDPOINT.format(customer_id=customer_id)
        logger.info("API #1 started for customer ID: %s", customer_id)

        response_data = self.api_client.get(endpoint)
        if not isinstance(response_data, dict):
            logger.warning("API #1 returned non-dict response for %s", customer_id)
            response_data = {}

        result = response_data.get("result")
        if not isinstance(result, dict):
            result = {}

        # 1. Account Name
        account_name = self._safe_get(result, "displayName")

        # 2. Support Manager ("Yes" / "No")
        support_role = result.get("supportRole")
        support_manager = self._determine_support_manager(support_role)
        logger.info("Support Manager detected for %s: %s", customer_id, support_manager)

        # 3. Seller Tier: result.gmv.response.details.darwin_tier_v2.tier_name
        seller_tier = self._safe_get(
            result, "gmv", "response", "details", "darwin_tier_v2", "tier_name"
        )
        if not seller_tier:
            # Secondary fallback if structure varies
            seller_tier = self._safe_get(result, "darwin_tier_v2", "tier_name")

        # 4. Signed Up Date: result.profileInfo.created_at
        signed_up_date = self._safe_get(result, "profileInfo", "created_at")

        # 5. Live Date: result.liveDate
        live_date = self._safe_get(result, "liveDate")

        logger.info("API #1 successful for customer ID: %s", customer_id)

        return {
            "customer_id": str(customer_id).strip(),
            "account_name": account_name,
            "support_manager": support_manager,
            "seller_tier": seller_tier,
            "signed_up_date": signed_up_date,
            "live_date": live_date,
        }
