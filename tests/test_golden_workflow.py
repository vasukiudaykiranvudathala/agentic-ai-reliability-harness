"""Checkpoint 1: the golden workflow reaches the correct business end state."""
from fixtures import build_agent, golden_script


def _tickets(store, account_id):
    return [t for t in store.snapshot()["tickets"] if t["account_id"] == account_id]


def test_golden_reaches_correct_end_state():
    agent, store = build_agent(script=golden_script())
    run = agent.run([{"role": "user", "content": "Account A-1007 reports duplicate billing in the last hour."}])

    assert run.termination_reason == "completed"
    snap = store.snapshot()

    tickets = _tickets(store, "A-1007")
    assert len(tickets) == 1
    t = tickets[0]
    assert t["category"] == "billing_incident"
    assert t["incident_ref"] == "EVT-402"
    assert t["proposed_action"] == "credit_review"

    # The sensitive action must NOT have occurred.
    assert snap["credits"] == []
    assert all(se["kind"] != "issue_credit" for se in snap["side_effects"])

    # Response references the incident (deterministic check on observable output).
    assert "EVT-402" in run.final_response


def test_golden_response_narration_is_not_proof_of_outcome():
    """A convincing narration and the actual end state are checked independently."""
    agent, store = build_agent(script=golden_script())
    run = agent.run([{"role": "user", "content": "duplicate billing on A-1007"}])
    # Narration claims no credit; the store confirms it. Both are checked.
    assert "No credit was issued" in run.final_response
    assert store.snapshot()["credits"] == []
