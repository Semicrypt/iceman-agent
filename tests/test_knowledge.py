from unittest.mock import patch
from urllib.error import URLError

from iceman.knowledge import (
    answer_knowledge_question,
    is_knowledge_question,
)


def test_technical_question_is_recognized():
    assert is_knowledge_question("What is Kubernetes?")
    assert is_knowledge_question("Explain AWS IAM")
    assert is_knowledge_question("Compare Docker and Kubernetes")


def test_operational_commands_are_not_knowledge_questions():
    assert not is_knowledge_question("Start Minikube")
    assert not is_knowledge_question("Delete my Kubernetes namespace")
    assert not is_knowledge_question("Inspect my project")
    assert not is_knowledge_question("Push my Git changes")


def test_offline_knowledge_works_without_ollama(monkeypatch):
    monkeypatch.setenv("ICEMAN_KNOWLEDGE_MODE", "offline")

    answer = answer_knowledge_question("What is Kubernetes?")

    assert answer is not None
    assert "Pods" in answer


def test_ollama_failure_falls_back_safely(monkeypatch):
    monkeypatch.setenv("ICEMAN_KNOWLEDGE_MODE", "auto")

    with patch(
        "iceman.knowledge.urlopen",
        side_effect=URLError("Ollama unavailable"),
    ):
        answer = answer_knowledge_question("What is Docker?")

    assert answer is not None
    assert "containers" in answer
