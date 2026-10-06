"""The six simulated tools.

Read tools return data from the authoritative state store. Write tools
(create_ticket, issue_credit) mutate the store through its idempotent write
methods, so a retried call with the same idempotency key does not create a
duplicate side effect. All side effects land only in the harness state store;
nothing here touches a real external system.
"""
from __future__ import annotations

from ..state.store import StateStore
from . import schemas as S


class ToolSimulators:
    def __init__(self, store: StateStore) -> None:
        self._store = store

    @property
    def store(self):
        return self._store

    # ---- read tools -------------------------------------------------------
    def get_account(self, a: S.GetAccountArgs) -> S.ToolResult:
        acct = self._store.get_account(a.account_id)
        if acct is None:
            return S.ToolResult(status="error", error=f"account_not_found:{a.account_id}")
        return S.ToolResult(status="ok", data=acct)

    def get_policy(self, a: S.GetPolicyArgs) -> S.ToolResult:
        pol = self._store.get_policy(a.topic)
        if pol is None:
            return S.ToolResult(status="error", error=f"policy_not_found:{a.topic}")
        return S.ToolResult(status="ok", data=pol)

    def check_incident_status(self, a: S.CheckIncidentArgs) -> S.ToolResult:
        inc = self._store.get_incident(a.service, a.region)
        if inc is None:
            return S.ToolResult(status="ok", data={"active": False})
        return S.ToolResult(status="ok", data={"active": True, **inc})

    def search_knowledge_base(self, a: S.SearchKnowledgeBaseArgs) -> S.ToolResult:
        hits = self._store.search_kb(a.query)
        return S.ToolResult(status="ok", data={"results": hits})

    # ---- write tools (idempotent) ----------------------------------------
    def create_ticket(self, a: S.CreateTicketArgs) -> S.ToolResult:
        outcome = self._store.create_ticket(
            account_id=a.account_id,
            category=a.category,
            summary=a.summary,
            proposed_action=a.proposed_action,
            incident_ref=a.incident_ref,
            idempotency_key=a.idempotency_key,
        )
        return S.ToolResult(status="ok", data=outcome)

    def issue_credit(self, a: S.IssueCreditArgs) -> S.ToolResult:
        outcome = self._store.issue_credit(
            account_id=a.account_id,
            amount=a.amount,
            reason=a.reason,
            idempotency_key=a.idempotency_key,
        )
        return S.ToolResult(status="ok", data=outcome)

    def dispatch(self, tool: str, parsed_args) -> S.ToolResult:
        fn = getattr(self, tool, None)
        if fn is None:
            return S.ToolResult(status="error", error=f"unknown_tool:{tool}")
        return fn(parsed_args)
