import tempfile
import unittest
from pathlib import Path

from iceman.probing import discover_application_check_profile


class CheckProfileAmbiguityTests(unittest.TestCase):
    def test_equal_strength_implementations_are_not_guessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("a.js", "b.js"):
                (root / name).write_text(
                    """
const axios = require("axios");
async function checkService(service) {
  try {
    await axios.get(service.url, { timeout: 5000 });
  } catch (error) {
    console.log(`❌ ${service.name} | DOWN`);
  }
}
console.log("Monitoring Cycle Started");
""",
                    encoding="utf-8",
                )

            profile = discover_application_check_profile(root)
            self.assertIsNone(profile)


if __name__ == "__main__":
    unittest.main()
