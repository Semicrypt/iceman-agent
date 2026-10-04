import os
import unittest
from unittest.mock import patch

from iceman.http_context import (
    _headers_allowlist,
    _host_proxy_presence,
    _SAFE_RESPONSE_HEADERS,
)


class FakeHeaders:
    def items(self):
        return [
            ("Server", "cloudflare"),
            ("CF-Ray", "abc-XYZ"),
            ("Set-Cookie", "SECRET=do-not-show"),
            ("Authorization", "Bearer secret"),
            ("Location", "https://example.com/path?token=secret"),
        ]


class HttpContextSafetyTests(unittest.TestCase):
    def test_response_header_allowlist_excludes_secrets(self):
        kept = _headers_allowlist(
            FakeHeaders(),
            _SAFE_RESPONSE_HEADERS,
        )
        self.assertEqual(kept["server"], "cloudflare")
        self.assertEqual(kept["cf-ray"], "abc-XYZ")
        self.assertNotIn("set-cookie", kept)
        self.assertNotIn("authorization", kept)
        self.assertEqual(
            kept["location"],
            "https://example.com/path?[query omitted]",
        )

    @patch.dict(
        os.environ,
        {
            "HTTPS_PROXY": "http://secret-proxy.example:8080",
            "NO_PROXY": "localhost",
        },
        clear=True,
    )
    def test_proxy_values_are_never_returned(self):
        presence = _host_proxy_presence()
        self.assertEqual(presence, ["HTTPS_PROXY", "NO_PROXY"])
        self.assertNotIn("secret-proxy", repr(presence))


if __name__ == "__main__":
    unittest.main()
