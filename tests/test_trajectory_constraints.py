"""Trajectory constraints (Chapter 8): required evidence and ordering.

The evaluator does not enforce one golden sequence. It checks that required
evidence was gathered and that ordering constraints held, so a wrong order or a
missing read fails while any other valid order passes.
"""
from pathlib import Path

from arh.harness.evaluators import build_context, evaluate_all, default_evaluators
from arh.harness.evaluators.trajectory import TrajectoryEvaluator
from arh.harness.runner import Runner
from arh.harness.scenario import load_scenarios, OrderingConstraint

REPO = Path(__file__).resolve().parents[1]
DATA, SCN = REPO / "datasets", REPO / "scenarios"


def _run(rel):
    scenario = load_scenarios(SCN / rel)[0]
    return scenario, Runner(datasets_dir=DATA).run(scenario)


def test_golden_satisfies_required_evidence_and_ordering():
    scenario, result = _run("golden/golden-billing-incident-001.jsonl")
    by = {r.evaluator: r for r in evaluate_all(build_context(result, scenario), default_evaluators())}
    assert by["trajectory"].status == "pass"


def test_missing_required_evidence_fails():
    scenario, result = _run("golden/golden-billing-incident-001.jsonl")
    # Demand evidence the golden run never gathers.
    scenario.expectations.trajectory.required_evidence = ["search_knowledge_base"]
    res = TrajectoryEvaluator().evaluate(build_context(result, scenario))
    assert res.status == "fail"
    assert "search_knowledge_base" in res.evidence["missing_required_evidence"]


def test_violated_ordering_constraint_fails():
    scenario, result = _run("golden/golden-billing-incident-001.jsonl")
    # Require create_ticket before get_account: the golden run does the reverse.
    scenario.expectations.trajectory.ordering_constraints = [
        OrderingConstraint(before="create_ticket", after="get_account")]
    res = TrajectoryEvaluator().evaluate(build_context(result, scenario))
    assert res.status == "fail"
    assert res.evidence["ordering_violations"]


def test_no_restriction_when_allowed_is_none():
    scenario, result = _run("golden/golden-billing-incident-001.jsonl")
    scenario.expectations.trajectory.allowed_tool_requests = None  # no restriction
    scenario.expectations.trajectory.required_evidence = []
    scenario.expectations.trajectory.ordering_constraints = []
    res = TrajectoryEvaluator().evaluate(build_context(result, scenario))
    assert res.status == "pass" and "non_permitted_tools" not in res.evidence


def _find(calls, tool):
    return next(c for c in calls if c.tool == tool)


def test_timed_out_evidence_does_not_count():
    # The Chapter 1 failure: check_incident_status executes-then-times-out. A
    # tool call is not evidence unless it succeeded and matched.
    scenario, result = _run("golden/golden-billing-incident-001.jsonl")
    ctx = build_context(result, scenario)
    _find(ctx.record.tool_calls, "check_incident_status").status = "error"  # simulate timeout
    res = TrajectoryEvaluator().evaluate(ctx)
    assert res.status == "fail"
    assert "active_billing_incident" in res.evidence["unmet_evidence"]


def test_mismatched_evidence_does_not_count():
    scenario, result = _run("golden/golden-billing-incident-001.jsonl")
    ctx = build_context(result, scenario)
    _find(ctx.record.tool_calls, "check_incident_status").result = {"active": True, "status": "resolved"}
    res = TrajectoryEvaluator().evaluate(ctx)
    assert res.status == "fail"  # wrong incident status, so evidence unmet


def test_guardrail_flags_unexpected_request_but_scenario_passes():
    scenario, result = _run("adversarial/guardrail-credit-attempt-blocked-001.jsonl")
    ctx = build_context(result, scenario)
    results = evaluate_all(ctx, default_evaluators())
    by = {r.evaluator: r for r in results}
    assert by["trajectory"].status == "fail"          # requesting issue_credit is unsafe
    assert "issue_credit" in by["trajectory"].evidence["unexpected_requests"]
    assert by["guardrails"].status == "pass"          # the gateway blocked it
    assert by["outcome"].status == "pass"             # no credit committed
    from arh.harness.evaluators import aggregate
    overall, _ = aggregate(results, scenario)
    assert overall is True                            # scenario passes on outcome+guardrails


def test_watchdog_termination_fails_trajectory():
    scenario, result = _run("golden/golden-billing-incident-001.jsonl")
    ctx = build_context(result, scenario)
    ctx.record.self_terminated = False
    ctx.record.terminated_by = "watchdog"
    res = TrajectoryEvaluator().evaluate(ctx)
    assert res.status == "fail" and res.evidence["terminated_by"] == "watchdog"


def test_acceptable_terminations_are_configurable():
    scenario, result = _run("golden/golden-billing-incident-001.jsonl")
    ctx = build_context(result, scenario)
    ctx.record.termination_reason = "safely_refused"
    ctx.record.tool_calls.clear()          # isolate the termination check for this unit test
    t = scenario.expectations.trajectory
    t.evidence_requirements, t.required_evidence, t.ordering_constraints = [], [], []
    t.allowed_tool_requests = None
    scenario.expectations.acceptable_terminations = ["completed", "safely_refused"]
    res = TrajectoryEvaluator().evaluate(ctx)
    assert res.status == "pass"
