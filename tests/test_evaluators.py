"""Increment (c): evaluators, aggregation, and fail-safe behavior."""
from pathlib import Path

import pytest

from arh.harness.evaluators import (
    EvaluationContext,
    EvaluationResult,
    aggregate,
    build_context,
    default_evaluators,
    evaluate_all,
    run_evaluator,
)
from arh.harness.runner import Runner
from arh.harness.scenario import load_scenarios

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "datasets"
SCN = REPO / "scenarios"


def _run(rel):
    scenario = load_scenarios(SCN / rel)[0]
    result = Runner(datasets_dir=DATA).run(scenario)
    return scenario, result


def _by_name(results):
    return {r.evaluator: r for r in results}


def test_golden_passes_all_evaluators():
    scenario, result = _run("golden/golden-billing-incident-001.jsonl")
    ctx = build_context(result, scenario)
    results = evaluate_all(ctx, default_evaluators())
    by = _by_name(results)
    assert by["outcome"].status == "pass"
    assert by["trajectory"].status == "pass"
    assert by["guardrails"].status == "not_exercised"  # agent never attempted the credit
    assert by["response"].status == "pass"
    overall, detail = aggregate(results, scenario)
    assert overall is True, detail


def test_negative_scenario_passes_when_agent_avoids_the_unsafe_action():
    scenario, result = _run("negative/negative-no-unsolicited-credit-001.jsonl")
    ctx = build_context(result, scenario)
    results = evaluate_all(ctx, default_evaluators())
    by = _by_name(results)
    # A review ticket exists, no credit was issued, and the response does not
    # claim a refund. The scenario passes because the agent behaved correctly.
    assert by["outcome"].status == "pass"
    assert by["guardrails"].status == "not_exercised"  # kept safe by restraint, not by the control
    assert by["response"].status == "pass"
    overall, _ = aggregate(results, scenario)
    assert overall is True


def test_outcome_evaluator_fails_when_prohibited_side_effect_committed():
    scenario, result = _run("control/control-invariant-violation-001.jsonl")
    ctx = build_context(result, scenario)
    results = evaluate_all(ctx, default_evaluators())
    by = _by_name(results)
    # The credit committed: outcome expected credits absent -> fail; guardrails
    # safety violated -> fail. The suite must not report this as passing.
    assert by["outcome"].status == "fail"
    assert by["guardrails"].status == "fail"
    overall, detail = aggregate(results, scenario)
    assert overall is False, detail


def _ctx(scenario, result):
    return build_context(result, scenario)


class RaisingEvaluator:
    name = "response"  # impersonate a real evaluator name for aggregation
    version = "x"

    def evaluate(self, context):  # noqa: ANN001
        raise RuntimeError("boom")


class MalformedEvaluator:
    name = "response"
    version = "x"

    def evaluate(self, context):  # noqa: ANN001
        return {"status": "pass"}  # not an EvaluationResult


class SlowEvaluator:
    name = "response"
    version = "x"

    def evaluate(self, context):  # noqa: ANN001
        import time
        time.sleep(0.3)
        return EvaluationResult(evaluator=self.name, evaluator_version=self.version, status="pass", verdict="late")


def test_raising_evaluator_becomes_error_and_cannot_pass():
    scenario, result = _run("golden/golden-billing-incident-001.jsonl")
    r = run_evaluator(RaisingEvaluator(), _ctx(scenario, result))
    assert r.status == "error"
    # A scenario that requires response=pass cannot pass with an errored evaluator.
    overall, _ = aggregate([r], scenario)
    assert overall is False


def test_malformed_evaluator_return_becomes_error():
    scenario, result = _run("golden/golden-billing-incident-001.jsonl")
    r = run_evaluator(MalformedEvaluator(), _ctx(scenario, result))
    assert r.status == "error"


def test_slow_evaluator_times_out_and_cannot_pass():
    scenario, result = _run("golden/golden-billing-incident-001.jsonl")
    r = run_evaluator(SlowEvaluator(), _ctx(scenario, result), timeout_ms=50)
    assert r.status == "timeout"
    overall, _ = aggregate([r], scenario)
    assert overall is False


def test_broken_evaluator_cannot_turn_a_failing_run_green():
    """The core fail-safe guarantee: a broken evaluator in the set never
    upgrades an otherwise-failing scenario to pass."""
    scenario, result = _run("control/control-invariant-violation-001.jsonl")
    ctx = _ctx(scenario, result)
    # Replace the outcome evaluator with one that raises; everything else runs.
    evaluators = [e for e in default_evaluators() if e.name != "outcome"] + [_RaisingOutcome()]
    results = evaluate_all(ctx, evaluators)
    by = _by_name(results)
    assert by["outcome"].status == "error"
    overall, _ = aggregate(results, scenario)
    assert overall is False


class _RaisingOutcome:
    name = "outcome"
    version = "x"

    def evaluate(self, context):  # noqa: ANN001
        raise ValueError("outcome evaluator crashed")


def test_evaluation_results_attach_to_record_when_requested():
    scenario = load_scenarios(SCN / "golden" / "golden-billing-incident-001.jsonl")[0]
    result = Runner(datasets_dir=DATA).run(scenario, evaluators=default_evaluators())
    names = {er["evaluator"] for er in result.record.evaluation_results}
    assert names == {"response", "trajectory", "outcome", "guardrails"}
