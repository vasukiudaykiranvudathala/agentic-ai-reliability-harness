"""Increment (d): fault injection and safe-vs-false-success behavior."""
from pathlib import Path

from arh.harness.evaluators import aggregate, build_context, default_evaluators, evaluate_all
from arh.harness.runner import Runner
from arh.harness.scenario import load_scenarios

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "datasets"
SCN = REPO / "scenarios"


def _run(rel):
    scenario = load_scenarios(SCN / rel)[0]
    result = Runner(datasets_dir=DATA).run(scenario)
    return scenario, result


def _suite(scenario, result):
    return aggregate(evaluate_all(build_context(result, scenario), default_evaluators()), scenario)


def test_faithful_agent_stops_safely_under_tool_timeout():
    scenario, result = _run("recovery/recovery-tool-timeout-faithful-001.jsonl")
    rec = result.record
    assert rec.termination_reason == "completed"
    # The faulted incident check surfaced as an error; the agent opened no ticket.
    assert any(t.tool == "check_incident_status" and t.status == "error" for t in rec.tool_calls)
    assert result.final_state["tickets"] == []
    overall, detail = _suite(scenario, result)
    assert overall is True, detail


def test_false_success_agent_is_caught_by_outcome():
    scenario, result = _run("recovery/recovery-tool-timeout-false-success-001.jsonl")
    rec = result.record
    # The agent ignored the failed dependency and opened a ticket, then claimed success.
    assert len(result.final_state["tickets"]) == 1
    assert "confirmed" in (rec.final_response or "").lower()
    overall, _ = _suite(scenario, result)
    assert overall is False  # outcome expected no ticket -> the false success is caught


def test_after_commit_timeout_retry_is_idempotent():
    scenario, result = _run("recovery/recovery-after-commit-idempotent-001.jsonl")
    rec = result.record
    # First create_ticket: committed then response lost (error). Retry: duplicate suppressed.
    assert rec.tool_calls[0].status == "error"
    assert rec.tool_calls[0].reason.startswith("fault_after_commit")
    # The response failed, but the business operation committed: two dimensions.
    assert rec.tool_calls[0].commit_status == "committed"
    assert rec.tool_calls[0].delivery_status == "timeout"
    # The retry is an idempotent duplicate suppression, not a second execution.
    assert rec.tool_calls[1].commit_status == "duplicate_suppressed"
    assert len(result.final_state["tickets"]) == 1
    overall, _ = _suite(scenario, result)
    assert overall is True
