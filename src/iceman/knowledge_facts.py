"""Curated offline reference notes from official technical documentation."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ReferenceNote:
    topic: str
    pattern: str
    facts: tuple[str, ...]
    source: str


NOTES = (
    ReferenceNote(
        "Kubernetes architecture",
        r"\b(?:kubernetes|k8s|pods?|kubelet|kubectl|minikube)\b",
        (
            "A Kubernetes cluster consists of a control plane and worker nodes.",
            "The kube-apiserver exposes the Kubernetes API; it is the "
            "control-plane front end, not a proxy for a separate API server.",
            "Control-plane components include kube-apiserver, etcd, "
            "kube-scheduler, and kube-controller-manager.",
            "Worker nodes run kubelet and a container runtime; "
            "kube-proxy is optional in some networking configurations.",
            "A Pod contains one or more closely coupled containers. "
            "A running Pod is bound to one node, not spread across nodes.",
            "Containers in a Pod share an IP address and network namespace "
            "and can share storage volumes.",
            "Containers in a Pod are co-scheduled on the same node. "
            "Scale Pod replicas, not individual containers within a Pod.",
            "Kubernetes does not require Docker Engine. "
            "Its built-in dockershim integration was removed in version 1.24.",
        ),
        "https://kubernetes.io/docs/concepts/overview/components/",
    ),
    ReferenceNote(
        "Container runtimes",
        r"\b(?:docker|containerd|cri-o|dockershim|container runtime)\b",
        (
            "Docker builds, packages, and runs containerized applications.",
            "Kubernetes orchestrates containerized workloads using Pods.",
            "Kubernetes nodes use compatible container runtimes such as "
            "containerd or CRI-O; Docker Engine is not mandatory.",
            "Docker Engine does not implement CRI directly. "
            "It requires an adapter such as cri-dockerd.",
        ),
        "https://kubernetes.io/docs/setup/production-environment/container-runtimes/",
    ),
    ReferenceNote(
        "Amazon EKS",
        r"\b(?:eks|elastic kubernetes service)\b",
        (
            "Amazon EKS manages Kubernetes control-plane infrastructure.",
            "EKS workloads can run on managed EC2 nodes, "
            "self-managed nodes, AWS Fargate, and other supported compute options.",
            "EKS commonly uses Amazon VPC CNI for Pod networking.",
            "The standard Amazon VPC CNI assigns Pod IP addresses "
            "from the VPC; do not describe it as generic overlay networking.",
            "EKS supports Kubernetes Horizontal Pod Autoscaler "
            "and multiple node autoscaling approaches.",
            "Node autoscaling can use Kubernetes Cluster Autoscaler, "
            "Karpenter, or EKS Auto Mode, depending on configuration.",
        ),
        "https://docs.aws.amazon.com/eks/latest/userguide/eks-architecture.html",
    ),
    ReferenceNote(
        "EKS vertical autoscaling",
        r"\b(?:eks|elastic kubernetes service)\b",
        (
            "Amazon EKS supports Kubernetes Vertical Pod Autoscaler "
            "when its components are installed and configured.",
            "VPA adjusts Pod resource requests, while HPA adjusts "
            "the number of Pod replicas.",
            "Both Amazon EKS and Azure AKS can use VPA.",
        ),
        "https://docs.aws.amazon.com/eks/latest/userguide/vertical-pod-autoscaler.html",
    ),
    ReferenceNote(
        "Azure AKS",
        r"\b(?:aks|azure kubernetes service)\b",
        (
            "Azure AKS is Microsoft's managed Kubernetes service.",
            "AKS networking supports Azure CNI networking models "
            "including overlay and flat networking.",
            "AKS supports Horizontal Pod Autoscaler and "
            "Vertical Pod Autoscaler, subject to configuration.",
            "Azure also supports cluster node autoscaling.",
            "Azure CNI Overlay uses a separate Pod address range; "
            "flat Azure CNI networking uses VNet addresses for Pods.",
            "Do not claim AKS is inherently better at scaling than EKS. "
            "Compare specific features and configurations instead.",
            "Azure Private Link provides private endpoints for "
            "supported services; Azure is not limited to public endpoints.",
        ),
        "https://learn.microsoft.com/en-us/azure/aks/use-vertical-pod-autoscaler",
    ),
    ReferenceNote(
        "Azure private networking",
        r"\b(?:private endpoint|private link|vpc endpoint)\b",
        (
            "Azure Private Link provides private connectivity to "
            "supported services using private endpoints in a virtual network.",
            "AWS VPC endpoints and Azure Private Link address "
            "related private-connectivity requirements, "
            "but their implementations are not identical.",
        ),
        "https://learn.microsoft.com/en-us/azure/private-link/private-endpoint-overview/",
    ),
    ReferenceNote(
        "AWS IAM",
        r"\b(?:aws iam|identity and access management|least privilege)\b",
        (
            "AWS IAM policies define permitted or denied actions "
            "on resources, optionally subject to conditions.",
            "Least privilege means granting only the permissions "
            "necessary to perform a particular task.",
            "Use appropriately scoped permissions and review "
            "them as workload requirements change.",
        ),
        "https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html",
    ),
)


def build_grounding(question: str) -> str:
    """Select relevant trusted notes without fetching websites."""

    matches = [
        note
        for note in NOTES
        if re.search(note.pattern, question, re.IGNORECASE)
    ]

    if not matches:
        return (
            "No curated reference note covers this question. "
            "Do not invent specific technical facts or citations. "
            "Acknowledge uncertainty where necessary."
        )

    sections = []

    for note in matches[:5]:
        facts = "\n".join(f"- {fact}" for fact in note.facts)

        sections.append(
            f"Topic: {note.topic}\n"
            f"{facts}\n"
            f"Official documentation: {note.source}"
        )

    return "\n\n".join(sections)
