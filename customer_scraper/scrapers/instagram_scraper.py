"""
Instagram Account Scraper & Matching Module.

Searches for seller brand/account Instagram profiles via DuckDuckGo Search (DDGS)
with resilient multi-engine fallbacks (DDG HTML, Google search, redirect parsing)
and scores matching confidence to determine official brand Instagram handles.
"""

import logging
import re
import time
from typing import Any, Dict, List, Optional
import urllib.parse

import requests

logger = logging.getLogger("customer_scraper")

# Support both ddgs and duckduckgo_search packages
try:
    from ddgs import DDGS
    from ddgs.exceptions import DDGSException
except ImportError:
    try:
        from duckduckgo_search import DDGS
        from duckduckgo_search.exceptions import DuckDuckGoSearchException as DDGSException
    except ImportError:
        DDGS = None
        DDGSException = Exception

# Instagram reserved / non-profile system paths
INVALID_INSTAGRAM_USERNAMES = {
    "instagram",
    "about",
    "accounts",
    "explore",
    "directory",
    "legal",
    "privacy",
    "terms",
    "developer",
    "press",
    "api",
    "web",
    "reels",
    "reel",
    "stories",
    "direct",
    "emails",
    "p",
    "tv",
    "help",
    "support",
}


def normalize_text(text: Optional[str]) -> str:
    """
    Normalize brand/account name for comparison by removing business suffixes
    and non-alphanumeric characters.
    """
    if not text:
        return ""

    s = str(text).lower().strip()

    # Remove common business entity suffixes
    s = re.sub(
        r"\b(private limited|pvt ltd|pvt\. ltd|limited|ltd|llp|inc|incorporated|corp|corporation|co|company|enterprises|enterprise|store|retail|traders|trader|solutions)\b",
        "",
        s,
    )

    # Keep only alphanumeric characters
    s = re.sub(r"[^a-z0-9]+", " ", s)

    return " ".join(s.split())


def brand_key(text: Optional[str]) -> str:
    """
    Converts brand names like 'The Derma Co' into compact comparable form 'thedermaco'.
    """
    if not text:
        return ""
    return normalize_text(text).replace(" ", "")


def extract_instagram_url_from_string(raw_url: str) -> Optional[str]:
    """
    Decodes and extracts a clean Instagram profile URL from any raw link,
    redirect parameter (e.g. DDG uddg=...), or query string.
    """
    if not raw_url:
        return None

    # 1. Unquote if encoded (e.g. DDG uddg redirect)
    decoded = urllib.parse.unquote(str(raw_url).strip())

    # 2. Extract profile username
    m = re.search(r"https?://(?:www\.)?instagram\.com/([a-zA-Z0-9._]+)/?", decoded, re.IGNORECASE)
    if not m:
        return None

    username = m.group(1).lower().strip()
    if username in INVALID_INSTAGRAM_USERNAMES:
        return None

    invalid_prefixes = ("p", "reel", "stories", "tv", "explore", "accounts")
    for pref in invalid_prefixes:
        if username == pref or username.startswith(f"{pref}/"):
            return None

    if not re.match(r"^[a-zA-Z0-9._]+$", username):
        return None

    return f"https://www.instagram.com/{username}/"


def get_instagram_username(url: Optional[str]) -> Optional[str]:
    """
    Extracts the clean Instagram username from a URL.
    """
    if not url:
        return None

    formatted = extract_instagram_url_from_string(url)
    if not formatted:
        return None

    match = re.search(r"instagram\.com/([^/]+)/$", formatted, re.IGNORECASE)
    if match:
        return match.group(1).lower()
    return None


def is_valid_instagram_profile(url: Optional[str]) -> bool:
    """
    Verifies that the URL points to an actual Instagram user profile.
    """
    return extract_instagram_url_from_string(url) is not None


def clean_instagram_url(url: Optional[str]) -> Optional[str]:
    """
    Standardizes profile URL format to https://www.instagram.com/{username}/
    """
    return extract_instagram_url_from_string(url)


def calculate_match_score(
    brand_name: str,
    username: str,
    title: str = "",
    snippet: str = "",
) -> int:
    """
    Scores how closely an Instagram search result matches the brand/account name.
    Higher score = stronger match confidence.
    """
    brand_norm = normalize_text(brand_name)
    brand_comp = brand_key(brand_name)

    user_norm = normalize_text(username)
    user_comp = brand_key(username)

    title_norm = normalize_text(title)
    snippet_norm = normalize_text(snippet)

    score = 0

    # 1. Exact username match
    if user_comp and user_comp == brand_comp:
        score += 60
    # 2. Brand contained in username or vice versa
    elif brand_comp and brand_comp in user_comp:
        score += 40
    elif user_comp and user_comp in brand_comp:
        score += 30

    # 3. Brand name tokens overlap
    brand_tokens = set(brand_norm.split())
    user_tokens = set(user_norm.split())
    if brand_tokens and user_tokens and brand_tokens.intersection(user_tokens):
        score += 25

    # 4. Brand name appears in title or snippet
    if brand_norm and brand_norm in title_norm:
        score += 25
    if brand_norm and brand_norm in snippet_norm:
        score += 15

    # 5. 'official' keyword present in title or snippet
    combined_text = f"{title_norm} {snippet_norm}"
    if "official" in combined_text:
        score += 10

    # 6. Penalize fan/unofficial accounts
    bad_keywords = ["fan", "fans", "fanpage", "memes", "meme", "unofficial", "fake", "backup", "parody"]
    for kw in bad_keywords:
        if kw in user_norm:
            score -= 30

    return score


