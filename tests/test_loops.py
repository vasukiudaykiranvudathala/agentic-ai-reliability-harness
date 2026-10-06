"""Increment (d): loop / non-progress detection, and legitimate iteration."""
from pathlib import Path

import pytest

from arh.harness.loops import LoopConfig, LoopDetector
from arh.harness.runner import Runner
from arh.harness.scenario import load_scenarios

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "datasets"
SCN = REPO / "scenarios"


def _rec(rel):
    scenario = load_scenarios(SCN / rel)[0]
    return Runner(datasets_dir=DATA).run(scenario).record


def test_exact_repetition_terminates_loop_exact():
    rec = _rec("adversarial/loop-exact-001.jsonl")
    assert rec.termination_reason == "loop_exact"
    assert rec.termination_detail["rule"] == "loop_exact"
    assert len(rec.tool_calls) == 3  # fires on the third identical action


def test_non_progress_distinct_calls_terminate_stall():
    rec = _rec("adversarial/stall-001.jsonl")
    assert rec.termination_reason == "stall"


def test_alternating_reads_terminate_oscillation():
    rec = _rec("adversarial/oscillation-001.jsonl")
    assert rec.termination_reason == "loop_oscillation"


def test_legitimate_multi_account_completes_unflagged():
    rec = _rec("alternate/multi-account-legitimate-001.jsonl")
    assert rec.termination_reason == "completed"
    # Two distinct account reads plus a policy read and a ticket: no loop flag.
    assert rec.termination_detail is None


def test_detector_unit_amplification():
    """A varied but wasteful pattern that dodges exact/stall still trips
    amplification once enough calls pile up with few informative outcomes."""
    d = LoopDetector(LoopConfig(exact_repeat=99, stall=99, amplification_min_calls=6, amplification_ratio=3.0))
    reason = None
    for i in range(8):
        # Same two observations recycled, but action args vary so exact/stall are suppressed here.
        tool = "get_account"
        args = {"account_id": "A-1007", "nonce": i}       # distinct actions
        data = {"account_id": "A-1007"}                    # SAME observation each time
        reason = d.observe(tool=tool, arguments=args, status="executed", data=data,
                           side_effect_count=0, state_signature="S0")
        if reason:
            break
    assert reason == "amplification"


def test_detector_does_not_flag_progressing_distinct_reads():
    d = LoopDetector(LoopConfig())
    reasons = []
    for i in range(5):
        r = d.observe(tool="get_account", arguments={"account_id": f"A-{i}"},
                      status="executed", data={"account_id": f"A-{i}"},
                      side_effect_count=0, state_signature="S0")
        reasons.append(r)
    assert all(r is None for r in reasons)


def test_state_projection_hash_ignores_the_journal():
    """Comment 4: the cycle state signature must exclude the append-only journal,
    or every write step changes the hash and no cycle can ever repeat."""
    from arh.state.snapshot import StateSnapshot
    base = {"tickets": [{"id": "T1"}], "credits": []}
    a = StateSnapshot.capture({**base, "side_effects": [{"seq": 1}]}, sequence=1, captured_vt_ms=0.0)
    b = StateSnapshot.capture({**base, "side_effects": [{"seq": 1}, {"seq": 2}]}, sequence=2, captured_vt_ms=0.0)
    # Full hash differs (journal grew) but the outcome projection is identical.
    assert a.state_hash != b.state_hash
    assert a.projection_hash(exclude=("side_effects",)) == b.projection_hash(exclude=("side_effects",))
