import unittest
from unittest.mock import patch

from iceman.http_context import (
    HttpContextSnapshot,
    capture_host_context,
)


class HttpContextHostSelectionTests(unittest.TestCase):
    @patch("iceman.http_context._capture_host_once")
    def test_successful_host_sample_is_selected_from_mixed_attempts(self, capture):
        capture.side_effect = [
            HttpContextSnapshot(
                context="host",
                client="urllib",
                status_code=403,
            ),
            HttpContextSnapshot(
                context="host",
                client="urllib",
                status_code=200,
                final_url="https://openai.com/",
            ),
            HttpContextSnapshot(
                context="host",
                client="urllib",
                status_code=403,
            ),
        ]

        snapshot = capture_host_context(
            "https://openai.com/",
            attempts=3,
        )

        self.assertEqual(snapshot.status_code, 200)
        self.assertEqual(snapshot.sample_statuses, [403, 200, 403])
        self.assertIn("successful host sample", snapshot.note)


if __name__ == "__main__":
    unittest.main()
