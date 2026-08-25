"""
Unit tests for Chrome session & custom path resolution.
"""

import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from auth.chrome_session import (
    resolve_chrome_paths,
    get_chrome_user_data_path,
    extract_full_session_from_chrome,
    parse_curl_command,
)


class TestChromeSessionCustomPath(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_resolve_chrome_paths_placeholder(self):
        res = resolve_chrome_paths("Enter_YOUR_PATH")
        # Should ignore placeholder and fall back to default
        self.assertIsNotNone(res["user_data"])

    def test_resolve_chrome_paths_custom_exe(self):
        fake_exe = self.test_dir / "custom_chrome.exe"
        fake_exe.write_text("dummy binary", encoding="utf-8")

        res = resolve_chrome_paths(str(fake_exe))
        self.assertEqual(res["executable"], fake_exe)

    def test_resolve_chrome_paths_custom_install_dir(self):
        fake_dir = self.test_dir / "Google" / "Chrome" / "Application"
        fake_dir.mkdir(parents=True)
        fake_exe = fake_dir / "chrome.exe"
        fake_exe.write_text("dummy binary", encoding="utf-8")

        res = resolve_chrome_paths(str(fake_dir))
        self.assertEqual(res["executable"], fake_exe)

    def test_resolve_chrome_paths_custom_user_data_dir(self):
        fake_user_data = self.test_dir / "CustomUserData"
        fake_user_data.mkdir(parents=True)
        (fake_user_data / "Local State").write_text("{}", encoding="utf-8")

        res = resolve_chrome_paths(str(fake_user_data))
        self.assertEqual(res["user_data"], fake_user_data)

    def test_parse_curl_command(self):
        curl_cmd = (
            'curl -H "Host: example.com" '
            '-H "Cookie: connect.sid=s%3Atest12345; is_login=true; XyZ7pQ9rS2T1uV8wA3bC6dE4fG0h=csrf999" '
            '-H "x-marketplace-context: ALL"'
        )
        parsed = parse_curl_command(curl_cmd)
        self.assertEqual(parsed["cookies"]["connect.sid"], "s%3Atest12345")
        self.assertEqual(parsed["cookies"]["is_login"], "true")
        self.assertEqual(parsed["cookies"]["XyZ7pQ9rS2T1uV8wA3bC6dE4fG0h"], "csrf999")
        self.assertEqual(parsed["headers"]["x-marketplace-context"], "ALL")

    def test_extract_full_session_from_chrome_with_cookies(self):
        mock_cookies = {
            "connect.sid": "mock_sid_123",
            "is_login": "true",
            "XyZ7pQ9rS2T1uV8wA3bC6dE4fG0h": "mock_csrf_abc",
        }

        with patch("auth.chrome_session.extract_cookies_from_chrome_db", return_value=mock_cookies):
            session = extract_full_session_from_chrome(custom_path=str(self.test_dir), save_to_file=False)
            self.assertIsNotNone(session)
            self.assertEqual(session["cookies"]["connect.sid"], "mock_sid_123")
            self.assertEqual(session["headers"]["FK-CSRF-TOKEN"], "mock_csrf_abc")
            self.assertEqual(session["headers"]["fk-csrf-token"], "mock_csrf_abc")


if __name__ == "__main__":
    unittest.main()
