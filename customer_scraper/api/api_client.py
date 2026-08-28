"""
Shared API Client module.

Handles HTTP requests (GET, POST), connection timeouts, exponential backoff retries,
session management, auth expiry detection, and standard JSON response parsing.
"""

import json
import logging
import time
from typing import Any, Dict, Optional, Union
import requests

from auth.auth_manager import AuthManager, AuthExpiredError
from config.settings import (
    BASE_URL,
    DEFAULT_SELLER_ID,
    REQUEST_TIMEOUT,
    MAX_REQUEST_RETRIES,
    BACKOFF_FACTOR,
    MAX_AUTH_RETRIES,
)

logger = logging.getLogger("customer_scraper")


class APIError(Exception):
    """Base exception for API errors."""
    pass


class APIResponseError(APIError):
    """Raised when an API returns a non-200 status or unexpected payload structure."""
    def __init__(self, message: str, status_code: Optional[int] = None, response_text: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


class APIClient:
    """
    HTTP client configured with authentication, retries, and error handling.
    """

    def __init__(self, auth_manager: Optional[AuthManager] = None):
        self.auth_manager = auth_manager or AuthManager()
        self.base_url = BASE_URL.rstrip("/")

    def _build_url(self, endpoint_or_url: str) -> str:
        if endpoint_or_url.startswith("http://") or endpoint_or_url.startswith("https://"):
            return endpoint_or_url
        if not endpoint_or_url.startswith("/"):
            endpoint_or_url = "/" + endpoint_or_url
        return f"{self.base_url}{endpoint_or_url}"

    def request(
        self,
        method: str,
        endpoint_or_url: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = REQUEST_TIMEOUT,
    ) -> Any:
        """
        Executes an HTTP request with automatic retry, session management, and auth expiry handling.
        """
        full_url = self._build_url(endpoint_or_url)
        auth_attempts = 0

        while auth_attempts <= MAX_AUTH_RETRIES:
            session = self.auth_manager.get_session()
            
            # Prepare merged headers if specified
            req_headers = {}
            if headers:
                req_headers.update(headers)

            # Dynamically inject freshest CSRF token from auth_manager on every request/retry
            current_csrf = (
                self.auth_manager.headers.get("FK-CSRF-TOKEN")
                or self.auth_manager.headers.get("fk-csrf-token")
                or self.auth_manager.cookies.get("XyZ7pQ9rS2T1uV8wA3bC6dE4fG0h")
            )
            if current_csrf:
                req_headers["FK-CSRF-TOKEN"] = current_csrf
                req_headers["fk-csrf-token"] = current_csrf

            retry_count = 0
            while retry_count < MAX_REQUEST_RETRIES:
                try:
                    logger.debug("Executing %s request to %s (Attempt %d)", method.upper(), full_url, retry_count + 1)
                    
                    response = session.request(
                        method=method.upper(),
                        url=full_url,
                        params=params,
                        json=json_data,
                        headers=req_headers,
                        timeout=timeout,
                    )

                    # Check for session expiration
                    if self.auth_manager.is_session_expired(response):
                        logger.warning("Session expired or invalid detected on endpoint %s (Status: %s)", full_url, response.status_code)
                        print("\n" + "=" * 75)
                        print(f"⚠️ [API AUTH ERROR] {method.upper()} {full_url}")
                        print(f"  Status Code:      {response.status_code}")
                        if json_data is not None:
                            print(f"  Request Payload:  {json.dumps(json_data)}")
                        if params:
                            print(f"  Request Params:   {json.dumps(params)}")
                        print(f"  CSRF Token Used:  {req_headers.get('FK-CSRF-TOKEN') or req_headers.get('fk-csrf-token') or 'NONE'}")
                        print(f"  Response Body:    {response.text[:1500] if response.text else '<EMPTY>'}")
                        print("=" * 75 + "\n")
                        break  # Break inner loop to trigger auth refresh

                    # Check HTTP status
                    if response.status_code == 200:
                        try:
                            return response.json()
                        except ValueError as json_err:
                            logger.error("Failed to parse JSON response from %s: %s", full_url, str(json_err))
                            print(f"❌ [JSON PARSE ERROR] {full_url} returned invalid JSON: {response.text[:500]}")
                            raise APIResponseError(
                                f"Invalid JSON response from {full_url}",
                                status_code=response.status_code,
                                response_text=response.text[:500]
                            )

                    # Handle 404 or other 4xx client errors that are not auth related
                    if 400 <= response.status_code < 500:
                        logger.error("Client error %d returned from %s: %s", response.status_code, full_url, response.text[:200])
                        print("\n" + "=" * 75)
                        print(f"❌ [CLIENT ERROR {response.status_code}] {method.upper()} {full_url}")
                        if json_data is not None:
                            print(f"  Request Payload:  {json.dumps(json_data)}")
                        print(f"  Response Body:    {response.text[:1500] if response.text else '<EMPTY>'}")
                        print("=" * 75 + "\n")
                        raise APIResponseError(
                            f"HTTP Client Error {response.status_code} for {full_url}",
                            status_code=response.status_code,
                            response_text=response.text[:500]
                        )

                    # For 5xx server errors, retry with backoff
                    if response.status_code >= 500:
                        retry_count += 1
                        if retry_count < MAX_REQUEST_RETRIES:
                            logger.warning(
                                "Server error %d from %s (%s). Retrying in %d seconds...",
                                response.status_code,
                                full_url,
                                response.text[:120],
                                BACKOFF_FACTOR ** retry_count,
                            )
                            time.sleep(BACKOFF_FACTOR ** retry_count)
                            continue
                        else:
                            logger.warning(
                                "Server error %d from %s after %d attempts. Skipping endpoint.",
                                response.status_code,
                                full_url,
                                MAX_REQUEST_RETRIES
                            )
                            raise APIResponseError(
                                f"Server error {response.status_code} on {full_url}",
                                status_code=response.status_code,
                                response_text=response.text[:500]
                            )

                except (requests.ConnectionError, requests.Timeout) as net_err:
                    retry_count += 1
                    sleep_time = BACKOFF_FACTOR ** retry_count
                    if retry_count < MAX_REQUEST_RETRIES:
                        logger.warning(
                            "Network connection issue (%s) reaching %s. Retrying (%d/%d) in %ds...",
                            type(net_err).__name__,
                            full_url,
                            retry_count,
                            MAX_REQUEST_RETRIES,
                            sleep_time,
                        )
                        time.sleep(sleep_time)
                    else:
                        logger.error(
                            "Network failure contacting %s after %d retries. "
                            "Please verify your network/VPN connection to Flipkart internal cloud.",
                            full_url,
                            MAX_REQUEST_RETRIES
                        )
                        raise APIError(f"Connection to {full_url} failed ({type(net_err).__name__}). Please check your VPN/network access.")
                except APIError:
                    raise
                except Exception as ex:
                    logger.error("Unexpected error during request to %s: %s", full_url, str(ex))
                    raise APIError(f"Request failed: {str(ex)}")

            # If inner loop broke due to session expiry
            auth_attempts += 1
            if auth_attempts <= MAX_AUTH_RETRIES:
                # Extract sellerId if available in params or url
                seller_id = None
                if params and isinstance(params, dict) and "sellerId" in params:
                    seller_id = params["sellerId"]
                elif "sellerId=" in full_url:
                    import urllib.parse
                    parsed = urllib.parse.urlparse(full_url)
                    query_params = urllib.parse.parse_qs(parsed.query)
                    if "sellerId" in query_params:
                        seller_id = query_params["sellerId"][0]

                # Identify which API failed so we only refresh that specific page
                target_api = "all"
                if any(x in full_url for x in ("approval-store", "requestsV2", "sellerDashboard")):
                    target_api = "api2"
                elif "getSellerDetails" in full_url:
                    target_api = "api1"
                elif "getSellerContacts" in full_url or "get-locations" in full_url:
                    target_api = "api3"

                refresh_target_seller = seller_id or DEFAULT_SELLER_ID
                logger.info("Triggering auth refresh for seller %s (Target: %s, Auth attempt %d/%d)...", refresh_target_seller, target_api.upper(), auth_attempts, MAX_AUTH_RETRIES)
                try:
                    refreshed = self.auth_manager.refresh_session(seller_id=refresh_target_seller, target_api=target_api)
                    if not refreshed:
                        raise AuthExpiredError(f"Unable to refresh session for {target_api}.")
                except Exception as auth_err:
                    logger.error("Auth refresh failed: %s", str(auth_err))
                    raise AuthExpiredError(f"Authentication failed: {str(auth_err)}")
            else:
                logger.error("Exceeded maximum auth retry attempts (%d).", MAX_AUTH_RETRIES)
                raise AuthExpiredError(
                    f"Script failed because session failed to get after {MAX_AUTH_RETRIES} attempts (website may be logged out in Chrome). Please open Chrome, log into Flipkart Seller Portal, and rerun python main.py."
                )

        raise APIError(f"Request to {full_url} failed after maximum retry attempts.")

    def get(self, endpoint_or_url: str, params: Optional[Dict[str, Any]] = None, **kwargs) -> Any:
        """Helper for GET requests."""
        return self.request("GET", endpoint_or_url, params=params, **kwargs)

    def post(self, endpoint_or_url: str, json_data: Optional[Dict[str, Any]] = None, **kwargs) -> Any:
        """Helper for POST requests."""
        return self.request("POST", endpoint_or_url, json_data=json_data, **kwargs)
