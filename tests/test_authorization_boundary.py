"""Checkpoint 2 + 4: authorization is enforced outside the agent, fails closed."""
from arh.model.base import Decision, ToolCall
from arh.model.scripted import from_sequence
from arh.security.authorization import AuthorizationDecision, PolicyDecisionPoint
from arh.security.identity import Identity
from arh.security.policy import demo_policy_set
from fixtures import CREDIT_OFFICER, SUPPORT_AGENT, build_agent


def _credit_attempt_script():
    return from_sequence(
        [
            Decision(kind="tool_call", tool_call=ToolCall(
                tool="issue_credit",
                arguments={"account_id": "A-1007", "amount": 20.0, "reason": "goodwill",
                           "idempotency_key": "wf-credit-1"})),
            Decision(kind="final_answer", final_answer="done"),
        ],
        "scripted:credit-attempt",
    )


def test_credit_denied_for_insufficient_scope():
    # support-agent lacks write:credit -> PDP denies before any side effect.
    agent, store = build_agent(script=_credit_attempt_script(), identity=SUPPORT_AGENT)
    run = agent.run([{"role": "user", "content": "please credit A-1007"}])
    outcome = run.tool_outcomes[0]
    assert outcome.status == "rejected"
    assert outcome.authorization.decision == "deny"
    assert outcome.authorization.reason == "insufficient_scope"
    assert store.snapshot()["credits"] == []


def test_credit_blocked_when_approval_missing_even_with_scope():
    # credit-officer HAS write:credit, but default approvals deny -> still blocked.
    agent, store = build_agent(script=_credit_attempt_script(), identity=CREDIT_OFFICER)
    run = agent.run([{"role": "user", "content": "credit A-1007"}])
    outcome = run.tool_outcomes[0]
    assert outcome.status == "rejected"
    assert outcome.authorization.decision == "allow"  # scope was fine
    assert outcome.approval.result == "denied"        # approval was not
    assert store.snapshot()["credits"] == []


def test_credit_allowed_with_scope_and_explicit_approval():
    from arh.security.approvals import PreApprovedApprovalService

    approvals = PreApprovedApprovalService(approvals={("issue_credit", "A-1007")})
    agent, store = build_agent(script=_credit_attempt_script(), identity=CREDIT_OFFICER, approvals=approvals)
    run = agent.run([{"role": "user", "content": "credit A-1007, approved"}])
    outcome = run.tool_outcomes[0]
    assert outcome.status == "executed"
    assert outcome.approval.result == "approved"
    credits = store.snapshot()["credits"]
    assert len(credits) == 1 and credits[0]["account_id"] == "A-1007"


def test_gateway_fails_closed_when_authorization_service_raises():
    class RaisingAuthz:
        def decide(self, tool, arguments, identity):  # noqa: ANN001
            raise RuntimeError("PDP down")

    agent, store = build_agent(script=_credit_attempt_script(), identity=CREDIT_OFFICER, authorizer=RaisingAuthz())
    run = agent.run([{"role": "user", "content": "credit A-1007"}])
    outcome = run.tool_outcomes[0]
    assert outcome.status == "rejected"
    assert outcome.authorization.reason == "authorization_unavailable_fail_closed"
    assert store.snapshot()["credits"] == []


def test_gateway_fails_closed_on_invalid_authorization_result():
    class BogusAuthz:
        def decide(self, tool, arguments, identity):  # noqa: ANN001
            return "yes, allow it"  # not an AuthorizationDecision

    agent, store = build_agent(script=_credit_attempt_script(), identity=CREDIT_OFFICER, authorizer=BogusAuthz())
    run = agent.run([{"role": "user", "content": "credit A-1007"}])
    assert run.tool_outcomes[0].status == "rejected"
    assert store.snapshot()["credits"] == []


def test_unknown_tool_and_bad_arguments_are_rejected_before_execution():
    bad = from_sequence(
        [
            Decision(kind="tool_call", tool_call=ToolCall(
                tool="create_ticket", arguments={"account_id": "A-1007"})),  # missing required fields
            Decision(kind="tool_call", tool_call=ToolCall(
                tool="create_ticket", arguments={
                    "account_id": "A-1007", "category": "billing_incident", "summary": "x",
                    "proposed_action": "credit_review", "made_up_field": True})),  # extra field
            Decision(kind="final_answer", final_answer="stopped"),
        ],
        "scripted:bad-args",
    )
    agent, store = build_agent(script=bad)
    run = agent.run([{"role": "user", "content": "go"}])
    assert run.tool_outcomes[0].status == "rejected"
    assert run.tool_outcomes[0].reason.startswith("invalid_arguments")
    assert run.tool_outcomes[1].status == "rejected"
    assert store.snapshot()["tickets"] == []


def test_approval_service_outage_is_classified_as_unavailable():
    """An approval-service exception must fail closed AND be recorded as an
    outage, not collapsed into a normal policy denial."""
    class _Throwing:
        def check(self, tool, arguments, *, tool_version=None):
            raise RuntimeError("pdp down")
    agent, store = build_agent(script=_credit_attempt_script(), identity=CREDIT_OFFICER, approvals=_Throwing())
    run = agent.run([{"role": "user", "content": "credit A-1007, approved"}])
    outcome = run.tool_outcomes[0]
    assert outcome.status == "rejected"
    assert outcome.reason.startswith("approval_unavailable:")
