import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iceman.probing import discover_postgres_target


class ProbeNoDockerTests(unittest.TestCase):
    @patch(
        "iceman.probing.run_command",
        side_effect=PermissionError("docker unavailable"),
    )
    def test_database_fallback_gracefully_handles_unavailable_docker(
        self,
        run_command,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            resolution = discover_postgres_target(
                Path(tmp),
                "Openai",
            )

        self.assertFalse(resolution.resolved)
        self.assertIn(
            "No running Compose PostgreSQL",
            resolution.note,
        )


if __name__ == "__main__":
    unittest.main()
