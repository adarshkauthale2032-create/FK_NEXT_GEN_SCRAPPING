"""
Unit tests for AuthManager and Playwright CDP session handling.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from auth.auth_manager import AuthManager, AuthExpiredError
from auth.playwright_session import PlaywrightSessionHandler


class TestAuthManager(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.session_path = self.test_dir / "session.json"

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_load_session_from_json_file(self):
        # Create session file
        session_data = {
            "cookies": {
                "SESSION_ID": "mock_cookie_12345",
                "XyZ7pQ9rS2T1uV8wA3bC6dE4fG0h": "csrf_token_abc",
            },
            "headers": {
                "User-Agent": "CustomAgent/1.0",
            },
        }
        with open(self.session_path, "w", encoding="utf-8") as f:
            json.dump(session_data, f)

        auth = AuthManager(session_path=self.session_path)

        self.assertEqual(auth.cookies.get("SESSION_ID"), "mock_cookie_12345")
        self.assertEqual(auth.headers.get("FK-CSRF-TOKEN"), "csrf_token_abc")
        self.assertEqual(auth.session.cookies.get("SESSION_ID"), "mock_cookie_12345")

    def test_clear_session_clears_memory(self):
        auth = AuthManager(session_path=self.session_path)
        auth.cookies = {"test_c": "123"}
        auth.headers = {"test_h": "456"}
        auth._save_to_file()

        auth.clear_session()
        self.assertEqual(auth.cookies, {})
        self.assertIn("User-Agent", auth.headers)

    def test_refresh_session_persists_session(self):
        auth = AuthManager(session_path=self.session_path)

        mock_refresh_output = {
            "cookies": {
                "connect.sid": "token_xyz",
                "XyZ7pQ9rS2T1uV8wA3bC6dE4fG0h": "csrf_playwright",
            },
            "headers": {
                "User-Agent": "PlaywrightBrowser/1.0",
                "FK-CSRF-TOKEN": "csrf_playwright",
            },
        }

        with patch.object(
            auth.playwright_handler, "refresh_and_extract_session", return_value=mock_refresh_output
        ):
            success = auth.refresh_session(seller_id="218598a2b41c4bcd")
            self.assertTrue(success)
            self.assertEqual(auth.cookies.get("connect.sid"), "token_xyz")
            self.assertEqual(auth.headers.get("FK-CSRF-TOKEN"), "csrf_playwright")

    def test_refresh_session_target_api(self):
        auth = AuthManager(session_path=self.session_path)
        mock_refresh_output = {
            "cookies": {"connect.sid": "token_api2"},
            "headers": {"FK-CSRF-TOKEN": "csrf_api2"},
        }
        with patch.object(
            auth.playwright_handler, "refresh_and_extract_session", return_value=mock_refresh_output
        ) as mock_refresh:
            success = auth.refresh_session(seller_id="seller_123", target_api="api2")
            self.assertTrue(success)
            mock_refresh.assert_called_once_with(seller_id="seller_123", target_api="api2", force_new_tab=False)
            self.assertEqual(auth.cookies.get("connect.sid"), "token_api2")
            self.assertEqual(auth.headers.get("FK-CSRF-TOKEN"), "csrf_api2")

    def test_is_session_expired_status_codes(self):
        auth = AuthManager(session_path=self.session_path)

        mock_resp_401 = MagicMock()
        mock_resp_401.status_code = 401
        mock_resp_401.history = []
        self.assertTrue(auth.is_session_expired(mock_resp_401))

        mock_resp_403 = MagicMock()
        mock_resp_403.status_code = 403
        mock_resp_403.history = []
        self.assertTrue(auth.is_session_expired(mock_resp_403))

        mock_resp_200 = MagicMock()
        mock_resp_200.status_code = 200
        mock_resp_200.history = []
        mock_resp_200.headers = {"Content-Type": "application/json"}
        mock_resp_200.json.return_value = {"status": "success"}
        self.assertFalse(auth.is_session_expired(mock_resp_200))


    def test_refresh_session_raises_auth_expired_error_when_failed(self):
        auth = AuthManager(session_path=self.session_path)
        with patch.object(auth.playwright_handler, "refresh_and_extract_session", return_value=None):
            with self.assertRaises(AuthExpiredError):
                auth.refresh_session(seller_id="seller_123", target_api="api2")


class TestPlaywrightSessionHandler(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.session_file = self.test_dir / "session.json"

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_handler_initialization(self):
        handler = PlaywrightSessionHandler(
            session_file=self.session_file,
        )
        self.assertEqual(handler.session_file, self.session_file)

    def test_async_refresh_auto_opens_tab2_when_missing(self):
        import asyncio
        handler = PlaywrightSessionHandler(session_file=self.session_file)

        mock_page_tab1 = MagicMock()
        mock_page_tab1.url = "https://suv-flipkart.seller-support.fkcloud.it/#app/seller/123/info"
        mock_page_tab1.goto = MagicMock(return_value=asyncio.sleep(0.01))
        mock_page_tab1.evaluate = MagicMock(return_value=asyncio.sleep(0.01, result="connect.sid=sid123; XyZ7pQ9rS2T1uV8wA3bC6dE4fG0h=csrf456"))

        mock_page_tab2 = MagicMock()
        mock_page_tab2.url = "https://suv-flipkart.seller-support.fkcloud.it/sellerDashboard/index.html?sellerId=123#dashboard/listings/trackApprovalRequestsV2?requestState=APPROVED"
        mock_page_tab2.goto = MagicMock(return_value=asyncio.sleep(0.01))
        mock_page_tab2.evaluate = MagicMock(return_value=asyncio.sleep(0.01, result="connect.sid=sid123; XyZ7pQ9rS2T1uV8wA3bC6dE4fG0h=csrf456"))

        mock_context = MagicMock()
        mock_context.pages = [mock_page_tab1]
        mock_context.new_page = MagicMock(return_value=asyncio.sleep(0.01, result=mock_page_tab2))
        mock_context.cookies = MagicMock(return_value=asyncio.sleep(0.01, result=[
            {"name": "connect.sid", "value": "sid123"},
            {"name": "XyZ7pQ9rS2T1uV8wA3bC6dE4fG0h", "value": "csrf456"},
        ]))

        mock_browser = MagicMock()
        mock_browser.contexts = [mock_context]

        mock_p = MagicMock()
        mock_p.chromium.connect_over_cdp = MagicMock(return_value=asyncio.sleep(0.01, result=mock_browser))

        with patch("auth.playwright_session.is_cdp_available", return_value=True):
            with patch("playwright.async_api.async_playwright") as mock_pw:
                mock_pw_instance = MagicMock()
                mock_pw_instance.__aenter__ = MagicMock(return_value=asyncio.sleep(0.01, result=mock_p))
                mock_pw_instance.__aexit__ = MagicMock(return_value=asyncio.sleep(0.01))
                mock_pw.return_value = mock_pw_instance

                result = asyncio.run(handler._async_refresh_and_extract_session(seller_id="123", target_api="api2"))
                self.assertIsNotNone(result)
                self.assertIn("connect.sid", result.get("cookies", {}))
                # Verify new_page was called to open Tab 2
                mock_context.new_page.assert_called()


if __name__ == "__main__":
    unittest.main()
