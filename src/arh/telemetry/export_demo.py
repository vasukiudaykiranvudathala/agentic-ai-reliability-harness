"""Run the golden scenario and export its spans via OTLP to a local collector.

Usage (with the Compose profile running):
    ARH_OTLP_ENDPOINT=http://localhost:4318/v1/traces \\
        python -m arh.telemetry.export_demo
Open Jaeger at http://localhost:16686 and look for service
"agentic-reliability-harness".
"""
from __future__ import annotations

from pathlib import Path

from ..harness.runner import Runner
from ..harness.scenario import load_scenarios
from .otel import build_tracer_provider, emit_record_spans


def main() -> None:
    repo = Path(__file__).resolve().parents[3]
    scenario = load_scenarios(repo / "scenarios" / "golden" / "golden-billing-incident-001.jsonl")[0]
    result = Runner(datasets_dir=repo / "datasets").run(scenario)
    provider = build_tracer_provider()  # honors ARH_OTLP_ENDPOINT if set
    emit_record_spans(result.record, provider)
    provider.force_flush()
    print(f"emitted spans for {result.record.record_id} (termination={result.record.termination_reason})")


if __name__ == "__main__":
    main()
