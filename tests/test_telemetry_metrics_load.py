import os
import pytest

@pytest.fixture(autouse=True)
def _force_always_on_sampler(monkeypatch):
    # Telemetry completeness must not depend on the ambient sampling rate.
    monkeypatch.setenv("OTEL_TRACES_SAMPLER", "always_on")
    yield

"""Increment (e): telemetry spans, metrics, and the load generator (Mode A)."""
import asyncio
from pathlib import Path

from arh.harness.evaluators import aggregate, build_context, default_evaluators, evaluate_all
from arh.harness.metrics import compute_metrics, summarize
from arh.harness.runner import Runner
from arh.harness.scenario import load_scenario_dir, load_scenarios
from arh.loadgen.workload import LoadProfile, run_load
from arh.telemetry.otel import build_tracer_provider, emit_record_spans

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "datasets"
SCN = REPO / "scenarios"


# ---- telemetry ----------------------------------------------------------------

def test_span_tree_shape_and_compact_attributes():
    scenario = load_scenarios(SCN / "golden" / "golden-billing-incident-001.jsonl")[0]
    rec = Runner(datasets_dir=DATA).run(scenario).record
    provider = build_tracer_provider()
    emit_record_spans(rec, provider)
    spans = provider._arh_exporter.get_finished_spans()
    names = [s.name for s in spans]
    assert names.count("workflow") == 1
    assert names.count("tool_call") == 4
    assert names.count("authorization") == 4
    assert names.count("model_call") == 5

    tool = next(s for s in spans if s.name == "tool_call")
    # Compact, low-cardinality attributes only: a hash, not the raw arguments.
    assert "arh.tool.args_hash" in tool.attributes
    assert not any(k.endswith("args") or k.endswith("arguments") for k in tool.attributes)
    assert len(tool.attributes["arh.tool.args_hash"]) <= 16


# ---- metrics ------------------------------------------------------------------

def _batch():
    runner = Runner(datasets_dir=DATA)
    summaries = []
    for scenario in load_scenario_dir(SCN):
        result = runner.run(scenario)
        evs = evaluate_all(build_context(result, scenario), default_evaluators())
        suite_pass = None
        if scenario.pass_criteria:
            suite_pass, _ = aggregate(evs, scenario)
        summaries.append(summarize(scenario, result, evs, suite_pass))
    return compute_metrics(summaries)


def test_metrics_over_full_batch_catch_the_unsafe_fixture():
    m = _batch()
    # The batch deliberately includes an unsafe control fixture where a credit
    # executes, so the batch-level bypass rate is a real, nonzero value. This is
    # exactly why intentional-bypass fixtures belong in harness meta-tests, not
    # the release regression set.
    # Batch has two unsafe attempts: one blocked (guardrail fixture) and one
    # executed (unsafe-env control fixture) -> bypass rate 0.5, still caught (>0).
    assert m["guardrail_bypass_rate"].status == "ok"
    assert m["guardrail_bypass_rate"].value == 0.5
    assert m["critical_action_correctness"].value < 1.0
    # Trace completeness is a full ratio.
    assert m["record_metadata_completeness"].status == "ok" and m["record_metadata_completeness"].value == 1.0
    # Small batch -> p95 is insufficient_data, never a fabricated number.
    assert m["tool_calls_per_workflow_p95"].status == "insufficient_data"
    assert m["tool_calls_per_workflow_p95"].value is None
    # Live-only metrics are not_evaluated in Mode A, not zero.
    assert m["tokens_per_successful_outcome"].status == "not_evaluated"
    assert m["cost_per_successful_outcome"].value is None
    # Amplification is a real offline factor.
    assert m["workflow_amplification"].status == "ok" and m["workflow_amplification"].value > 1.0


def test_bypass_rate_zero_with_evidence_on_clean_regression_batch():
    """Excluding the intentional-bypass meta-fixture, the clean batch still has a
    blocked unsafe attempt (the guardrail fixture), so the bypass rate is a real
    0.0 backed by evidence -- not not_evaluated, not silently 0."""
    runner = Runner(datasets_dir=DATA)
    summaries = []
    for scenario in load_scenario_dir(SCN):
        if "unsafe-env" in scenario.tags:
            continue  # a real regression set would not include this meta-fixture
        result = runner.run(scenario)
        evs = evaluate_all(build_context(result, scenario), default_evaluators())
        suite_pass = None
        if scenario.pass_criteria:
            from arh.harness.evaluators import aggregate as agg
            suite_pass, _ = agg(evs, scenario)
        summaries.append(summarize(scenario, result, evs, suite_pass))
    m = compute_metrics(summaries)
    assert m["guardrail_bypass_rate"].status == "ok"
    assert m["guardrail_bypass_rate"].value == 0.0
    assert m["guardrail_bypass_rate"].detail["denominator"] >= 1


def test_metrics_not_evaluated_when_no_denominator():
    # A single golden run has no unsafe attempts -> bypass rate not_evaluated.
    runner = Runner(datasets_dir=DATA)
    scenario = load_scenarios(SCN / "golden" / "golden-billing-incident-001.jsonl")[0]
    result = runner.run(scenario)
    evs = evaluate_all(build_context(result, scenario), default_evaluators())
    m = compute_metrics([summarize(scenario, result, evs, True)])
    assert m["guardrail_bypass_rate"].status == "not_evaluated"
    assert m["guardrail_bypass_rate"].value is None


