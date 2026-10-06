"""Tool gateway: the policy enforcement point (PEP).

This is the ONLY path from an agent's requested action to tool execution. For
every request it, in order:
  1. resolves the tool and its version from the registry;
  2. validates and parses arguments against the typed schema;
  3. asks the PDP for an authorization decision, failing CLOSED if the
     authorization service raises or returns an invalid result;
  4. if policy requires approval, consults the approval service;
  5. only then executes the simulator and lets the side effect commit.

Every request yields a GatewayOutcome recording the authorization decision,
the approval result, and the execution status, whether or not it executed.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ValidationError

from ..model.base import ToolCall, ToolDefinition
from ..security.approvals import ApprovalResult, ApprovalService
from ..security.authorization import AuthorizationDecision, AuthorizationService
from ..clock import Clock
from ..security.identity import Identity
from ..security.policy import PolicySet
from . import schemas as S
from .simulators import ToolSimulators


class GatewayOutcome(BaseModel):
    tool: str
    tool_version: str | None
    status: Literal["executed", "rejected", "error"]
    reason: str
    authorization: AuthorizationDecision | None = None
    approval: ApprovalResult | None = None
    result: S.ToolResult | None = None
    # Authoritative commit evidence retained for the HARNESS journal even when
    # the acknowledgment to the agent is lost. Never surfaced to the agent.
    committed_result: S.ToolResult | None = None


# A control that cannot produce a trustworthy decision is INDETERMINATE, not a
# policy deny. Both block execution; the distinction is recorded so an outage is
# not mistaken for a normal denial.
_INDETERMINATE = AuthorizationDecision(
    decision="indeterminate", reason="authorization_unavailable_fail_closed"
)


class ToolGateway:
    def __init__(
        self,
        *,
        simulators: ToolSimulators,
        authorizer: AuthorizationService,
        approvals: ApprovalService,
        policy_set: PolicySet,
        tool_defs: list[ToolDefinition],
        principal: Identity,
        clock: Clock | None = None,
        fault_injector=None,
    ) -> None:
        self._sim = simulators
        # The execution identity is bound to a trusted runtime context at
        # construction. It is never copied from model output, tool arguments,
        # conversation state, or user text.
        self._principal = principal
        self._faults = fault_injector
        self._authorizer = authorizer
        self._approvals = approvals
        self._policy_set = policy_set
        self._versions = {t.name: t.version for t in tool_defs}
        self._clock = clock
        self._action_seq = 0

    _WRITE_TOOLS = ("create_ticket", "issue_credit")

    def execute(self, call: ToolCall) -> GatewayOutcome:
        """The single path to tool execution. For state-changing tools it
        records an append-only journal entry for the attempt, committed or not,
        with before/after state hashes so a prohibited commit is detectable."""
        if call.tool not in self._WRITE_TOOLS:
            return self._execute_inner(call)
        store = self._sim.store
        before = store.snapshot().state_hash
        outcome = self._execute_inner(call)
        after = store.snapshot().state_hash
        self._journal(call, outcome, before, after)
        return outcome

    def _commit_status(self, outcome: GatewayOutcome) -> str:
        if outcome.status == "executed":
            data = outcome.result.data if outcome.result else {}
            return "duplicate_suppressed" if data.get("duplicate_suppressed") else "committed"
        r = outcome.reason or ""
        if r.startswith("authorization_denied"):
            return "rejected_authorization"
        if r.startswith("approval_not_granted"):
            return "rejected_approval"
        if r.startswith("invalid_arguments"):
            return "rejected_arguments"
        if r.startswith("fault_after_commit"):
            return "committed_ack_lost"
        return "error_uncommitted"

    def _journal(self, call: ToolCall, outcome: GatewayOutcome, before: str, after: str) -> None:
        self._action_seq += 1
        vt = self._clock.now_ms() if self._clock is not None else 0.0
        # Authoritative commit evidence: the delivered result if present, else the
        # committed_result retained across a lost acknowledgment.
        _src = outcome.result or outcome.committed_result
        _rdata = _src.data if _src else {}
        _rid = _rdata.get("ticket_id") or _rdata.get("credit_id") or _rdata.get("id")
        self._sim.store.record_action({
            "action_id": f"act-{self._action_seq:04d}",
            "tool": call.tool,
            "resource_id": _rid,
            "tool_version": outcome.tool_version,
            "idempotency_key": call.arguments.get("idempotency_key"),
            "principal": self._principal.caller,
            "authorization": (outcome.authorization.decision if outcome.authorization else None),
            "authorization_reason": (outcome.authorization.reason if outcome.authorization else None),
            "approval": (outcome.approval.result if outcome.approval else None),
            "attempted_vt_ms": vt,
            "commit_status": self._commit_status(outcome),
            "before_state_hash": before,
            "after_state_hash": after,
            "reason": outcome.reason,
        })

    def _execute_inner(self, call: ToolCall) -> GatewayOutcome:
        tool, arguments, identity = call.tool, call.arguments, self._principal
        version = self._versions.get(tool)
        if version is None:
            return GatewayOutcome(tool=tool, tool_version=None, status="rejected",
                                  reason="unknown_tool")

        # (2) argument validation -- fabricated/missing/extra args fail here.
        arg_model = S.ARG_MODELS.get(tool)
        try:
            parsed = arg_model(**arguments)
        except (ValidationError, TypeError) as exc:
            return GatewayOutcome(tool=tool, tool_version=version, status="rejected",
                                  reason=f"invalid_arguments:{type(exc).__name__}")

        # (3) authorization -- fail closed on any error, injected outage, or invalid result.
        # An authorization-service outage is a resilience fault (distinct from a
        # correct policy denial): the action is blocked and control health is
        # indeterminate, never allowed to proceed.
        if self._faults is not None:
            authz_inv = self._faults.begin_call("authorization_service", None)
            authz_fault = self._faults.fault_for("authorization_service", None, authz_inv, "before_execution")
            if authz_fault is not None:
                return GatewayOutcome(
                    tool=tool, tool_version=version, status="rejected",
                    reason=f"authorization_unavailable:{authz_fault.effect}",
                    authorization=AuthorizationDecision(
                        decision="indeterminate", reason="authorization_unavailable_fail_closed"))
        try:
            decision = self._authorizer.decide(tool, arguments, identity)
            if not isinstance(decision, AuthorizationDecision) or decision.decision not in (
                    "allow", "deny", "indeterminate"):
                decision = _INDETERMINATE   # a malformed decision is not trustworthy
        except Exception:  # noqa: BLE001 -- fail closed on any authz failure
            decision = _INDETERMINATE

        if decision.decision != "allow":
            # deny is expected policy behavior; indeterminate is a control or
            # dependency failure. Both block; the reason distinguishes them.
            prefix = "authorization_denied" if decision.decision == "deny" else "authorization_unavailable"
            return GatewayOutcome(tool=tool, tool_version=version, status="rejected",
                                  reason=f"{prefix}:{decision.reason}",
                                  authorization=decision)

        # (4) approval for sensitive actions.
        approval: ApprovalResult | None = None
        tp = self._policy_set.for_tool(tool)
        if tp is not None and tp.requires_approval:
            approval_prefix = "approval_not_granted"   # policy evaluated (approved/denied)
            try:
                approval = self._approvals.check(tool, arguments, tool_version=version)
            except Exception:  # noqa: BLE001 -- fail closed on approval failure
                # An outage is fail-closed: the policy did NOT evaluate the request.
                approval = ApprovalResult(result="denied", reason="approval_service_unavailable")
                approval_prefix = "approval_unavailable"
            if approval.result != "approved":
                return GatewayOutcome(tool=tool, tool_version=version, status="rejected",
                                      reason=f"{approval_prefix}:{approval.reason or approval.result}",
                                      authorization=decision, approval=approval)

        # (5) execute, with optional fault injection around the boundary.
        invocation = None
        if self._faults is not None:
            invocation = self._faults.begin_call("tool", tool)
            pre = self._faults.fault_for("tool", tool, invocation, "before_execution")
            if pre is not None:
                self._faults.apply_latency(pre)
                return GatewayOutcome(tool=tool, tool_version=version, status="error",
                                      reason=f"fault_{pre.effect}",
                                      authorization=decision, approval=approval)
        try:
            result = self._sim.dispatch(tool, parsed)  # side effect commits here for writes
        except Exception as exc:  # noqa: BLE001
            return GatewayOutcome(tool=tool, tool_version=version, status="error",
                                  reason=f"tool_execution_error:{type(exc).__name__}",
                                  authorization=decision, approval=approval)
        if self._faults is not None and invocation is not None:
            mid = self._faults.fault_for("tool", tool, invocation, "during_execution")
            if mid is not None:
                result = self._faults.transform(mid, result)
            post = self._faults.fault_for("tool", tool, invocation, "after_commit_before_response")
            if post is not None:
                self._faults.apply_latency(post)
                # The side effect committed, but the acknowledgment is lost. Keep
                # the authoritative commit evidence for the journal; the agent
                # still receives no result.
                return GatewayOutcome(tool=tool, tool_version=version, status="error",
                                      reason=f"fault_after_commit_{post.effect}",
                                      authorization=decision, approval=approval,
                                      committed_result=result)
        return GatewayOutcome(tool=tool, tool_version=version, status="executed",
                              reason="ok", authorization=decision, approval=approval,
                              result=result)
