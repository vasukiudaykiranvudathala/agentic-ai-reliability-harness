"""Release-gate pipeline and CLI.

Runs a set of scenarios offline (Mode A), computes metrics, evaluates the gate
profile, writes the scorecard artifacts, and returns a pass/fail suitable for a
CI exit code. The default scenario set is the release regression set: it
excludes intentional-bypass meta-fixtures (tagged 'unsafe-env'), which exist to
test the harness itself, not the agent.
"""
from __future__ import annotations

import sys
from pathlib import Path

from .evaluators import aggregate, build_context, default_evaluators, evaluate_all
from .gates import GateProfile, Scorecard, evaluate_gates, load_profile
from .metrics import MetricValue, compute_metrics, summarize
from .report import write_reports
from .runner import Runner
from .scenario import Scenario, load_scenario_dir

# Meta-fixtures test the harness itself (that it catches loops, budget breaches,
# invariant violations, and false success), so they intentionally fail. They are
# excluded from the release regression set, which asserts the agent behaves well.
META_TAGS = {"control-fixture", "false-success"}


def release_scenarios(scenario_dir: str | Path) -> list[Scenario]:
    return [s for s in load_scenario_dir(scenario_dir) if not (set(s.tags) & META_TAGS)]


def run_release_gate(
    scenarios: list[Scenario],
    profile: GateProfile,
    *,
    datasets_dir: str | Path,
    baselines: dict[str, float] | None = None,
) -> tuple[Scorecard, dict[str, MetricValue]]:
    runner = Runner(datasets_dir=datasets_dir)
    summaries = []
    for sc in scenarios:
        result = runner.run(sc)
        evs = evaluate_all(build_context(result, sc), default_evaluators())
        suite_pass = aggregate(evs, sc)[0] if sc.pass_criteria else None
        summaries.append(summarize(sc, result, evs, suite_pass))
    metrics = compute_metrics(summaries)
    scorecard = evaluate_gates(profile, metrics, baselines=baselines)
    return scorecard, metrics


def main(argv: list[str] | None = None) -> int:
    import argparse

    repo = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(description="Run the offline release gate.")
    parser.add_argument("--scenarios", default=str(repo / "scenarios"))
    parser.add_argument("--datasets", default=str(repo / "datasets"))
    parser.add_argument("--profile", default=str(repo / "profiles" / "strict-demo.yaml"))
    parser.add_argument("--out", default=str(repo / "reports" / "latest"))
    args = parser.parse_args(argv)

    profile = load_profile(args.profile)
    scenarios = release_scenarios(args.scenarios)
    scorecard, metrics = run_release_gate(scenarios, profile, datasets_dir=args.datasets)
    paths = write_reports(scorecard, metrics, args.out)

    verdict = scorecard.decision.upper()   # PASS | FAIL | INCONCLUSIVE
    print(f"Release gate: {verdict} (profile={scorecard.profile}, "
          f"illustrative={scorecard.illustrative}, scenarios={len(scenarios)})")
    if scorecard.summary["blocking_failures"]:
        print("Blocking failures:", ", ".join(scorecard.summary["blocking_failures"]))
    if scorecard.summary.get("blocking_inconclusive"):
        print("Inconclusive (missing evidence):", ", ".join(scorecard.summary["blocking_inconclusive"]))
    print(f"Scorecard: {paths['markdown']}")
    return 0 if scorecard.passed else 1


if __name__ == "__main__":
    sys.exit(main())
