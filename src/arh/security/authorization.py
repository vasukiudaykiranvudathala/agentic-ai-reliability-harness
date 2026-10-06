"""Policy decision point (PDP).

Given a tool, its arguments, and the caller identity, the PDP returns an
allow/deny decision against the policy set. It fails closed: an unknown tool
or a tool with no policy is denied. Scope matching supports a trailing "*"
wildcard (e.g. "read:*" grants "read:account").

The gateway (the enforcement point) is responsible for treating a PDP that
raises or returns an invalid result as a denial. See tools/gateway.py.
"""
from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from .identity import Identity
from .policy import PolicySet


class AuthorizationDecision(BaseModel):
    model_config = ConfigDict(frozen=True)
    decision: Literal["allow", "deny", "indeterminate"]
    reason: str
    policy: str | None = None
    required_scope: str | None = None


@runtime_checkable
class AuthorizationService(Protocol):
    def decide(self, tool: str, arguments: dict, identity: Identity) -> AuthorizationDecision: ...


def _scope_matches(granted: str, required: str) -> bool:
    if granted == required:
        return True
    if granted.endswith(":*"):
        return required.startswith(granted[:-1])  # "read:" prefix
    return False


def has_scope(authority: tuple[str, ...], required: str) -> bool:
    return any(_scope_matches(g, required) for g in authority)


class PolicyDecisionPoint:
    def __init__(self, policy_set: PolicySet) -> None:
        self._policy_set = policy_set

    def decide(self, tool: str, arguments: dict, identity: Identity) -> AuthorizationDecision:
        tp = self._policy_set.for_tool(tool)
        if tp is None:
            return AuthorizationDecision(
                decision="deny", reason="no_policy_for_tool", policy=self._policy_set.name
            )
        if not has_scope(identity.authority, tp.required_scope):
            return AuthorizationDecision(
                decision="deny",
                reason="insufficient_scope",
                policy=self._policy_set.name,
                required_scope=tp.required_scope,
            )
        return AuthorizationDecision(
            decision="allow", reason="scope_granted", policy=self._policy_set.name,
            required_scope=tp.required_scope,
        )
