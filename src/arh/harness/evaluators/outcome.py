"""Outcome verifier: did the agent reach an intended business end state?

Machine-repeatable against the scenario's explicit acceptance conditions, read
from the immutable final-state snapshot. A scenario passes if ANY acceptable
alternative is fully satisfied. If a referenced authoritative collection is not
present in the snapshot, the state could not be read at the consistency point
and the result is 'indeterminate', never a silent pass. Never consults the
agent's narration.
"""
from __future__ import annotations

from ..assertions import evaluate_assertion
from .base import EvaluationContext, EvaluationResult


def _available_collections(final_state) -> set[str]:
    decoded = final_state.decoded() if hasattr(final_state, "decoded") else dict(final_state)
    return set(decoded.keys())


class OutcomeEvaluator:
    name = "outcome"
    version = "2.1"

    def evaluate(self, context: EvaluationContext) -> EvaluationResult:
        alternatives = context.scenario.expectations.acceptable_outcomes
        if not alternatives:
            # No acceptable STATE outcome was declared. This is not a pass; it is
            # "not evaluated". A scenario that changes state must declare one (the
            # loader enforces that a case declares some acceptance criteria).
            return EvaluationResult(evaluator=self.name, evaluator_version=self.version,
                                    status="not_evaluated",
                                    verdict="no acceptable state outcome declared for this scenario",
                                    evidence={"alternatives": []})

        # Consistency check: every referenced collection must be present. A
        # missing collection means the authoritative state was not readable.
        available = _available_collections(context.final_state)
        referenced = {a.collection for alt in alternatives for a in alt.all_of}
        missing_sources = sorted(referenced - available)
        if missing_sources:
            return EvaluationResult(evaluator=self.name, evaluator_version=self.version,
                                    status="indeterminate",
                                    verdict=f"authoritative state incomplete: missing {missing_sources}",
                                    evidence={"missing_sources": missing_sources})

        alt_reports = []
        any_hold = False
        for ai, alt in enumerate(alternatives):
            checks = []
            # An empty conjunction is NOT a satisfied outcome. An alternative with
            # no assertions asserts nothing and must never count as a pass.
            all_hold = len(alt.all_of) > 0
            for i, a in enumerate(alt.all_of):
                holds, evidence = evaluate_assertion(a, context.final_state)
                checks.append({"index": i, "kind": a.kind, "collection": a.collection,
                               "holds": holds, "evidence": evidence})
                all_hold = all_hold and holds
            alt_reports.append({"alternative": ai, "satisfied": all_hold, "checks": checks})
            any_hold = any_hold or all_hold

        status = "pass" if any_hold else "fail"
        verdict = ("business end state verified" if any_hold
                   else f"no acceptable outcome satisfied ({len(alternatives)} checked)")
        return EvaluationResult(evaluator=self.name, evaluator_version=self.version,
                                status=status, verdict=verdict,
                                evidence={"alternatives": alt_reports})
