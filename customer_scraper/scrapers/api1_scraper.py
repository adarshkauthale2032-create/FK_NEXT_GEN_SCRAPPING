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

    def _format_date_only(self, val: Any) -> str:
        """
        Extracts only the date portion (YYYY-MM-DD) from timestamp strings, ISO dates, or epochs.
        """
        if val is None:
            return ""
        val_str = str(val).strip()
        if not val_str or val_str.lower() in ("null", "none"):
            return ""

        # Handle ISO strings with 'T' (e.g. 2019-07-04T05:11:39.000+00:00)
        if "T" in val_str:
            return val_str.split("T")[0].strip()

        # Handle space-separated date & time (e.g. 2019-07-04 05:11:39)
        if " " in val_str:
            return val_str.split(" ")[0].strip()

        # Handle numeric epoch timestamp
        if val_str.isdigit():
            try:
                import datetime
                num = int(val_str)
                if num > 100000000000:
                    dt = datetime.datetime.fromtimestamp(num / 1000.0)
                else:
                    dt = datetime.datetime.fromtimestamp(num)
                return dt.strftime("%Y-%m-%d")
            except Exception:
                pass

        # Return first 10 chars if it resembles YYYY-MM-DD
        if len(val_str) >= 10 and val_str[4] == "-" and val_str[7] == "-":
            return val_str[:10]

        return val_str

    def get_seller_details(self, customer_id: str) -> Dict[str, Any]:
        """
        Fetches and extracts API #1 seller details.

        Returns:
            Dict containing:
                customer_id: str
                account_name: str
                support_manager: "Yes" | "No"
                seller_tier: str
                signed_up_date: str (YYYY-MM-DD only)
                live_date: str (YYYY-MM-DD only)
        """
        endpoint = API1_ENDPOINT.format(customer_id=customer_id)
        logger.info("API #1 started for customer ID: %s", customer_id)

        try:
            response_data = self.api_client.get(endpoint)
        except Exception as e:
            logger.warning("API #1 encountered an error for customer %s (%s). Proceeding with fallback.", customer_id, str(e))
            response_data = {}

        if not isinstance(response_data, dict):
            logger.warning("API #1 returned non-dict response for %s", customer_id)
            response_data = {}

        result = response_data.get("result")
        if not isinstance(result, dict):
            result = {}

        # 1. Account Name
        account_name = self._safe_get(result, "displayName")

        # 2. Account Status: result.gmv.response.details.state_details.state
        account_status = self._safe_get(
            result, "gmv", "response", "details", "state_details", "state"
        )
        if not account_status:
            account_status = self._safe_get(result, "state_details", "state")
        if not account_status:
            account_status = self._safe_get(result, "state")
        if not account_status:
            account_status = self._safe_get(
                response_data, "gmv", "response", "details", "state_details", "state"
            )

        # 3. Support Manager ("Yes" / "No")
        support_role = result.get("supportRole")
        support_manager = self._determine_support_manager(support_role)
        logger.info("Support Manager detected for %s: %s", customer_id, support_manager)

        # 4. Seller Tier: result.gmv.response.details.darwin_tier_v2.tier_name
        seller_tier = self._safe_get(
            result, "gmv", "response", "details", "darwin_tier_v2", "tier_name"
        )
        if not seller_tier:
            # Secondary fallback if structure varies
            seller_tier = self._safe_get(result, "darwin_tier_v2", "tier_name")

        # 5. Signed Up Date: result.profileInfo.created_at (formatted to date only)
        raw_signed_up = self._safe_get(result, "profileInfo", "created_at")
        signed_up_date = self._format_date_only(raw_signed_up)

        # 6. Live Date: result.liveDate (formatted to date only)
        raw_live_date = self._safe_get(result, "liveDate")
        live_date = self._format_date_only(raw_live_date)

        logger.info("API #1 successful for customer ID: %s (Status: %s)", customer_id, account_status)

        return {
            "customer_id": str(customer_id).strip(),
            "account_name": account_name,
            "account_status": account_status,
            "support_manager": support_manager,
            "seller_tier": seller_tier,
            "signed_up_date": signed_up_date,
            "live_date": live_date,
        }
