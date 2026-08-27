"""
Unit tests for cURL command parsing and session helper.
"""

import unittest
from unittest.mock import MagicMock, patch

from auth.chrome_session import parse_curl_command, extract_full_session_from_chrome


class TestChromeSessionHelper(unittest.TestCase):
    def test_parse_curl_command_standard(self):
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

    def test_parse_curl_command_windows_escape(self):
        curl_cmd = (
            'curl --url ^"https://suv-flipkart.seller-support.fkcloud.it/getSellerDetails?sellerId=218598a2b41c4bcd^" '
            '-H ^"FK-CSRF-TOKEN: M2tLWNFjU6pTANACxW0cBLWg^" '
            '-b ^"connect.sid=s%3Aabc123; is_login=true^"'
        )
        parsed = parse_curl_command(curl_cmd)
        self.assertEqual(parsed["cookies"]["connect.sid"], "s%3Aabc123")
        self.assertEqual(parsed["cookies"]["is_login"], "true")
        self.assertEqual(parsed["headers"]["FK-CSRF-TOKEN"], "M2tLWNFjU6pTANACxW0cBLWg")
        self.assertEqual(parsed["headers"]["fk-csrf-token"], "M2tLWNFjU6pTANACxW0cBLWg")

    def test_extract_full_session_from_chrome_delegates_to_playwright(self):
        mock_data = {
            "cookies": {"connect.sid": "mock_123"},
            "headers": {"FK-CSRF-TOKEN": "token_abc"},
        }
        with patch.object(
            PlaywrightSessionHandler, "refresh_and_extract_session", return_value=mock_data
        ):
            from auth.playwright_session import PlaywrightSessionHandler
            res = extract_full_session_from_chrome(seller_id="218598a2b41c4bcd")
            self.assertEqual(res, mock_data)


if __name__ == "__main__":
    unittest.main()
