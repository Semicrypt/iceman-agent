from __future__ import annotations

from dataclasses import dataclass

from iceman.devops_actions import analyze_terraform_plan


_RISK_ORDER = {
    "high": 3,
    "medium": 2,
    "low": 1,
}


@dataclass(frozen=True)
class TerraformRemediationOption:
    number: int
    title: str
    provider: str
    resource: str
    risk: str
    finding: str
    recommendation: str
    requires: str
    note: str = ""


@dataclass(frozen=True)
class TerraformRemediationAdvice:
    providers: tuple[str, ...]
    destructive: bool
    plan_summary: str
    options: tuple[TerraformRemediationOption, ...]
    note: str


def _risk_and_recommendation(
    finding: str,
) -> tuple[str, str, str]:
    lower = finding.lower()

    if (
        "world-open ingress" in lower
        or "world-open inbound" in lower
    ):
        return (
            "high",
            (
                "Restrict inbound access to explicitly approved source "
                "networks or trusted security controls. Preserve only the "
                "ports that are operationally required."
            ),
            (
                "Operator-approved source ranges/security relationships and "
                "confirmation of the service's actual access requirements."
            ),
        )

    if (
        "wildcard action" in lower
        or "highly privileged role" in lower
    ):
        return (
            "high",
            (
                "Replace broad permissions with the minimum actions, "
                "resources, and scope required by the workload."
            ),
            (
                "An authoritative list of operations and resources the "
                "principal must access. Iceman will not invent permissions."
            ),
        )

    if (
        "publicly accessible" in lower
        or "database warning" in lower
        and "public network access" in lower
    ):
        return (
            "high",
            (
                "Prefer private database connectivity and narrowly scoped "
                "network controls unless public reachability is an explicit "
                "architecture requirement."
            ),
            (
                "Confirmation of the intended client networks, application "
                "topology, and database connectivity requirements."
            ),
        )

    if "disables storage encryption" in lower:
        return (
            "high",
            (
                "Enable provider-supported encryption at rest and review "
                "whether the change requires migration or resource "
                "replacement before proceeding."
            ),
            (
                "Encryption/key-management requirements and a reviewed "
                "migration or replacement plan if Terraform reports one."
            ),
        )

    if (
        "public-access protection" in lower
        or "public acl" in lower
        or "public principal" in lower
        or "blob/container public access" in lower
    ):
        return (
            "high",
            (
                "Disable unintended public object access and restore the "
                "provider's public-access protections unless public content "
                "is an explicit requirement."
            ),
            (
                "Confirmation of whether anonymous/public object access is "
                "actually required."
            ),
        )

    if "registry admin account" in lower:
        return (
            "high",
            (
                "Disable the registry admin account and use scoped identity/"
                "RBAC-based authentication where the deployment model allows."
            ),
            (
                "The workload identity and minimum registry permissions "
                "required for push/pull operations."
            ),
        )

    if "legacy minimum tls" in lower:
        return (
            "medium",
            (
                "Raise the minimum TLS setting to an organization-approved "
                "modern version after validating client compatibility."
            ),
            (
                "Compatibility confirmation for all legitimate clients "
                "before tightening the minimum TLS version."
            ),
        )

    if "purge protection" in lower:
        return (
            "medium",
            (
                "Enable purge protection when it matches the required secret "
                "recovery and retention policy."
            ),
            (
                "The organization's Key Vault recovery, retention, and "
                "deletion policy."
            ),
        )

    if "local cluster accounts enabled" in lower:
        return (
            "medium",
            (
                "Disable local cluster accounts when centralized identity "
                "and operational break-glass requirements permit it."
            ),
            (
                "A verified administrator-access and break-glass strategy."
            ),
        )

    if "without a private cluster api endpoint" in lower:
        return (
            "medium",
            (
                "Consider a private cluster API endpoint when the network "
                "architecture and operator-access model support it."
            ),
            (
                "Confirmed operator connectivity and cluster-management "
                "network requirements."
            ),
        )

    if (
        "public ipv4" in lower
        or "public ip resource" in lower
        or "network interface with a public ip" in lower
        or "internet-facing" in lower
        or "public frontend ip" in lower
        or "exposure observation" in lower
        and "public network access" in lower
    ):
        return (
            "medium",
            (
                "Confirm that public exposure is intentional. If it is not, "
                "prefer private addressing/connectivity and controlled ingress."
            ),
            (
                "The workload's intended exposure model and approved ingress "
                "path. Iceman will not invent network ranges."
            ),
        )

    return (
        "medium",
        (
            "Review the reported Terraform configuration and change the HCL "
            "toward the intended least-privilege, least-exposure state."
        ),
        (
            "Operator confirmation of the intended infrastructure state."
        ),
    )


def _resource_from_finding(
    finding: str,
) -> tuple[str, str] | None:
    prefixes = (
        ("AWS ", "AWS"),
        ("Azure ", "Azure"),
    )

    provider = None
    for prefix, label in prefixes:
        if finding.startswith(prefix):
            provider = label
            break

    if provider is None or ":" not in finding:
        return None

    remainder = finding.split(":", 1)[1].strip()
    if not remainder:
        return None

    resource = remainder.split(None, 1)[0].rstrip(".")
    if not resource:
        return None

    return provider, resource


