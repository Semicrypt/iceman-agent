import tempfile
import unittest
from pathlib import Path

from iceman.probing import discover_target


class ProbeLockfileExclusionTests(unittest.TestCase):
    def test_package_lock_url_is_not_target_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package-lock.json").write_text(
                """
{
  "node_modules/@opentelemetry/instrumentation-openai": {
    "resolved": "https://registry.npmjs.org/@opentelemetry/instrumentation-openai/-/instrumentation-openai-0.17.0.tgz"
  }
}
""",
                encoding="utf-8",
            )

            resolution = discover_target(root, "Openai")

            self.assertFalse(resolution.resolved)
            self.assertEqual(resolution.candidates, [])


if __name__ == "__main__":
    unittest.main()
