from io import BytesIO
from unittest.mock import patch

import pytest

from iceman.knowledge import answer_knowledge_question
from iceman.shell import run_request


def test_ollama_streams_incrementally(monkeypatch):
    monkeypatch.setenv("ICEMAN_KNOWLEDGE_MODE", "auto")

    response = BytesIO(
        b'{"response":"Kuber","done":false}\n'
        b'{"response":"netes","done":false}\n'
        b'{"done":true}\n'
    )

    updates = []

    with patch("iceman.knowledge.urlopen", return_value=response):
        answer = answer_knowledge_question(
            "What is Kubernetes?",
            on_update=updates.append,
        )

    assert answer == "Kubernetes"
    assert updates == ["Kuber", "Kubernetes"]


def test_educational_question_never_executes(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("ICEMAN_KNOWLEDGE_MODE", "offline")

    def forbidden(*args, **kwargs):
        pytest.fail("Educational question entered action execution")

    monkeypatch.setattr(
        "iceman.shell._dispatch_trusted_intent",
        forbidden,
    )
    monkeypatch.setattr(
        "iceman.shell.execute_with_confirmation",
        forbidden,
    )
    monkeypatch.setattr(
        "iceman.shell.make_plan",
        forbidden,
    )

    result = run_request(
        "Explain Kubernetes architecture, including "
        "control plane, nodes, and Pods.",
        project_path=tmp_path,
    )

    assert result == 0