def build_terraform_remediation_advice(
    plan_output: str,
) -> TerraformRemediationAdvice:
    analysis = analyze_terraform_plan(plan_output)

    providers = tuple(
        str(item)
        for item in analysis.get("providers", [])
    )

    totals = analysis.get(
        "totals",
        {
            "add": 0,
            "change": 0,
            "destroy": 0,
        },
    )

    plan_summary = (
        f"{totals['add']} to add, "
        f"{totals['change']} to change, "
        f"{totals['destroy']} to destroy"
    )

    grouped: dict[
        tuple[str, str],
        dict[str, object],
    ] = {}

    for key in ("aws", "azure"):
        provider_analysis = analysis.get(key)
        if not isinstance(provider_analysis, dict):
            continue

        findings = provider_analysis.get(
            "findings",
            [],
        )
        if not isinstance(findings, list):
            continue

        for raw_finding in findings:
            finding = str(raw_finding)
            identity = _resource_from_finding(
                finding
            )
            if identity is None:
                continue

            provider, resource = identity
            risk, recommendation, requires = (
                _risk_and_recommendation(
                    finding
                )
            )

            group = grouped.setdefault(
                identity,
                {
                    "provider": provider,
                    "resource": resource,
                    "risk": risk,
                    "findings": [],
                    "recommendations": [],
                    "requires": [],
                },
            )

            if (
                _RISK_ORDER[risk]
                > _RISK_ORDER[str(group["risk"])]
            ):
                group["risk"] = risk

            cast_findings = group["findings"]
            cast_recommendations = group[
                "recommendations"
            ]
            cast_requires = group["requires"]

            if finding not in cast_findings:
                cast_findings.append(finding)

            if recommendation not in cast_recommendations:
                cast_recommendations.append(
                    recommendation
                )

            if requires not in cast_requires:
                cast_requires.append(requires)

    ranked = sorted(
        grouped.values(),
        key=lambda item: (
            -_RISK_ORDER[str(item["risk"])],
            str(item["provider"]),
            str(item["resource"]),
        ),
    )

    options: list[TerraformRemediationOption] = []

    if bool(analysis.get("destructive")):
        replacements = int(
            analysis.get("replacements", 0)
        )
        replacement_note = (
            f" The plan includes {replacements} replacement(s)."
            if replacements
            else ""
        )

        options.append(
            TerraformRemediationOption(
                number=0,
                title=(
                    "Review destructive or replacement changes "
                    "before any future apply"
                ),
                provider="Terraform",
                resource="plan",
                risk="high",
                finding=(
                    "The analyzed plan contains destruction or "
                    "replacement."
                    + replacement_note
                ),
                recommendation=(
                    "Confirm lifecycle impact, dependency ordering, "
                    "backup/recovery requirements, and whether each "
                    "destructive action is intended. Then rerun a "
                    "fresh Terraform plan."
                ),
                requires=(
                    "Explicit operator review of the resources being "
                    "destroyed/replaced and their recovery or migration "
                    "requirements."
                ),
                note=(
                    "Iceman will not run terraform apply or terraform "
                    "destroy."
                ),
            )
        )

    for group in ranked:
        if len(options) >= 9:
            break

        findings = list(group["findings"])
        recommendations = list(
            group["recommendations"]
        )
        requirements = list(group["requires"])

        options.append(
            TerraformRemediationOption(
                number=0,
                title=(
                    f"Harden {group['resource']}"
                ),
                provider=str(group["provider"]),
                resource=str(group["resource"]),
                risk=str(group["risk"]),
                finding=" ".join(
                    str(item)
                    for item in findings
                ),
                recommendation=" ".join(
                    str(item)
                    for item in recommendations
                ),
                requires=" ".join(
                    str(item)
                    for item in requirements
                ),
                note=(
                    "After changing HCL, run terraform fmt/validate "
                    "and analyze a fresh plan before considering apply."
                ),
            )
        )

    cost_resources: list[str] = []
    for key in ("aws", "azure"):
        provider_analysis = analysis.get(key)
        if not isinstance(provider_analysis, dict):
            continue

        values = provider_analysis.get(
            "cost_resources",
            [],
        )
        if isinstance(values, list):
            for value in values:
                rendered = str(value)
                if rendered not in cost_resources:
                    cost_resources.append(rendered)

    if cost_resources and len(options) < 10:
        options.append(
            TerraformRemediationOption(
                number=0,
                title="Review commonly billable resource changes",
                provider="Cloud",
                resource="plan",
                risk="low",
                finding=(
                    "The plan touches commonly billable resource "
                    "classes: "
                    + ", ".join(cost_resources[:8])
                    + "."
                ),
                recommendation=(
                    "Confirm each resource is required and review sizing, "
                    "lifecycle, region, and service-specific cost controls "
                    "before any future apply."
                ),
                requires=(
                    "current provider pricing plus the intended region, "
                    "SKU/size, usage pattern, and account/subscription "
                    "context for any monetary estimate."
                ),
                note=(
                    "Iceman does not invent cloud prices from Terraform "
                    "plan text."
                ),
            )
        )

    numbered = tuple(
        TerraformRemediationOption(
            number=index,
            title=option.title,
            provider=option.provider,
            resource=option.resource,
            risk=option.risk,
            finding=option.finding,
            recommendation=option.recommendation,
            requires=option.requires,
            note=option.note,
        )
        for index, option in enumerate(
            options[:10],
            start=1,
        )
    )

    if numbered:
        note = (
            "Terraform remediation is advisory-only in Iceman v0.11.0. "
            "No HCL is edited and neither terraform apply nor terraform "
            "destroy is prepared or executed. Make deliberate configuration "
            "changes, "
            "then rerun a fresh Terraform plan to verify the result."
        )
    else:
        note = (
            "No bounded AWS/Azure security, exposure, destructive-change, "
            "or cost-awareness remediation was established from this plan. "
            "Manual plan review is still required before any future apply."
        )

    return TerraformRemediationAdvice(
        providers=providers,
        destructive=bool(
            analysis.get("destructive")
        ),
        plan_summary=plan_summary,
        options=numbered,
        note=note,
    )
