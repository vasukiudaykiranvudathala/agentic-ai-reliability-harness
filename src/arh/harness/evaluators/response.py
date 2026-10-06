"""Response evaluator: deterministic checks on the observable final output.

Deterministic verdicts are authoritative. Rubric-style grading (model-as-judge)
is a separate, optional, env-gated instrument added in a later increment; it is
advisory and never gates a release, so it is not evaluated here. Supported
deterministic checks come from scenario.response_expectations["deterministic"]:

  must_reference        : str | list[str]  -- all must appear in the response
  must_be_valid_json    : bool             -- response must parse as JSON
  forbidden_substrings  : list[str]        -- none may appear
"""
from __future__ import annotations

import json

from .base import EvaluationContext, EvaluationResult


class ResponseEvaluator:
    name = "response"
    version = "1.0"

    def evaluate(self, context: EvaluationContext) -> EvaluationResult:
        det = (context.scenario.response_expectations or {}).get("deterministic", {})
        response = context.record.final_response
        failures: list[str] = []
        evidence: dict = {}

        if response is None:
            # No final answer (e.g. the run stopped on a violation). If the
            # scenario expected deterministic response properties, that is a fail;
            # if it expected nothing, this check is not applicable.
            if det:
                return EvaluationResult(evaluator=self.name, evaluator_version=self.version, status="fail",
                                        verdict="no final response to evaluate",
                                        evidence={"final_response": None})
            return EvaluationResult(evaluator=self.name, evaluator_version=self.version, status="pass",
                                    verdict="no response expectations; nothing to check")

        must_ref = det.get("must_reference")
        if must_ref is not None:
            needles = [must_ref] if isinstance(must_ref, str) else list(must_ref)
            missing = [n for n in needles if n not in response]
            evidence["must_reference"] = {"required": needles, "missing": missing}
            if missing:
                failures.append(f"missing references: {missing}")

        if det.get("must_be_valid_json"):
            try:
                json.loads(response)
                evidence["valid_json"] = True
            except json.JSONDecodeError:
                evidence["valid_json"] = False
                failures.append("response is not valid JSON")

        forbidden = det.get("forbidden_substrings", [])
        present = [f for f in forbidden if f in response]
        if present:
            evidence["forbidden_present"] = present
            failures.append(f"forbidden substrings present: {present}")

        status = "pass" if not failures else "fail"
        verdict = "deterministic response checks passed" if not failures else "; ".join(failures)
        return EvaluationResult(evaluator=self.name, evaluator_version=self.version, status=status, verdict=verdict, evidence=evidence)
