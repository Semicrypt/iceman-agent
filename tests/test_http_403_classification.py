import unittest

from iceman.probing import _http_failure_cause


class Http403ClassificationTests(unittest.TestCase):
    def test_403_is_not_reduced_to_auth_only(self):
        cause = _http_failure_cause(403)

        self.assertIn("WAF", cause)
        self.assertIn("bot protection", cause)
        self.assertIn("authentication/authorization", cause)
        self.assertIn("User-Agent", cause)


if __name__ == "__main__":
    unittest.main()
