import asyncio
import json
import os
from datetime import datetime

from playwright.async_api import async_playwright


CHROME_CDP_URL = "http://127.0.0.1:9222"
YOUTUBE_URL = "https://www.youtube.com/"
SESSION_FILE = "session.json"
REFRESH_INTERVAL = 10


async def save_session(context, request):
    try:
        print("[DEBUG] save_session() called")

        headers = await request.all_headers()

        # Get current cookies, but don't write authentication values
        # to disk.
        browser_cookies = await context.cookies(
            "https://www.youtube.com"
        )

        cookie_names = sorted(
            cookie["name"]
            for cookie in browser_cookies
        )

        # Remove sensitive authentication headers.
        sensitive = {
            "authorization",
            "cookie",
            "proxy-authorization",
            "x-goog-authuser",
        }

        safe_headers = {
            name: value
            for name, value in headers.items()
            if name.lower() not in sensitive
        }

        data = {
            "captured_at": datetime.now().isoformat(),
            "request": {
                "method": request.method,
                "url": request.url
            },
            "cookies": {
                "available": cookie_names
            },
            "headers": safe_headers
        }

        with open(
            SESSION_FILE,
            "w",
            encoding="utf-8"
        ) as f:
            json.dump(
                data,
                f,
                indent=4,
                ensure_ascii=False
            )

        print()
        print("=" * 70)
        print("[SESSION] session.json CREATED/UPDATED")
        print(
            f"[SESSION] {os.path.abspath(SESSION_FILE)}"
        )
        print(
            f"[SESSION] API: {request.url}"
        )
        print(
            f"[SESSION] Cookies: {len(cookie_names)}"
        )
        print(
            f"[SESSION] Headers: {len(safe_headers)}"
        )
        print("=" * 70)
        print()

    except Exception as e:
        print(f"[ERROR] save_session(): {e}")


async def main():

    async with async_playwright() as p:

        print("[INFO] Connecting to Chrome...")

        try:
            browser = await p.chromium.connect_over_cdp(
                CHROME_CDP_URL
            )
        except Exception as e:
            print("[ERROR] Could not connect to Chrome")
            print(e)
            return

        print("[INFO] Connected to Chrome")

        if not browser.contexts:
            print("[ERROR] No browser context")
            return

        context = browser.contexts[0]

        print(
            f"[INFO] Existing pages: "
            f"{len(context.pages)}"
        )

        # ------------------------------------------------------------
        # Capture ALL requests from the Chrome context.
        # ------------------------------------------------------------

        async def handle_request(request):

            url = request.url

            # Print every YouTube internal API request.
            if "youtubei" in url:

                print()
                print("[YOUTUBEI REQUEST]")
                print(f"Method : {request.method}")
                print(f"URL    : {url}")

                # Save information from any youtubei request.
                await save_session(
                    context,
                    request
                )

        context.on(
            "request",
            handle_request
        )

        # ------------------------------------------------------------
        # Find YouTube tab.
        # ------------------------------------------------------------

        youtube_page = None

        for page in context.pages:

            try:

                if "youtube.com" in page.url:

                    youtube_page = page

                    print(
                        "[INFO] Found existing YouTube tab"
                    )

                    print(
                        f"[INFO] {page.url}"
                    )

                    break

            except Exception:
                pass

        # ------------------------------------------------------------
        # Open YouTube if it isn't already open.
        # ------------------------------------------------------------

        if youtube_page is None:

            print(
                "[INFO] YouTube tab not found"
            )

            print(
                "[INFO] Opening YouTube..."
            )

            youtube_page = await context.new_page()

            await youtube_page.goto(
                YOUTUBE_URL,
                wait_until="domcontentloaded"
            )

        # ------------------------------------------------------------
        # Refresh loop.
        # ------------------------------------------------------------

        print()
        print("=" * 70)
        print("[INFO] Monitoring started")
        print("[INFO] Printing YouTube internal API requests")
        print(
            f"[INFO] Refresh every "
            f"{REFRESH_INTERVAL} seconds"
        )
        print("=" * 70)
        print()

        while True:

            try:

                if youtube_page.is_closed():

                    print(
                        "[INFO] YouTube tab closed"
                    )

                    youtube_page = await context.new_page()

                    await youtube_page.goto(
                        YOUTUBE_URL,
                        wait_until="domcontentloaded"
                    )

                else:

                    print(
                        f"[REFRESH] {youtube_page.url}"
                    )

                    await youtube_page.reload(
                        wait_until="domcontentloaded"
                    )

                await asyncio.sleep(
                    REFRESH_INTERVAL
                )

            except Exception as e:

                print(
                    f"[ERROR] {e}"
                )

                await asyncio.sleep(5)


if __name__ == "__main__":

    try:
        asyncio.run(main())

    except KeyboardInterrupt:

        print()
        print("[INFO] Stopped")