"""The authored golden trajectory is observation-conditioned (Chapter 5).

A rule fires only when the observation matches its guard. If check_incident
returns an unexpected result, the adapter yields a fixture-mismatch rather than
marching on to create_ticket.
"""
from pathlib import Path
from arh.model.base import Observation, ObservedToolResult, ToolDefinition
from arh.model.scripted import from_fixture_file

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "model_scripts" / "golden_billing_incident.json"
NO_TOOLS: list[ToolDefinition] = []


def _obs(results):
    return Observation(messages=[], tool_definitions=NO_TOOLS, tool_results=results)


def test_first_decision_is_get_account():
    a = from_fixture_file(FIX)
    d = a.decide(_obs([]))
    assert d.kind == "tool_call" and d.tool_call.tool == "get_account"


def test_proceeds_to_get_policy_only_when_incident_active():
    a = from_fixture_file(FIX)
    obs = _obs([
        ObservedToolResult(tool="get_account", status="executed", data={"account_id": "A-1007"}),
        ObservedToolResult(tool="check_incident_status", status="executed", data={"active": True, "id": "EVT-402"}),
    ])
    d = a.decide(obs)
    assert d.kind == "tool_call" and d.tool_call.tool == "get_policy"


def test_fixture_mismatch_when_incident_not_active():
    a = from_fixture_file(FIX)
    obs = _obs([
        ObservedToolResult(tool="get_account", status="executed", data={"account_id": "A-1007"}),
        ObservedToolResult(tool="check_incident_status", status="executed", data={"active": False}),
    ])
    d = a.decide(obs)
    assert d.kind == "final_answer"
    assert "fixture mismatch" in d.final_answer  # did NOT proceed to create_ticket
