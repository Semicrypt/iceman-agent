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


AZURE_PLAN = """
Terraform will perform the following actions:

  # azurerm_network_security_rule.admin will be created
  + resource "azurerm_network_security_rule" "admin" {
      + access                     = "Allow"
      + direction                  = "Inbound"
      + source_address_prefix      = "*"
      + destination_port_range     = "3389"
    }

  # azurerm_public_ip.web will be created
  + resource "azurerm_public_ip" "web" {
      + allocation_method = "Static"
    }

  # azurerm_mssql_server.main will be created
  + resource "azurerm_mssql_server" "main" {
      + public_network_access_enabled = true
      + minimum_tls_version           = "1.0"
    }

  # azurerm_storage_account.assets will be created
  + resource "azurerm_storage_account" "assets" {
      + allow_nested_items_to_be_public = true
      + min_tls_version                 = "TLS1_0"
    }

  # azurerm_key_vault.main will be created
  + resource "azurerm_key_vault" "main" {
      + purge_protection_enabled       = false
      + public_network_access_enabled  = true
    }

  # azurerm_role_assignment.owner will be created
  + resource "azurerm_role_assignment" "owner" {
      + role_definition_name = "Owner"
    }

  # azurerm_container_registry.app will be created
  + resource "azurerm_container_registry" "app" {
      + admin_enabled                 = true
      + public_network_access_enabled = true
    }

  # azurerm_kubernetes_cluster.platform will be created
  + resource "azurerm_kubernetes_cluster" "platform" {
      + private_cluster_enabled = false
      + local_account_disabled  = false
    }

  # azurerm_linux_virtual_machine.api will be created
  + resource "azurerm_linux_virtual_machine" "api" {
      + size = "Standard_B2s"
    }

  # azurerm_nat_gateway.outbound will be created
  + resource "azurerm_nat_gateway" "outbound" {
    }

Plan: 10 to add, 0 to change, 0 to destroy.
"""


def test_azure_resource_family_counts():
    analysis = analyze_terraform_plan(
        AZURE_PLAN
    )

    azure = analysis["azure"]

    assert azure["resource_count"] == 10
    assert azure["categories"] == {
        "compute": 1,
        "containers": 2,
        "networking": 2,
        "security": 1,
        "database": 1,
        "storage": 1,
        "secrets": 1,
        "identity": 1,
    }


def test_world_open_rdp_is_flagged():
    summary = summarize_terraform_plan(
        AZURE_PLAN
    )

    assert any(
        "world-open inbound access reaching RDP (3389)"
        in item
        for item in summary
    )


def test_public_ip_is_observed():
    summary = summarize_terraform_plan(
        AZURE_PLAN
    )

    assert any(
        "azurerm_public_ip.web"
        in item
        and "public IP resource"
        in item
        for item in summary
    )


def test_sql_public_network_and_legacy_tls_are_flagged():
    summary = summarize_terraform_plan(
        AZURE_PLAN
    )

    assert any(
        "azurerm_mssql_server.main"
        in item
        and "public network access"
        in item
        for item in summary
    )

    assert any(
        "azurerm_mssql_server.main"
        in item
        and "legacy minimum TLS version 1.0"
        in item
        for item in summary
    )


def test_storage_public_access_and_legacy_tls_are_flagged():
    summary = summarize_terraform_plan(
        AZURE_PLAN
    )

    assert any(
        "azurerm_storage_account.assets"
        in item
        and "public access"
        in item
        for item in summary
    )

    assert any(
        "azurerm_storage_account.assets"
        in item
        and "TLS1_0"
        in item
        for item in summary
    )


def test_key_vault_protection_and_exposure_are_flagged():
    summary = summarize_terraform_plan(
        AZURE_PLAN
    )

    assert any(
        "azurerm_key_vault.main"
        in item
        and "purge protection"
        in item
        for item in summary
    )

    assert any(
        "azurerm_key_vault.main"
        in item
        and "public network access"
        in item
        for item in summary
    )


def test_privileged_role_assignment_is_flagged():
    summary = summarize_terraform_plan(
        AZURE_PLAN
    )

    assert any(
        "azurerm_role_assignment.owner"
        in item
        and "Owner"
        in item
        for item in summary
    )


def test_acr_admin_account_is_flagged():
    summary = summarize_terraform_plan(
        AZURE_PLAN
    )

    assert any(
        "azurerm_container_registry.app"
        in item
        and "admin account"
        in item
        for item in summary
    )


def test_aks_public_api_and_local_accounts_are_observed():
    summary = summarize_terraform_plan(
        AZURE_PLAN
    )

    assert any(
        "azurerm_kubernetes_cluster.platform"
        in item
        and "without a private cluster API endpoint"
        in item
        for item in summary
    )

    assert any(
        "azurerm_kubernetes_cluster.platform"
        in item
        and "local cluster accounts enabled"
        in item
        for item in summary
    )


def test_azure_cost_awareness_has_no_dollar_estimate():
    summary = summarize_terraform_plan(
        AZURE_PLAN
    )

    cost = next(
        item
        for item in summary
        if item.startswith(
            "Azure cost awareness:"
        )
    )

    assert (
        "azurerm_linux_virtual_machine.api"
        in cost
    )
    assert (
        "azurerm_nat_gateway.outbound"
        in cost
    )
    assert "$" not in cost
    assert "No dollar estimate" in cost


def test_removed_world_open_nsg_rule_is_not_false_positive():
    output = """
  # azurerm_network_security_rule.admin will be updated in-place
  ~ resource "azurerm_network_security_rule" "admin" {
      ~ source_address_prefix = "*" -> "10.0.0.0/8"
        destination_port_range = "22"
        access                 = "Allow"
        direction              = "Inbound"
    }

Plan: 0 to add, 1 to change, 0 to destroy.
"""

    summary = summarize_terraform_plan(
        output
    )

    assert not any(
        "world-open inbound access"
        in item
        for item in summary
    )


def test_changed_storage_flag_uses_planned_side():
    output = """
  # azurerm_storage_account.assets will be updated in-place
  ~ resource "azurerm_storage_account" "assets" {
      ~ allow_nested_items_to_be_public = false -> true
    }

Plan: 0 to add, 1 to change, 0 to destroy.
"""

    summary = summarize_terraform_plan(
        output
    )

    assert any(
        "blob/container public access"
        in item
        for item in summary
    )


def test_destroy_only_azure_cost_resource_does_not_claim_cost_activity():
    output = """
  # azurerm_nat_gateway.old will be destroyed
  - resource "azurerm_nat_gateway" "old" {
    }

Plan: 0 to add, 0 to change, 1 to destroy.
"""

    summary = summarize_terraform_plan(
        output
    )

    assert not any(
        item.startswith(
            "Azure cost awareness:"
        )
        for item in summary
    )


def test_reasoner_routes_azure_terraform_analysis():
    decision = _decision(
        "analyze azure terraform plan"
    )

    assert (
        decision.action
        == ReasoningAction.EXECUTE_DEVOPS_ACTION
    )
    assert (
        decision.devops_action
        == DevOpsAction.TERRAFORM_EXPLAIN_PLAN
    )
