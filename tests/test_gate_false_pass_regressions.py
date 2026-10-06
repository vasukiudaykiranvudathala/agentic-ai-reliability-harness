"""Negative controls for the false-pass behaviors found in external review.

Each test reproduces a reviewer probe and asserts the gate or aggregation now
refuses to green on missing, invalid, empty, or insufficient evidence.
"""
import pytest

from arh.harness.gates import GateProfile, GateSpec, evaluate_gates
from arh.harness.metrics import MetricValue
from arh.harness.evaluators.base import aggregate, EvaluationResult
from arh.harness.scenario import OutcomeAlternative, OutcomeAssertion, ScenarioExpectations
from arh.harness.evaluators.outcome import OutcomeEvaluator
from arh.state.snapshot import StateSnapshot
from types import SimpleNamespace


def _mv(name, value, status, **detail):
    return MetricValue(name=name, value=value, unit="ratio", source_class="offline",
                       status=status, detail=detail)


def _blocking(metric, minimum_sample_size=None, minimum_attempts=None):
    return GateProfile(profile="t", gates=[GateSpec(
        metric=metric, type="absolute", operator="ge", threshold=0.9, severity="blocking",
        minimum_sample_size=minimum_sample_size, minimum_attempts=minimum_attempts)])


# --- Blocker: invalid evidence states cannot green a gate ---
@pytest.mark.parametrize("status", ["error", "indeterminate", "not_evaluated", "insufficient_data"])
def test_invalid_status_with_favorable_value_does_not_pass(status):
    sc = evaluate_gates(_blocking("m"), {"m": _mv("m", 1.0, status, denominator=100)})
    assert sc.decision != "pass"
    assert sc.results[0].status == "inconclusive"


def test_provisional_metric_does_not_pass_without_optin():
    sc = evaluate_gates(_blocking("m"), {"m": _mv("m", 1.0, "provisional", denominator=100)})
    assert sc.results[0].status == "inconclusive"


def test_provisional_metric_passes_only_with_explicit_optin():
    profile = GateProfile(profile="t", gates=[GateSpec(
        metric="m", type="absolute", operator="ge", threshold=0.9, severity="blocking",
        allow_provisional=True)])
    sc = evaluate_gates(profile, {"m": _mv("m", 1.0, "provisional", denominator=100)})
    assert sc.decision == "pass"


# --- Blocker: sample-size declarations are enforced ---
def test_minimum_sample_size_1000_not_met_by_sample_1():
    sc = evaluate_gates(_blocking("m", minimum_sample_size=1000),
                        {"m": _mv("m", 1.0, "ok", denominator=1, sample_size=1)})
    assert sc.decision != "pass"
    assert sc.results[0].status == "inconclusive"


# --- Blocker: an unexercised required guardrail does not satisfy aggregation ---
def test_required_guardrail_not_exercised_does_not_pass_aggregation():
    scenario = SimpleNamespace(pass_criteria={"guardrails": "pass"})
    results = [EvaluationResult(evaluator="guardrails", evaluator_version="x",
                               status="not_exercised", verdict="no attempt")]
    overall, detail = aggregate(results, scenario)
    assert overall is False
    assert detail["guardrails"]["ok"] is False


# --- Blocker: missing / empty outcome requirements do not pass ---
def _outcome(alternatives, state):
    snap = StateSnapshot.capture(state, sequence=1, captured_vt_ms=0.0)
    ctx = SimpleNamespace(scenario=SimpleNamespace(
        expectations=ScenarioExpectations(acceptable_outcomes=alternatives)), final_state=snap)
    return OutcomeEvaluator().evaluate(ctx)


def test_no_acceptable_outcomes_is_not_evaluated_not_pass():
    res = _outcome([], {"tickets": [], "credits": [], "side_effects": []})
    assert res.status == "not_evaluated"


def test_empty_alternative_is_rejected_at_construction():
    with pytest.raises(Exception):
        OutcomeAlternative()  # empty all_of asserts nothing


def test_empty_alternative_cannot_reach_a_scenario():
    # Even via model_construct, an empty alternative is re-validated (and rejected)
    # when placed into ScenarioExpectations, so it can never green a scenario.
    with pytest.raises(Exception):
        ScenarioExpectations(acceptable_outcomes=[OutcomeAlternative.model_construct(all_of=[])])


def test_profile_no_longer_carries_an_advisory_sample_size():
    # The advisory profile-level field is gone; enforcement is per-gate only.
    from arh.harness.gates import GateProfile
    assert not hasattr(GateProfile(profile="t", gates=[]), "minimum_sample_size")
