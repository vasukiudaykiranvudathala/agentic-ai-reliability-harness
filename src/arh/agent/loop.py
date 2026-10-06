"""The agent under test: it owns its loop and calls the gateway directly.

The agent requests a decision from the model, and when the model asks for a
tool it passes the request straight to the gateway, which authorizes and
executes it. The agent enforces no authorization itself. Its runtime limits are
enforced by an ExecutionSupervisor that is part of the deployed system, so the
agent stops itself on budget or loop conditions.

The control plane does not route tool calls. It supplies an optional observer
that is invoked after each executed action; the observer records evidence,
checks scenario invariants, runs richer non-progress analysis, and may return a
reason that asks the agent to stop. That return value is the only way the
control plane halts the SUE, and it never authorizes or forwards the action.
"""
from __future__ import annotations

from typing import Callable

from pydantic import BaseModel

from ..clock import Clock
from ..model.base import Decision, ModelAdapter, Observation, ObservedToolResult, ToolDefinition
from ..security.identity import Identity
from ..tools.gateway import GatewayOutcome, ToolGateway
from .supervisor import ExecutionSupervisor

# observer(index, tool, arguments, outcome) -> optional cancel reason
Observer = Callable[[int, str, dict, GatewayOutcome], "str | None"]


class Step(BaseModel):
    index: int
    kind: str
    tool: str | None = None
    arguments: dict | None = None
    gateway_status: str | None = None
    gateway_reason: str | None = None


class AgentRun(BaseModel):
    final_response: str | None
    steps: list[Step]
    tool_outcomes: list[GatewayOutcome]
    termination_reason: str
    model_calls: int


class AgentSession:
    """Observation building and observe bookkeeping shared by the agent."""

    def __init__(self, *, model: ModelAdapter, tool_defs: list[ToolDefinition], identity: Identity) -> None:
        self._model = model
        self._tool_defs = tool_defs
        self._identity = identity
        self._messages: list[dict] = []
        self._observed: list[ObservedToolResult] = []

    @property
    def identity(self) -> Identity:
        return self._identity

    @property
    def model_calls(self) -> int:
        return getattr(self._model, "call_count", 0)

    def start(self, initial_messages: list[dict]) -> None:
        self._messages = list(initial_messages)
        self._observed = []

    def propose(self) -> Decision:
        obs = Observation(messages=self._messages, tool_definitions=self._tool_defs, tool_results=self._observed)
        return self._model.decide(obs)

    def observe(self, tool: str, outcome: GatewayOutcome) -> None:
        self._observed.append(ObservedToolResult(
            tool=tool, status=outcome.status,
            data=(outcome.result.data if outcome.result else {}),
            error=(None if outcome.status == "executed" else outcome.reason)))
        self._messages.append({"role": "tool", "name": tool, "status": outcome.status})


class Agent:
    def __init__(
        self, *, model: ModelAdapter, gateway: ToolGateway, tool_defs: list[ToolDefinition],
        identity: Identity, clock: Clock, max_steps: int = 12,
    ) -> None:
        self._gateway = gateway
        self._clock = clock
        self._max_steps = max_steps
        self._session = AgentSession(model=model, tool_defs=tool_defs, identity=identity)

    def run(
        self, initial_messages: list[dict], *,
        supervisor: ExecutionSupervisor | None = None, observer: Observer | None = None,
    ) -> AgentRun:
        supervisor = supervisor or ExecutionSupervisor(
            max_steps=self._max_steps, max_tool_calls=10 ** 9, deadline_ms=None, clock=self._clock)
        self._session.start(initial_messages)
        steps: list[Step] = []
        outcomes: list[GatewayOutcome] = []
        final_response: str | None = None
        termination = "completed"
        index = 0

        while True:
            stop = supervisor.before_decision()
            if stop:
                termination = stop
                break

            decision = self._session.propose()
            if decision.kind == "final_answer":
                final_response = decision.final_answer
                steps.append(Step(index=index, kind="final_answer"))
                termination = "completed"
                break

            call = decision.tool_call
            stop = supervisor.before_action(call)
            if stop:
                termination = stop
                break

            # The SUE calls the gateway directly. The gateway authorizes and
            # executes under the trusted identity bound to it at construction.
            outcome = self._gateway.execute(call)
            outcomes.append(outcome)
            steps.append(Step(index=index, kind="tool_call", tool=call.tool, arguments=call.arguments,
                              gateway_status=outcome.status, gateway_reason=outcome.reason))

            cancel = observer(index, call.tool, call.arguments, outcome) if observer else None
            self._session.observe(call.tool, outcome)
            loop_stop = supervisor.after_action(call, outcome)

            if cancel == "invariant_violation":   # safety halt takes precedence
                termination = cancel
                break
            if loop_stop:                         # SUE self-protection (exact repetition)
                termination = loop_stop
                break
            if cancel:                            # control-plane non-progress or watchdog
                termination = cancel
                break
            index += 1

        return AgentRun(final_response=final_response, steps=steps, tool_outcomes=outcomes,
                        termination_reason=termination, model_calls=self._session.model_calls)
