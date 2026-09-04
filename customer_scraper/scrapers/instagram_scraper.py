"""
Instagram Account Scraper & Matching Module.

Searches for seller brand Instagram profiles via Chrome CDP (port 9222) Google Search
in a single reusable browser tab (with resilient fallback to DDGS / HTML search),
validates brand presence in the Instagram URL, and extracts the Instagram followers count.
"""

import asyncio
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse
import urllib.request

import requests

from config.settings import CDP_URL

logger = logging.getLogger("customer_scraper")

# Support both ddgs and duckduckgo_search packages as fallback
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
    redirect parameter (e.g. DDG uddg=..., Google /url?q=...), or query string.
    """
    if not raw_url:
        return None

    # 1. Unquote if encoded
    decoded = urllib.parse.unquote(str(raw_url).strip())

    # Handle Google redirect query parameter
    if "/url?q=" in decoded or "url=" in decoded:
        try:
            parsed = urllib.parse.urlparse(decoded)
            qs = urllib.parse.parse_qs(parsed.query)
            if "q" in qs:
                decoded = qs["q"][0]
            elif "url" in qs:
                decoded = qs["url"][0]
        except Exception:
            pass

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

    # 4. Multi-word token matching (e.g. 'lens' and 'kart' in 'lens_kart')
    b_tokens = [re.sub(r"[^a-z0-9]", "", t) for t in normalize_text(b_raw).split() if len(t) >= 3]
    if b_tokens:
        matched_tokens = [t for t in b_tokens if t in u_compact]
        if len(matched_tokens) == len(b_tokens):
            return True
        if matched_tokens and len(matched_tokens[0]) >= 4 and len(matched_tokens[0]) >= (len(b_compact) * 0.4):
            return True

    return False


def extract_instagram_followers_from_text(text: Optional[str]) -> str:
    """
    Extracts the Instagram followers count from text (Google search snippet,
    Instagram meta tags, or page content).

    Examples:
        '125K Followers, 450 Following, 1,200 Posts' -> '125K'
        '50.2k followers • 1,234 posts' -> '50.2K'
        '1,234 Followers' -> '1,234'
        '1.5M Followers' -> '1.5M'
        'Followers: 45K' -> '45K'
    """
    if not text:
        return ""

    s = str(text)

    # 1. Matches: '125K Followers' or '1,234 followers' or '50.2k followers' or '12.5M Followers'
    m = re.search(r'([\d.,]+\s*[kKmMbB]?)\s+Followers\b', s, re.IGNORECASE)
    if m:
        raw_cnt = m.group(1).strip()
        if raw_cnt and raw_cnt[-1].lower() in ('k', 'm', 'b'):
            return raw_cnt[:-1].strip() + raw_cnt[-1].upper()
        return raw_cnt

    # 2. Matches: 'Followers:\s*125K'
    m = re.search(r'Followers\s*:\s*([\d.,]+\s*[kKmMbB]?)', s, re.IGNORECASE)
    if m:
        raw_cnt = m.group(1).strip()
        if raw_cnt and raw_cnt[-1].lower() in ('k', 'm', 'b'):
            return raw_cnt[:-1].strip() + raw_cnt[-1].upper()
        return raw_cnt

    # 3. Matches in meta tags: 'content="125K Followers, ..."'
    m = re.search(r'content="([^"]*[\d.,]+\s*[kKmMbB]?\s+Followers[^"]*)"', s, re.IGNORECASE)
    if m:
        meta_content = m.group(1)
        sub_m = re.search(r'([\d.,]+\s*[kKmMbB]?)\s+Followers\b', meta_content, re.IGNORECASE)
        if sub_m:
            raw_cnt = sub_m.group(1).strip()
            if raw_cnt and raw_cnt[-1].lower() in ('k', 'm', 'b'):
                return raw_cnt[:-1].strip() + raw_cnt[-1].upper()
            return raw_cnt

    return ""


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
    Scraper and matcher for discovering seller brand Instagram accounts and followers.
    Uses Chrome DevTools Protocol (CDP: 9222) to perform Google searches in a single
    reusable browser tab, validates the profile URL, and extracts follower counts.
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
        self.cache: Dict[str, Dict[str, str]] = {}
        self.http_session = requests.Session()
        self.http_session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })

    def _is_cdp_available(self) -> bool:
        """Quickly checks if Chrome is listening on CDP port 9222."""
        try:
            req = urllib.request.Request(
                f"{self.cdp_url}/json/version",
                headers={"User-Agent": "FlipkartSessionHandler"},
            )
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                return resp.status == 200
        except Exception:
            return False

    async def _async_google_search(self, clean_brand: str) -> Tuple[Optional[str], str]:
        """
        Connects to Chrome CDP (port 9222) and performs a Google search for the brand's
        official Instagram handle in a SINGLE reusable browser tab (no extra tabs created).
        Extracts both the verified Instagram profile URL and the followers count.
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            print(f"[INSTA DEBUG] Playwright not installed. Skipping CDP search for '{clean_brand}'.")
            return None, ""

        query = f"{clean_brand} official instagram handle"
        search_url = f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}"

        print(f"\n[INSTA CDP] Connecting to Chrome 9222 for Google search: '{query}'...")

        try:
            async with async_playwright() as p:
                try:
                    browser = await asyncio.wait_for(p.chromium.connect_over_cdp(self.cdp_url), timeout=5.0)
                except Exception as e:
                    print(f"[INSTA CDP] ⚠️ Could not connect to Chrome CDP (port 9222): {e}")
                    return None, ""

                if not browser.contexts:
                    print("[INSTA CDP] ⚠️ No browser contexts found in Chrome.")
                    return None, ""

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

                if search_tab is None:
                    for page in context.pages:
                        try:
                            p_url = page.url.lower()
                            if "fkcloud.it" not in p_url and "flipkart" not in p_url:
                                search_tab = page
                                break
                        except Exception:
                            pass

                if search_tab is None:
                    print("[INSTA CDP] Opening dedicated search tab in Chrome...")
                    try:
                        search_tab = await asyncio.wait_for(context.new_page(), timeout=4.0)
                    except Exception as ex_np:
                        print(f"[INSTA CDP] Notice creating tab: {ex_np}")
                        search_tab = context.pages[0] if context.pages else None

                if not search_tab:
                    print("[INSTA CDP] ⚠️ No active tab available for Google search.")
                    return None, ""

                print(f"[INSTA CDP] Navigating search tab to Google query for '{clean_brand}'...")
                try:
                    await asyncio.wait_for(
                        search_tab.goto(search_url, wait_until="domcontentloaded"),
                        timeout=7.0,
                    )
                except Exception as ex_nav:
                    print(f"[INSTA CDP] Page load note (proceeding with current DOM): {ex_nav}")

                # Brief wait for elements
                await asyncio.sleep(0.6)

                # Extract Instagram anchor links and snippet text
                print(f"[INSTA CDP] Parsing search results for '{clean_brand}'...")
                links_data = []
                try:
                    links_data = await asyncio.wait_for(
                        search_tab.evaluate("""() => {
                            const results = [];
                            const anchors = document.querySelectorAll('a[href]');
                            for (const a of anchors) {
                                const href = a.getAttribute('href') || a.href || '';
                                if (href.includes('instagram.com')) {
                                    let container = a.closest('div.g') || a.closest('div[data-sokoban-container]') || a.closest('div.MjjYud') || a.parentElement?.parentElement || a;
                                    results.push({
                                        href: href,
                                        text: a.innerText || '',
                                        snippet: container.innerText || '',
                                    });
                                }
                            }
                            return results;
                        }"""),
                        timeout=4.0,
                    )
                except Exception as ex_eval:
                    print(f"[INSTA CDP] Evaluation note: {ex_eval}")
                    links_data = []

                # Fallback to page content regex if needed
                page_content = ""
                if not links_data:
                    try:
                        page_content = await asyncio.wait_for(search_tab.content(), timeout=3.0)
                        if page_content:
                            found_raw_urls = re.findall(r'https?://(?:www\.)?instagram\.com/[a-zA-Z0-9._]+/?', page_content)
                            links_data = [{"href": u, "text": "", "snippet": ""} for u in set(found_raw_urls)]
                    except Exception:
                        pass

                print(f"[INSTA CDP] Found {len(links_data)} Instagram candidate link(s) on Google.")

                # Filter and validate extracted candidate URLs
                for item in links_data:
                    raw_href = item.get("href", "")
                    formatted_url = extract_instagram_url_from_string(raw_href)
                    if not formatted_url:
                        continue

                    username = get_instagram_username(formatted_url)
                    if not username:
                        continue

                    # Verify brand name is contained in the Instagram URL / username
                    matched = is_brand_in_instagram_url(clean_brand, formatted_url)
                    print(f"   [Candidate] @{username} ({formatted_url}) -> Brand match: {'YES ✅' if matched else 'NO ❌'}")

                    if matched:
                        snippet_text = item.get("snippet", "") or item.get("text", "")
                        followers_count = extract_instagram_followers_from_text(snippet_text)

                        if not followers_count and page_content:
                            followers_count = extract_instagram_followers_from_text(page_content)

                        if not followers_count:
                            followers_count = self.fetch_instagram_followers(formatted_url)

                        print(f"[INSTA CDP] ✅ Verified match: {formatted_url} | Followers: {followers_count or 'N/A'} for '{clean_brand}'")
                        logger.info("📸 [Google Instagram Match] Found: %s | Followers: %s (Brand: '%s')", formatted_url, followers_count or "N/A", clean_brand)
                        return formatted_url, followers_count

                print(f"[INSTA CDP] ℹ️ No matching profile containing brand '{clean_brand}' in Google results.")
                return None, ""

        except Exception as e_all:
            print(f"[INSTA CDP] ⚠️ Error during Google search for '{clean_brand}': {e_all}")
            return None, ""

    def fetch_instagram_followers(self, instagram_url: Optional[str], snippet_text: str = "") -> str:
        """
        Extracts followers count from snippet text or fetches Instagram profile metadata.
        """
        if snippet_text:
            cnt = extract_instagram_followers_from_text(snippet_text)
            if cnt:
                return cnt

        if not instagram_url:
            return ""

        formatted_url = extract_instagram_url_from_string(instagram_url)
        if not formatted_url:
            return ""

        try:
            resp = self.http_session.get(
                formatted_url,
                timeout=2.5,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            )
            if resp.status_code == 200:
                cnt = extract_instagram_followers_from_text(resp.text)
                if cnt:
                    return cnt
        except Exception as ex:
            logger.debug("[Instagram Follower Fetch] %s: %s", formatted_url, str(ex))

        return ""

    def search_instagram_with_details(self, brand_name: Optional[str]) -> Dict[str, str]:
        """
        Discovers official Instagram profile URL and followers count for a brand.
        Has strict timeouts (max 15-20 seconds total) so the scraper never gets stuck.

        Returns:
            Dict containing 'instagram_url' and 'instagram_followers'.
        """
        if not brand_name:
            return {"instagram_url": "", "instagram_followers": ""}

        clean_brand = str(brand_name).strip()
        if not clean_brand or clean_brand.lower() in ("null", "none", "", "n/a", "na"):
            return {"instagram_url": "", "instagram_followers": ""}

        # Ignore synthetic placeholder brand names
        if clean_brand.lower().startswith("brand_"):
            return {"instagram_url": "", "instagram_followers": ""}

        cache_key = clean_brand.lower()
        if cache_key in self.cache:
            cached_data = self.cache[cache_key]
            return cached_data

        print(f"\n🔍 [INSTAGRAM SEARCH] Searching Instagram for brand: '{clean_brand}'...")
        start_time = time.time()

        # ------------------------------------------------------------
        # Engine 1: Google Search inside active Chrome Browser (Port 9222)
        # ------------------------------------------------------------
        if self._is_cdp_available():
            try:
                # Wrap with strict asyncio timeout of 12 seconds
                async def run_with_timeout():
                    return await asyncio.wait_for(self._async_google_search(clean_brand), timeout=12.0)

                found_url, followers_cnt = asyncio.run(run_with_timeout())
                if found_url:
                    result = {
                        "instagram_url": found_url,
                        "instagram_followers": followers_cnt or "",
                    }
                    self.cache[cache_key] = result
                    print(f"📸 [INSTAGRAM FOUND] Brand '{clean_brand}' -> {found_url} (Followers: {followers_cnt or 'N/A'})\n")
                    return result
            except asyncio.TimeoutError:
                print(f"[INSTAGRAM] ⏱️ Chrome CDP Google search timed out (12s) for '{clean_brand}'. Continuing to fallback...")
            except Exception as ex_cdp:
                print(f"[INSTAGRAM] CDP Google Search notice: {ex_cdp}")

        # If already took more than 15 seconds, don't wait further
        elapsed = time.time() - start_time
        if elapsed > 15.0:
            print(f"[INSTAGRAM] ⏱️ Search duration {elapsed:.1f}s exceeded limit for '{clean_brand}'. Returning empty.\n")
            empty_result = {"instagram_url": "", "instagram_followers": ""}
            self.cache[cache_key] = empty_result
            return empty_result

        # ------------------------------------------------------------
        # Engine 2: Fallback to DuckDuckGo / HTML Search (Strict 5s timeout)
        # ------------------------------------------------------------
        print(f"[INSTAGRAM] Trying fallback search engine for '{clean_brand}'...")
        queries = [
            f"site:instagram.com {clean_brand}",
            f"{clean_brand} instagram official",
        ]

        all_candidates: List[Dict[str, Any]] = []

        if DDGS is not None:
            try:
                ddgs = DDGS(timeout=4)
                for query in queries:
                    if time.time() - start_time > 18.0:
                        break
                    results = []
                    try:
                        results = list(ddgs.text(keywords=query, max_results=5))
                    except Exception:
                        try:
                            results = list(ddgs.text(query, max_results=5))
                        except Exception:
                            pass

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
                            followers = extract_instagram_followers_from_text(snippet) or extract_instagram_followers_from_text(title)

                            if not any(x["url"] == formatted_url for x in all_candidates):
                                all_candidates.append({
                                    "url": formatted_url,
                                    "username": username,
                                    "score": score,
                                    "title": title,
                                    "followers": followers,
                                    "snippet": snippet,
                                })

                    if all_candidates:
                        break

            except Exception as ex_ddgs:
                logger.debug("[Instagram DDGS Engine notice] %s", str(ex_ddgs))

        # Direct DuckDuckGo HTML Fallback if still no candidates
        if not all_candidates and (time.time() - start_time < 18.0):
            try:
                resp = self.http_session.post(
                    "https://html.duckduckgo.com/html/",
                    data={"q": f"site:instagram.com {clean_brand}", "b": ""},
                    timeout=4,
                )
                if resp.status_code == 200:
                    found_links = re.findall(r'href="([^"]+)"', resp.text)
                    for raw_l in found_links:
                        formatted_url = extract_instagram_url_from_string(raw_l)
                        if formatted_url:
                            username = get_instagram_username(formatted_url)
                            if username:
                                score = calculate_match_score(clean_brand, username)
                                followers = extract_instagram_followers_from_text(raw_l)
                                if not any(x["url"] == formatted_url for x in all_candidates):
                                    all_candidates.append({
                                        "url": formatted_url,
                                        "username": username,
                                        "score": score,
                                        "title": "",
                                        "followers": followers,
                                        "snippet": "",
                                    })
            except Exception as ex_html:
                logger.debug("[Instagram HTML Fallback notice] %s", str(ex_html))

        # Evaluation & Filtering: Enforce that brand name is included in URL
        valid_candidates = [
            c for c in all_candidates
            if is_brand_in_instagram_url(clean_brand, c["url"]) and c["score"] >= self.min_score
        ]

        if not valid_candidates:
            print(f"[INSTAGRAM] ℹ️ Search completed: No valid Instagram account found for brand '{clean_brand}'. Continuing.\n")
            empty_result = {"instagram_url": "", "instagram_followers": ""}
            self.cache[cache_key] = empty_result
            return empty_result

        valid_candidates.sort(key=lambda x: x["score"], reverse=True)
        best = valid_candidates[0]

        found_url = best["url"]
        followers = best.get("followers", "") or self.fetch_instagram_followers(found_url, best.get("snippet", ""))

        print(f"📸 [INSTAGRAM MATCH] Found & Validated for '{clean_brand}' -> {found_url} | Followers: {followers or 'N/A'}\n")
        logger.info("📸 [Instagram Found & Validated] '%s' -> %s | Followers: %s (Score: %d)", clean_brand, found_url, followers or "N/A", best["score"])
        result = {
            "instagram_url": found_url,
            "instagram_followers": followers,
        }
        self.cache[cache_key] = result
        return result

    def search_instagram(self, brand_name: Optional[str]) -> Optional[str]:
        """
        Backward-compatible method returning normalized Instagram profile URL or None.
        """
        res = self.search_instagram_with_details(brand_name)
        return res.get("instagram_url") or None
