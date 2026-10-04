import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iceman.probing import (
    TargetCandidate,
    TargetResolution,
    resolve_target,
)


class ProbeResolutionFallbackTests(unittest.TestCase):
    @patch("iceman.probing.discover_postgres_target")
    @patch("iceman.probing.discover_target")
    def test_database_used_when_files_do_not_resolve(
        self,
        discover_target,
        discover_postgres_target,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            discover_target.return_value = TargetResolution(
                subject="Openai",
                resolved=False,
                note="No file target.",
            )
            candidate = TargetCandidate(
                subject="Openai",
                url="https://example.com/health",
                source_file="PostgreSQL services.url",
                line_number=None,
                score=100,
                context="Openai -> https://example.com/health",
                source_kind="database",
            )
            discover_postgres_target.return_value = TargetResolution(
                subject="Openai",
                resolved=True,
                selected=candidate,
                confidence="high",
            )

            resolution = resolve_target(root, "Openai")
            self.assertTrue(resolution.resolved)
            self.assertEqual(
                resolution.selected.source_kind,
                "database",
            )

    @patch("iceman.probing.discover_postgres_target")
    @patch("iceman.probing.discover_target")
    def test_strong_file_resolution_wins(
        self,
        discover_target,
        discover_postgres_target,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = TargetCandidate(
                subject="Openai",
                url="https://example.com/health",
                source_file="checks.js",
                line_number=4,
                score=20,
                context="",
                source_kind="file",
            )
            discover_target.return_value = TargetResolution(
                subject="Openai",
                resolved=True,
                selected=candidate,
                confidence="high",
            )

            resolution = resolve_target(root, "Openai")

            self.assertEqual(
                resolution.selected.source_kind,
                "file",
            )
            discover_postgres_target.assert_not_called()


if __name__ == "__main__":
    unittest.main()
