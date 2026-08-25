"""
Unit tests for AuthManager and Playwright Persistent Context session handling.
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
        self.profile_dir = self.test_dir / "browser_profile"

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

        auth = AuthManager(
            session_path=self.session_path,
            profile_dir=self.profile_dir,
        )

        self.assertEqual(auth.cookies.get("SESSION_ID"), "mock_cookie_12345")
        self.assertEqual(auth.headers.get("FK-CSRF-TOKEN"), "csrf_token_abc")
        self.assertEqual(auth.session.cookies.get("SESSION_ID"), "mock_cookie_12345")

    def test_login_with_playwright_persists_session(self):
        auth = AuthManager(
            session_path=self.session_path,
            profile_dir=self.profile_dir,
        )

        mock_login_output = {
            "cookies": {
                "AUTH_TOKEN": "token_xyz",
                "XyZ7pQ9rS2T1uV8wA3bC6dE4fG0h": "csrf_playwright",
            },
            "headers": {
                "User-Agent": "PlaywrightBrowser/1.0",
                "FK-CSRF-TOKEN": "csrf_playwright",
            },
        }

        with patch.object(
            auth.playwright_handler, "launch_login_session", return_value=mock_login_output
        ):
            success = auth.login_with_playwright()
            self.assertTrue(success)
            self.assertEqual(auth.cookies.get("AUTH_TOKEN"), "token_xyz")
            self.assertEqual(auth.headers.get("FK-CSRF-TOKEN"), "csrf_playwright")
            self.assertTrue(self.session_path.exists())

            # Verify written json
            with open(self.session_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
                self.assertEqual(saved["cookies"]["AUTH_TOKEN"], "token_xyz")

    def test_is_session_expired_status_codes(self):
        auth = AuthManager(
            session_path=self.session_path,
            profile_dir=self.profile_dir,
        )

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

    def test_refresh_session_triggers_playwright_login(self):
        auth = AuthManager(
            session_path=self.session_path,
            profile_dir=self.profile_dir,
        )

        with patch.object(auth, "login_with_playwright", return_value=True) as mock_login:
            refreshed = auth.refresh_session()
            self.assertTrue(refreshed)
            mock_login.assert_called_once()


class TestPlaywrightSessionHandler(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.profile_dir = self.test_dir / "browser_profile"

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_handler_initialization(self):
        handler = PlaywrightSessionHandler(
            profile_dir=self.profile_dir,
            base_url="https://test.example.com",
        )
        self.assertEqual(handler.profile_dir, self.profile_dir)
        self.assertTrue(self.profile_dir.exists())
        self.assertEqual(handler.base_url, "https://test.example.com")


if __name__ == "__main__":
    unittest.main()
