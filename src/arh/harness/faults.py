"""Fault injector.

Faults are declared in the scenario and armed deterministically. Each fault is
a typed spec: target (model or a named tool), trigger (first / nth / always),
effect, and phase. The gateway consults the injector around tool execution:

  before_execution            the effect happens instead of execution; no side
                              effect commits (timeout, error, rate_limit).
  during_execution            the result is transformed (partial, stale,
                              malformed) or delayed (slowdown).
  after_commit_before_response
                              the tool commits its side effect, then the
                              response is lost. The agent sees an error and may
                              retry. Paired with idempotency, a retry with the
                              same key must not duplicate the side effect.

Slowdowns and timeouts advance the injected clock rather than sleeping, so the
offline suite stays fast. Authorization unavailability is NOT a fault effect;
it lives on the security path (authorization_unavailable / _error).
"""
from __future__ import annotations

from collections import defaultdict

from ..clock import Clock
from ..tools.schemas import ToolResult
from .scenario import FaultSpec

_DEFAULT_TIMEOUT_MS = 5000.0


class FaultInjector:
    def __init__(self, faults: list[FaultSpec], clock: Clock) -> None:
        self._clock = clock
        self._by_target: dict[tuple[str, str | None], list[FaultSpec]] = defaultdict(list)
        for f in faults:
            self._by_target[(f.target_type, f.target_name)].append(f)
        self._counts: dict[tuple[str, str | None], int] = defaultdict(int)

    def begin_call(self, target_type: str, target_name: str | None) -> int:
        key = (target_type, target_name)
        self._counts[key] += 1
        return self._counts[key]

    def _should_fire(self, f: FaultSpec, invocation: int) -> bool:
        if f.trigger == "always":
            return True
        if f.trigger == "first":
            return invocation == 1
        if f.trigger == "nth":
            return invocation == (f.invocation or 1)
        return False

    def fault_for(
        self, target_type: str, target_name: str | None, invocation: int, phase: str
    ) -> FaultSpec | None:
        for f in self._by_target.get((target_type, target_name), []):
            if f.phase == phase and self._should_fire(f, invocation):
                return f
        return None

    def apply_latency(self, f: FaultSpec) -> None:
        if f.effect == "slowdown":
            self._clock.sleep(float(f.parameters.get("ms", 1000)))
        elif f.effect == "timeout":
            self._clock.sleep(float(f.parameters.get("ms", _DEFAULT_TIMEOUT_MS)))

    def transform(self, f: FaultSpec, result: ToolResult) -> ToolResult:
        if f.effect == "slowdown":
            self.apply_latency(f)
            return result
        if f.effect == "partial":
            return ToolResult(status="ok", data={"_partial": True})
        if f.effect == "stale":
            data = dict(result.data)
            data["_stale"] = True
            return ToolResult(status="ok", data=data)
        if f.effect == "malformed_json":
            return ToolResult(status="error", error="malformed_response")
        return result
