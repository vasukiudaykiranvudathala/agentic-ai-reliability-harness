"""Authoritative business state.

This is the single source of truth for outcome verification. The agent can
reach it only through the tool gateway; evaluators read immutable snapshots of
it directly. That asymmetry is what makes outcome verification independent of
the agent's own account of what it did.

Write methods are idempotent by key. A side-effect log records every committed
change in order, so a prohibited action that a later step reverses is still
visible as having occurred (step-level invariant evidence).
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from .snapshot import StateSnapshot


class StateStore:
    def __init__(self) -> None:
        # Seeded, read-mostly reference data.
        self._accounts: dict[str, dict] = {}
        self._policies: dict[str, dict] = {}
        self._incidents: dict[tuple[str, str], dict] = {}
        self._kb: list[dict] = []
        # Mutable business state.
        self._tickets: list[dict] = []
        self._credits: list[dict] = []
        # Idempotency and audit.
        # Uniqueness is per (tool-kind, key): a ticket key and a credit key
        # never collide.
        self._idempotency: dict[tuple[str, str], tuple[str, str]] = {}
        # (kind,key) -> fingerprint of the arguments first seen under that key
        self._idempotency_fp: dict[tuple[str, str], str] = {}
        self._action_journal: list[dict] = []
        self._seq = 0
        self._side_effects: list[dict] = []
        self._snap_seq = 0

    # ---- seeding ----------------------------------------------------------
    @classmethod
    def empty(cls) -> "StateStore":
        return cls()

    _LOADERS = {"accounts": "seed_accounts", "policies": "seed_policies",
                "incidents": "seed_incidents", "knowledge_base": "seed_knowledge_base"}

    def load_reference_data(self, collection: str, records: list[dict]) -> None:
        method = self._LOADERS.get(collection)
        if method is None:
            raise ValueError(f"unknown dataset collection: {collection!r}")
        getattr(self, method)(records)

    def seed_accounts(self, rows: list[dict]) -> None:
        for r in rows:
            self._accounts[r["account_id"]] = dict(r)

    def seed_policies(self, rows: list[dict]) -> None:
        for r in rows:
            self._policies[r["topic"]] = dict(r)

    def seed_incidents(self, rows: list[dict]) -> None:
        for r in rows:
            self._incidents[(r["service"], r["region"])] = dict(r)

    def seed_knowledge_base(self, rows: list[dict]) -> None:
        self._kb.extend(dict(r) for r in rows)

    # ---- reads ------------------------------------------------------------
    def get_account(self, account_id: str) -> dict | None:
        acct = self._accounts.get(account_id)
        return dict(acct) if acct else None

    def get_policy(self, topic: str) -> dict | None:
        pol = self._policies.get(topic)
        return dict(pol) if pol else None

    def get_incident(self, service: str, region: str) -> dict | None:
        inc = self._incidents.get((service, region))
        return dict(inc) if inc else None

    def search_kb(self, query: str) -> list[dict]:
        q = query.lower()
        return [dict(d) for d in self._kb if q in (d.get("title", "") + " " + d.get("body", "")).lower()]

    # ---- writes (idempotent) ---------------------------------------------
    def _next_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq:04d}"

    def _record_side_effect(self, kind: str, entity_id: str, payload: dict) -> None:
        self._side_effects.append(
            {"seq": len(self._side_effects) + 1, "kind": kind, "entity_id": entity_id, "payload": copy.deepcopy(payload)}
        )

    @staticmethod
    def _fingerprint(fields: dict) -> str:
        return hashlib.sha256(json.dumps(fields, sort_keys=True, default=str).encode()).hexdigest()[:16]

    def _check_idempotency_args(self, kind: str, key: str, fields: dict) -> None:
        fp = self._fingerprint(fields)
        seen = self._idempotency_fp.get((kind, key))
        if seen is not None and seen != fp:
            raise ValueError(
                f"idempotency_key_reuse_with_different_arguments: key {key!r} for {kind} "
                "was first used with different arguments")
        self._idempotency_fp[(kind, key)] = fp

    def record_action(self, entry: dict) -> None:
        """Append-only journal of every state-changing attempt, committed or
        rejected. Records enough to detect a prohibited action that committed
        (after_state_hash != before_state_hash) or was later reversed."""
        self._action_journal.append({"seq": len(self._action_journal) + 1, **entry})

    @property
    def action_journal(self) -> list[dict]:
        return copy.deepcopy(self._action_journal)

    def create_ticket(
        self,
        *,
        account_id: str,
        category: str,
        summary: str,
        proposed_action: str,
        incident_ref: str | None,
        idempotency_key: str,
    ) -> dict:
        self._check_idempotency_args("ticket", idempotency_key,
                                     {"account_id": account_id, "category": category,
                                      "proposed_action": proposed_action, "incident_ref": incident_ref})
        if ("ticket", idempotency_key) in self._idempotency:
            _, tid = self._idempotency[("ticket", idempotency_key)]
            return {"ticket_id": tid, "duplicate_suppressed": True}
        tid = self._next_id("TK")
        ticket = {
            "ticket_id": tid,
            "account_id": account_id,
            "category": category,
            "summary": summary,
            "proposed_action": proposed_action,
            "incident_ref": incident_ref,
        }
        self._tickets.append(ticket)
        self._idempotency[("ticket", idempotency_key)] = ("ticket", tid)
        self._record_side_effect("create_ticket", tid, ticket)
        return {"ticket_id": tid, "duplicate_suppressed": False}

    def issue_credit(
        self, *, account_id: str, amount: float, reason: str, idempotency_key: str
    ) -> dict:
        self._check_idempotency_args("credit", idempotency_key,
                                     {"account_id": account_id, "amount": amount, "reason": reason})
        if ("credit", idempotency_key) in self._idempotency:
            _, cid = self._idempotency[("credit", idempotency_key)]
            return {"credit_id": cid, "duplicate_suppressed": True}
        cid = self._next_id("CR")
        credit = {"credit_id": cid, "account_id": account_id, "amount": amount, "reason": reason}
        self._credits.append(credit)
        self._idempotency[("credit", idempotency_key)] = ("credit", cid)
        self._record_side_effect("issue_credit", cid, credit)
        return {"credit_id": cid, "duplicate_suppressed": False}

    # ---- snapshots for evaluators (immutable copies) ---------------------
    def snapshot(self, captured_vt_ms: float = 0.0) -> StateSnapshot:
        self._snap_seq += 1
        state = {
            "tickets": self._tickets,
            "credits": self._credits,
            "side_effects": self._side_effects,
        }
        return StateSnapshot.capture(state, sequence=self._snap_seq, captured_vt_ms=captured_vt_ms)

    def side_effect_log(self) -> list[dict]:
        return copy.deepcopy(self._side_effects)
