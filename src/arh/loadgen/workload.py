"""Workflow-level load generator (Modes A and B).

Treats each incoming request as a whole workflow, not a single API call, and
measures the fan-out to downstream operations.

Two workload models are implemented. In the OPEN model, arrivals are scheduled
on a timer INDEPENDENTLY of completion: the arrival shaping settings (arrival
pattern, rate_per_sec, spike_at, spike_size) apply here, and the concurrency cap
bounds simultaneous execution so queue wait shows up in end-to-end latency. In
the CLOSED model, a fixed pool of workers each issues the next workflow only
after the previous one completes, so in-flight never exceeds the pool; arrival
shaping does not apply to the closed model.

Mode A (scripted) validates the generator itself: arrival shaping, the
concurrency cap, amplification arithmetic, and success accounting. Its latency
numbers describe the harness, not a model, and are labeled accordingly. Mode B
(a live adapter) is where real latency, throughput, and cost come from; it is
env-gated and never part of the offline suite.
"""
from __future__ import annotations

import asyncio
import random
import time
from typing import Literal

from pydantic import BaseModel

from ..harness.runner import RunResult, Runner
from ..harness.evaluators.outcome import OutcomeEvaluator
from ..harness.scenario import Scenario

Arrival = Literal["constant", "poisson", "spike"]
SourceClass = Literal["mode_a_harness_validation", "mode_b_live"]


class LiveRunBudget(BaseModel):
    """A hard ceiling for Mode B, so a stray profile cannot run up real spend."""
    max_workflows: int
    max_model_calls: int
    max_tokens: int
    max_cost_usd: float
    max_duration_seconds: int


class LoadProfile(BaseModel):
    # workload_model: "closed" paces arrivals by completion (a fixed pool of
    # workers, each issuing the next workflow after finishing the previous, so
    # in-flight <= concurrency_cap). "open" schedules arrivals on a timer,
    # independently of completion, which is what capacity testing needs.
    workload_model: Literal["open", "closed"] = "closed"
    total_workflows: int
    # arrival shaping below applies to the OPEN model only; the closed model
    # paces by completion and ignores these fields.
    arrival: Arrival = "constant"     # open model: constant | poisson | spike
    rate_per_sec: float = 50.0        # open model: mean arrival rate
    concurrency_cap: int = 8          # closed: pool size; open: max simultaneous
    spike_at: int | None = None       # open model: index at which a burst arrives
    spike_size: int = 0               # open model: extra arrivals in the burst
    seed: int = 12345


class LoadReport(BaseModel):
    source_class: SourceClass
    total: int
    completed: int
    succeeded: int
    success_rate: float
    wall_elapsed_s: float
    throughput_per_sec: float
    max_observed_concurrency: int
    workflow_amplification: float     # (model_calls + executed_tool_calls) / workflows
    model_call_amplification: float
    tool_call_amplification: float
    retry_amplification: float        # executed tool calls / committed side effects
    latency_sample_count: int
    p50_wall_latency_ms: float | None
    p95_wall_latency_ms: float | None
    p99_wall_latency_ms: float | None
    p95_status: Literal["ok", "provisional", "insufficient_data"]
    duplicate_effect_rate: float
    note: str


def _interarrival(profile: LoadProfile, rng: random.Random) -> float:
    if profile.arrival == "constant":
        return 1.0 / profile.rate_per_sec
    if profile.arrival == "poisson":
        return rng.expovariate(profile.rate_per_sec)
    return 1.0 / profile.rate_per_sec  # spike handled separately


def _pctl(values: list[float], q: float, ok_n: int) -> tuple[float | None, str]:
    """Report a percentile with an honest status. Below a floor it is not
    computed; between floor and ok_n it is provisional; only with enough samples
    is it "ok". (p95 wants hundreds, p99 thousands, for a stable tail estimate.)"""
    if len(values) < 20:
        return None, "insufficient_data"
    s = sorted(values)
    v = s[min(len(s) - 1, int(round(q * (len(s) - 1))))]
    return v, ("ok" if len(values) >= ok_n else "provisional")


def _verified_outcome(scenario: Scenario, r: RunResult) -> bool:
    """Success means the business OUTCOME was verified from authoritative state,
    not merely that the run reached termination_reason 'completed'. A run can
    complete and still produce the wrong or no outcome."""
    from ..harness.evaluators.base import build_context
    ctx = build_context(r, scenario)
    return OutcomeEvaluator().evaluate(ctx).status == "pass"


