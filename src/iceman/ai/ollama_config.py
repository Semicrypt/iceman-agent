"""Persistent Ollama model preference and local discovery."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.request import urlopen

DEFAULT_MODEL = "qwen2.5:1.5b"
OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"


def config_path() -> Path:
    xdg = os.getenv("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "iceman" / "settings.json"


def chosen_model() -> tuple[str, str]:
    """Return model name and preference source."""
    override = os.getenv("ICEMAN_OLLAMA_MODEL", "").strip()
    if override:
        return override, "environment"

    try:
        data = json.loads(config_path().read_text())
        selected = data.get("ollama_model")
        if isinstance(selected, str) and selected.strip():
            return selected.strip(), "saved"
    except (OSError, ValueError, TypeError, AttributeError):
        pass

    return DEFAULT_MODEL, "default"


def installed_models() -> tuple[str, ...] | None:
    """Return installed names, or None if Ollama is unavailable."""
    try:
        with urlopen(OLLAMA_TAGS_URL, timeout=1.5) as response:
            data = json.loads(response.read(500_000))

        models = data["models"]
        if not isinstance(models, list):
            return None

        names = {
            item["name"]
            for item in models
            if isinstance(item, dict)
            and isinstance(item.get("name"), str)
        }
        return tuple(sorted(names))

    except (OSError, ValueError, TypeError, KeyError):
        return None


def save_model(name: str, available: tuple[str, ...]) -> None:
    """Persist only a model confirmed installed on this machine."""
    if name not in available:
        raise ValueError("The selected model is not installed.")

    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"ollama_model": name}, indent=2) + "\n"
    )
