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


def is_brand_in_instagram_url(brand_name: Optional[str], instagram_url: Optional[str]) -> bool:
    """
    Validates whether the brand name (in any format, e.g. lenskart, lens.kart, lens_kart)
    is contained within the Instagram profile URL/username.

    Returns True if the brand name is present in the Instagram URL (case-insensitively
    and ignoring punctuation/separators like ., _, -), otherwise False.
    """
    if not brand_name or not instagram_url:
        return False

    formatted_url = extract_instagram_url_from_string(instagram_url)
    if not formatted_url:
        return False

    username = get_instagram_username(formatted_url)
    if not username:
        return False

    u_raw = username.lower().strip()
    u_compact = re.sub(r"[^a-z0-9]", "", u_raw)
    if not u_compact:
        return False

    b_raw = str(brand_name).strip().lower()
    # Ignore synthetic placeholder names like BRAND_1
    if b_raw.startswith("brand_") or b_raw in ("null", "none", ""):
        return False

    b_compact = re.sub(r"[^a-z0-9]", "", b_raw)
    b_norm_key = brand_key(b_raw)

    if not b_compact:
        return False

    # 1. Compact brand name is inside username (e.g. 'lenskart' in 'lenskartofficial' or 'lens.kart' -> 'lenskart')
    if len(b_compact) >= 2 and b_compact in u_compact:
        return True

    # 2. Normalized brand key is inside username (e.g. 'bombayshaving' in 'bombayshavingcompany')
    if len(b_norm_key) >= 2 and b_norm_key in u_compact:
        return True

    # 3. Compact username is inside brand name (e.g. 'kalivera' in 'kaliverahealthcare')
    if len(u_compact) >= 3 and (u_compact in b_compact or (b_norm_key and u_compact in b_norm_key)):
        return True

    # 4. Multi-word token matching (e.g. 'lens' and 'kart' in 'lens_kart', or primary brand word in username)
    b_tokens = [re.sub(r"[^a-z0-9]", "", t) for t in normalize_text(b_raw).split() if len(t) >= 3]
    if b_tokens:
        matched_tokens = [t for t in b_tokens if t in u_compact]
        if len(matched_tokens) == len(b_tokens):
            return True
        if matched_tokens and len(matched_tokens[0]) >= 4 and len(matched_tokens[0]) >= (len(b_compact) * 0.4):
            return True

    return False


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
    Scraper and matcher for discovering seller brand Instagram accounts.
    Uses Chrome DevTools Protocol (CDP: 9222) to perform Google searches in a single
    reusable browser tab and extracts the first verified official Instagram profile URL.
    """

    def __init__(
        self,
        cdp_url: str = CDP_URL,
        request_delay: float = 0.5,
        max_results: int = 10,
        min_score: int = 20,
    ):
        self.cdp_url = cdp_url
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

    def _is_cdp_available(self) -> bool:
        """Quickly checks if Chrome is listening on CDP port 9222."""
        import urllib.request
        try:
            req = urllib.request.Request(
                f"{self.cdp_url}/json/version",
                headers={"User-Agent": "FlipkartSessionHandler"},
            )
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                return resp.status == 200
        except Exception:
            return False

    async def _async_google_search(self, clean_brand: str) -> Optional[str]:
        """
        Connects to Chrome CDP (port 9222) and performs a Google search for the brand's
        official Instagram handle in a SINGLE reusable browser tab (no extra tabs created).
        Extracts and returns the first verified Instagram profile URL containing the brand name.
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.debug("Playwright not installed, skipping CDP Google search.")
            return None

        query = f"{clean_brand} official instagram handle"
        search_url = f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}"

        async with async_playwright() as p:
            try:
                browser = await asyncio.wait_for(p.chromium.connect_over_cdp(self.cdp_url), timeout=6.0)
            except Exception as e:
                logger.debug("[Instagram CDP] Could not connect to Chrome CDP: %s", str(e))
                return None

            if not browser.contexts:
                return None

            context = browser.contexts[0]

            # ------------------------------------------------------------
            # Reuse ONE dedicated tab in Chrome (do not open new tabs per brand)
            # ------------------------------------------------------------
            search_tab = None
            for page in context.pages:
                try:
                    p_url = page.url.lower()
                    if "google.com" in p_url or "about:blank" in p_url:
                        search_tab = page
                        break
                except Exception:
                    pass

            # If no google/blank tab found, find any tab that is not the Flipkart portal
            if search_tab is None:
                for page in context.pages:
                    try:
                        p_url = page.url.lower()
                        if "fkcloud.it" not in p_url and "flipkart" not in p_url:
                            search_tab = page
                            break
                    except Exception:
                        pass

            # If all open tabs are Flipkart tabs, create ONE dedicated search tab
            if search_tab is None:
                try:
                    search_tab = await context.new_page()
                except Exception:
                    search_tab = context.pages[0] if context.pages else None

            if not search_tab:
                return None

            # Navigate the single tab to Google Search
            logger.info("🔍 [Google Search in Chrome 9222] Searching for '%s'...", clean_brand)
            try:
                await asyncio.wait_for(
                    search_tab.goto(search_url, wait_until="domcontentloaded"),
                    timeout=10.0,
                )
            except Exception as ex_nav:
                logger.debug("[Google Search Nav Notice] %s", str(ex_nav))

            # Wait briefly for search elements to render
            await asyncio.sleep(1.0)

            # Extract all anchor links from Google search result page
            try:
                links_data = await search_tab.evaluate("""() => {
                    const results = [];
                    const anchors = document.querySelectorAll('a[href]');
                    for (const a of anchors) {
                        const href = a.getAttribute('href') || a.href || '';
                        if (href.includes('instagram.com')) {
                            results.push({
                                href: href,
                                text: a.innerText || '',
                            });
                        }
                    }
                    return results;
                }""")
            except Exception as ex_eval:
                logger.debug("[Google Search Eval Notice] %s", str(ex_eval))
                links_data = []

            # Check page content with regex fallback
            if not links_data:
                try:
                    content = await search_tab.content()
                    found_raw_urls = re.findall(r'https?://(?:www\.)?instagram\.com/[a-zA-Z0-9._]+/?', content)
                    links_data = [{"href": u, "text": ""} for u in found_raw_urls]
                except Exception:
                    pass

            # Filter and validate extracted Instagram candidate URLs
            for item in links_data:
                raw_href = item.get("href", "")
                if "/url?q=" in raw_href:
                    parsed = urllib.parse.urlparse(raw_href)
                    qs = urllib.parse.parse_qs(parsed.query)
                    if "q" in qs:
                        raw_href = qs["q"][0]

                formatted_url = extract_instagram_url_from_string(raw_href)
                if not formatted_url:
                    continue

                username = get_instagram_username(formatted_url)
                if not username:
                    continue

                # Verify brand name is contained in the Instagram URL / username
                if is_brand_in_instagram_url(clean_brand, formatted_url):
                    logger.info("📸 [Google Instagram Match] Found: %s for brand '%s'", formatted_url, clean_brand)
                    return formatted_url

            logger.info("ℹ️ [Google Search] No matching Instagram profile containing brand '%s' in Google results.", clean_brand)
            return None

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

        # ------------------------------------------------------------
        # Engine 1: Google Search inside active Chrome Browser (Port 9222)
        # ------------------------------------------------------------
        if self._is_cdp_available():
            try:
                found_url = asyncio.run(self._async_google_search(clean_brand))
                if found_url:
                    self.cache[cache_key] = found_url
                    return found_url
            except Exception as ex_cdp:
                logger.debug("[Instagram CDP Google Search Notice] %s", str(ex_cdp))

        # ------------------------------------------------------------
        # Engine 2: Fallback to DuckDuckGo / HTML Search
        # ------------------------------------------------------------
        logger.info("🔍 [Fallback Search] Searching for '%s'...", clean_brand)
        queries = [
            f"site:instagram.com {clean_brand}",
            f"{clean_brand} instagram official",
            f"{clean_brand} instagram",
        ]

        all_candidates: List[Dict[str, Any]] = []

        if DDGS is not None:
            try:
                ddgs = DDGS(timeout=8)
                for query in queries:
                    results = []
                    try:
                        results = list(ddgs.text(keywords=query, max_results=self.max_results))
                    except Exception:
                        try:
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
                        break

            except Exception as ex_ddgs:
                logger.debug("[Instagram DDGS Engine notice] %s", str(ex_ddgs))

        # Direct DuckDuckGo HTML Fallback
        if not all_candidates:
            try:
                for query in [f"site:instagram.com {clean_brand}", f"{clean_brand} instagram"]:
                    resp = self.http_session.post(
                        "https://html.duckduckgo.com/html/",
                        data={"q": query, "b": ""},
                        timeout=6,
                    )
                    if resp.status_code == 200:
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

        # Evaluation & Filtering: Enforce that brand name is included in URL
        valid_candidates = [
            c for c in all_candidates
            if is_brand_in_instagram_url(clean_brand, c["url"]) and c["score"] >= self.min_score
        ]

        if not valid_candidates:
            self.cache[cache_key] = None
            return None

        valid_candidates.sort(key=lambda x: x["score"], reverse=True)
        best = valid_candidates[0]

        found_url = best["url"]
        logger.info("📸 [Instagram Found & Validated] '%s' -> %s (Score: %d)", clean_brand, found_url, best["score"])
        self.cache[cache_key] = found_url
        return found_url

