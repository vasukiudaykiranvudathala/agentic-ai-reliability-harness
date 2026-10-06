"""Grouped schema capabilities (Chapter 6): acceptable outcomes and the
content-hashed dataset catalog."""
from types import SimpleNamespace

import pytest

from arh.harness.datasets import DatasetCatalog
from arh.harness.evaluators.outcome import OutcomeEvaluator
from arh.harness.scenario import (DatasetRef, OutcomeAlternative, OutcomeAssertion,
                                  ScenarioExpectations)
from arh.state.snapshot import StateSnapshot


def _snap(state):
    return StateSnapshot.capture(state, sequence=1, captured_vt_ms=0.0)


def test_acceptable_outcomes_pass_when_any_alternative_holds():
    exp = ScenarioExpectations(acceptable_outcomes=[
        OutcomeAlternative(all_of=[OutcomeAssertion(
            kind="count", collection="tickets", where={"account_id": "A-1"}, expected=1)]),
        OutcomeAlternative(all_of=[OutcomeAssertion(
            kind="absent", collection="tickets", where={"account_id": "A-1"})]),
    ])
    ctx = SimpleNamespace(scenario=SimpleNamespace(expectations=exp),
                          final_state=_snap({"tickets": [], "credits": [], "side_effects": []}))
    res = OutcomeEvaluator().evaluate(ctx)
    assert res.status == "pass"  # the second alternative (no ticket) is satisfied


def test_acceptable_outcomes_fail_when_no_alternative_holds():
    exp = ScenarioExpectations(acceptable_outcomes=[
        OutcomeAlternative(all_of=[OutcomeAssertion(
            kind="present", collection="tickets", where={"account_id": "A-1"})]),
    ])
    ctx = SimpleNamespace(scenario=SimpleNamespace(expectations=exp),
                          final_state=_snap({"tickets": [], "credits": [], "side_effects": []}))
    assert OutcomeEvaluator().evaluate(ctx).status == "fail"


def test_catalog_verifies_content_hash(tmp_path):
    (tmp_path / "accounts.jsonl").write_text('{"account_id":"A-1"}\n')
    cat = DatasetCatalog(tmp_path)
    records, content_hash = cat.load_verified(DatasetRef(collection="accounts"))
    assert records == [{"account_id": "A-1"}] and len(content_hash) == 16
    with pytest.raises(ValueError):
        cat.load_verified(DatasetRef(collection="accounts", content_hash="0000000000000000"))
    with pytest.raises(ValueError):
        cat.load_verified(DatasetRef(collection="unknown_collection"))
