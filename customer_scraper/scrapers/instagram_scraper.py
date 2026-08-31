"""
Instagram Account Scraper & Matching Module.

Searches DuckDuckGo / DDGS for seller brand/account Instagram profiles, validates URLs,
and scores matching confidence to determine official brand Instagram handles.
"""

import logging
import re
import time
from typing import Any, Dict, List, Optional

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
        r"\b(private limited|pvt ltd|pvt\. ltd|limited|ltd|llp|inc|incorporated|corp|corporation|co|company)\b",
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


def get_instagram_username(url: Optional[str]) -> Optional[str]:
    """
    Extracts the clean Instagram username from a URL.
    """
    if not url:
        return None

    clean_url = str(url).strip().split("?")[0].split("#")[0].rstrip("/")
    match = re.search(r"instagram\.com/([^/]+)$", clean_url, re.IGNORECASE)
    if not match:
        return None

    return match.group(1).lower()


def is_valid_instagram_profile(url: Optional[str]) -> bool:
    """
    Verifies that the URL points to an actual Instagram user profile.
    Rejects posts (/p/), reels (/reel/), stories, system pages, and invalid characters.
    """
    if not url:
        return False

    clean_url = str(url).strip()
    if "instagram.com" not in clean_url.lower():
        return False

    username = get_instagram_username(clean_url)
    if not username or username in INVALID_INSTAGRAM_USERNAMES:
        return False

    invalid_parts = [
        "/p/",
        "/reel/",
        "/reels/",
        "/tv/",
        "/stories/",
        "/explore/",
        "/accounts/",
        "/direct/",
    ]
    lower_url = clean_url.lower()
    for part in invalid_parts:
        if part in lower_url:
            return False

    if not re.match(r"^[a-zA-Z0-9._]+$", username):
        return False

    return True


def clean_instagram_url(url: Optional[str]) -> Optional[str]:
    """
    Standardizes profile URL format to https://www.instagram.com/{username}/
    """
    if not url:
        return None
    username = get_instagram_username(url)
    if not username:
        return None
    return f"https://www.instagram.com/{username}/"


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
    # 2. Brand contained in username
    elif brand_comp and brand_comp in user_comp:
        score += 40
    # 3. Username contained in brand
    elif user_comp and user_comp in brand_comp:
        score += 30

    # 4. Exact brand name in title
    if brand_norm and brand_norm in title_norm:
        score += 30

    # 5. Brand appears in snippet
    if brand_norm and brand_norm in snippet_norm:
        score += 15

    # 6. 'official' keyword present in title or snippet
    combined_text = f"{title_norm} {snippet_norm}"
    if "official" in combined_text:
        score += 10

    # 7. Penalize fan/unofficial accounts
    bad_keywords = ["fan", "fans", "fanpage", "memes", "meme", "unofficial", "fake", "backup", "parody"]
    for kw in bad_keywords:
        if kw in user_norm:
            score -= 30

    return score


class InstagramScraper:
    """
    Scraper and matcher for discovering seller brand Instagram accounts via search.
    """

    def __init__(self, request_delay: float = 1.0, max_results: int = 10, min_score: int = 30):
        self.request_delay = request_delay
        self.max_results = max_results
        self.min_score = min_score
        self.cache: Dict[str, Optional[str]] = {}

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

        if DDGS is None:
            logger.debug("[Instagram] DDGS package not available; skipping search.")
            self.cache[cache_key] = None
            return None

        queries = [
            f'site:instagram.com "{clean_brand}" "official"',
            f'site:instagram.com "{clean_brand}" Instagram',
        ]

        all_candidates: List[Dict[str, Any]] = []

        try:
            ddgs = DDGS(timeout=15)
            for query in queries:
                try:
                    results = ddgs.text(
                        query=query,
                        region="in-en",
                        safesearch="moderate",
                        max_results=self.max_results,
                        backend="auto",
                    )
                except Exception as e:
                    logger.debug("[Instagram Search Error] Query '%s': %s", query, str(e))
                    continue

                if not results:
                    continue

                for res in results:
                    link = res.get("href") or res.get("url") or ""
                    if not link or not is_valid_instagram_profile(link):
                        continue

                    username = get_instagram_username(link)
                    if not username:
                        continue

                    title = res.get("title", "")
                    snippet = res.get("body", "")
                    score = calculate_match_score(clean_brand, username, title, snippet)
                    formatted_url = clean_instagram_url(link)

                    if formatted_url and not any(x["url"] == formatted_url for x in all_candidates):
                        all_candidates.append({
                            "url": formatted_url,
                            "username": username,
                            "score": score,
                            "title": title,
                        })

                # Short delay between queries
                if self.request_delay > 0:
                    time.sleep(0.5)

            if not all_candidates:
                logger.debug("[Instagram] No profile found for '%s'", clean_brand)
                self.cache[cache_key] = None
                return None

            all_candidates.sort(key=lambda x: x["score"], reverse=True)
            best = all_candidates[0]

            if best["score"] < self.min_score:
                logger.debug(
                    "[Instagram Low Confidence] '%s' -> %s (Score: %d < %d threshold)",
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

        except Exception as ex:
            logger.warning("[Instagram Search Error] Failed searching for '%s': %s", clean_brand, str(ex))
            self.cache[cache_key] = None
            return None
