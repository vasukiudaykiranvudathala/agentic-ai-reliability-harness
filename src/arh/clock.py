"""Injected clock.

Deterministic offline tests use VirtualClock so simulated slowdowns and
timeouts advance logical time instead of making the suite actually sleep.
Live modes use SystemClock. Nothing in the SUE or harness reads wall-clock
time directly; they read it through a Clock.
"""
from __future__ import annotations

import time
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now_ms(self) -> float: ...
    def sleep(self, ms: float) -> None: ...


class SystemClock:
    """Real monotonic clock for live modes."""

    def now_ms(self) -> float:
        return time.monotonic() * 1000.0

    def sleep(self, ms: float) -> None:
        time.sleep(ms / 1000.0)


class VirtualClock:
    """Logical clock. sleep() advances time without blocking."""

    def __init__(self, start_ms: float = 0.0) -> None:
        self._t = start_ms

    def now_ms(self) -> float:
        return self._t

    def sleep(self, ms: float) -> None:
        self._t += ms

    # Explicit advance for fault injection / tests.
    def advance(self, ms: float) -> None:
        self._t += ms
