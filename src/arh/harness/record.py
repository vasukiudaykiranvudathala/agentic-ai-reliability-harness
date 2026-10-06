"""Typed execution record.

The canonical, assertable artifact of one workflow run, cross-linked to a
trace by trace_id. It captures observable actions, tool calls, authorization
decisions, and state transitions. It does not capture private chain-of-thought;
if the model emits reasoning text in its output it is treated as output and
redacted, not recorded as telemetry.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

AdapterMode = Literal["scripted", "transcript", "live"]

TerminationReason = Literal[
    "completed",
    "step_budget",
    "tool_call_budget",
    "token_budget",
    "wallclock_budget",
    "invariant_violation",
    "loop_exact",
    "loop_oscillation",
    "stall",
    "amplification",
    "watchdog",
]


class InvariantCheck(BaseModel):
    index: int          # position of the invariant in the scenario list
    holds: bool
    evidence: dict


class StepRecord(BaseModel):
    index: int
    kind: Literal["tool_call", "final_answer"]
    tool: str | None = None
    tool_version: str | None = None
    arguments_redacted: dict | None = None
    arguments_hash: str | None = None
    gateway_status: str | None = None
    gateway_reason: str | None = None
    invariant_checks: list[InvariantCheck] = []


class ToolCallRecord(BaseModel):
    index: int
    tool: str
    tool_version: str | None
    status: str
    reason: str
    arguments_hash: str | None = None
    result: dict = {}  # tool result data (control-plane evidence; redact in production)
    commit_status: str = "not_applicable"   # not_started | not_committed | committed | duplicate_suppressed | unknown
    delivery_status: str = "delivered"       # delivered | timeout | error | malformed


class AuthorizationDecisionRecord(BaseModel):
    index: int
    tool: str
    decision: str
    reason: str
    policy: str | None = None
    approval_result: str | None = None


class StateTransition(BaseModel):
    step_index: int
    tickets: int
    credits: int
    side_effects: int


class ExecutionRecord(BaseModel):
    schema_version: str
    record_id: str
    scenario_id: str
    trace_id: str
    started_at: datetime
    ended_at: datetime
    adapter_mode: AdapterMode
    seed: int | None
    reproducibility: dict
    identity: dict
    steps: list[StepRecord]
    tool_calls: list[ToolCallRecord]
    authorization_decisions: list[AuthorizationDecisionRecord]
    state_transitions: list[StateTransition]
    token_usage: dict | None
    cost: dict | None
    latencies: dict
    termination_reason: TerminationReason
    termination_detail: dict | None = None
    final_response: str | None
    invariants_held: bool
    self_terminated: bool = True
    terminated_by: str = "sue"   # sue | control_plane | watchdog
    model_calls: int
    evaluation_results: list[dict] = []  # populated by evaluators in Increment (c)
