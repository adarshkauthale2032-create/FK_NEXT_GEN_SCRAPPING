"""
API #3 Scraper: Seller Contact Details.

Fetches login/primary mobile numbers and login/primary email addresses.
"""

import logging
from typing import Any, Dict, Optional
from api.api_client import APIClient
from auth.auth_manager import AuthExpiredError
from config.settings import API3_ENDPOINT, GENERIC_EMAIL_DOMAINS

logger = logging.getLogger("customer_scraper")


def determine_is_d2c(*emails: Optional[str]) -> str:
    """
    Checks if any provided email contains a unique/custom domain (not gmail.com or generic email services).
    Returns 'Yes' if a custom/unique domain is found, else 'No'.
    """
    for email in emails:
        if not email:
            continue
        email_str = str(email).strip().lower()
        if "@" in email_str:
            parts = email_str.split("@")
            domain = parts[-1].strip()
            # Must be a valid domain with a dot, not a generic provider, and not null/none
            if domain and "." in domain and domain not in GENERIC_EMAIL_DOMAINS and domain not in ("null", "none"):
                return "Yes"
    return "No"


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
                isD2C: str ('Yes' / 'No')
        """
        endpoint = API3_ENDPOINT.format(customer_id=customer_id)
        logger.info("API #3 started for customer ID: %s", customer_id)

        try:
            response_data = self.api_client.get(endpoint)
        except AuthExpiredError:
            raise
        except Exception as e:
            logger.warning("API #3 encountered an error for customer %s (%s). Proceeding with empty contacts.", customer_id, str(e))
            response_data = {}

        if not isinstance(response_data, dict):
            logger.warning("API #3 returned non-dict response for %s", customer_id)
            response_data = {}

        result = response_data.get("result")
        if not isinstance(result, dict):
            # Fallback if top-level contains keys directly
            result = response_data

        # 1. Mobile number: loginMobileNumber
        mobile_number = self._safe_get(result, "loginMobileNumber")

        # 2. Registered mobile number: result.profileInfo.mobile_number (fallback to primaryMobileNumber)
        registered_mobile_number = (
            self._safe_get(result, "profileInfo", "mobile_number")
            or self._safe_get(result, "profileinfo", "mobile_number")
            or self._safe_get(result, "primaryMobileNumber")
        )

        # 3. Email ID: loginEmail
        email_id = self._safe_get(result, "loginEmail")

        # 4. Registered email ID: result.profileInfo.email_id (fallback to primaryEmail)
        registered_email_id = (
            self._safe_get(result, "profileInfo", "email_id")
            or self._safe_get(result, "profileinfo", "email_id")
            or self._safe_get(result, "primaryEmail")
        )

        # 5. Determine isD2C based on email domains
        is_d2c = determine_is_d2c(email_id, registered_email_id)

        logger.info("API #3 successful for customer ID: %s | isD2C: %s", customer_id, is_d2c)

        return {
            "customer_id": str(customer_id).strip(),
            "mobile_number": mobile_number,
            "registered_mobile_number": registered_mobile_number,
            "email_id": email_id,
            "registered_email_id": registered_email_id,
            "isD2C": is_d2c,
            "is_d2c": is_d2c,
        }
