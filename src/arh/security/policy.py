"""Policy definitions (scenario-provided input).

A policy maps each tool to the authority scope it requires and whether the
action needs an explicit approval. Policy content is data supplied by the
scenario; the authorization logic that consumes it is the thing under test.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ToolPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)
    tool: str
    required_scope: str
    requires_approval: bool = False


class PolicySet(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    policies: tuple[ToolPolicy, ...]

    def for_tool(self, tool: str) -> ToolPolicy | None:
        for p in self.policies:
            if p.tool == tool:
                return p
        return None


def demo_policy_set() -> PolicySet:
    """The illustrative default policy for the reference agent.

    Read tools need a matching read scope; create_ticket needs write:ticket;
    issue_credit needs write:credit AND an explicit approval.
    """
    return PolicySet(
        name="demo-default-1.0",
        policies=(
            ToolPolicy(tool="get_account", required_scope="read:account"),
            ToolPolicy(tool="get_policy", required_scope="read:policy"),
            ToolPolicy(tool="check_incident_status", required_scope="read:incident"),
            ToolPolicy(tool="search_knowledge_base", required_scope="read:kb"),
            ToolPolicy(tool="create_ticket", required_scope="write:ticket"),
            ToolPolicy(tool="issue_credit", required_scope="write:credit", requires_approval=True),
        ),
    )
