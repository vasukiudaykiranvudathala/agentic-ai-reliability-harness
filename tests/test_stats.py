"""Rule of three, Wilson interval, and assurance-driven sample sizing (Chapter 17)."""
from arh.harness.stats import rule_of_three_bound, wilson_interval, zero_event_sample_size


def test_rule_of_three_bound_matches_the_examples():
    # Zero observed failures in 200 runs -> true rate under ~1.5%.
    assert abs(rule_of_three_bound(200) - 0.015) < 0.001
    # Under ~0.1% needs thousands.
    assert abs(rule_of_three_bound(3000) - 0.001) < 0.0002
    # Exact bound is slightly under 3/n.
    assert rule_of_three_bound(200) < 3 / 200 + 1e-9
    assert rule_of_three_bound(0) == 1.0   # no trials: no assurance


def test_wilson_interval_contains_estimate_and_shrinks_with_n():
    lo, hi = wilson_interval(94, 100)
    assert 0.0 < lo < 0.94 < hi < 1.0
    lo2, hi2 = wilson_interval(940, 1000)     # same proportion, 10x the data
    assert (hi2 - lo2) < (hi - lo)            # the interval shrinks
    assert wilson_interval(0, 0) == (0.0, 1.0)  # no data: maximal uncertainty


def test_zero_event_sample_size_planning():
    # To bound an unseen event rate below 1% at 95% confidence: ~ -ln(0.05)/0.01.
    n = zero_event_sample_size(0.01)
    assert 295 <= n <= 305
    # Tighter bound needs more trials.
    assert zero_event_sample_size(0.001) > zero_event_sample_size(0.01)
