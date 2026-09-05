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
            mock_refresh.assert_called_once_with(seller_id="seller_123", target_api="api2")
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


if __name__ == "__main__":
    unittest.main()
