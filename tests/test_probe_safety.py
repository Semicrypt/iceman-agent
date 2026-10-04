import tempfile
import unittest
from pathlib import Path

from iceman.probing import investigate_target


class ProbeSafetyTests(unittest.TestCase):
    def test_metadata_target_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "checks.js").write_text(
                """
const check = {
  name: "Metadata",
  url: "http://169.254.169.254/latest/meta-data/"
};
""",
                encoding="utf-8",
            )

            result = investigate_target(root, "Metadata")

            self.assertTrue(result.blocked)
            self.assertIn("metadata", result.likely_cause.lower())
            self.assertEqual(result.steps, [])

    def test_embedded_credentials_are_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "checks.js").write_text(
                """
const check = {
  name: "Secret",
  url: "https://user:pass@example.com/status"
};
""",
                encoding="utf-8",
            )

            result = investigate_target(root, "Secret")
            self.assertTrue(result.blocked)
            self.assertIn("credentials", result.likely_cause.lower())


if __name__ == "__main__":
    unittest.main()
