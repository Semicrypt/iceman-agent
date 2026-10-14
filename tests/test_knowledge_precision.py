import json
from io import BytesIO
from unittest.mock import patch

from iceman.knowledge import answer_knowledge_question
from iceman.knowledge_facts import build_grounding


def test_kubernetes_precision():
    facts = build_grounding(
        "Explain Kubernetes architecture and Pods"
    )

    assert "kube-apiserver exposes" in facts
    assert "share an IP address" in facts
    assert "Scale Pod replicas" in facts


def test_runtime_and_cloud_precision():
    runtime = build_grounding(
        "Compare Docker and Kubernetes"
    )
    cloud = build_grounding(
        "Compare AWS EKS and Azure AKS"
    )

    assert "cri-dockerd" in runtime
    assert "Pod IP addresses" in cloud
    assert "Horizontal Pod Autoscaler" in cloud
    assert "inherently better" in cloud


def test_ollama_receives_precision_notes(monkeypatch):
    monkeypatch.setenv("ICEMAN_KNOWLEDGE_MODE", "auto")

    captured = {}

    def fake_urlopen(request, timeout):
        captured.update(json.loads(request.data))
        return BytesIO(
            b'{"response":"Pods share networking.",'
            b'"done":true,"done_reason":"stop"}\n'
        )

    with patch(
        "iceman.knowledge.urlopen",
        side_effect=fake_urlopen,
    ):
        answer = answer_knowledge_question(
            "Explain Kubernetes architecture and Pods"
        )

    assert "share an IP address" in captured["prompt"]
    assert "kube-apiserver exposes" in captured["prompt"]
    assert answer == "Pods share networking."


def test_eks_vertical_autoscaling_reference():
    from iceman.knowledge_facts import build_grounding

    notes = build_grounding("Compare AWS EKS and Azure AKS")

    assert "Both Amazon EKS and Azure AKS can use VPA" in notes
    assert "vertical-pod-autoscaler.html" in notes
