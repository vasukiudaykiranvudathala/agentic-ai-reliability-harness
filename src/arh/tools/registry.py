"""Tool registration and versioning.

The registry is what the model is shown (names, versions, arg schemas) and
what the gateway consults to resolve a tool. It carries no authority.
"""
from __future__ import annotations

from ..model.base import ToolDefinition


def default_tool_definitions() -> list[ToolDefinition]:
    v = "1.1"
    return [
        ToolDefinition(name="get_account", version=v, description="Fetch account record by id.",
                       args_schema={"account_id": "str"}),
        ToolDefinition(name="get_policy", version=v, description="Fetch remediation policy by topic.",
                       args_schema={"topic": "str"}),
        ToolDefinition(name="check_incident_status", version=v,
                       description="Check whether a service/region has an active incident.",
                       args_schema={"service": "str", "region": "str"}),
        ToolDefinition(name="search_knowledge_base", version=v, description="Search internal KB.",
                       args_schema={"query": "str"}),
        ToolDefinition(name="create_ticket", version=v, description="Create a remediation ticket.",
                       args_schema={"account_id": "str", "category": "str", "summary": "str",
                                    "proposed_action": "str", "incident_ref": "str?",
                                    "idempotency_key": "str"}),
        ToolDefinition(name="issue_credit", version=v,
                       description="Issue an account credit. Sensitive: requires authority and approval.",
                       args_schema={"account_id": "str", "amount": "float", "reason": "str",
                                    "idempotency_key": "str"}),
    ]
