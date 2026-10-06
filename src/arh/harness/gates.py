"""Release-gate engine.

Turns computed metrics into a ship / no-ship decision through a scorecard. A
gate names a metric, a comparison, a threshold, a severity, and a scope. Two
gate types are supported:

  absolute    compare the metric to a fixed threshold (e.g. == 1.0, <= 0.02)
  regression  compare against a stored baseline (e.g. no drop > 2 points,
              no rise > 10 percent)

The handbook teaches how to CALIBRATE thresholds (baseline collection, risk
class, sample size, false-positive tolerance, business impact). The numbers in
profiles/strict-demo.yaml are an illustrative example so the repository runs
out of the box, not universal recommendations; the profile is marked
illustrative and carries a minimum_sample_size.

Fail-safe rules:
  * A metric that is not_evaluated or insufficient_data does NOT silently pass.
    Its handling is explicit per gate via on_missing (default: block).
  * A gate whose metric is absent is treated as a blocking failure.
  * A minimum_attempts guard lets rate gates require enough evidence before
    they can pass, so a 0/0 "clean" run cannot green a safety gate by vacuity.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from .metrics import MetricValue

Operator = Literal["eq", "le", "ge", "lt", "gt"]
GateType = Literal["absolute", "regression"]
Severity = Literal["blocking", "warning"]
OnMissing = Literal["block", "warn", "skip"]
RegressionKind = Literal["drop_points", "rise_points", "rise_percent", "drop_percent"]


class GateSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric: str
    type: GateType = "absolute"
    operator: Operator | None = None            # for absolute
    threshold: float | None = None              # for absolute
    regression_kind: RegressionKind | None = None  # for regression
    max_delta: float | None = None              # for regression (points or percent)
    severity: Severity = "blocking"
    minimum_attempts: int | None = None         # rate gates: require this many denom
    minimum_sample_size: int | None = None      # percentile/mean gates
    on_missing: OnMissing = "block"
    allow_provisional: bool = False   # provisional evidence needs an explicit opt-in


class GateProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: str
    illustrative: bool = True
    gates: list[GateSpec]


class GateResult(BaseModel):
    metric: str
    severity: Severity
    status: Literal["pass", "fail", "warn", "skipped", "inconclusive"]
    reason: str
    observed: float | None = None
    threshold: float | None = None
    baseline: float | None = None


class Scorecard(BaseModel):
    profile: str
    illustrative: bool
    # Three states: a measured blocking failure is "fail"; a blocking gate whose
    # evidence is missing/insufficient is "inconclusive" (a release is NOT
    # supported, but no regression was proven); otherwise "pass".
    decision: Literal["pass", "fail", "inconclusive"]
    passed: bool          # convenience: decision == "pass"
    results: list[GateResult]
    summary: dict


_OPS = {
    "eq": lambda a, b: a == b,
    "le": lambda a, b: a <= b,
    "ge": lambda a, b: a >= b,
    "lt": lambda a, b: a < b,
    "gt": lambda a, b: a > b,
}


def _missing_result(g: GateSpec, reason: str) -> GateResult:
    status = {"block": "inconclusive", "warn": "warn", "skip": "skipped"}[g.on_missing]
    return GateResult(metric=g.metric, severity=g.severity, status=status,
                      reason=f"{reason} (on_missing={g.on_missing})")


def _evidence_result(g: GateSpec, mv: MetricValue) -> GateResult | None:
    """Return an inconclusive GateResult when the metric's evidence cannot support
    a blocking decision, else None. Only 'ok' (and 'provisional' when the gate
    opts in) may proceed to comparison; error, indeterminate, not_evaluated, and
    insufficient_data can never green a gate."""
    if mv.status in ("not_evaluated", "insufficient_data", "indeterminate", "error"):
        return _missing_result(g, f"metric {mv.status}")
    if mv.status == "provisional" and not g.allow_provisional:
        return _missing_result(g, "metric provisional and gate does not allow provisional")
    if mv.value is None:
        return _missing_result(g, "metric value is None")
    if g.minimum_attempts is not None:
        denom = mv.detail.get("denominator")
        if denom is None or denom < g.minimum_attempts:
            return _missing_result(g, f"insufficient attempts ({denom} < {g.minimum_attempts})")
    # minimum_sample_size: enforce the gate's own declared minimum against the
    # metric's sample. The profile-level default is advisory and is not blanket-
    # applied to correctness gates whose denominators are naturally small
    # (critical actions, fault scenarios), which would wrongly block them.
    if g.minimum_sample_size is not None:
        sample = mv.detail.get("sample_size", mv.detail.get("denominator", mv.detail.get("n")))
        if sample is None or sample < g.minimum_sample_size:
            return _missing_result(g, f"insufficient sample ({sample} < {g.minimum_sample_size})")
    return None


def _eval_absolute(g: GateSpec, mv: MetricValue) -> GateResult:
    ineligible = _evidence_result(g, mv)
    if ineligible is not None:
        return ineligible
    ok = _OPS[g.operator](mv.value, g.threshold)
    status = "pass" if ok else ("warn" if g.severity == "warning" else "fail")
    return GateResult(metric=g.metric, severity=g.severity, status=status,
                      reason=("ok" if ok else f"{mv.value} not {g.operator} {g.threshold}"),
                      observed=mv.value, threshold=g.threshold)


def _eval_regression(g: GateSpec, mv: MetricValue, baseline: float | None) -> GateResult:
    ineligible = _evidence_result(g, mv)
    if ineligible is not None:
        return ineligible
    if baseline is None:
        return _missing_result(g, "no baseline for regression gate")
    obs = mv.value
    if g.regression_kind == "drop_points":
        delta = baseline - obs
    elif g.regression_kind == "rise_points":
        delta = obs - baseline
    elif g.regression_kind == "rise_percent":
        delta = ((obs - baseline) / baseline * 100.0) if baseline else float("inf")
    elif g.regression_kind == "drop_percent":
        delta = ((baseline - obs) / baseline * 100.0) if baseline else float("inf")
    else:
        return _missing_result(g, "unknown regression_kind")
    ok = delta <= g.max_delta
    status = "pass" if ok else ("warn" if g.severity == "warning" else "fail")
    return GateResult(metric=g.metric, severity=g.severity, status=status,
                      reason=("ok" if ok else f"{g.regression_kind} {delta:.3f} > {g.max_delta}"),
                      observed=obs, threshold=g.max_delta, baseline=baseline)


def evaluate_gates(
    profile: GateProfile,
    metrics: dict[str, MetricValue],
    *,
    baselines: dict[str, float] | None = None,
) -> Scorecard:
    baselines = baselines or {}
    results: list[GateResult] = []
    for g in profile.gates:
        mv = metrics.get(g.metric)
        if mv is None:
            results.append(_missing_result(g, "metric absent"))
            continue
        if g.type == "absolute":
            results.append(_eval_absolute(g, mv))
        else:
            results.append(_eval_regression(g, mv, baselines.get(g.metric)))

    blocking_fail = any(r.severity == "blocking" and r.status == "fail" for r in results)
    blocking_inconclusive = any(
        r.severity == "blocking" and r.status == "inconclusive" for r in results)
    decision = "fail" if blocking_fail else ("inconclusive" if blocking_inconclusive else "pass")
    summary = {
        "blocking_failures": [r.metric for r in results if r.severity == "blocking" and r.status == "fail"],
        "blocking_inconclusive": [r.metric for r in results
                                  if r.severity == "blocking" and r.status == "inconclusive"],
        "warnings": [r.metric for r in results if r.status == "warn"],
        "skipped": [r.metric for r in results if r.status == "skipped"],
    }
    return Scorecard(profile=profile.profile, illustrative=profile.illustrative,
                     decision=decision, passed=(decision == "pass"),
                     results=results, summary=summary)


def load_profile(path) -> GateProfile:
    import yaml  # local import so PyYAML is only needed when loading a profile
    with open(path) as fh:
        data = yaml.safe_load(fh)
    return GateProfile(**data)
