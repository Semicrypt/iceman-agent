import tempfile
import unittest
from pathlib import Path

from iceman.probing import discover_application_check_profile


class CheckProfileDiscoveryTests(unittest.TestCase):
    def test_minerva_style_axios_monitor_is_discovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backend = root / "backend" / "services"
            backend.mkdir(parents=True)

            (backend / "monitor.service.js").write_text(
                """
const axios = require("axios");

async function checkService(service) {
  try {
    const started = Date.now();
    const response = await axios.get(service.url, {
      timeout: 5000
    });
    console.log(`✅ ${service.name} | ${response.status} | ${Date.now() - started} ms`);
  } catch (error) {
    console.log(`❌ ${service.name} | DOWN`);
  }
}

console.log("Monitoring Cycle Started");
""",
                encoding="utf-8",
            )

            profile = discover_application_check_profile(root)

            self.assertIsNotNone(profile)
            self.assertEqual(profile.client, "axios")
            self.assertEqual(profile.method, "GET")
            self.assertEqual(profile.timeout_ms, 5000)
            self.assertEqual(
                profile.success_policy,
                "Axios default HTTP 2xx",
            )
            self.assertEqual(profile.accepted_status_min, 200)
            self.assertEqual(profile.accepted_status_max, 299)
            self.assertEqual(profile.fidelity, "high")
            self.assertIn(
                "backend/services/monitor.service.js",
                profile.source_file,
            )

    def test_sensitive_header_is_omitted_and_reduces_fidelity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backend = root / "backend"
            backend.mkdir()

            (backend / "monitor.js").write_text(
                """
const axios = require("axios");
async function checkService(service) {
  try {
    await axios.get(service.url, {
      timeout: 4000,
      headers: {
        Authorization: token,
        "User-Agent": "MinervaMonitor/1.0"
      }
    });
    console.log(`ok ${service.name}`);
  } catch (error) {
    console.log(`❌ ${service.name} | DOWN`);
  }
}
console.log("Monitoring Cycle Started");
""",
                encoding="utf-8",
            )

            profile = discover_application_check_profile(root)

            self.assertIsNotNone(profile)
            self.assertIn("Authorization", profile.omitted_header_names)
            self.assertEqual(
                profile.headers["User-Agent"],
                "MinervaMonitor/1.0",
            )
            self.assertNotEqual(profile.fidelity, "high")


if __name__ == "__main__":
    unittest.main()
