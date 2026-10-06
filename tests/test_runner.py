"""Increment (b): runner, budgets, and step-level invariants (pass + fail paths)."""
from pathlib import Path

from arh.harness.runner import Runner
from arh.harness.scenario import load_scenarios

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "datasets"
SCN = REPO / "scenarios"


def _runner():
    return Runner(datasets_dir=DATA)


def _one(path):
    return load_scenarios(path)[0]


def test_golden_scenario_completes_with_complete_record():
    scenario = _one(SCN / "golden" / "golden-billing-incident-001.jsonl")
    result = _runner().run(scenario)
    rec = result.record

    assert rec.termination_reason == "completed"
    assert rec.invariants_held is True
    assert rec.scenario_id == "golden-billing-incident-001"
    assert rec.adapter_mode == "scripted"
    assert rec.token_usage is None and rec.cost is None  # scripted mode

    # Record completeness: ids, reproducibility, identity, and observable activity.
    assert rec.trace_id and rec.record_id.startswith("rec-")
    assert rec.reproducibility["model_configuration"] == "scripted:golden-001"
    assert rec.reproducibility["prompt_version"] == "sys-2.3"
    assert rec.identity["caller"] == "support-agent-role"
    assert len(rec.tool_calls) == 4
    assert [t.tool for t in rec.tool_calls] == [
        "get_account", "check_incident_status", "get_policy", "create_ticket"]
    assert all(a.decision == "allow" for a in rec.authorization_decisions)
    assert rec.model_calls == 5

    # Authoritative end state.
    tickets = result.final_state["tickets"]
    assert len(tickets) == 1 and tickets[0]["incident_ref"] == "EVT-402"
    assert result.final_state["credits"] == []

    # Every recorded invariant check holds, at init and after each side effect.
    for step in rec.steps:
        for chk in step.invariant_checks:
            assert chk.holds is True


def test_tool_call_budget_stops_before_exceeding():
    scenario = _one(SCN / "control" / "control-tool-call-budget-001.jsonl")
    rec = _runner().run(scenario).record
    assert rec.termination_reason == "tool_call_budget"
    # Budget is 3: exactly 3 executed, the 4th never started.
    assert len(rec.tool_calls) == 3


def test_step_budget_stops_before_exceeding():
    scenario = _one(SCN / "control" / "control-step-budget-001.jsonl")
    rec = _runner().run(scenario).record
    assert rec.termination_reason == "step_budget"
    assert len(rec.tool_calls) == 2


def test_invariant_violation_stops_immediately_and_preserves_evidence():
    scenario = _one(SCN / "control" / "control-invariant-violation-001.jsonl")
    result = _runner().run(scenario)
    rec = result.record

    assert rec.termination_reason == "invariant_violation"
    assert rec.invariants_held is False
    # The prohibited credit actually committed (permissive env) and is preserved.
    assert len(result.final_state["credits"]) == 1
    assert any(se["kind"] == "issue_credit" for se in result.final_state["side_effects"])
    # There is no final_answer step: execution stopped at the violating side effect.
    assert rec.final_response is None
    assert not any(s.kind == "final_answer" for s in rec.steps)
    # The violating step recorded a failed invariant check as evidence.
    violated = [c for s in rec.steps for c in s.invariant_checks if not c.holds]
    assert violated, "expected at least one recorded failed invariant check"


def test_malformed_scenario_is_a_hard_load_error(tmp_path):
    import pytest
    from arh.harness.scenario import ScenarioLoadError

    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"schema_version":"1.0","id":"x"}\n')  # missing required fields
    with pytest.raises(ScenarioLoadError):
        load_scenarios(bad)
