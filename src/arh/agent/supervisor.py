"""SUE execution supervisor: production runtime limits, enforced inside the agent.

The deployed agent must stop itself. This supervisor enforces the production
step, tool-call, and time budgets and a runtime loop guard, and it is part of
the system you deploy, not part of the test rig. The harness runs a separate,
independent watchdog whose deadline is strictly larger; if that watchdog has to
fire, it means this supervisor failed, and the harness records the run as a
failure rather than a clean self-termination.
"""
from __future__ import annotations

import hashlib
import json
from typing import Protocol, runtime_checkable

from ..clock import Clock
from ..model.base import GatewayOutcomeLike, ToolCall


def action_signature(tool: str, arguments: dict) -> str:
    return hashlib.sha256(
        (tool + "|" + json.dumps(arguments, sort_keys=True, default=str)).encode()
    ).hexdigest()[:16]


@runtime_checkable
class LoopGuard(Protocol):
    def observe(self, action_signature: str) -> bool: ...


class ExactRepetitionGuard:
    """Minimal runtime loop protection: stop after N identical actions.

    Richer non-progress analysis (oscillation, stall, amplification) uses
    authoritative state and belongs to the control-plane evaluator, not to the
    agent's own runtime guard."""

    def __init__(self, limit: int = 3) -> None:
        self.limit = limit
        self._last: str | None = None
        self._count = 0

    def observe(self, action_signature: str) -> bool:
        if action_signature == self._last:
            self._count += 1
        else:
            self._last = action_signature
            self._count = 1
        return self._count >= self.limit

    @property
    def evidence(self) -> dict:
        return {"rule": "loop_exact", "action_signature": self._last, "consecutive": self._count}


class ExecutionSupervisor:
    def __init__(
        self,
        *,
        max_steps: int,
        max_tool_calls: int,
        deadline_ms: float | None,
        clock: Clock,
        loop_guard: LoopGuard | None = None,
    ) -> None:
        self.max_steps = max_steps
        self.max_tool_calls = max_tool_calls
        self.deadline_ms = deadline_ms
        self._clock = clock
        self._loop_guard = loop_guard or ExactRepetitionGuard()
        self._steps = 0
        self._tool_calls = 0
        self._start = clock.now_ms()

    def before_decision(self) -> str | None:
        if self._steps >= self.max_steps:
            return "step_budget"
        if self.deadline_ms is not None and (self._clock.now_ms() - self._start) >= self.deadline_ms:
            return "wallclock_budget"
        self._steps += 1
        return None

    def before_action(self, call: ToolCall) -> str | None:
        # Terminate before starting an action that would exceed the budget.
        if self._tool_calls + 1 > self.max_tool_calls:
            return "tool_call_budget"
        self._tool_calls += 1
        return None

    def after_action(self, call: ToolCall, outcome: GatewayOutcomeLike) -> str | None:
        if self._loop_guard.observe(action_signature(call.tool, call.arguments)):
            return "loop_exact"
        return None

    def loop_evidence(self) -> dict | None:
        return getattr(self._loop_guard, "evidence", None)
