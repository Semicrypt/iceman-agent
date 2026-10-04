from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Plan:
    understood: bool
    explanation: str
    command: str | None = None


def _has_all(text: str, *words: str) -> bool:
    return all(word in text for word in words)


def make_plan(prompt: str) -> Plan:
    text = " ".join(prompt.lower().strip().split())

    if _has_all(text, "docker", "container"):
        return Plan(True, "List Docker containers on this machine.", "docker ps -a")

    if _has_all(text, "docker", "image"):
        return Plan(True, "List Docker images on this machine.", "docker images")

    if "git status" in text or _has_all(text, "git", "status"):
        return Plan(True, "Show the Git working-tree status.", "git status")

    if _has_all(text, "git", "branch"):
        return Plan(True, "Show local Git branches.", "git branch")

    if ("aws" in text and "identity" in text) or ("aws" in text and "who" in text) or "who am i on aws" in text:
        return Plan(
            True,
            "Show the AWS identity for the currently configured credentials.",
            "aws sts get-caller-identity",
        )

    if _has_all(text, "terraform", "plan"):
        return Plan(True, "Create a Terraform execution plan.", "terraform plan")

    if _has_all(text, "terraform", "validate"):
        return Plan(True, "Validate the Terraform configuration.", "terraform validate")

    if ("kubernetes" in text or "kubectl" in text or "k8s" in text) and "pods" in text:
        return Plan(True, "List Kubernetes pods in the current namespace.", "kubectl get pods")

    if ("kubernetes" in text or "kubectl" in text or "k8s" in text) and "nodes" in text:
        return Plan(True, "List Kubernetes nodes.", "kubectl get nodes")

    return Plan(
        False,
        "I don't understand that request yet. This request is not mapped to a trusted Iceman workflow or bounded command intent.",
        None,
    )
