import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from iceman.probing import investigate_target


class InvestigationNormalizationIntegrationTests(unittest.TestCase):
    @patch("iceman.probing._docker_compose_services", return_value=[])
    def test_investigation_boundary_normalizes_subject(
        self,
        compose_services,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            # No URL is needed for this regression; the important assertion is
            # that the stored/search subject becomes Openai, not the full phrase.
            result = investigate_target(
                root,
                "why Openai is down",
            )

            self.assertEqual(result.subject, "Openai")
            self.assertEqual(result.resolution.subject, "Openai")
            self.assertIn(
                '"Openai"',
                result.likely_cause,
            )
            self.assertNotIn(
                '"why Openai is down"',
                result.likely_cause,
            )


if __name__ == "__main__":
    unittest.main()
