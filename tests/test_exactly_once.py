"""Checkpoint 3: exactly-once side effect under a lost-response retry.

Models the after-commit-before-response fault at the semantic level: the
write commits, the acknowledgment is lost, and the agent retries with the same
idempotency key. The store must suppress the duplicate.
"""
from arh.model.base import Decision, ToolCall
from arh.model.scripted import from_sequence
from fixtures import build_agent


def _double_create_same_key():
    args = {
        "account_id": "A-1007", "category": "billing_incident",
        "summary": "Duplicate billing during active incident EVT-402.",
        "proposed_action": "credit_review", "incident_ref": "EVT-402",
        "idempotency_key": "wf-golden-001",
    }
    return from_sequence(
        [
            Decision(kind="tool_call", tool_call=ToolCall(tool="create_ticket", arguments=args)),
            # Response "lost"; agent retries the identical call.
            Decision(kind="tool_call", tool_call=ToolCall(tool="create_ticket", arguments=dict(args))),
            Decision(kind="final_answer", final_answer="ticket created (retried once)"),
        ],
        "scripted:retry-same-key",
    )


def test_retry_with_same_idempotency_key_creates_one_ticket():
    agent, store = build_agent(script=_double_create_same_key())
    run = agent.run([{"role": "user", "content": "open ticket for A-1007"}])

    assert run.tool_outcomes[0].status == "executed"
    assert run.tool_outcomes[1].status == "executed"
    assert run.tool_outcomes[0].result.data["duplicate_suppressed"] is False
    assert run.tool_outcomes[1].result.data["duplicate_suppressed"] is True
    # Same ticket id returned both times.
    assert run.tool_outcomes[0].result.data["ticket_id"] == run.tool_outcomes[1].result.data["ticket_id"]

    snap = store.snapshot()
    assert len(snap["tickets"]) == 1
    # Only one committed side effect, despite two accepted calls.
    assert sum(1 for se in snap["side_effects"] if se["kind"] == "create_ticket") == 1


def test_retry_with_different_key_would_duplicate():
    """Contrast: without a stable key, a retry is a genuine duplicate. Proves the
    idempotency key is what provides exactly-once, not the tool itself."""
    a = {"account_id": "A-1007", "category": "billing_incident", "summary": "x",
         "proposed_action": "credit_review", "incident_ref": "EVT-402", "idempotency_key": "k1"}
    b = dict(a, idempotency_key="k2")
    script = from_sequence(
        [
            Decision(kind="tool_call", tool_call=ToolCall(tool="create_ticket", arguments=a)),
            Decision(kind="tool_call", tool_call=ToolCall(tool="create_ticket", arguments=b)),
            Decision(kind="final_answer", final_answer="two tickets"),
        ],
        "scripted:retry-diff-key",
    )
    agent, store = build_agent(script=script)
    agent.run([{"role": "user", "content": "go"}])
    assert len(store.snapshot()["tickets"]) == 2
