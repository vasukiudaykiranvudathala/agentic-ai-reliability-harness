"""Report generator.

Writes two artifacts per release-gate run: a machine-readable JSON scorecard
(metrics + gate results) and a human-readable Markdown summary. Both go to a
git-ignored reports/ directory; a sanitized example is kept under
reports/examples/. Illustrative values and source classes are labeled inline so
a scripted-mode number is never mistaken for a model measurement.
"""
from __future__ import annotations

import json
from pathlib import Path

from .gates import Scorecard
from .metrics import MetricValue


def scorecard_to_json(scorecard: Scorecard, metrics: dict[str, MetricValue]) -> dict:
    return {
        "profile": scorecard.profile,
        "illustrative": scorecard.illustrative,
        "passed": scorecard.passed,
        "summary": scorecard.summary,
        "gate_results": [r.model_dump() for r in scorecard.results],
        "metrics": {k: v.model_dump() for k, v in metrics.items()},
    }


def scorecard_to_markdown(scorecard: Scorecard, metrics: dict[str, MetricValue]) -> str:
    lines: list[str] = []
    verdict = "PASS" if scorecard.passed else "FAIL"
    lines.append(f"# Release gate: {verdict}")
    lines.append("")
    lines.append(f"Profile: `{scorecard.profile}`"
                 + ("  (ILLUSTRATIVE thresholds)" if scorecard.illustrative else ""))
    if scorecard.summary["blocking_failures"]:
        lines.append(f"Blocking failures: {', '.join(scorecard.summary['blocking_failures'])}")
    if scorecard.summary["warnings"]:
        lines.append(f"Warnings: {', '.join(scorecard.summary['warnings'])}")
    if scorecard.summary["skipped"]:
        lines.append(f"Skipped: {', '.join(scorecard.summary['skipped'])}")
    lines.append("")
    lines.append("## Gates")
    lines.append("")
    lines.append("| Metric | Severity | Status | Observed | Threshold | Reason |")
    lines.append("|---|---|---|---|---|---|")
    for r in scorecard.results:
        obs = "" if r.observed is None else f"{r.observed:.3f}"
        thr = "" if r.threshold is None else f"{r.threshold:.3f}"
        lines.append(f"| {r.metric} | {r.severity} | {r.status} | {obs} | {thr} | {r.reason} |")
    lines.append("")
    lines.append("## Metrics")
    lines.append("")
    lines.append("| Metric | Value | Unit | Source | Status |")
    lines.append("|---|---|---|---|---|")
    for name, mv in sorted(metrics.items()):
        val = "" if mv.value is None else f"{mv.value:.3f}"
        lines.append(f"| {name} | {val} | {mv.unit} | {mv.source_class} | {mv.status} |")
    lines.append("")
    return "\n".join(lines)


def write_reports(
    scorecard: Scorecard, metrics: dict[str, MetricValue], out_dir: str | Path
) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "scorecard.json"
    md_path = out / "scorecard.md"
    json_path.write_text(json.dumps(scorecard_to_json(scorecard, metrics), indent=2))
    md_path.write_text(scorecard_to_markdown(scorecard, metrics))
    return {"json": json_path, "markdown": md_path}
