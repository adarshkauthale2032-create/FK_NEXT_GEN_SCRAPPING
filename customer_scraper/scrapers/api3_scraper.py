"""
API #3 Scraper: Seller Contact Details.

Fetches login/primary mobile numbers and login/primary email addresses.
"""

import logging
from typing import Any, Dict, Optional
from api.api_client import APIClient
from config.settings import API3_ENDPOINT

logger = logging.getLogger("customer_scraper")


class API3Scraper:
    """
    Scraper module for API #3 (getSellerContacts).
    """

    def __init__(self, api_client: APIClient):
        self.api_client = api_client

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

    def get_seller_contacts(self, customer_id: str) -> Dict[str, Any]:
        """
        Fetches and extracts API #3 contact details.

        Returns:
            Dict containing:
                customer_id: str
                mobile_number: str
                registered_mobile_number: str
                email_id: str
                registered_email_id: str
        """
        endpoint = API3_ENDPOINT.format(customer_id=customer_id)
        logger.info("API #3 started for customer ID: %s", customer_id)

        response_data = self.api_client.get(endpoint)
        if not isinstance(response_data, dict):
            logger.warning("API #3 returned non-dict response for %s", customer_id)
            response_data = {}

        result = response_data.get("result")
        if not isinstance(result, dict):
            # Fallback if top-level contains keys directly
            result = response_data

        # 1. Mobile number: loginMobileNumber
        mobile_number = self._safe_get(result, "loginMobileNumber")

        # 2. Registered mobile number: primaryMobileNumber
        registered_mobile_number = self._safe_get(result, "primaryMobileNumber")

        # 3. Email ID: loginEmail
        email_id = self._safe_get(result, "loginEmail")

        # 4. Registered email ID: primaryEmail
        registered_email_id = self._safe_get(result, "primaryEmail")

        logger.info("API #3 successful for customer ID: %s", customer_id)

        return {
            "customer_id": str(customer_id).strip(),
            "mobile_number": mobile_number,
            "registered_mobile_number": registered_mobile_number,
            "email_id": email_id,
            "registered_email_id": registered_email_id,
        }
