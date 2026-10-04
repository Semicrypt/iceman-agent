from __future__ import annotations

import platform
import shutil
import sys
from dataclasses import dataclass


@dataclass
class ToolCheck:
    name: str
    installed: bool
    path: str | None


def system_summary() -> dict[str, str]:
    return {
        "OS": platform.platform(),
        "Python": sys.version.split()[0],
        "Architecture": platform.machine(),
    }


def check_tools() -> list[ToolCheck]:
    tools = ["git", "gh", "docker", "aws", "terraform", "kubectl"]
    return [
        ToolCheck(name=tool, installed=bool(path := shutil.which(tool)), path=path)
        for tool in tools
    ]
