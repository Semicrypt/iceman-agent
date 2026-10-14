import json
from io import BytesIO

import pytest

from iceman.ai import ollama_config as config


def test_default_model(monkeypatch, tmp_path):
    monkeypatch.delenv("ICEMAN_OLLAMA_MODEL", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    assert config.chosen_model() == (
        "qwen2.5:1.5b", "default"
    )


def test_persisted_model(monkeypatch, tmp_path):
    monkeypatch.delenv("ICEMAN_OLLAMA_MODEL", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    config.save_model(
        "qwen3.5:4b", ("qwen2.5:1.5b", "qwen3.5:4b")
    )

    assert config.chosen_model() == ("qwen3.5:4b", "saved")


def test_environment_override(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("ICEMAN_OLLAMA_MODEL", "qwen3.5:4b")

    assert config.chosen_model() == (
        "qwen3.5:4b", "environment"
    )


def test_uninstalled_model_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    with pytest.raises(ValueError):
        config.save_model("unknown:latest", ("qwen2.5:1.5b",))

    assert not config.config_path().exists()


def test_installed_model_discovery(monkeypatch):
    def fake_urlopen(url, timeout):
        assert url == config.OLLAMA_TAGS_URL
        return BytesIO(
            json.dumps({
                "models": [
                    {"name": "qwen3.5:4b"},
                    {"name": "qwen2.5:1.5b"},
                ]
            }).encode()
        )

    monkeypatch.setattr(config, "urlopen", fake_urlopen)

    assert config.installed_models() == (
        "qwen2.5:1.5b",
        "qwen3.5:4b",
    )