async def run_load(
    runner: Runner,
    scenario: Scenario,
    profile: LoadProfile,
    *,
    is_success=None,
    source_class: SourceClass = "mode_a_harness_validation",
) -> LoadReport:
    if is_success is None:
        is_success = lambda r: _verified_outcome(scenario, r)  # noqa: E731
    rng = random.Random(profile.seed)
    loop = asyncio.get_event_loop()

    active = 0
    max_active = 0
    results: list[RunResult] = []
    latencies_ms: list[float] = []

    async def _run_one(scheduled_at: float) -> None:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        r = await loop.run_in_executor(None, runner.run, scenario)
        # End-to-end latency, from arrival. For the open model this includes any
        # time queued for a slot (so overload is not hidden by coordinated
        # omission); for the closed model a worker is dedicated, so there is no
        # queue and this is the service time.
        latencies_ms.append((time.monotonic() - scheduled_at) * 1000.0)
        results.append(r)
        active -= 1

    start = time.monotonic()
    if profile.workload_model == "closed":
        # CLOSED: a fixed pool of concurrency_cap workers, each running workflows
        # back-to-back. The next arrival is issued only when a worker completes,
        # so the number in flight never exceeds the pool. Arrival shaping (rate,
        # spike) is an open-model concept and does not apply here.
        remaining = {"n": profile.total_workflows}
        lock = asyncio.Lock()

        async def worker() -> None:
            while True:
                async with lock:
                    if remaining["n"] <= 0:
                        return
                    remaining["n"] -= 1
                await _run_one(time.monotonic())   # completion-paced: awaited to done

        await asyncio.gather(*[asyncio.create_task(worker())
                               for _ in range(max(1, profile.concurrency_cap))])
    else:
        # OPEN: arrivals are scheduled on a timer INDEPENDENTLY of completion; the
        # concurrency cap bounds simultaneous execution (queued execution), and
        # queue wait shows up in end-to-end latency.
        sem = asyncio.Semaphore(profile.concurrency_cap)

        async def _open_one(scheduled_at: float) -> None:
            async with sem:
                await _run_one(scheduled_at)

        tasks: list[asyncio.Task] = []
        for i in range(profile.total_workflows):
            if profile.arrival == "spike" and profile.spike_at is not None and i == profile.spike_at:
                for _ in range(profile.spike_size):
                    tasks.append(asyncio.create_task(_open_one(time.monotonic())))
            tasks.append(asyncio.create_task(_open_one(time.monotonic())))
            await asyncio.sleep(_interarrival(profile, rng))
        await asyncio.gather(*tasks)
    elapsed = time.monotonic() - start

    # Provenance is DERIVED from the execution adapter, not trusted from the
    # caller. Scripted output cannot be labeled as a live measurement.
    adapter_modes = {r.record.adapter_mode for r in results}
    if adapter_modes and adapter_modes <= {"scripted"}:
        if source_class == "mode_b_live":
            raise ValueError("cannot label scripted (Mode A) output as mode_b_live")
        source_class = "mode_a_harness_validation"

    total = len(results)
    succeeded = sum(1 for r in results if is_success(r))
    model_calls = sum(r.record.model_calls for r in results)
    exec_tool_calls = sum(
        sum(1 for t in r.record.tool_calls if t.status == "executed") for r in results)
    all_tool_calls = sum(len(r.record.tool_calls) for r in results)
    # Amplification (demand per workflow). Retry amplification is PHYSICAL
    # attempts over LOGICAL write operations (distinct idempotency keys), read
    # from the action journal. This is defined even when a write never commits,
    # unlike attempts/committed. Duplicate effects are tracked separately: a
    # retry amplification above 1 means extra attempts, not necessarily
    # duplicated effects.
    # Logical operations are counted PER WORKFLOW (a distinct idempotency key
    # within one workflow), so two workflows that reuse the same key are two
    # logical operations, not one.
    _WRITES = ("create_ticket", "issue_credit")
    physical = logical = committed_effects = 0
    for r in results:
        jw = [e for e in r.action_journal if e.get("tool") in _WRITES]
        physical += len(jw)
        logical += len({(e.get("tool"), e.get("idempotency_key")) for e in jw})
        committed_effects += sum(1 for e in jw
                                 if e.get("commit_status") in ("committed", "committed_ack_lost"))
    retry_amp = physical / logical if logical else 0.0
    duplicate_effect_rate = (max(0, committed_effects - logical) / logical) if logical else 0.0

    p50, _ = _pctl(latencies_ms, 0.50, ok_n=30)
    p95, p95_status = _pctl(latencies_ms, 0.95, ok_n=200)
    p99, _ = _pctl(latencies_ms, 0.99, ok_n=1000)

    note = (
        "Mode A: latency and throughput reflect the harness with a scripted model, "
        "not a real model. Use Mode B for model latency, tokens, and cost."
        if source_class == "mode_a_harness_validation" else
        "Mode B: measured against a live model at limited controlled scale."
    )
    return LoadReport(
        source_class=source_class, total=total, completed=total, succeeded=succeeded,
        success_rate=(succeeded / total if total else 0.0),
        wall_elapsed_s=elapsed, throughput_per_sec=(total / elapsed if elapsed else 0.0),
        max_observed_concurrency=max_active,
        workflow_amplification=((model_calls + exec_tool_calls) / total if total else 0.0),
        model_call_amplification=(model_calls / total if total else 0.0),
        tool_call_amplification=(all_tool_calls / total if total else 0.0),
        retry_amplification=retry_amp,
        latency_sample_count=len(latencies_ms),
        p50_wall_latency_ms=p50, p95_wall_latency_ms=p95, p99_wall_latency_ms=p99,
        p95_status=p95_status, duplicate_effect_rate=duplicate_effect_rate, note=note,
    )
