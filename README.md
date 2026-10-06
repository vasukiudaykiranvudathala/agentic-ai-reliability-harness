# Agentic AI Reliability Harness (reference implementation)

A controlled execution, measurement, and evaluation layer around an agent. It
supplies reproducible scenarios, records model and tool activity, verifies the
business outcome against authoritative state, enforces budgets, and (in later
increments) injects failures, evaluates guardrails, measures performance, and
applies release gates.

The default suite runs fully offline with a scripted model adapter, so results
are deterministic and CI stays green without any model API.

## Status

- Increment (a): system-under-evaluation core (agent loop, model boundary,
  six simulated tools, trusted security layer, tool gateway, state store).
- Increment (b): scenario + execution-record models, JSONL loader, the runner
  with budget enforcement and step-level invariant checks, trace collector.
- Increment (c): read-only EvaluationContext; response, trajectory, outcome,
  and guardrail evaluators; fail-safe wrapper (a broken evaluator can never
  turn a failing run green); scenario aggregation.
- Increment (d): loop / non-progress detection (loop_exact, loop_oscillation,
  stall, amplification) driven by a progress function; fault injector with
  typed effects and phases, including after-commit-before-response paired with
  idempotency. Faithful-vs-false-success behavior is tested on both sides.
- Increment (e): OpenTelemetry span mapping (compact attributes only); metrics
  module with denominator-zero (not_evaluated) and minimum-sample
  (insufficient_data) handling and offline/live source classes; workflow-level
  load generator (Mode A validated; Mode B env-gated). Optional Docker Compose
  profile: OTel Collector + Jaeger by default, Prometheus/Grafana under 'perf'.
- Increment (f): release-gate engine (absolute + regression gates, fail-safe on
  missing/insufficient metrics, minimum_attempts guard); calibration-first
  design with an illustrative profiles/strict-demo.yaml; report generator
  (JSON + Markdown scorecard); GitHub Actions release-gate workflow. The
  reference harness is now feature-complete.

## Requirements

- Python 3.12+ (verified on 3.12 Linux and 3.13 Windows)
- pydantic 2.6+, pytest 8+

## Install and run

Linux / macOS:

    python3 -m venv .venv && . .venv/bin/activate
    pip install -e ".[dev]"
    pytest -v

Windows (PowerShell):

    py -3.13 -m venv .venv
    .venv\Scripts\activate
    pip install -e ".[dev]"
    pytest -v

Expected: the full test suite passes (146 tests at the time of writing; run `pytest -q` for the current count).

## Run a scenario

    from pathlib import Path
    from arh.harness.runner import Runner
    from arh.harness.scenario import load_scenarios

    runner = Runner(datasets_dir="datasets")
    scenario = load_scenarios("scenarios/golden/golden-billing-incident-001.jsonl")[0]
    result = runner.run(scenario)
    print(result.record.termination_reason, result.record.invariants_held)
    print(result.final_state["tickets"])

## Run the release gate

    python -m arh.harness.release_gate            # offline, exits non-zero on a blocking failure
    # writes reports/latest/scorecard.{json,md}

Thresholds live in profiles/strict-demo.yaml and are ILLUSTRATIVE. The handbook
teaches how to calibrate your own; copy the profile and adjust.

## Optional telemetry stack

The test suite needs no containers. To view traces in Jaeger:

    docker compose up            # OTel Collector + Jaeger (traces)
    ARH_OTLP_ENDPOINT=http://localhost:4318/v1/traces python -m arh.telemetry.export_demo
    # open http://localhost:16686

Add the metrics stack (Prometheus + Grafana) with: docker compose --profile perf up

## Layout

    src/arh/
      agent/       agent under test: bounded loop + stepwise session
      model/       model boundary + scripted (offline) adapter
      security/    trusted layer: identity, policy, PDP, approvals
      tools/       gateway (PEP), registry, schemas, simulators
      state/       authoritative business state
      harness/     control plane: scenario, runner, record, trace, assertions
    datasets/      seed data (accounts, policies, incidents, knowledge_base)
    scenarios/     JSONL test cases (golden, control fixtures)
    reports/       generated records (git-ignored); sanitized samples in examples/
    tests/         pass and fail path tests for every property

## Design invariants

- The agent holds no authority. The tool gateway is the only path to
  execution and calls the authorization layer before any tool runs, failing
  closed if authorization is unavailable.
- The scripted adapter never sees the scenario; it receives only the same
  observations a real model would.
- Outcome and invariants are verified against the authoritative state store,
  never against the agent's narration.
- A prohibited side effect that a later step would reverse is still recorded as
  having occurred; invariants are checked step-level with evidence preserved.
