"""Scenario model and JSONL loader.

Scenarios are the control plane's specification of a test. The schema is grouped
by visibility class so accidental leakage is structurally harder than it would
be in one flat object: stimulus and runtime configuration reach the system under
evaluation only through production interfaces; trusted-environment configuration
shapes the world but is not handed to the model; harness controls and evaluator
expectations never reach the model at all. A malformed case is a hard load error
with its line number, never a silent skip.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator

SCHEMA_VERSION = "2.0"

Category = Literal[
    "golden", "alternate", "negative", "boundary", "ambiguous", "multiturn",
    "long_context", "adversarial", "recovery", "concurrency", "soak", "control",
]

AssertionKind = Literal["count", "field_match", "absent", "present"]

FaultTargetType = Literal["model", "tool", "state_store", "authorization_service"]
FaultTrigger = Literal["first", "nth", "always"]
FaultEffect = Literal[
    "timeout", "error", "malformed_json", "partial", "stale", "slowdown",
    "rate_limit", "duplicate", "state_store_failure", "context_pressure",
]
FaultPhase = Literal["before_execution", "during_execution", "after_commit_before_response"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OutcomeAssertion(_Strict):
    """Structured selector over the authoritative state snapshot.

    collection is one of the snapshot keys (tickets, credits, side_effects).
    where filters items by exact field match. field/expected apply to
    field_match. A defined, testable selector, not a path mini-language.
    """
    kind: AssertionKind
    collection: str
    where: dict[str, JsonValue] = Field(default_factory=dict)
    field: str | None = None
    expected: JsonValue | None = None


class OutcomeAlternative(_Strict):
    """One acceptable end state. Every assertion in all_of must hold. A scenario
    passes if ANY alternative is satisfied, so ambiguous cases can accept, for
    example, either a clarification with no side effect or a safe refusal. An
    alternative must contain at least one assertion: an empty all_of asserts
    nothing and would pass vacuously, which is rejected here."""
    all_of: list[OutcomeAssertion] = Field(default_factory=list)

    @model_validator(mode="after")
    def _non_empty(self) -> "OutcomeAlternative":
        if not self.all_of:
            raise ValueError("an acceptable-outcome alternative must contain at least one assertion")
        return self


class FaultSpec(_Strict):
    target_type: FaultTargetType
    target_name: str | None = None
    trigger: FaultTrigger
    invocation: int | None = None
    effect: FaultEffect
    phase: FaultPhase = "before_execution"
    parameters: dict[str, JsonValue] = Field(default_factory=dict)


class Reproducibility(_Strict):
    prompt_version: str
    model_configuration: str
    tool_version: str
    dataset_version: str


# ---- stimulus and runtime (reach the SUE only via production interfaces) -----

class ScenarioMessage(_Strict):
    role: str
    content: str | None = None
    name: str | None = None


class ScenarioInput(_Strict):
    messages: list[ScenarioMessage] = Field(default_factory=list)


class DatasetRef(_Strict):
    """Reference to a seeded reference-data collection. The catalog validates the
    name and records a content hash, so a run pins the data it read."""
    collection: str
    version: str = SCHEMA_VERSION
    content_hash: str | None = None


class RuntimeConfiguration(_Strict):
    # The tool surface presented to the model, as it would be in production. This
    # is availability, not authority: seeing a tool does not permit executing it.
    available_tools: list[str] = Field(default_factory=list)
    dataset_refs: list[DatasetRef] = Field(default_factory=list)
    incident: dict[str, JsonValue] | None = None


# ---- trusted environment (shapes the world; not handed to the model) ---------

class PrincipalFixture(_Strict):
    caller: str
    authority: list[str] = Field(default_factory=list)


class ApprovalFixture(_Strict):
    """A trusted external approval. It binds an authenticated approver to a
    specific action scope with an expiry. The model or a user message can never
    create one by saying 'approved'."""
    approval_id: str
    approver: str
    principal: str
    tool: str
    tool_version: str | None = None
    action_digest: str | None = None
    scope: dict[str, JsonValue] = Field(default_factory=dict)
    issued_vt_ms: int = 0
    expires_vt_ms: int | None = None
    single_use: bool = True


class TrustedEnvironment(_Strict):
    principal: PrincipalFixture
    # Authorization policy for the gateway's decision point. Distinct from the
    # business remediation policy, which is seeded reference data read via
    # get_policy and never loaded here.
    authorization_policy_refs: list[str] = Field(default_factory=list)
    approvals: list[ApprovalFixture] = Field(default_factory=list)


# ---- harness controls (never reach the model) --------------------------------

class RuntimeBudgets(_Strict):
    max_steps: int
    max_tool_calls: int
    max_tokens: int | None = None
    wall_clock_ms: int


class WatchdogConfiguration(_Strict):
    """The independent outer guard. Its limits are strictly beyond the SUE's own
    budgets; if it fires, the SUE failed to stop itself."""
    extra_actions: int = 8
    extra_ms: int = 5000


class HarnessControls(_Strict):
    runtime_budgets: RuntimeBudgets
    watchdog: WatchdogConfiguration = Field(default_factory=WatchdogConfiguration)
    faults: list[FaultSpec] = Field(default_factory=list)
    seed: int | None = None


# ---- evaluator expectations (never reach the model) --------------------------

class OrderingConstraint(_Strict):
    before: str
    after: str


class FieldEquals(_Strict):
    field: str
    expected: JsonValue


class EvidenceRequirement(_Strict):
    """Evidence is a successful, matching result before a dependent action, not
    merely a tool invocation. The source tool must have executed successfully,
    its result must satisfy every predicate, and it must precede the action."""
    name: str
    source_tool: str
    result_predicates: list[FieldEquals] = Field(default_factory=list)
    must_precede_tool: str
    require_success: bool = True


class TrajectoryExpectations(_Strict):
    required_evidence: list[str] = Field(default_factory=list)
    allowed_tool_requests: list[str] | None = None
    forbidden_tool_requests: list[str] = Field(default_factory=list)
    ordering_constraints: list[OrderingConstraint] = Field(default_factory=list)
    evidence_requirements: list[EvidenceRequirement] = Field(default_factory=list)


class ResponseExpectations(_Strict):
    deterministic: dict[str, JsonValue] = Field(default_factory=dict)


class ScenarioExpectations(_Strict):
    trajectory: TrajectoryExpectations = Field(default_factory=TrajectoryExpectations)
    invariants: list[OutcomeAssertion] = Field(default_factory=list)
    acceptable_outcomes: list[OutcomeAlternative] = Field(default_factory=list)
    response: ResponseExpectations = Field(default_factory=ResponseExpectations)
    pass_criteria: dict[str, str] = Field(default_factory=dict)
    acceptable_terminations: list[str] = Field(default_factory=lambda: ["completed"])


# ---- environment compatibility view (for existing readers) -------------------

class _EnvironmentView:
    def __init__(self, scenario: "Scenario") -> None:
        te, rt = scenario.trusted_environment, scenario.runtime
        self.datasets = [d.collection for d in rt.dataset_refs]
        self.incident = rt.incident
        self.identity = {"caller": te.principal.caller, "authority": list(te.principal.authority)}
        self.policies = list(te.authorization_policy_refs)
        self.approvals = te.approvals


class Scenario(_Strict):
    schema_version: str
    id: str
    description: str
    category: Category
    tags: list[str] = Field(default_factory=list)
    stimulus: ScenarioInput
    runtime: RuntimeConfiguration
    trusted_environment: TrustedEnvironment
    controls: HarnessControls
    expectations: ScenarioExpectations
    reproducibility: Reproducibility

    # ---- backward-compatible views over the grouped structure ----------------
    @property
    def input(self) -> dict[str, Any]:
        return {"messages": [m.model_dump(exclude_none=True) for m in self.stimulus.messages]}

    @property
    def environment(self) -> _EnvironmentView:
        return _EnvironmentView(self)

    @property
    def permitted_tools(self) -> list[str]:
        return self.expectations.trajectory.allowed_tool_requests or []

    @property
    def forbidden_actions(self) -> list[str]:
        return self.expectations.trajectory.forbidden_tool_requests

    @property
    def budgets(self) -> RuntimeBudgets:
        return self.controls.runtime_budgets

    @property
    def faults(self) -> list[FaultSpec]:
        return self.controls.faults

    @property
    def seed(self) -> int | None:
        return self.controls.seed

    @property
    def invariants(self) -> list[OutcomeAssertion]:
        return self.expectations.invariants

    @property
    def expected_outcome(self) -> list[OutcomeAssertion]:
        alts = self.expectations.acceptable_outcomes
        return alts[0].all_of if len(alts) == 1 else []

    @property
    def response_expectations(self) -> dict[str, Any]:
        return {"deterministic": self.expectations.response.deterministic}

    @property
    def pass_criteria(self) -> dict[str, str]:
        return self.expectations.pass_criteria


class ScenarioLoadError(Exception):
    pass


def _require_acceptance_criteria(sc: Scenario, where: str) -> None:
    """A scenario must declare something machine-checkable. A control fixture is
    exempt (its behavior is asserted by the harness's own tests). Any other case
    that declares no acceptable state outcome, no invariant, and no pass
    criteria is a defect, not an accidental pass, and is rejected at load."""
    if "control-fixture" in sc.tags:
        return
    e = sc.expectations
    if not e.acceptable_outcomes and not e.invariants and not e.pass_criteria:
        raise ScenarioLoadError(
            f"{where}: scenario '{sc.id}' declares no acceptance criteria "
            "(acceptable_outcomes, invariants, or pass_criteria). A state-changing "
            "case with no outcome check cannot be an accidental pass.")


def load_scenarios(path: str | Path) -> list[Scenario]:
    """Load one JSONL file. Raises ScenarioLoadError on any malformed line."""
    p = Path(path)
    out: list[Scenario] = []
    with open(p) as fh:
        for lineno, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ScenarioLoadError(f"{p}:{lineno}: invalid JSON: {exc}") from exc
            try:
                sc = Scenario(**obj)
            except ValidationError as exc:
                raise ScenarioLoadError(f"{p}:{lineno}: schema validation failed: {exc}") from exc
            _require_acceptance_criteria(sc, f"{p}:{lineno}")
            out.append(sc)
    return out


def load_scenario_dir(path: str | Path) -> list[Scenario]:
    p = Path(path)
    scenarios: list[Scenario] = []
    for f in sorted(p.rglob("*.jsonl")):
        scenarios.extend(load_scenarios(f))
    return scenarios
