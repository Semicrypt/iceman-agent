"""Read-only Cloud and DevOps knowledge answering."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from urllib.request import Request, urlopen

from iceman.redaction import redact_text
from iceman.knowledge_facts import build_grounding
from iceman.ai.ollama_config import chosen_model


QUESTION = re.compile(
    r"^\s*(?:please\s+)?"
    r"(?:what(?:'s|\s+(?:is|are|does))|"
    r"how\s+(?:does|do)|explain|describe|compare|"
    r"teach\s+me|why\s+(?:is|does)|difference\s+between)\b",
    re.IGNORECASE,
)

TOPIC = re.compile(
    r"\b(?:aws|azure|gcp|google cloud|cloud|devops|"
    r"kubernetes|k8s|kubectl|minikube|docker|container|pods?|"
    r"helm|terraform|opentofu|ansible|cloudformation|"
    r"linux|bash|nginx|dns|tls|ssl|networking|"
    r"ci/cd|cicd|jenkins|github actions|gitlab|argocd|"
    r"prometheus|grafana|cloudwatch|"
    r"iam|ec2|eks|aks|gke|vpc|vnet|s3)\b",
    re.IGNORECASE,
)

FOUNDATIONS = {
    "kubernetes": (
        "Kubernetes is an open-source platform for orchestrating "
        "containerized applications. It manages deployment, scaling, "
        "networking, and recovery. Pods are its smallest deployable units."
    ),
    "docker": (
        "Docker provides tools for building, packaging, and running "
        "applications in containers. Kubernetes can orchestrate containers "
        "but does not require Docker as its container runtime."
    ),
    "terraform": (
        "Terraform is an Infrastructure as Code tool. It describes "
        "infrastructure declaratively, builds execution plans, and "
        "tracks managed resources through state."
    ),
    "iam": (
        "Identity and Access Management controls identities, permissions, "
        "and access to resources. Follow least privilege: grant only "
        "the permissions required for a specific task."
    ),
    "aws": (
        "Amazon Web Services provides cloud infrastructure and managed "
        "services for compute, storage, networking, databases, and more."
    ),
    "azure": (
        "Microsoft Azure provides cloud services including virtual "
        "machines, networking, databases, containers, and identity."
    ),
    "gcp": (
        "Google Cloud provides infrastructure and managed services "
        "for computing, storage, Kubernetes, networking, and data."
    ),
}

SYSTEM_PROMPT = """
You are Iceman, a Cloud and DevOps technical knowledge assistant.

Answer the user's technical question accurately and clearly.
Use practical examples when helpful.
Distinguish established facts from uncertain claims.
Do not claim that you inspected the user's computer or cloud resources.
Never claim to have executed commands or changed infrastructure.
This is an explanation-only request.
If the question requires current information you cannot verify, say so.
Prefer concise, well-structured technical explanations.
Use the supplied official-documentation reference notes as factual constraints.
Never contradict those notes. If insufficient, acknowledge uncertainty.
For comparisons, give concrete differences rather than generic claims.
Avoid repetition. Prefer responses under 180 words.
Do not claim that documentation was retrieved live.
Never invent documentation citations or URLs.
Do not infer that a cloud provider lacks a capability simply because it was not mentioned in the reference notes.
Compare actual capabilities rather than declaring one cloud platform universally superior.
Explain Kubernetes Pods accurately: their containers share networking and are scheduled together on one node.

Answer in 2 to 4 short paragraphs, usually under 180 words.
Avoid unnecessary repetition.
Complete your final sentence before ending.
For Kubernetes questions, distinguish Pods from containers and do not imply that Docker is required as its runtime.
"""


def is_knowledge_question(question: str) -> bool:
    """Accept explicit technical questions, not operational instructions."""
    return bool(
        len(question) <= 2000
        and QUESTION.search(question)
        and TOPIC.search(question)
    )


def _offline_answer(question: str) -> str:
    explanations = [
        answer
        for keyword, answer in FOUNDATIONS.items()
        if re.search(
            rf"(?<!\w){re.escape(keyword)}(?!\w)",
            question,
            re.IGNORECASE,
        )
    ]

    if explanations:
        return "\n\n".join(explanations[:3])

    return (
        "This is a Cloud or DevOps knowledge question, but I don't "
        "have a sufficiently detailed built-in explanation yet. "
        "For broader offline technical answers, connect an "
        "optional local language model through Ollama."
    )


def _ollama_answer(
    question: str,
    on_update: Callable[[str], None] | None = None,
) -> str:
    model, _source = chosen_model()

    payload = {
        "model": model,
        "prompt": (
            SYSTEM_PROMPT
            + "\n\nBundled reference notes (not live-fetched):\n"
            + build_grounding(question)
            + "\n\nUser question:\n"
            + redact_text(question)
        ),
        "stream": True,
        "think": False,
        "options": {
            "temperature": 0.2,
            "num_predict": 512,
        },
    }

    request = Request(
        "http://127.0.0.1:11434/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    parts = []
    total_bytes = 0
    done_reason = None

    with urlopen(request, timeout=120) as response:
        for line in response:
            total_bytes += len(line)

            if total_bytes > 2_000_000:
                raise ValueError("Ollama response exceeded limit")

            if not line.strip():
                continue

            event = json.loads(line)

            if not isinstance(event, dict):
                raise ValueError("Invalid Ollama response")

            if event.get("error"):
                raise ValueError("Ollama reported an error")

            chunk = event.get("response", "")

            if not isinstance(chunk, str):
                raise ValueError("Invalid response chunk")

            if chunk:
                parts.append(chunk)

                if on_update is not None:
                    on_update("".join(parts))

            if event.get("done") is True:
                done_reason = event.get("done_reason")
                break

    answer = "".join(parts)

    if not answer.strip():
        raise ValueError("Ollama returned an empty response")

    cleaned = re.sub(
        r"[\x00-\x08\x0b-\x1f\x7f]",
        "",
        answer,
    ).strip()

    if done_reason == "length":
        cleaned += (
            "\n\n[Answer may be incomplete: the model reached "
            "its generation limit. Ask to continue.]"
        )

    return cleaned


def answer_knowledge_question(
    question: str,
    on_update: Callable[[str], None] | None = None,
) -> str | None:
    if not is_knowledge_question(question):
        return None

    mode = os.getenv(
        "ICEMAN_KNOWLEDGE_MODE", "auto"
    ).strip().lower()

    if mode == "off":
        return None

    if mode != "offline":
        try:
            return _ollama_answer(question, on_update=on_update)
        except (OSError, ValueError, TypeError, KeyError):
            pass

    answer = _offline_answer(question)

    if on_update is not None:
        on_update(answer)

    return answer
