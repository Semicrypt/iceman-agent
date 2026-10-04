import tempfile
import unittest
from pathlib import Path

from iceman.probing import discover_target


class ProbeDiscoveryTests(unittest.TestCase):
    def test_resolves_subject_and_nearby_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "checks.js").write_text(
                """
const checks = [
  {
    name: "Openai",
    url: "https://example.com/health"
  }
];
""",
                encoding="utf-8",
            )

            resolution = discover_target(root, "Openai")

            self.assertTrue(resolution.resolved)
            self.assertIsNotNone(resolution.selected)
            self.assertEqual(
                resolution.selected.url,
                "https://example.com/health",
            )

    def test_unknown_subject_is_not_resolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "checks.js").write_text(
                'const url = "https://example.com";',
                encoding="utf-8",
            )

            resolution = discover_target(root, "Missing Target")
            self.assertFalse(resolution.resolved)


if __name__ == "__main__":
    unittest.main()
