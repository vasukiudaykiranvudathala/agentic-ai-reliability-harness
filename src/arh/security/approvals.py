"""Approval checkpoints for sensitive actions.

When policy marks an action as requiring approval, the gateway consults the
approval service before execution. The default service grants nothing, so a
sensitive action cannot proceed unless an approval is explicitly present. This
models the rule that an agent deciding to call a tool is not the same as the
action being approved.
"""
from __future__ import annotations

import hashlib
import json
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict


class ApprovalResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    result: Literal["approved", "denied", "not_required"]
    approver: str | None = None
    reason: str | None = None


@runtime_checkable
class ApprovalService(Protocol):
    def check(self, tool: str, arguments: dict, *, tool_version=None) -> ApprovalResult: ...


class AutoDenyApprovalService:
    """No approvals are granted. Sensitive actions are blocked by default."""

    def check(self, tool: str, arguments: dict, *, tool_version=None) -> ApprovalResult:
        return ApprovalResult(result="denied", reason="no_approval_on_record")


class PreApprovedApprovalService:
    """Grants approval for an explicit allow-list of (tool, account_id) pairs.

    Used by scenarios that legitimately carry a prior human approval.
    """

    def __init__(self, approvals: set[tuple[str, str]], approver: str = "duty-manager") -> None:
        self._approvals = approvals
        self._approver = approver

    def check(self, tool: str, arguments: dict, *, tool_version=None) -> ApprovalResult:
        key = (tool, str(arguments.get("account_id")))
        if key in self._approvals:
            return ApprovalResult(result="approved", approver=self._approver)
        return ApprovalResult(result="denied", reason="not_in_approval_list")


class BoundApprovalService:
    """Grants approval only for an action that matches a trusted fixture on tool,
    principal, and scope, is not expired, and (for single-use grants) has not
    already been consumed. This is what an approval should be: bound to the exact
    action, not a bare allow-list of tool/account pairs.
    """

    def __init__(self, fixtures, *, caller: str, clock=None) -> None:
        self._fixtures = list(fixtures)
        self._caller = caller
        self._clock = clock
        self._consumed: set[str] = set()

    @staticmethod
    def _scope_matches(scope: dict, arguments: dict) -> bool:
        # Every scoped field must match the request exactly (account, amount, ...).
        return all(str(arguments.get(k)) == str(v) for k, v in scope.items())

    @staticmethod
    def digest(tool: str, arguments: dict) -> str:
        """Canonical digest of the exact action (tool + arguments, excluding the
        idempotency key). A fixture that declares an action_digest binds the
        approval to this exact action."""
        material = {"tool": tool,
                    "args": {k: v for k, v in arguments.items() if k != "idempotency_key"}}
        return hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode()).hexdigest()[:16]

    def check(self, tool: str, arguments: dict, *, tool_version=None) -> ApprovalResult:
        now = self._clock.now_ms() if self._clock is not None else 0.0
        # Search ALL eligible fixtures before concluding no valid approval exists:
        # a mismatch on one fixture must not hide a matching later fixture.
        reasons: list[str] = []
        for f in self._fixtures:
            if f.tool != tool or f.principal != self._caller:
                continue
            if not self._scope_matches(f.scope, arguments):
                reasons.append("approval_scope_mismatch"); continue
            if f.issued_vt_ms is not None and now < f.issued_vt_ms:
                reasons.append("approval_not_yet_valid"); continue
            if f.expires_vt_ms is not None and now >= f.expires_vt_ms:
                reasons.append("approval_expired"); continue
            if f.action_digest is not None and f.action_digest != self.digest(tool, arguments):
                reasons.append("approval_digest_mismatch"); continue
            if f.tool_version is not None and f.tool_version != tool_version:
                reasons.append("approval_tool_version_unsupported"); continue
            if f.single_use and f.approval_id in self._consumed:
                reasons.append("approval_replayed"); continue
            if f.single_use:
                self._consumed.add(f.approval_id)
            return ApprovalResult(result="approved", approver=f.approver)
        return ApprovalResult(result="denied", reason=(reasons[0] if reasons else "no_matching_approval"))
