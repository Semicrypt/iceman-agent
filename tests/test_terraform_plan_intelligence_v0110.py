from pathlib import Path

from iceman.devops_actions import (
    DevOpsAction,
    analyze_terraform_plan,
    summarize_terraform_plan,
)
from iceman.reasoner import ReasoningAction, reason_request
from iceman.session import SessionState


def _decision(prompt: str):
    return reason_request(prompt, SessionState(Path(".")))


AWS_AZURE_MIXED_PLAN = """
Terraform will perform the following actions:

  # aws_instance.web will be created
  + resource "aws_instance" "web" {
      + id = (known after apply)
    }

  # azurerm_resource_group.platform will be updated in-place
  ~ resource "azurerm_resource_group" "platform" {
        name = "platform"
    }

  # module.network.aws_security_group.old will be destroyed
  - resource "aws_security_group" "old" {
      - id = "sg-example" -> null
    }

Plan: 1 to add, 1 to change, 1 to destroy.
"""


REPLACEMENT_PLAN = """
Terraform will perform the following actions:

  # aws_instance.api must be replaced
-/+ resource "aws_instance" "api" {
      ~ ami = "ami-old" -> "ami-new" # forces replacement
    }

Plan: 1 to add, 0 to change, 1 to destroy.
"""


def test_analyze_mixed_aws_azure_plan():
    analysis = analyze_terraform_plan(AWS_AZURE_MIXED_PLAN)

    assert analysis["totals"] == {
        "add": 1,
        "change": 1,
        "destroy": 1,
    }
    assert analysis["providers"] == ["AWS", "AzureRM"]
    assert analysis["destructive"] is True

    actions = {
        item["address"]: item["action"]
        for item in analysis["changes"]
    }

    assert actions["aws_instance.web"] == "create"
    assert actions["azurerm_resource_group.platform"] == "update"
    assert (
        actions["module.network.aws_security_group.old"]
        == "destroy"
    )


def test_summary_reports_provider_awareness():
    summary = summarize_terraform_plan(AWS_AZURE_MIXED_PLAN)

    assert any(
        "AWS, AzureRM" in item
        for item in summary
    )
    assert any(
        "CREATE aws_instance.web [AWS]" in item
        for item in summary
    )
    assert any(
        "UPDATE azurerm_resource_group.platform [AzureRM]" in item
        for item in summary
    )


def test_summary_highlights_destructive_change():
    summary = summarize_terraform_plan(AWS_AZURE_MIXED_PLAN)

    assert any(
        "destructive-change warning" in item
        for item in summary
    )
    assert any(
        "DESTROY module.network.aws_security_group.old [AWS]"
        in item
        for item in summary
    )


def test_replacement_is_explicitly_destructive():
    analysis = analyze_terraform_plan(REPLACEMENT_PLAN)
    summary = summarize_terraform_plan(REPLACEMENT_PLAN)

    assert analysis["replacements"] == 1
    assert analysis["destructive"] is True
    assert any(
        "replacement warning" in item
        for item in summary
    )
    assert any(
        "REPLACE aws_instance.api [AWS]" in item
        for item in summary
    )


def test_no_changes_behavior_is_preserved():
    summary = summarize_terraform_plan(
        "No changes. Your infrastructure matches the configuration."
    )

    assert summary == [
        "Terraform reports no infrastructure changes."
    ]


def test_non_destructive_create_plan_has_no_destructive_warning():
    output = """
  # azurerm_storage_account.logs will be created
  + resource "azurerm_storage_account" "logs" {
    }

Plan: 1 to add, 0 to change, 0 to destroy.
"""

    analysis = analyze_terraform_plan(output)
    summary = summarize_terraform_plan(output)

    assert analysis["destructive"] is False
    assert not any(
        "destructive-change warning" in item
        for item in summary
    )


def test_data_read_is_classified_without_being_destructive():
    output = """
  # data.aws_caller_identity.current will be read during apply
 <= data "aws_caller_identity" "current" {
    }

Plan: 0 to add, 0 to change, 0 to destroy.
"""

    analysis = analyze_terraform_plan(output)

    assert analysis["changes"][0]["action"] == "read"
    assert analysis["changes"][0]["provider"] == "AWS"
    assert analysis["destructive"] is False


def test_resource_details_are_bounded():
    resource_lines = "\n".join(
        f"  # aws_s3_bucket.bucket_{index} will be created"
        for index in range(20)
    )
    output = (
        resource_lines
        + "\n\nPlan: 20 to add, 0 to change, 0 to destroy.\n"
    )

    summary = summarize_terraform_plan(output)

    rendered_resources = [
        item for item in summary
        if item.startswith("Terraform resource:")
    ]

    assert len(rendered_resources) == 12
    assert any(
        "8 additional planned resource action(s)"
        in item
        for item in summary
    )


def test_reasoner_routes_analyze_terraform_plan():
    decision = _decision("analyze terraform plan")

    assert decision.action == ReasoningAction.EXECUTE_DEVOPS_ACTION
    assert decision.devops_action == DevOpsAction.TERRAFORM_EXPLAIN_PLAN


def test_reasoner_routes_review_terraform_plan():
    decision = _decision("review the terraform plan")

    assert decision.action == ReasoningAction.EXECUTE_DEVOPS_ACTION
    assert decision.devops_action == DevOpsAction.TERRAFORM_EXPLAIN_PLAN
