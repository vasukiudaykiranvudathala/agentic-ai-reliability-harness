"""The harness runner.

Owns the control-plane responsibilities Phase 2 assigned it:

  * builds the test environment from the scenario (seeds datasets, sets the
    caller identity and authority, loads policy, arms approvals);
  * constructs the agent under that identity via an authored script resolved
    from the model_configuration id (the adapter never sees the scenario);
  * drives the agent stepwise and enforces budgets by terminating BEFORE
    starting an action that would exceed a budget;
  * checks invariants after environment initialization and after every
    attempted side effect, records each verdict with the step, preserves the
    evidence, and stops immediately on a violation;
  * assembles a complete ExecutionRecord plus an immutable final-state snapshot
    and per-step state history for the evaluators.
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from ..agent.loop import Agent
from ..agent.supervisor import ExecutionSupervisor, ExactRepetitionGuard
from ..clock import Clock, VirtualClock
from ..security.approvals import ApprovalService, AutoDenyApprovalService, BoundApprovalService, PreApprovedApprovalService
from ..security.authorization import AuthorizationService, PolicyDecisionPoint
from ..security.identity import Identity
from ..security.policy import PolicySet, demo_policy_set
from ..state.store import StateStore
from ..tools.gateway import ToolGateway
from ..tools.registry import default_tool_definitions
from .datasets import DatasetCatalog, seed_store
from ..tools.simulators import ToolSimulators
from .assertions import evaluate_assertion
from .faults import FaultInjector
from .loops import LoopDetector, LoopConfig
from .record import (
    AuthorizationDecisionRecord,
    ExecutionRecord,
    InvariantCheck,
    StateTransition,
    StepRecord,
    ToolCallRecord,
)
from .redaction import arguments_hash, redact_arguments
from .scenario import Scenario
from .scripts import build_adapter
from .trace import TraceCollector

def _seed_store(scenario: Scenario, datasets_dir: Path) -> StateStore:
    catalog = DatasetCatalog(datasets_dir)
    return seed_store(tuple(scenario.runtime.dataset_refs), catalog)


class RunResult(BaseModel):
    record: ExecutionRecord
    final_state: object  # StateSnapshot
    state_history: tuple  # tuple[StateSnapshot, ...]
    action_journal: tuple = ()


def _build_identity(scenario: Scenario) -> Identity:
    p = scenario.trusted_environment.principal
    return Identity(caller=p.caller, authority=tuple(p.authority))


def _build_approvals(scenario: Scenario, *, clock=None) -> ApprovalService:
    approvals = scenario.trusted_environment.approvals
    if not approvals:
        return AutoDenyApprovalService()
    return BoundApprovalService(
        approvals, caller=scenario.trusted_environment.principal.caller, clock=clock)


class Runner:
    def __init__(
        self,
        *,
        datasets_dir: str | Path,
        policy_set: PolicySet | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._datasets_dir = Path(datasets_dir)
        self._policy_set = policy_set or demo_policy_set()
        self._clock = clock or VirtualClock()

    def run(
        self,
        scenario: Scenario,
        *,
        authorizer: AuthorizationService | None = None,
        evaluators=None,
    ) -> RunResult:
        store = _seed_store(scenario, self._datasets_dir)
        identity = _build_identity(scenario)
        approvals = _build_approvals(scenario, clock=self._clock)
        all_defs = default_tool_definitions()
        avail = scenario.runtime.available_tools
        tool_defs = [t for t in all_defs if t.name in avail] if avail else all_defs
        fault_injector = FaultInjector(scenario.faults, self._clock) if scenario.faults else None
        gateway = ToolGateway(
            simulators=ToolSimulators(store),
            authorizer=authorizer or PolicyDecisionPoint(self._policy_set),
            approvals=approvals,
            policy_set=self._policy_set,
            tool_defs=tool_defs,
            principal=identity,
            clock=self._clock,
            fault_injector=fault_injector,
        )
        adapter = build_adapter(scenario.reproducibility.model_configuration)
        budgets = scenario.budgets

        # SUE runtime supervisor: production limits enforced inside the agent,
        # so the deployed system stops itself on budget or exact-repetition.
        supervisor = ExecutionSupervisor(
            max_steps=budgets.max_steps, max_tool_calls=budgets.max_tool_calls,
            deadline_ms=float(budgets.wall_clock_ms), clock=self._clock,
            loop_guard=ExactRepetitionGuard(LoopConfig().exact_repeat))
        agent = Agent(model=adapter, gateway=gateway, tool_defs=tool_defs,
                      identity=identity, clock=self._clock)

        collector = TraceCollector(scenario.id, adapter_mode="scripted", seed=scenario.seed)
        messages = scenario.input.get("messages", [])

        state_history: list = []
        start_ms = self._clock.now_ms()

        # Baseline invariants after environment initialization.
        init_snap = store.snapshot(self._clock.now_ms())
        state_history.append(init_snap)
        init_checks, init_ok = self._check_invariants(scenario, init_snap)
        collector.add_step(StepRecord(index=-1, kind="tool_call", tool="__init__",
                                      invariant_checks=init_checks))

        # Control-plane non-progress analysis. Exact repetition is the SUE's own
        # runtime guard, so this detector looks only for oscillation, stall, and
        # amplification, using authoritative-state signatures.
        detector = LoopDetector(LoopConfig(exact_repeat=10 ** 9))
        # Independent harness watchdog, strictly beyond the SUE limits. If it
        # fires, the SUE failed to stop itself and the run is a failure.
        wd = scenario.controls.watchdog
        watchdog_max_actions = max(budgets.max_steps, budgets.max_tool_calls) + wd.extra_actions
        watchdog_deadline_ms = float(budgets.wall_clock_ms) + float(wd.extra_ms)
        obs_state = {"termination_detail": None, "invariants_held": init_ok, "actions": 0}

        def observer(index, tool, arguments, outcome):
            arg_hash = arguments_hash(arguments)
            _WRITES = ("create_ticket", "issue_credit")
            reason = outcome.reason or ""
            data = outcome.result.data if outcome.result else {}
            if outcome.status == "executed":
                delivery_status = "delivered"
                if data.get("duplicate_suppressed"):
                    commit_status = "duplicate_suppressed"
                elif tool in _WRITES:
                    commit_status = "committed"
                else:
                    commit_status = "not_applicable"
            elif outcome.status == "error" and reason.startswith("fault_after_commit"):
                # The side effect committed; only the acknowledgment was lost.
                commit_status, delivery_status = "committed", "timeout"
            elif outcome.status == "error":
                commit_status = "not_committed" if tool in _WRITES else "not_applicable"
                delivery_status = "malformed" if "malformed" in reason else "timeout"
            else:  # rejected
                commit_status = "not_started"
                delivery_status = "delivered"
            collector.add_tool_call(ToolCallRecord(
                index=index, tool=tool, tool_version=outcome.tool_version,
                status=outcome.status, reason=outcome.reason, arguments_hash=arg_hash,
                result=data, commit_status=commit_status, delivery_status=delivery_status))
            if outcome.authorization is not None:
                collector.add_authorization(AuthorizationDecisionRecord(
                    index=index, tool=tool, decision=outcome.authorization.decision,
                    reason=outcome.authorization.reason, policy=outcome.authorization.policy,
                    approval_result=(outcome.approval.result if outcome.approval else None)))
            snap = store.snapshot(self._clock.now_ms())
            state_history.append(snap)
            collector.add_state_transition(StateTransition(
                step_index=index, tickets=len(snap["tickets"]),
                credits=len(snap["credits"]), side_effects=len(snap["side_effects"])))
            checks, ok = self._check_invariants(scenario, snap)
            collector.add_step(StepRecord(
                index=index, kind="tool_call", tool=tool, tool_version=outcome.tool_version,
                arguments_redacted=redact_arguments(tool, arguments), arguments_hash=arg_hash,
                gateway_status=outcome.status, gateway_reason=outcome.reason,
                invariant_checks=checks))
            if not ok:
                obs_state["invariants_held"] = False
                return "invariant_violation"
            reason = detector.observe(
                tool=tool, arguments=arguments, status=outcome.status,
                data=(outcome.result.data if outcome.result else {}),
                side_effect_count=len(snap["side_effects"]),
                state_signature=snap.projection_hash(exclude=("side_effects",)))
            if reason is not None:
                obs_state["termination_detail"] = detector.evidence
                return reason
            obs_state["actions"] += 1
            if obs_state["actions"] > watchdog_max_actions or \
                    (self._clock.now_ms() - start_ms) >= watchdog_deadline_ms:
                return "watchdog"
            return None

        if not init_ok:
            termination = "invariant_violation"
            final_response = None
            model_calls = 0
        else:
            run = agent.run(messages, supervisor=supervisor, observer=observer)
            termination = run.termination_reason
            final_response = run.final_response
            model_calls = run.model_calls
            if termination == "completed":
                n_tool_steps = len([s for s in run.steps if s.kind == "tool_call"])
                collector.add_step(StepRecord(index=n_tool_steps, kind="final_answer"))

        invariants_held = obs_state["invariants_held"]
        # The termination detail must describe the reason that actually stopped
        # the run. The SUE's exact-repetition guard and the control-plane
        # detector can both fire on the same action; report the winner's evidence.
        if termination == "loop_exact":
            termination_detail = supervisor.loop_evidence()
        elif termination in ("stall", "loop_oscillation", "amplification"):
            termination_detail = obs_state["termination_detail"]
        else:
            termination_detail = None
        # Attribute who stopped the run. Only the SUE's own budget/loop guards are
        # self-termination; a control-plane cancellation (invariant, stall,
        # oscillation, amplification) and the watchdog are harness intervention.
        _SUE_SELF = {"completed", "step_budget", "tool_call_budget", "wallclock_budget", "loop_exact"}
        _CONTROL_PLANE = {"invariant_violation", "stall", "loop_oscillation", "amplification"}
        if termination == "watchdog":
            terminated_by = "watchdog"
        elif termination in _CONTROL_PLANE:
            terminated_by = "control_plane"
        else:
            terminated_by = "sue"
        self_terminated = terminated_by == "sue"

        final_snap = store.snapshot(self._clock.now_ms())
        if not state_history or final_snap.state_hash != state_history[-1].state_hash:
            state_history.append(final_snap)

        latencies = {"e2e_ms": self._clock.now_ms() - start_ms}
        record = collector.assemble(
            reproducibility=scenario.reproducibility.model_dump(),
            identity={"caller": identity.caller, "authority": list(identity.authority)},
            latencies=latencies,
            termination_reason=termination,  # type: ignore[arg-type]
            termination_detail=termination_detail,
            final_response=final_response,
            invariants_held=invariants_held,
            self_terminated=self_terminated, terminated_by=terminated_by,
            model_calls=model_calls,
            token_usage=None,  # scripted mode: no token accounting
            cost=None,
        )
        result = RunResult(record=record, final_state=final_snap, state_history=state_history,
                            action_journal=tuple(store.action_journal))
        if evaluators:
            from .evaluators import build_context, evaluate_all
            ctx = build_context(result, scenario)
            results = evaluate_all(ctx, evaluators)
            record.evaluation_results = [r.model_dump() for r in results]
        return result

    @staticmethod
    def _check_invariants(scenario: Scenario, snapshot: dict) -> tuple[list[InvariantCheck], bool]:
        checks: list[InvariantCheck] = []
        ok = True
        for i, inv in enumerate(scenario.invariants):
            holds, evidence = evaluate_assertion(inv, snapshot)
            checks.append(InvariantCheck(index=i, holds=holds, evidence=evidence))
            if not holds:
                ok = False
        return checks, ok
