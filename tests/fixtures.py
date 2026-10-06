"""Test wiring for Increment (a).

Builds a fully wired system under evaluation from the sample datasets and
provides the authored golden decision script. The scripted adapter is given
ONLY the authored decisions; it never sees scenario expectations.
"""
from __future__ import annotations

import json
from pathlib import Path

from arh.agent.loop import Agent
from arh.clock import VirtualClock
from arh.model.base import Decision, ToolCall
from arh.model.scripted import from_sequence
from arh.security.approvals import ApprovalService, AutoDenyApprovalService
from arh.security.authorization import AuthorizationService, PolicyDecisionPoint
from arh.security.identity import Identity
from arh.security.policy import PolicySet, demo_policy_set
from arh.state.store import StateStore
from arh.tools.gateway import ToolGateway
from arh.tools.registry import default_tool_definitions
from arh.tools.simulators import ToolSimulators

DATA = Path(__file__).resolve().parents[1] / "datasets"


def _read_jsonl(name: str) -> list[dict]:
    with open(DATA / name) as fh:
        return [json.loads(line) for line in fh if line.strip()]


def seeded_store() -> StateStore:
    store = StateStore()
    store.seed_accounts(_read_jsonl("accounts.jsonl"))
    store.seed_policies(_read_jsonl("policies.jsonl"))
    store.seed_incidents(_read_jsonl("incidents.jsonl"))
    store.seed_knowledge_base(_read_jsonl("knowledge_base.jsonl"))
    return store


SUPPORT_AGENT = Identity(caller="support-agent-role", authority=("read:*", "write:ticket"))
CREDIT_OFFICER = Identity(caller="credit-officer-role", authority=("read:*", "write:ticket", "write:credit"))


def build_agent(
    *,
    script,
    identity: Identity = SUPPORT_AGENT,
    authorizer: AuthorizationService | None = None,
    approvals: ApprovalService | None = None,
    policy_set: PolicySet | None = None,
    store: StateStore | None = None,
    max_steps: int = 12,
):
    store = store or seeded_store()
    policy_set = policy_set or demo_policy_set()
    tool_defs = default_tool_definitions()
    sims = ToolSimulators(store)
    gateway = ToolGateway(
        simulators=sims,
        authorizer=authorizer or PolicyDecisionPoint(policy_set),
        approvals=approvals or AutoDenyApprovalService(),
        policy_set=policy_set,
        tool_defs=tool_defs,
        principal=identity,
        clock=VirtualClock(),
    )
    agent = Agent(
        model=script,
        gateway=gateway,
        tool_defs=tool_defs,
        identity=identity,
        clock=VirtualClock(),
        max_steps=max_steps,
    )
    return agent, store


def golden_script(config_id: str = "scripted:golden-001", idempotency_key: str = "wf-golden-001"):
    """Canonical golden adapter, resolved from the harness script registry so
    the repository has a single source for the authored golden path."""
    from arh.harness.scripts import build_adapter
    return build_adapter(config_id)


def build_gateway(*, identity: Identity = SUPPORT_AGENT, store: StateStore | None = None,
                  policy_set: PolicySet | None = None, clock=None, fault_injector=None,
                  approvals: ApprovalService | None = None):
    """A gateway (and its store) for focused security/resilience tests."""
    clock = clock or VirtualClock()
    store = store or seeded_store()
    policy_set = policy_set or demo_policy_set()
    tool_defs = default_tool_definitions()
    sims = ToolSimulators(store)
    gateway = ToolGateway(
        simulators=sims, authorizer=PolicyDecisionPoint(policy_set),
        approvals=approvals or AutoDenyApprovalService(), policy_set=policy_set,
        tool_defs=tool_defs, principal=identity, clock=clock, fault_injector=fault_injector)
    return gateway, store
