from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path


IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    ".next",
    ".cache",
    "coverage",
    "__pycache__",
    ".terraform",
    "vendor",
}

MAX_FILES = 5000
MAX_TEXT_BYTES = 512_000


@dataclass
class Detection:
    category: str
    technology: str
    evidence: list[str] = field(default_factory=list)


@dataclass
class Inspection:
    path: Path
    detections: list[Detection]
    scanned_files: int
    skipped_files: int

    @property
    def technologies(self) -> list[str]:
        return [d.technology for d in self.detections]


def _iter_files(root: Path):
    count = 0

    for current_dir, dirnames, filenames in os.walk(root):
        # Prune generated/heavy directories before walking into them.
        dirnames[:] = [name for name in dirnames if name not in IGNORED_DIRS]

        for filename in filenames:
            if count >= MAX_FILES:
                return

            path = Path(current_dir) / filename
            count += 1
            yield path


def _relative(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _read_text(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_TEXT_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _read_json(path: Path) -> dict:
    text = _read_text(path)
    if not text:
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _dependency_names(package_json: dict) -> set[str]:
    names: set[str] = set()
    for section in ("dependencies", "devDependencies", "peerDependencies"):
        values = package_json.get(section, {})
        if isinstance(values, dict):
            names.update(values.keys())
    return names


def inspect_project(path: Path) -> Inspection:
    root = path.resolve()

    detections: dict[tuple[str, str], Detection] = {}
    scanned_files = 0
    skipped_files = 0

    def add(category: str, technology: str, evidence: str) -> None:
        key = (category, technology)
        detection = detections.setdefault(
            key,
            Detection(category=category, technology=technology),
        )
        if evidence not in detection.evidence and len(detection.evidence) < 5:
            detection.evidence.append(evidence)

    if (root / ".git").exists():
        add("Source Control", "Git", ".git directory")

    files = list(_iter_files(root))
    scanned_files = len(files)

    for file in files:
        rel = _relative(root, file)
        name = file.name.lower()
        suffix = file.suffix.lower()

        if name == "dockerfile" or name.startswith("dockerfile."):
            add("Infrastructure", "Docker", rel)

        if name in {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}:
            add("Infrastructure", "Docker Compose", rel)

        if suffix == ".tf":
            add("Infrastructure", "Terraform", rel)
            text = _read_text(file).lower()
            if 'provider "aws"' in text or "hashicorp/aws" in text:
                add("Cloud", "AWS", rel)

        if ".github/workflows" in rel.replace("\\", "/"):
            add("CI/CD", "GitHub Actions", rel)

        if name == "package.json":
            package = _read_json(file)
            deps = _dependency_names(package)
            add("Runtime", "Node.js", rel)

            if "react" in deps:
                add("Frontend", "React", rel)
            if "next" in deps:
                add("Frontend", "Next.js", rel)
            if "vite" in deps:
                add("Frontend Tooling", "Vite", rel)
            if "tailwindcss" in deps or "@tailwindcss/postcss" in deps or "@tailwindcss/vite" in deps:
                add("Frontend Tooling", "Tailwind CSS", rel)
            if "express" in deps:
                add("Backend", "Express", rel)
            if "socket.io" in deps:
                add("Backend", "Socket.IO", rel)
            if "pg" in deps or "postgres" in deps:
                add("Database", "PostgreSQL", rel)
            if "mongoose" in deps or "mongodb" in deps:
                add("Database", "MongoDB", rel)
            if any(dep == "@aws-sdk/client-sts" or dep.startswith("@aws-sdk/") for dep in deps):
                add("Cloud", "AWS SDK", rel)

        if name in {"requirements.txt", "pyproject.toml", "pipfile"}:
            text = _read_text(file).lower()
            add("Runtime", "Python", rel)
            if "boto3" in text:
                add("Cloud", "AWS SDK", rel)
            if "fastapi" in text:
                add("Backend", "FastAPI", rel)
            if "flask" in text:
                add("Backend", "Flask", rel)
            if "django" in text:
                add("Backend", "Django", rel)

        if name in {".env", ".env.example", ".env.local", ".env.production", ".env.development"}:
            add("Configuration", "Environment Variables", rel)

        if suffix in {".yml", ".yaml"}:
            text = _read_text(file)
            lowered = text.lower()
            if re.search(r"(?m)^\s*kind:\s*(deployment|service|ingress|statefulset|daemonset|job|cronjob|configmap|secret)\s*$", lowered):
                add("Infrastructure", "Kubernetes", rel)

        if name in {"schema.prisma"}:
            add("Database Tooling", "Prisma", rel)

        if suffix in {".sql"}:
            # SQL alone does not prove PostgreSQL, but it is useful project evidence.
            add("Database", "SQL", rel)

        if name in {"nginx.conf", "default.conf"} and "nginx" in rel.lower():
            add("Infrastructure", "Nginx", rel)

    # Docker Compose often reveals database/cloud components not present in package manifests.
    for file in files:
        if file.name.lower() in {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}:
            text = _read_text(file).lower()
            rel = _relative(root, file)
            if "postgres:" in text or "image: postgres" in text:
                add("Database", "PostgreSQL", rel)
            if "mongo:" in text or "image: mongo" in text:
                add("Database", "MongoDB", rel)
            if "redis:" in text or "image: redis" in text:
                add("Database", "Redis", rel)

    # A README can provide light context, but it never overrides concrete detections.
    readme = root / "README.md"
    if readme.exists():
        text = _read_text(readme).lower()
        if "aws" in text:
            add("Cloud", "AWS", "README.md")

    ordered = sorted(
        detections.values(),
        key=lambda d: (d.category.lower(), d.technology.lower()),
    )

    return Inspection(
        path=root,
        detections=ordered,
        scanned_files=scanned_files,
        skipped_files=skipped_files,
    )
