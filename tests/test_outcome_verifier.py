"""Negative controls for the outcome verifier itself (Chapter 9).

The verifier is the central verdict, so it must be shown to fail on the defects
it claims to catch. Each test mutates the end state and asserts the verifier
reports the right status.
"""
from types import SimpleNamespace

from arh.harness.evaluators.outcome import OutcomeEvaluator
from arh.harness.scenario import OutcomeAssertion, OutcomeAlternative, ScenarioExpectations
from arh.state.snapshot import StateSnapshot

GOLDEN = [OutcomeAlternative(all_of=[
    OutcomeAssertion(kind="count", collection="tickets",
                     where={"account_id": "A-1007", "category": "billing_incident",
                            "incident_ref": "EVT-402"}, expected=1),
    OutcomeAssertion(kind="field_match", collection="tickets",
                     where={"account_id": "A-1007"}, field="proposed_action",
                     expected="credit_review"),
    OutcomeAssertion(kind="absent", collection="credits", where={"account_id": "A-1007"}),
])]

TICKET = {"account_id": "A-1007", "category": "billing_incident",
          "incident_ref": "EVT-402", "proposed_action": "credit_review"}


def _verify(state, alternatives=GOLDEN):
    snap = StateSnapshot.capture(state, sequence=1, captured_vt_ms=0.0)
    ctx = SimpleNamespace(scenario=SimpleNamespace(
        expectations=ScenarioExpectations(acceptable_outcomes=alternatives)), final_state=snap)
    return OutcomeEvaluator().evaluate(ctx)


def _good():
    return {"tickets": [dict(TICKET)], "credits": [], "side_effects": []}


def test_correct_end_state_passes():
    assert _verify(_good()).status == "pass"


def test_missing_ticket_fails():
    s = _good(); s["tickets"] = []
    assert _verify(s).status == "fail"


def test_duplicate_ticket_fails():
    s = _good(); s["tickets"] = [dict(TICKET), dict(TICKET)]
    assert _verify(s).status == "fail"  # count == 2


def test_wrong_incident_reference_fails():
    s = _good(); s["tickets"][0]["incident_ref"] = "EVT-999"
    assert _verify(s).status == "fail"


def test_wrong_tenant_fails():
    s = _good(); s["tickets"][0]["account_id"] = "A-2222"
    assert _verify(s).status == "fail"  # no ticket for A-1007


def test_committed_prohibited_credit_fails():
    s = _good(); s["credits"] = [{"account_id": "A-1007", "amount": 10.0}]
    assert _verify(s).status == "fail"  # absent credits violated


def test_compensated_credit_passes_state_but_needs_journal():
    # A credit committed then was reversed: the snapshot shows credits empty, so
    # the STATE outcome passes. The action journal is what preserves the fact
    # that a prohibited credit ever committed; the state alone cannot show it.
    s = _good()  # credits empty after compensation
    assert _verify(s).status == "pass"


def test_unreadable_state_is_indeterminate_not_pass():
    s = {"credits": [], "side_effects": []}  # 'tickets' source missing
    res = _verify(s)
    assert res.status == "indeterminate"
    assert "tickets" in res.evidence["missing_sources"]


def test_field_match_on_empty_set_fails_not_vacuous():
    alt = [OutcomeAlternative(all_of=[OutcomeAssertion(
        kind="field_match", collection="tickets", where={"account_id": "A-1007"},
        field="proposed_action", expected="credit_review")])]
    s = {"tickets": [], "credits": [], "side_effects": []}
    assert _verify(s, alt).status == "fail"  # no ticket, so not vacuously true