class InstagramScraper:
    """
    Scraper and matcher for discovering seller brand Instagram accounts via search.
    """

    def __init__(self, request_delay: float = 0.5, max_results: int = 10, min_score: int = 20):
        self.request_delay = request_delay
        self.max_results = max_results
        self.min_score = min_score
        self.cache: Dict[str, Optional[str]] = {}
        self.http_session = requests.Session()
        self.http_session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })

    def search_instagram(self, brand_name: Optional[str]) -> Optional[str]:
        """
        Searches for an official Instagram account for a given brand/account name.
        Uses in-memory caching to avoid redundant queries for duplicate account names.

        Returns:
            Normalized Instagram profile URL (e.g. 'https://www.instagram.com/brandname/') or None.
        """
        if not brand_name:
            return None

        clean_brand = str(brand_name).strip()
        if not clean_brand or clean_brand.lower() in ("null", "none", "", "n/a", "na"):
            return None

        cache_key = clean_brand.lower()
        if cache_key in self.cache:
            cached_url = self.cache[cache_key]
            logger.debug("[Instagram Cache] Hit for '%s' -> %s", clean_brand, cached_url or "NOT FOUND")
            return cached_url

        logger.info("🔍 [Instagram Search] Searching for '%s'...", clean_brand)
        queries = [
            f"site:instagram.com {clean_brand}",
            f"{clean_brand} instagram official",
            f"{clean_brand} instagram",
        ]

        all_candidates: List[Dict[str, Any]] = []

        # ------------------------------------------------------------
        # Engine 1: DuckDuckGo Search via DDGS Library
        # ------------------------------------------------------------
        if DDGS is not None:
            try:
                ddgs = DDGS(timeout=10)
                for query in queries:
                    results = []
                    try:
                        # Try keyword-based invocation
                        results = list(ddgs.text(keywords=query, max_results=self.max_results))
                    except Exception:
                        try:
                            # Try positional invocation
                            results = list(ddgs.text(query, max_results=self.max_results))
                        except Exception as e_ddg:
                            logger.debug("[DDGS Error] '%s': %s", query, str(e_ddg))

                    if results:
                        for res in results:
                            raw_link = res.get("href") or res.get("url") or res.get("link") or ""
                            formatted_url = extract_instagram_url_from_string(raw_link)
                            if not formatted_url:
                                continue

                            username = get_instagram_username(formatted_url)
                            if not username:
                                continue

                            title = res.get("title", "")
                            snippet = res.get("body", "")
                            score = calculate_match_score(clean_brand, username, title, snippet)

                            if not any(x["url"] == formatted_url for x in all_candidates):
                                all_candidates.append({
                                    "url": formatted_url,
                                    "username": username,
                                    "score": score,
                                    "title": title,
                                })

                    if all_candidates:
                        break  # Found candidates from first query, proceed to evaluation

            except Exception as ex_ddgs:
                logger.debug("[Instagram DDGS Engine notice] %s", str(ex_ddgs))

        # ------------------------------------------------------------
        # Engine 2: Direct DuckDuckGo HTML Fallback
        # ------------------------------------------------------------
        if not all_candidates:
            try:
                for query in [f"site:instagram.com {clean_brand}", f"{clean_brand} instagram"]:
                    resp = self.http_session.post(
                        "https://html.duckduckgo.com/html/",
                        data={"q": query, "b": ""},
                        timeout=8,
                    )
                    if resp.status_code == 200:
                        # Try BeautifulSoup if available
                        try:
                            from bs4 import BeautifulSoup
                            soup = BeautifulSoup(resp.text, "html.parser")
                            for a_tag in soup.find_all("a", href=True):
                                href = a_tag["href"]
                                formatted_url = extract_instagram_url_from_string(href)
                                if formatted_url:
                                    username = get_instagram_username(formatted_url)
                                    if username:
                                        score = calculate_match_score(clean_brand, username, a_tag.get_text())
                                        if not any(x["url"] == formatted_url for x in all_candidates):
                                            all_candidates.append({
                                                "url": formatted_url,
                                                "username": username,
                                                "score": score,
                                                "title": a_tag.get_text(),
                                            })
                        except ImportError:
                            # Regex fallback
                            found_links = re.findall(r'href="([^"]+)"', resp.text)
                            for raw_l in found_links:
                                formatted_url = extract_instagram_url_from_string(raw_l)
                                if formatted_url:
                                    username = get_instagram_username(formatted_url)
                                    if username:
                                        score = calculate_match_score(clean_brand, username)
                                        if not any(x["url"] == formatted_url for x in all_candidates):
                                            all_candidates.append({
                                                "url": formatted_url,
                                                "username": username,
                                                "score": score,
                                                "title": "",
                                            })

                    if all_candidates:
                        break
            except Exception as ex_html:
                logger.debug("[Instagram HTML Fallback notice] %s", str(ex_html))

        # ------------------------------------------------------------
        # Evaluation & Filtering
        # ------------------------------------------------------------
        if not all_candidates:
            logger.info("ℹ️ [Instagram] No matching profile found for '%s' (leaving blank)", clean_brand)
            self.cache[cache_key] = None
            return None

        all_candidates.sort(key=lambda x: x["score"], reverse=True)
        best = all_candidates[0]

        if best["score"] < self.min_score:
            logger.info(
                "ℹ️ [Instagram Low Confidence] '%s' -> %s (Score: %d < %d threshold, leaving blank)",
                clean_brand,
                best["url"],
                best["score"],
                self.min_score,
            )
            self.cache[cache_key] = None
            return None

        found_url = best["url"]
        logger.info("📸 [Instagram Found] '%s' -> %s (Score: %d)", clean_brand, found_url, best["score"])
        self.cache[cache_key] = found_url
        return found_url
