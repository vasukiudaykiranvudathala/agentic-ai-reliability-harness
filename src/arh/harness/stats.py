"""Small, exact statistical helpers for turning per-run verdicts into
system-level claims with honest uncertainty.

These make the safety statistics runnable rather than aspirational: a zero-event
rate is bounded, not proven zero (rule of three); a proportion is reported with
a Wilson interval; and a required assurance is turned into a sample size before
the run. All bound the TESTED distribution only; they say nothing about a
scenario or attack class that was never exercised.
"""
from __future__ import annotations

import math


def rule_of_three_bound(n: int, confidence: float = 0.95) -> float:
    """One-sided upper bound on an event rate given ZERO observed events in n
    independent trials with a stable probability: p_U = 1 - alpha**(1/n),
    which is approximately 3/n for moderate n. Bounds risk for the tested
    distribution only."""
    if n <= 0:
        return 1.0
    alpha = 1.0 - confidence
    return 1.0 - alpha ** (1.0 / n)


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion k/n. Well behaved near 0
    and 1 and for small n, UNLIKE the naive normal interval. Valid only for the
    population and sampling design the n trials represent: repeating one easy
    scenario is not the same evidence as n representative scenarios."""
    if n == 0:
        return (0.0, 1.0)
    phat = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (phat + z2 / (2 * n)) / denom
    half = (z * math.sqrt(phat * (1 - phat) / n + z2 / (4 * n * n))) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def zero_event_sample_size(p_star: float, confidence: float = 0.95) -> int:
    """Trials needed to demonstrate an event rate below p_star with the given
    confidence, assuming zero observed events: N >= ln(alpha)/ln(1 - p_star),
    which is about 3/p_star for small p_star."""
    if not 0.0 < p_star < 1.0:
        raise ValueError("p_star must be in (0, 1)")
    alpha = 1.0 - confidence
    return math.ceil(math.log(alpha) / math.log(1.0 - p_star))
