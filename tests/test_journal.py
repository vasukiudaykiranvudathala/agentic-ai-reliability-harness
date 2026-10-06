"""The append-only action journal (Chapter 5, comment 11).

Every state-changing attempt is journaled, committed or not, with before/after
state hashes. A committed write moves the hash; a rejected write does not. This
is how the harness later detects a prohibited action that committed or was
reversed.
"""
from arh.model.base import ToolCall
from fixtures import build_agent, golden_script, SUPPORT_AGENT

CREATE = ToolCall(tool="create_ticket", arguments={
    "account_id": "A-1007", "category": "billing_incident", "summary": "dup billing",
    "proposed_action": "credit_review", "incident_ref": "EVT-402", "idempotency_key": "wf-j1"})
CREDIT = ToolCall(tool="issue_credit", arguments={
    "account_id": "A-1007", "amount": 10.0, "reason": "x", "idempotency_key": "wf-j2"})


def _gateway():
    # build_agent wires a gateway with the support-agent principal (no write:credit).
    agent, store = build_agent(script=golden_script())
    return agent._gateway, store  # type: ignore[attr-defined]


def test_committed_write_is_journaled_and_moves_state_hash():
    gw, store = _gateway()
    out = gw.execute(CREATE)
    assert out.status == "executed"
    j = store.action_journal
    assert len(j) == 1
    e = j[0]
    assert e["tool"] == "create_ticket"
    assert e["commit_status"] == "committed"
    assert e["principal"] == SUPPORT_AGENT.caller
    assert e["idempotency_key"] == "wf-j1"
    assert e["before_state_hash"] != e["after_state_hash"]  # state changed


def test_denied_write_is_journaled_without_changing_state():
    gw, store = _gateway()
    out = gw.execute(CREDIT)  # support agent lacks write:credit authority
    assert out.status == "rejected"
    e = store.action_journal[-1]
    assert e["tool"] == "issue_credit"
    assert e["commit_status"] == "rejected_authorization"
    assert e["before_state_hash"] == e["after_state_hash"]  # no state change


def test_journal_is_append_only_across_attempts():
    gw, store = _gateway()
    gw.execute(CREATE)
    gw.execute(CREDIT)
    seqs = [e["seq"] for e in store.action_journal]
    assert seqs == [1, 2]