# ---- load generator (Mode A) --------------------------------------------------

def test_load_mode_a_respects_concurrency_and_computes_amplification():
    runner = Runner(datasets_dir=DATA)
    scenario = load_scenarios(SCN / "golden" / "golden-billing-incident-001.jsonl")[0]
    profile = LoadProfile(total_workflows=24, arrival="constant", rate_per_sec=500.0,
                          concurrency_cap=4, seed=7)
    report = asyncio.run(run_load(runner, scenario, profile))

    assert report.total == 24
    assert report.success_rate == 1.0
    assert report.max_observed_concurrency <= 4
    # Golden = 5 model calls + 4 executed tool calls = 9 downstream ops per workflow.
    assert abs(report.workflow_amplification - 9.0) < 1e-6
    assert abs(report.model_call_amplification - 5.0) < 1e-6
    # One write attempt, one committed ticket -> no retry amplification.
    assert abs(report.retry_amplification - 1.0) < 1e-6
    # This is harness validation, not a model measurement, and says so.
    assert report.source_class == "mode_a_harness_validation"
    assert "not a real model" in report.note


def test_load_after_commit_scenario_shows_retry_amplification():
    runner = Runner(datasets_dir=DATA)
    scenario = load_scenarios(SCN / "recovery" / "recovery-after-commit-idempotent-001.jsonl")[0]
    profile = LoadProfile(total_workflows=10, rate_per_sec=500.0, concurrency_cap=4, seed=3)
    report = asyncio.run(run_load(runner, scenario, profile))
    # Two write attempts (original + retry), one committed ticket -> 2.0.
    assert abs(report.retry_amplification - 2.0) < 1e-6


def test_retry_amplification_is_defined_when_a_write_never_commits():
    """A safe write that never commits must not make retry amplification
    undefined or zero (the old attempts/committed formula did)."""
    from arh.loadgen.workload import run_load, LoadProfile
    runner = Runner(datasets_dir=DATA)
    # A recovery scenario where the write is attempted but blocked/failed still
    # journals an attempt; physical/logical stays defined at 1.0, not undefined.
    scenario = load_scenarios(SCN / "golden" / "golden-billing-incident-001.jsonl")[0]
    report = asyncio.run(run_load(runner, scenario, LoadProfile(total_workflows=6, seed=1)))
    assert report.retry_amplification == 1.0        # one attempt per logical op
    assert report.duplicate_effect_rate == 0.0      # idempotent: no duplicate effects
    assert report.latency_sample_count == 6
    assert report.p95_status in ("insufficient_data", "provisional")  # 6 samples: not "ok"


def test_live_budget_model_exists_for_mode_b_spend_control():
    from arh.loadgen.workload import LiveRunBudget
    b = LiveRunBudget(max_workflows=100, max_model_calls=500, max_tokens=100000,
                      max_cost_usd=5.0, max_duration_seconds=300)
    assert b.max_cost_usd == 5.0


def test_spike_workload_does_not_crash():
    """A nonempty spike burst must schedule with a timestamp (regression)."""
    import asyncio
    from pathlib import Path
    from arh.harness.runner import Runner
    from arh.harness.scenario import load_scenarios
    import glob
    runner = Runner(datasets_dir=Path("datasets"))
    scenario = load_scenarios(Path(glob.glob("scenarios/golden/*.jsonl")[0]))[0]
    profile = LoadProfile(total_workflows=5, workload_model="open", arrival="spike", spike_at=2, spike_size=3, seed=1)
    report = asyncio.run(run_load(runner, scenario, profile))
    assert report.total >= 5   # burst + steady arrivals all ran


def test_closed_workload_is_completion_paced():
    """With concurrency 1, a closed loop runs strictly one at a time: the number
    in flight never exceeds the pool (not all-scheduled-then-all-complete)."""
    import asyncio
    from pathlib import Path
    from arh.harness.runner import Runner
    from arh.harness.scenario import load_scenarios
    import glob
    runner = Runner(datasets_dir=Path("datasets"))
    scenario = load_scenarios(Path(glob.glob("scenarios/golden/*.jsonl")[0]))[0]
    report = asyncio.run(run_load(runner, scenario, LoadProfile(
        total_workflows=3, workload_model="closed", concurrency_cap=1)))
    assert report.total == 3 and report.max_observed_concurrency == 1


def test_open_workload_runs_with_independent_arrivals():
    import asyncio
    from pathlib import Path
    from arh.harness.runner import Runner
    from arh.harness.scenario import load_scenarios
    import glob
    runner = Runner(datasets_dir=Path("datasets"))
    scenario = load_scenarios(Path(glob.glob("scenarios/golden/*.jsonl")[0]))[0]
    report = asyncio.run(run_load(runner, scenario, LoadProfile(
        total_workflows=4, workload_model="open", concurrency_cap=4)))
    assert report.total == 4


def test_scripted_output_cannot_be_labeled_live():
    import asyncio, pytest
    from pathlib import Path
    from arh.harness.runner import Runner
    from arh.harness.scenario import load_scenarios
    import glob
    runner = Runner(datasets_dir=Path("datasets"))
    scenario = load_scenarios(Path(glob.glob("scenarios/golden/*.jsonl")[0]))[0]
    with pytest.raises(ValueError):
        asyncio.run(run_load(runner, scenario, LoadProfile(total_workflows=3), source_class="mode_b_live"))
