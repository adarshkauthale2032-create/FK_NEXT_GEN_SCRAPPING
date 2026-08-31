"""
API #2 Scraper: Brand Approval Store Requests & Unique Brand Analysis.

Fetches approval request metrics via requestsV2-count, retrieves all approval request
records via requestsV2 with full pagination, computes unique case-insensitive brand counts,
and queries QnA Store (questionsV2) per unique brand request to extract Brand Owner,
Document Type (BAL/TM), and Brand Website Link.
"""

import json
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from api.api_client import APIClient
from auth.auth_manager import AuthExpiredError
from config.settings import API2_COUNT_ENDPOINT, API2_REQUESTS_ENDPOINT, API_QUESTIONS_ENDPOINT

logger = logging.getLogger("customer_scraper")


class API2Scraper:
    """
    Scraper module for API #2 (Approval Store requestsV2-count, requestsV2 & questionsV2).
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

    def get_question_answers(self, customer_id: str, request_id: str) -> Dict[str, str]:
        """
        Calls questionsV2 API against a specific request ID (processId) to extract:
        - Brand Owner ('Are you the brand owner')
        - Document Type ('Select the document type')
        - Brand Website Link ('Brand Website Link')

        Endpoint: /sellerDashboard/napi/qnaStore/questionsV2?processId={request_id}&sellerId={customer_id}
        """
        clean_req_id = str(request_id).strip()
        if not clean_req_id or clean_req_id.lower() in ("null", "none", "0"):
            return {"brand_owner": "", "document_type": "", "brand_website_link": ""}

        endpoint = API_QUESTIONS_ENDPOINT.format(request_id=clean_req_id, customer_id=customer_id)
        headers = {
            "Accept": "*/*",
            "Referer": f"https://suv-flipkart.seller-support.fkcloud.it/sellerDashboard/index.html?sellerId={customer_id}#dashboard/settings",
            "x-internal-env-type": "WEB",
            "x-requested-with": "XMLHttpRequest",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }

        try:
            response_data = self.api_client.get(endpoint, headers=headers)
        except AuthExpiredError:
            raise
        except Exception as e:
            logger.warning(
                "QnA API error for customer %s, processId %s: %s",
                customer_id,
                clean_req_id,
                str(e),
            )
            return {"brand_owner": "", "document_type": "", "brand_website_link": ""}

        qna_map: Dict[str, str] = {}

        def _traverse(node: Any) -> None:
            if isinstance(node, dict):
                if "question" in node and isinstance(node["question"], dict):
                    q_obj = node["question"]
                    q_text = str(q_obj.get("text") or "").strip()
                    ans_obj = node.get("answer")
                    ans_text = ""
                    if isinstance(ans_obj, dict):
                        ans_text = str(ans_obj.get("answer_text") or "").strip()
                    elif ans_obj is not None:
                        ans_text = str(ans_obj).strip()
                    if q_text and ans_text and ans_text.lower() not in ("null", "none"):
                        qna_map[q_text] = ans_text
                for val in node.values():
                    _traverse(val)
            elif isinstance(node, list):
                for item in node:
                    _traverse(item)

        _traverse(response_data)

        brand_owner = ""
        document_type = ""
        brand_website_link = ""

        for q_text, ans_val in qna_map.items():
            norm_q = q_text.lower().replace("?", "").replace(":", "").strip()
            if "are you the brand owner" in norm_q and not brand_owner:
                brand_owner = ans_val
            elif "select the document type" in norm_q and not document_type:
                document_type = ans_val
            elif "brand website link" in norm_q and not brand_website_link:
                brand_website_link = ans_val

        logger.info(
            "QnA API parsed for Request ID %s -> Brand Owner: %s, Document Type: %s, Website Link: %s",
            clean_req_id,
            brand_owner or "N/A",
            document_type or "N/A",
            brand_website_link or "N/A",
        )

        return {
            "brand_owner": brand_owner,
            "document_type": document_type,
            "brand_website_link": brand_website_link,
        }

    def get_approved_brands(
        self, customer_id: str, approved_count: int = 0
    ) -> Tuple[int, Dict[str, List[str]]]:
        """
        Fetches all approval request records via requestsV2 with full pagination,
        filters for approved requests, and groups request IDs by unique brand names.

        Returns:
            Tuple of (actual_brand_count: int, brand_requests_map: Dict[str, List[str]])
        """
        if approved_count <= 0:
            return 0, {}

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
            except AuthExpiredError:
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

                if brand_key not in brand_requests_map:
                    brand_requests_map[brand_key] = []
                if req_id and req_id.lower() not in ("null", "none") and req_id not in brand_requests_map[brand_key]:
                    brand_requests_map[brand_key].append(req_id)

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
        3. Calls QnA API (questionsV2) sequentially per unique brand request ID.
           Short-circuits immediately if document_type in (BAL, TM) or valid website link is found.

        Returns:
            Dict containing:
                customer_id: str
                approved_brand: int
                actual_brand_count: int
                unique_brands: List[str]
                request_id: str
                brand_owner: str
                document_type: str
                brand_website_link: str
                brand_is_d2c: bool
        """
        counts = self.get_approval_counts(customer_id)
        approved_count = counts.get("APPROVED", 0)

        actual_brand_count, brand_requests_map = self.get_approved_brands(
            customer_id=customer_id,
            approved_count=approved_count,
        )

        selected_request_id = ""
        selected_brand_owner = ""
        selected_document_type = ""
        selected_brand_website_link = ""
        brand_is_d2c = False

        # Iterate sequentially over unique brands and their request IDs
        for brand_name, req_ids in brand_requests_map.items():
            for req_id in req_ids:
                if not req_id:
                    continue

                qna_res = self.get_question_answers(customer_id, req_id)
                doc_type = qna_res.get("document_type", "").strip()
                web_link = qna_res.get("brand_website_link", "").strip()
                b_owner = qna_res.get("brand_owner", "").strip()

                # Record first encountered details as baseline
                if not selected_request_id:
                    selected_request_id = req_id
                    selected_brand_owner = b_owner
                    selected_document_type = doc_type
                    selected_brand_website_link = web_link

                # Check D2C eligibility conditions from Brand verification
                is_doc_match = doc_type.upper() in ("BAL", "TM")
                is_link_match = bool(
                    web_link
                    and web_link.lower() not in ("null", "none", "n/a", "na", "")
                    and ("." in web_link or "http" in web_link.lower())
                )

                if is_doc_match or is_link_match:
                    selected_request_id = req_id
                    selected_brand_owner = b_owner
                    selected_document_type = doc_type
                    selected_brand_website_link = web_link
                    brand_is_d2c = True
                    logger.info(
                        "🎯 [D2C BRAND MATCH] Seller %s: Request ID %s qualified for D2C (DocType: '%s', Website: '%s'). Short-circuiting remaining brand checks.",
                        customer_id,
                        req_id,
                        doc_type,
                        web_link,
                    )
                    break

            if brand_is_d2c:
                break

        return {
            "customer_id": str(customer_id).strip(),
            "approved_brand": approved_count,
            "actual_brand_count": actual_brand_count,
            "unique_brands": sorted(list(brand_requests_map.keys())),
            "request_id": selected_request_id,
            "brand_owner": selected_brand_owner,
            "document_type": selected_document_type,
            "brand_website_link": selected_brand_website_link,
            "brand_is_d2c": brand_is_d2c,
        }

    # Backward compatibility alias
    def get_listings_and_brand(self, customer_id: str) -> Dict[str, Any]:
        """Alias for get_brand_approval_details."""
        return self.get_brand_approval_details(customer_id)

