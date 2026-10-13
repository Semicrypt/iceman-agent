from pathlib import Path

from iceman.devops_actions import (
    DevOpsAction,
    analyze_terraform_plan,
    summarize_terraform_plan,
)
from iceman.reasoner import (
    ReasoningAction,
    reason_request,
)
from iceman.session import SessionState


def _decision(prompt: str):
    return reason_request(
        prompt,
        SessionState(Path(".")),
    )


AWS_PLAN = """
Terraform will perform the following actions:

  # aws_security_group.admin will be created
  + resource "aws_security_group" "admin" {
      + ingress = [
          + {
              + from_port   = 22
              + to_port     = 22
              + protocol    = "tcp"
              + cidr_blocks = ["0.0.0.0/0"]
            },
        ]
    }

  # aws_instance.web will be created
  + resource "aws_instance" "web" {
      + associate_public_ip_address = true
    }

  # aws_db_instance.main will be created
  + resource "aws_db_instance" "main" {
      + publicly_accessible = true
      + storage_encrypted   = false
    }

  # aws_s3_bucket_public_access_block.logs will be created
  + resource "aws_s3_bucket_public_access_block" "logs" {
      + block_public_acls       = false
      + block_public_policy     = false
      + ignore_public_acls      = false
      + restrict_public_buckets = false
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

  # aws_lb.app will be created
  + resource "aws_lb" "app" {
      + internal = false
    }

Plan: 7 to add, 0 to change, 0 to destroy.
"""


def test_aws_resource_family_counts():
    analysis = analyze_terraform_plan(
        AWS_PLAN
    )

    aws = analysis["aws"]

    assert aws["resource_count"] == 7
    assert aws["categories"] == {
        "compute": 1,
        "networking": 1,
        "security": 1,
        "load_balancing": 1,
        "database": 1,
        "storage": 1,
        "iam": 1,
    }


def test_world_open_ssh_is_flagged():
    summary = summarize_terraform_plan(
        AWS_PLAN
    )

    assert any(
        "world-open ingress reaching SSH (22)"
        in item
        for item in summary
    )


def test_ec2_public_ip_is_flagged():
    summary = summarize_terraform_plan(
        AWS_PLAN
    )

    assert any(
        "aws_instance.web"
        in item
        and "public IPv4" in item
        for item in summary
    )


def test_public_unencrypted_rds_is_flagged():
    summary = summarize_terraform_plan(
        AWS_PLAN
    )

    assert any(
        "aws_db_instance.main"
        in item
        and "publicly accessible" in item
        for item in summary
    )

    assert any(
        "aws_db_instance.main"
        in item
        and "disables storage encryption"
        in item
        for item in summary
    )


def test_s3_public_access_protection_is_flagged():
    summary = summarize_terraform_plan(
        AWS_PLAN
    )

    assert any(
        "aws_s3_bucket_public_access_block.logs"
        in item
        and "public-access protection" in item
        for item in summary
    )


def test_iam_wildcard_is_flagged():
    summary = summarize_terraform_plan(
        AWS_PLAN
    )

    assert any(
        "aws_iam_policy.admin"
        in item
        and "wildcard Action and Resource"
        in item
        for item in summary
    )


def test_cost_awareness_has_no_dollar_estimate():
    summary = summarize_terraform_plan(
        AWS_PLAN
    )

    cost = next(
        item
        for item in summary
        if item.startswith(
            "AWS cost awareness:"
        )
    )

    assert "aws_nat_gateway.nat" in cost
    assert "aws_db_instance.main" in cost
    assert "aws_lb.app" in cost
    assert "$" not in cost
    assert "No dollar estimate" in cost


def test_removed_world_open_rule_is_not_false_positive():
    output = """
  # aws_security_group.admin will be updated in-place
  ~ resource "aws_security_group" "admin" {
      ~ ingress = [
          - {
              - from_port   = 22
              - to_port     = 22
              - cidr_blocks = ["0.0.0.0/0"]
            },
          + {
              + from_port   = 22
              + to_port     = 22
              + cidr_blocks = ["10.0.0.0/8"]
            },
        ]
    }

Plan: 0 to add, 1 to change, 0 to destroy.
"""

    summary = summarize_terraform_plan(
        output
    )

    assert not any(
        "world-open ingress" in item
        for item in summary
    )


def test_changed_rds_value_uses_planned_side():
    output = """
  # aws_db_instance.main will be updated in-place
  ~ resource "aws_db_instance" "main" {
      ~ publicly_accessible = false -> true
    }

Plan: 0 to add, 1 to change, 0 to destroy.
"""

    summary = summarize_terraform_plan(
        output
    )

    assert any(
        "publicly accessible" in item
        for item in summary
    )


def test_module_name_with_underscore_does_not_break_aws_detection():
    output = """
  # module.network_stack.aws_security_group.old will be destroyed
  - resource "aws_security_group" "old" {
    }

Plan: 0 to add, 0 to change, 1 to destroy.
"""

    analysis = analyze_terraform_plan(
        output
    )

    change = analysis["changes"][0]

    assert change["provider"] == "AWS"
    assert (
        change["resource_type"]
        == "aws_security_group"
    )


def test_destroy_only_cost_resource_does_not_claim_cost_activity():
    output = """
  # aws_nat_gateway.old will be destroyed
  - resource "aws_nat_gateway" "old" {
    }

Plan: 0 to add, 0 to change, 1 to destroy.
"""

    summary = summarize_terraform_plan(
        output
    )

    assert not any(
        item.startswith(
            "AWS cost awareness:"
        )
        for item in summary
    )


def test_reasoner_routes_aws_terraform_analysis():
    decision = _decision(
        "analyze aws terraform plan"
    )

    assert (
        decision.action
        == ReasoningAction.EXECUTE_DEVOPS_ACTION
    )
    assert (
        decision.devops_action
        == DevOpsAction.TERRAFORM_EXPLAIN_PLAN
    )
