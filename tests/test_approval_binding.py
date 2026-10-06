"""Bound approvals must check principal, scope, expiry, and single-use (review)."""
import pytest
from arh.clock import VirtualClock
from arh.harness.scenario import ApprovalFixture
from arh.security.approvals import BoundApprovalService

def _fix(**kw):
    base = dict(approval_id="a1", approver="duty-manager", principal="credit-officer",
                tool="issue_credit", scope={"account_id": "A-1007", "amount": 25.0})
    base.update(kw); return ApprovalFixture(**base)

def _svc(fix, caller="credit-officer", clock=None):
    return BoundApprovalService([fix], caller=caller, clock=clock or VirtualClock())

def _args(**kw):
    a = {"account_id": "A-1007", "amount": 25.0, "reason": "x"}; a.update(kw); return a

def test_matching_action_is_approved():
    assert _svc(_fix()).check("issue_credit", _args()).result == "approved"

def test_wrong_principal_is_denied():
    r = _svc(_fix(), caller="attacker").check("issue_credit", _args())
    assert r.result == "denied"

def test_changed_amount_is_denied():
    r = _svc(_fix()).check("issue_credit", _args(amount=5000.0))
    assert r.result == "denied" and "scope" in r.reason

def test_expired_approval_is_denied():
    clock = VirtualClock(); clock.advance(200)
    r = _svc(_fix(expires_vt_ms=100), clock=clock).check("issue_credit", _args())
    assert r.result == "denied" and "expired" in r.reason

def test_replay_is_denied_for_single_use():
    svc = _svc(_fix(single_use=True))
    assert svc.check("issue_credit", _args()).result == "approved"
    r2 = svc.check("issue_credit", _args())
    assert r2.result == "denied" and "replay" in r2.reason


def test_future_issued_approval_is_denied():
    clock = VirtualClock()  # now = 0
    r = _svc(_fix(issued_vt_ms=1000), clock=clock).check("issue_credit", _args())
    assert r.result == "denied" and "not_yet_valid" in r.reason

def test_mismatched_action_digest_is_denied():
    r = _svc(_fix(action_digest="deadbeefdeadbeef")).check("issue_credit", _args())
    assert r.result == "denied" and "digest" in r.reason

def test_matching_action_digest_is_approved():
    d = BoundApprovalService.digest("issue_credit", _args())
    r = _svc(_fix(action_digest=d)).check("issue_credit", _args())
    assert r.result == "approved"

def test_unsupported_tool_version_is_denied():
    r = _svc(_fix(tool_version="v2")).check("issue_credit", _args(), tool_version="v1")
    assert r.result == "denied" and "tool_version" in r.reason

def test_later_matching_fixture_is_found_after_an_earlier_mismatch():
    wrong = _fix(approval_id="a0", scope={"account_id": "A-9999", "amount": 25.0})
    right = _fix(approval_id="a1")
    svc = BoundApprovalService([wrong, right], caller="credit-officer", clock=VirtualClock())
    assert svc.check("issue_credit", _args()).result == "approved"
