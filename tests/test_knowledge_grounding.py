import json
from io import BytesIO
from unittest.mock import patch

from iceman.knowledge import answer_knowledge_question
from iceman.knowledge_facts import build_grounding


def test_kubernetes_facts():
    notes = build_grounding("Explain Kubernetes architecture")

    assert "control plane and worker nodes" in notes
    assert "one node" in notes
    assert "does not require Docker Engine" in notes


def test_cloud_comparison_facts():
    notes = build_grounding("Compare AWS EKS and Azure AKS")

    assert "Amazon VPC CNI" in notes
    assert "Vertical Pod Autoscaler" in notes
    assert "Azure Private Link" in notes


def test_iam_grounding():
    notes = build_grounding("Explain AWS IAM and least privilege")

    assert "permissions" in notes
    assert "best-practices.html" in notes


def test_ollama_receives_grounded_prompt(monkeypatch):
    monkeypatch.setenv("ICEMAN_KNOWLEDGE_MODE", "auto")
    captured = {}

    def fake_urlopen(request, timeout):
        captured.update(json.loads(request.data))
        return BytesIO(
            b'{"response":"A Kubernetes Pod runs on one node.",'
            b'"done":true,"done_reason":"stop"}\n'
        )

    with patch(
        "iceman.knowledge.urlopen",
        side_effect=fake_urlopen,
    ):
        result = answer_knowledge_question(
            "Explain Kubernetes Pods"
        )

    assert "one node" in captured["prompt"]
    assert "kubernetes.io" in captured["prompt"]
    assert result == "A Kubernetes Pod runs on one node."


def test_truncated_answer_is_identified(monkeypatch):
    monkeypatch.setenv("ICEMAN_KNOWLEDGE_MODE", "auto")

    response = BytesIO(
        b'{"response":"An unfinished answer",'
        b'"done":true,"done_reason":"length"}\n'
    )

    with patch(
        "iceman.knowledge.urlopen",
        return_value=response,
    ):
        answer = answer_knowledge_question(
            "What is Kubernetes?"
        )

    assert "may be incomplete" in answer
