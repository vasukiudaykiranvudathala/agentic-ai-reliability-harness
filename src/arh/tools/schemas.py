"""Typed argument and result schemas for the six simulated tools.

Argument validation happens at the gateway before authorization. A tool call
with missing or malformed arguments is rejected; it never reaches execution
and never produces a side effect.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class _StrictArgs(BaseModel):
    # Reject unknown fields so a fabricated/extra parameter is a hard error.
    model_config = ConfigDict(extra="forbid")


class GetAccountArgs(_StrictArgs):
    account_id: str


class GetPolicyArgs(_StrictArgs):
    topic: str


class CheckIncidentArgs(_StrictArgs):
    service: str
    region: str


class SearchKnowledgeBaseArgs(_StrictArgs):
    query: str


class CreateTicketArgs(_StrictArgs):
    account_id: str
    category: str
    summary: str
    proposed_action: str
    incident_ref: str | None = None
    idempotency_key: str  # required: writes must be idempotent


class IssueCreditArgs(_StrictArgs):
    account_id: str
    amount: float
    reason: str
    idempotency_key: str  # required: writes must be idempotent


class ToolResult(BaseModel):
    status: Literal["ok", "error"]
    data: dict = {}
    error: str | None = None


# Maps tool name -> argument model. Used by the gateway to validate.
ARG_MODELS: dict[str, type[_StrictArgs]] = {
    "get_account": GetAccountArgs,
    "get_policy": GetPolicyArgs,
    "check_incident_status": CheckIncidentArgs,
    "search_knowledge_base": SearchKnowledgeBaseArgs,
    "create_ticket": CreateTicketArgs,
    "issue_credit": IssueCreditArgs,
}
