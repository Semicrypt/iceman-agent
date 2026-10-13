from pathlib import Path

from iceman.devops_actions import (
    DevOpsAction,
    DevOpsExecutionResult,
    DevOpsPlan,
    DevOpsRequest,
)
from iceman.reasoner import (
    ReasoningAction,
    reason_request,
)
from iceman.session import SessionState
from iceman.terraform_remediation import (
    build_terraform_remediation_advice,
)


AWS_RISK_PLAN = """
Terraform will perform the following actions:

  # aws_security_group.admin will be created
  + resource "aws_security_group" "admin" {
      + ingress = [{
          + from_port   = 22
          + to_port     = 22
          + cidr_blocks = ["0.0.0.0/0"]
        }]
    }

  # aws_db_instance.main will be created
  + resource "aws_db_instance" "main" {
      + publicly_accessible = true
      + storage_encrypted   = false
    }

  # aws_iam_policy.admin will be created
  + resource "aws_iam_policy" "admin" {
      + policy = jsonencode({
          + Statement = [{
              + Action   = "*"
              + Resource = "*"
            }]
        })
    }

  # aws_nat_gateway.nat will be created
  + resource "aws_nat_gateway" "nat" {
    }

Plan: 4 to add, 0 to change, 0 to destroy.
"""


AZURE_RISK_PLAN = """
Terraform will perform the following actions:

  # azurerm_network_security_rule.admin will be created
  + resource "azurerm_network_security_rule" "admin" {
      + access                 = "Allow"
      + direction              = "Inbound"
      + source_address_prefix  = "*"
      + destination_port_range = "3389"
    }

  # azurerm_role_assignment.owner will be created
  + resource "azurerm_role_assignment" "owner" {
      + role_definition_name = "Owner"
    }

  # azurerm_container_registry.app will be created
  + resource "azurerm_container_registry" "app" {
      + admin_enabled = true
    }

Plan: 3 to add, 0 to change, 0 to destroy.
"""


REPLACEMENT_PLAN = """
Terraform will perform the following actions:

  # aws_instance.api must be replaced
-/+ resource "aws_instance" "api" {
      ~ ami = "ami-old" -> "ami-new"
    }

Plan: 1 to add, 0 to change, 1 to destroy.
"""


def _decision(prompt: str):
    return reason_request(
        prompt,
        SessionState(Path(".")),
    )


def test_aws_remediation_advice_is_bounded_and_manual():
    advice = build_terraform_remediation_advice(
        AWS_RISK_PLAN
    )

    assert advice.providers == ("AWS",)
    assert 1 <= len(advice.options) <= 10

    rendered = "\n".join(
        option.recommendation
        for option in advice.options
    )

    assert "approved source" in rendered
    assert "minimum actions" in rendered
    assert "terraform apply" in advice.note
    assert "terraform destroy" in advice.note


def test_aws_related_findings_are_grouped_by_resource():
    advice = build_terraform_remediation_advice(
        AWS_RISK_PLAN
    )

    rds = [
        option
        for option in advice.options
        if option.resource
        == "aws_db_instance.main"
    ]

    assert len(rds) == 1
    assert "publicly accessible" in rds[0].finding
    assert "storage encryption" in rds[0].finding


def test_azure_remediation_covers_rbac_and_registry():
    advice = build_terraform_remediation_advice(
        AZURE_RISK_PLAN
    )

    rendered = "\n".join(
        option.finding
        for option in advice.options
    )

    assert "RDP (3389)" in rendered
    assert "highly privileged role 'Owner'" in rendered
    assert "registry admin account" in rendered


def test_azure_public_ip_gets_remediation_option():
    advice = build_terraform_remediation_advice(
        AZURE_RISK_PLAN
        + """
  # azurerm_public_ip.web will be created
  + resource "azurerm_public_ip" "web" {
      + allocation_method = "Static"
    }
"""
    )

    option = next(
        item
        for item in advice.options
        if item.resource == "azurerm_public_ip.web"
    )

    assert option.risk == "medium"
    assert "public exposure" in option.recommendation


def test_replacement_plan_gets_destructive_review_option():
    advice = build_terraform_remediation_advice(
        REPLACEMENT_PLAN
    )

    assert advice.destructive is True
    assert advice.options
    assert advice.options[0].risk == "high"
    assert "replacement" in advice.options[0].finding.lower()
    assert "fresh Terraform plan" in advice.options[0].recommendation


def test_cost_advice_does_not_invent_prices():
    advice = build_terraform_remediation_advice(
        AWS_RISK_PLAN
    )

    cost = next(
        option
        for option in advice.options
        if option.title
        == "Review commonly billable resource changes"
    )

    combined = (
        cost.finding
        + cost.recommendation
        + cost.requires
        + cost.note
    )

    assert "$" not in combined
    assert "current provider pricing" in combined


def test_no_risky_changes_returns_no_options():
    advice = build_terraform_remediation_advice(
        """
No changes. Your infrastructure matches the configuration.
"""
    )

    assert advice.options == ()
    assert "No bounded" in advice.note


def test_session_recognizes_latest_terraform_remediation_context():
    state = SessionState(Path("."))

    plan = DevOpsPlan(
        request=DevOpsRequest(
            action=DevOpsAction.TERRAFORM_EXPLAIN_PLAN,
        ),
        workspace=Path("."),
        title="Terraform plan",
    )

    result = DevOpsExecutionResult(
        plan=plan,
        success=True,
    )

    state.remember_devops_result(result)

    assert (
        state.latest_remediation_context()
        == "terraform"
    )


def test_non_terraform_devops_result_is_not_terraform_context():
    state = SessionState(Path("."))

    plan = DevOpsPlan(
        request=DevOpsRequest(
            action=DevOpsAction.DOCKER_LIST,
        ),
        workspace=Path("."),
        title="Docker list",
    )

    result = DevOpsExecutionResult(
        plan=plan,
        success=True,
    )

    state.remember_devops_result(result)

    assert (
        state.latest_remediation_context()
        is None
    )


def test_reasoner_routes_generic_fix_question_to_remediation():
    decision = _decision(
        "how should i fix it"
    )

    assert (
        decision.action
        == ReasoningAction.RECOMMEND_REMEDIATION
    )


def test_reasoner_routes_explicit_terraform_remediation():
    decision = _decision(
        "recommend terraform remediation"
    )

    assert (
        decision.action
        == ReasoningAction.RECOMMEND_REMEDIATION
    )
