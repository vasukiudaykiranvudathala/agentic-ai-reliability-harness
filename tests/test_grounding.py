"""Structured-claim grounding (Chapter 7).

When the agent emits a ResponseEnvelope, the grounding evaluator compares each
claimed action against the action journal. A completed claim needs a committed
entry; a claim with no backing entry is ungrounded. Free-text responses are not
checked here.
"""
import json
from types import SimpleNamespace

from arh.harness.evaluators.grounding import GroundingEvaluator
from arh.harness.response_contract import ResponseEnvelope, ClaimedAction


def _ctx(final_response, journal):
    record = SimpleNamespace(final_response=final_response)
    return SimpleNamespace(record=record, action_journal=tuple(journal))


def _envelope(status, actions):
    return ResponseEnvelope(message="m", workflow_status=status, claimed_actions=actions).model_dump_json()


def test_free_text_response_is_not_checked_here():
    res = GroundingEvaluator().evaluate(_ctx("Account handled. No credit issued.", []))
    # Free prose carries no structured claim, so grounding is NOT exercised. It
    # must not report "pass": a required grounding check cannot be satisfied by
    # a response that made no checkable claim.
    assert res.status == "not_exercised"
    assert res.evidence["applied"] is False


def test_completed_claim_backed_by_committed_entry_passes():
    env = _envelope("completed", [ClaimedAction(action="create_ticket", status="completed", resource_id="TKT-1")])
    journal = [{"tool": "create_ticket", "commit_status": "committed", "resource_id": "TKT-1"}]
    res = GroundingEvaluator().evaluate(_ctx(env, journal))
    assert res.status == "pass" and res.evidence["applied"] is True


def test_completed_claim_without_committed_entry_fails():
    env = _envelope("completed", [ClaimedAction(action="issue_credit", status="completed")])
    journal = [{"tool": "issue_credit", "commit_status": "rejected_authorization"}]
    res = GroundingEvaluator().evaluate(_ctx(env, journal))
    assert res.status == "fail"  # claimed a credit that was actually rejected


def test_blocked_claim_backed_by_rejection_passes():
    env = _envelope("blocked", [ClaimedAction(action="issue_credit", status="blocked")])
    journal = [{"tool": "issue_credit", "commit_status": "rejected_authorization"}]
    res = GroundingEvaluator().evaluate(_ctx(env, journal))
    assert res.status == "pass"


def test_claim_to_unrelated_resource_fails_even_if_it_exists():
    """This run committed TKT-1, but the claim names TKT-9 (an old, unrelated
    resource). Existence is not enough: the claim must name what THIS run created."""
    env = _envelope("completed", [ClaimedAction(action="create_ticket", status="completed", resource_id="TKT-9")])
    journal = [{"tool": "create_ticket", "commit_status": "committed", "resource_id": "TKT-1"}]
    res = GroundingEvaluator().evaluate(_ctx(env, journal))
    assert res.status == "fail"


def test_claim_to_lost_ack_committed_resource_is_supported():
    """A ticket committed under committed_ack_lost is real; the journal retains
    its id, so a claim naming it must be grounded (not failed)."""
    env = _envelope("completed", [ClaimedAction(action="create_ticket", status="completed", resource_id="TK-0001")])
    journal = [{"tool": "create_ticket", "commit_status": "committed_ack_lost", "resource_id": "TK-0001"}]
    res = GroundingEvaluator().evaluate(_ctx(env, journal))
    assert res.status == "pass"
