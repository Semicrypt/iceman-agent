import tempfile
import unittest
from pathlib import Path

from iceman.inspector import inspect_project


class InspectorTests(unittest.TestCase):
    def test_recursive_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "frontend").mkdir()
            (root / "backend").mkdir()
            (root / ".github" / "workflows").mkdir(parents=True)
            (root / "k8s").mkdir()

            (root / "frontend" / "package.json").write_text(
                '{"dependencies":{"react":"x","vite":"x","tailwindcss":"x"}}'
            )
            (root / "backend" / "package.json").write_text(
                '{"dependencies":{"express":"x","pg":"x","@aws-sdk/client-sts":"x"}}'
            )
            (root / "backend" / "Dockerfile").write_text("FROM node:20\n")
            (root / "docker-compose.yml").write_text(
                "services:\n  db:\n    image: postgres:17-alpine\n"
            )
            (root / ".github" / "workflows" / "ci.yml").write_text("name: CI\n")
            (root / "k8s" / "deployment.yaml").write_text(
                "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: app\n"
            )
            (root / "main.tf").write_text(
                'terraform { required_providers { aws = { source = "hashicorp/aws" } } }\nprovider "aws" {}\n'
            )

            result = inspect_project(root)
            tech = set(result.technologies)

            expected = {
                "Git",
                "React",
                "Vite",
                "Tailwind CSS",
                "Express",
                "PostgreSQL",
                "AWS SDK",
                "Docker",
                "Docker Compose",
                "GitHub Actions",
                "Kubernetes",
                "Terraform",
                "AWS",
            }
            self.assertTrue(expected.issubset(tech), expected - tech)


if __name__ == "__main__":
    unittest.main()
