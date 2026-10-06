"""Evaluation context, result type, and the fail-safe wrapper."""
from __future__ import annotations

import threading
import time
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel

from ...state.snapshot import StateSnapshot
from ..record import ExecutionRecord
from ..scenario import Scenario


class EvaluationContext(BaseModel):
    """Read-only view given to evaluators.

    final_state and state_history are immutable snapshots captured by the
    runner. Evaluators never receive the live store, so they cannot modify the
    evidence they are judging.
    """

    record: ExecutionRecord
    scenario: Scenario
    final_state: StateSnapshot
    state_history: tuple[StateSnapshot, ...]
    action_journal: tuple[dict, ...] = ()


class EvaluationResult(BaseModel):
    evaluator: str
    evaluator_version: str
    status: Literal["pass", "fail", "error", "timeout", "indeterminate", "not_exercised", "not_evaluated"]
    score: float | None = None
    verdict: str
    evidence: dict = {}
    duration_ms: float = 0.0


@runtime_checkable
class Evaluator(Protocol):
    name: str
    version: str

    def evaluate(self, context: EvaluationContext) -> EvaluationResult: ...


def build_context(run_result, scenario: Scenario) -> EvaluationContext:
    return EvaluationContext(
        record=run_result.record,
        scenario=scenario,
        final_state=run_result.final_state,
        state_history=tuple(run_result.state_history),
        action_journal=tuple(getattr(run_result, "action_journal", ()) or ()),
    )


def run_evaluator(
    evaluator: Evaluator, context: EvaluationContext, *, timeout_ms: float = 2000.0
) -> EvaluationResult:
    """Run one evaluator, converting any failure into a safe result.

    A raise -> status "error". Exceeding timeout_ms -> status "timeout". A
    return value that is not an EvaluationResult -> status "error". None of
    these can be mistaken for a pass.
    """
    name = getattr(evaluator, "name", type(evaluator).__name__)
    version = getattr(evaluator, "version", "0")
    box: dict = {}

    def target() -> None:
        try:
            box["value"] = evaluator.evaluate(context)
        except Exception as exc:  # noqa: BLE001 -- fail safe on any evaluator error
            box["error"] = f"{type(exc).__name__}: {exc}"

    t = threading.Thread(target=target, daemon=True)
    start = time.monotonic()
    t.start()
    t.join(timeout_ms / 1000.0)
    duration = (time.monotonic() - start) * 1000.0

    if t.is_alive():
        return EvaluationResult(evaluator=name, evaluator_version=version, status="timeout",
                                verdict=f"evaluator exceeded {timeout_ms:.0f}ms", duration_ms=duration)
    if "error" in box:
        return EvaluationResult(evaluator=name, evaluator_version=version, status="error",
                                verdict=f"evaluator raised: {box['error']}", duration_ms=duration)
    value = box.get("value")
    if not isinstance(value, EvaluationResult):
        return EvaluationResult(evaluator=name, evaluator_version=version, status="error",
                                verdict="evaluator returned a non-EvaluationResult value",
                                duration_ms=duration)
    # Stamp duration if the evaluator did not.
    if value.duration_ms == 0.0:
        value = value.model_copy(update={"duration_ms": duration})
    return value


def evaluate_all(
    context: EvaluationContext, evaluators: list[Evaluator], *, timeout_ms: float = 2000.0
) -> list[EvaluationResult]:
    return [run_evaluator(e, context, timeout_ms=timeout_ms) for e in evaluators]


def aggregate(results: list[EvaluationResult], scenario: Scenario) -> tuple[bool, dict]:
    """Decide overall pass/fail from evaluator results and scenario.pass_criteria.

    pass_criteria maps an evaluator name to the status required for the scenario
    to pass (e.g. {"outcome": "pass"}). A missing, errored, or timed-out
    evaluator can never satisfy a criterion. With no pass_criteria, every
    evaluator must be "pass".
    """
    by_name = {r.evaluator: r for r in results}
    criteria = scenario.pass_criteria or {name: "pass" for name in by_name}
    detail: dict = {}
    overall = True
    for name, required in criteria.items():
        r = by_name.get(name)
        got = r.status if r else "missing"
        # A required status is satisfied only by that exact status. not_exercised,
        # not_evaluated, indeterminate, error, and missing never satisfy a required
        # "pass": a control that was never exercised is not a control that passed.
        ok = got == required
        detail[name] = {"required": required, "got": got, "ok": ok}
        overall = overall and ok
    return overall, detail
