"""Trace identity and record assembly.

A lightweight collector for Increment (b): it mints ids and accumulates the
observable entries the runner produces, then assembles a typed ExecutionRecord.
The OpenTelemetry span model is layered on in the telemetry increment; the
record is the harness's canonical artifact regardless.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from .record import (
    AuthorizationDecisionRecord,
    ExecutionRecord,
    StateTransition,
    StepRecord,
    TerminationReason,
    ToolCallRecord,
)
from .scenario import SCHEMA_VERSION


def new_trace_id() -> str:
    return uuid.uuid4().hex


def new_record_id() -> str:
    return "rec-" + uuid.uuid4().hex[:16]


def now() -> datetime:
    return datetime.now(timezone.utc)


class TraceCollector:
    def __init__(self, scenario_id: str, adapter_mode: str, seed: int | None) -> None:
        self.trace_id = new_trace_id()
        self.record_id = new_record_id()
        self.scenario_id = scenario_id
        self.adapter_mode = adapter_mode
        self.seed = seed
        self.started_at = now()
        self.steps: list[StepRecord] = []
        self.tool_calls: list[ToolCallRecord] = []
        self.authorization_decisions: list[AuthorizationDecisionRecord] = []
        self.state_transitions: list[StateTransition] = []

    def add_step(self, step: StepRecord) -> None:
        self.steps.append(step)

    def add_tool_call(self, rec: ToolCallRecord) -> None:
        self.tool_calls.append(rec)

    def add_authorization(self, rec: AuthorizationDecisionRecord) -> None:
        self.authorization_decisions.append(rec)

    def add_state_transition(self, rec: StateTransition) -> None:
        self.state_transitions.append(rec)

    def assemble(
        self,
        *,
        reproducibility: dict,
        identity: dict,
        latencies: dict,
        termination_reason: TerminationReason,
        termination_detail: dict | None,
        final_response: str | None,
        invariants_held: bool,
        self_terminated: bool,
        terminated_by: str = "sue",
        model_calls: int,
        token_usage: dict | None = None,
        cost: dict | None = None,
    ) -> ExecutionRecord:
        return ExecutionRecord(
            schema_version=SCHEMA_VERSION,
            record_id=self.record_id,
            scenario_id=self.scenario_id,
            trace_id=self.trace_id,
            started_at=self.started_at,
            ended_at=now(),
            adapter_mode=self.adapter_mode,  # type: ignore[arg-type]
            seed=self.seed,
            reproducibility=reproducibility,
            identity=identity,
            steps=self.steps,
            tool_calls=self.tool_calls,
            authorization_decisions=self.authorization_decisions,
            state_transitions=self.state_transitions,
            token_usage=token_usage,
            cost=cost,
            latencies=latencies,
            termination_reason=termination_reason,
            termination_detail=termination_detail,
            final_response=final_response,
            invariants_held=invariants_held,
            self_terminated=self_terminated, terminated_by=terminated_by,
            model_calls=model_calls,
        )
