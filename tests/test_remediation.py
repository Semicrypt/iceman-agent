import unittest

from iceman.remediation import extract_missing_upstream


class RemediationParsingTests(unittest.TestCase):
    def test_extracts_nginx_upstream(self):
        logs = 'nginx: host not found in upstream "backend" in /etc/nginx/conf.d/default.conf:9'
        self.assertEqual(extract_missing_upstream(logs), "backend")

    def test_no_upstream(self):
        self.assertIsNone(extract_missing_upstream("ordinary log output"))


if __name__ == "__main__":
    unittest.main()
