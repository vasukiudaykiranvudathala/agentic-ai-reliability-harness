"""Reusing an idempotency key with different arguments is a violation (review)."""
import pytest
from tests.fixtures import seeded_store

def test_same_key_different_args_is_rejected():
    store = seeded_store()
    store.create_ticket(account_id="A-1007", category="billing_incident", summary="s",
                        proposed_action="credit_review", incident_ref="EVT-402", idempotency_key="k1")
    with pytest.raises(ValueError):
        store.create_ticket(account_id="A-1007", category="billing_incident", summary="s2",
                            proposed_action="refund", incident_ref="EVT-999", idempotency_key="k1")

def test_same_key_same_args_is_suppressed_not_rejected():
    store = seeded_store()
    a = store.create_ticket(account_id="A-1007", category="billing_incident", summary="s",
                            proposed_action="credit_review", incident_ref="EVT-402", idempotency_key="k2")
    b = store.create_ticket(account_id="A-1007", category="billing_incident", summary="s",
                            proposed_action="credit_review", incident_ref="EVT-402", idempotency_key="k2")
    assert b.get("duplicate_suppressed") and a["ticket_id"] == b["ticket_id"]
