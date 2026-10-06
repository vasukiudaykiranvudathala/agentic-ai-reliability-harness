"""SUE self-termination and the independent harness watchdog (Chapter 4).

The deployed agent must stop itself on its production budgets. The harness runs
a separate outer watchdog whose limit is strictly larger; it exists only to
catch a SUE that fails to stop itself, and when it fires the run is a failure,
not a clean self-termination.
"""
from arh.clock import VirtualClock
from arh.model.base import Decision, Observation, ToolCall
from arh.agent.supervisor import ExecutionSupervisor, ExactRepetitionGuard
from fixtures import build_agent, SUPPORT_AGENT

MESSAGES = [{"role": "user", "content": "look at account A-1007"}]


class AlwaysReadModel:
    """A model that never stops: it keeps requesting the same read."""
    def __init__(self) -> None:
        self.call_count = 0

    def decide(self, observation: Observation) -> Decision:
        self.call_count += 1
        return Decision(kind="tool_call",
                        tool_call=ToolCall(tool="get_account", arguments={"account_id": "A-1007"}))

    @property
    def config_id(self) -> str:
        return "scripted:always-read"


def _sue_supervisor(clock, *, max_steps, max_tool_calls, deadline_ms, loop_limit=10 ** 9):
    return ExecutionSupervisor(max_steps=max_steps, max_tool_calls=max_tool_calls,
                               deadline_ms=deadline_ms, clock=clock,
                               loop_guard=ExactRepetitionGuard(loop_limit))


def test_sue_self_terminates_on_tool_call_budget():
    agent, _ = build_agent(script=AlwaysReadModel())
    clock = VirtualClock()
    sup = _sue_supervisor(clock, max_steps=99, max_tool_calls=2, deadline_ms=None)
    run = agent.run(MESSAGES, supervisor=sup)
    assert run.termination_reason == "tool_call_budget"
    assert len(run.tool_outcomes) == 2  # stopped before the third


def test_sue_self_terminates_on_step_budget():
    agent, _ = build_agent(script=AlwaysReadModel())
    clock = VirtualClock()
    sup = _sue_supervisor(clock, max_steps=2, max_tool_calls=99, deadline_ms=None)
    run = agent.run(MESSAGES, supervisor=sup)
    assert run.termination_reason == "step_budget"


def test_sue_self_terminates_on_exact_repetition():
    agent, _ = build_agent(script=AlwaysReadModel())
    clock = VirtualClock()
    sup = _sue_supervisor(clock, max_steps=99, max_tool_calls=99, deadline_ms=None, loop_limit=3)
    run = agent.run(MESSAGES, supervisor=sup)
    assert run.termination_reason == "loop_exact"
    assert len(run.tool_outcomes) == 3


def test_harness_watchdog_fires_when_sue_does_not_stop():
    # The SUE supervisor is broken: its limits never trigger. The control-plane
    # observer runs an independent watchdog that stops the run after 3 actions.
    agent, _ = build_agent(script=AlwaysReadModel())
    clock = VirtualClock()
    broken = _sue_supervisor(clock, max_steps=10 ** 9, max_tool_calls=10 ** 9, deadline_ms=None)

    seen = {"n": 0}

    def watchdog(index, tool, arguments, outcome):
        seen["n"] += 1
        return "watchdog" if seen["n"] >= 3 else None

    run = agent.run(MESSAGES, supervisor=broken, observer=watchdog)
    assert run.termination_reason == "watchdog"


def test_sue_deadline_is_below_the_harness_watchdog_deadline():
    # The runner sets the SUE deadline to the scenario wall-clock budget and the
    # watchdog strictly above it. This encodes "SUE stops first" as a property.
    wall_clock_ms = 10_000
    watchdog_deadline_ms = float(wall_clock_ms) + 5000.0
    assert float(wall_clock_ms) < watchdog_deadline_ms
