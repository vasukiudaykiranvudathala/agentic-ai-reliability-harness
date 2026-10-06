"""An authorization-service outage is a resilience fault, not a policy denial
(Chapter 13, comment 7). The sensitive action is blocked; control health is
indeterminate; nothing commits."""
from arh.clock import VirtualClock
from arh.harness.faults import FaultInjector
from arh.harness.scenario import FaultSpec
from arh.model.base import ToolCall
from fixtures import build_gateway, SUPPORT_AGENT  # helper from fixtures

CREDIT = ToolCall(tool="issue_credit", arguments={
    "account_id": "A-1007", "amount": 10.0, "reason": "x", "idempotency_key": "wf-az1"})


def test_authorization_service_outage_blocks_and_does_not_commit():
    clock = VirtualClock()
    faults = FaultInjector([FaultSpec(target_type="authorization_service", trigger="always",
                                      effect="timeout", phase="before_execution")], clock)
    gateway, store = build_gateway(clock=clock, fault_injector=faults)
    out = gateway.execute(CREDIT)
    assert out.status == "rejected"
    assert out.reason.startswith("authorization_unavailable")   # not a policy denial
    assert store.snapshot()["credits"] == []                    # nothing committed


class _ThrowingPDP:
    def decide(self, tool, arguments, identity):
        raise RuntimeError("policy engine crashed")


class _MalformedPDP:
    def decide(self, tool, arguments, identity):
        return "not a decision object"  # invalid


def test_pdp_error_is_indeterminate_not_deny_and_blocks():
    clock = VirtualClock()
    from arh.harness.faults import FaultInjector
    gateway, store = build_gateway(clock=clock, fault_injector=FaultInjector([], clock))
    gateway._authorizer = _ThrowingPDP()  # type: ignore[attr-defined]
    out = gateway.execute(CREDIT)
    assert out.status == "rejected"
    assert out.authorization.decision == "indeterminate"      # not an ordinary deny
    assert out.reason.startswith("authorization_unavailable")
    assert store.snapshot()["credits"] == []


def test_malformed_pdp_decision_is_indeterminate_and_blocks():
    clock = VirtualClock()
    from arh.harness.faults import FaultInjector
    gateway, store = build_gateway(clock=clock, fault_injector=FaultInjector([], clock))
    gateway._authorizer = _MalformedPDP()  # type: ignore[attr-defined]
    out = gateway.execute(CREDIT)
    assert out.authorization.decision == "indeterminate" and out.status == "rejected"


def test_policy_denial_is_deny_not_indeterminate():
    # The support agent lacks write:credit scope: a real policy denial.
    clock = VirtualClock()
    from arh.harness.faults import FaultInjector
    gateway, store = build_gateway(clock=clock, fault_injector=FaultInjector([], clock))
    out = gateway.execute(CREDIT)
    assert out.authorization.decision == "deny"               # policy evaluated and rejected
    assert out.reason.startswith("authorization_denied")
