"""Evaluator set and orchestration.

Evaluators receive an immutable EvaluationContext and return a typed
EvaluationResult. They never touch the mutable state store. Each evaluator is
run through a fail-safe wrapper: a raise, a timeout, or a malformed return
becomes an error/timeout result and is treated as a failure, never a pass.
"""
from __future__ import annotations

from .base import (
    EvaluationContext,
    EvaluationResult,
    Evaluator,
    aggregate,
    build_context,
    evaluate_all,
    run_evaluator,
)
from .guardrails import GuardrailEvaluator
from .grounding import GroundingEvaluator
from .outcome import OutcomeEvaluator
from .response import ResponseEvaluator
from .trajectory import TrajectoryEvaluator


def default_evaluators() -> list[Evaluator]:
    return [
        ResponseEvaluator(),
        TrajectoryEvaluator(),
        OutcomeEvaluator(),
        GuardrailEvaluator(),
    ]


__all__ = [
    "EvaluationContext", "EvaluationResult", "Evaluator",
    "run_evaluator", "evaluate_all", "aggregate", "build_context",
    "ResponseEvaluator", "TrajectoryEvaluator", "OutcomeEvaluator",
    "GuardrailEvaluator", "GroundingEvaluator", "default_evaluators",
]
