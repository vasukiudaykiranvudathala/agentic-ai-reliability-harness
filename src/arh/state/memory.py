"""A persistent-memory test double.

Persistent memory is not part of the golden single-session workflow, but its
failure modes, cross-tenant and cross-session leakage, expiry, and poisoning by
provenance, can be tested offline against a small store. Each entry carries the
provenance and trust level that decide whether it may be trusted, and the store
enforces tenant and session isolation and expiry at read time. A memory read is
evidence, never authority: only an authoritative_fact is trusted, and a
security decision is never stored in general memory at all.
"""
from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

# Provenance/trust taxonomy. A model_inference or user_statement must never be
# silently promoted to an authoritative_fact.
TrustLevel = Literal[
    "authoritative_fact", "user_statement", "retrieved_content",
    "model_inference", "summary", "security_decision",
]


class MemoryEntry(BaseModel):
    model_config = ConfigDict(frozen=True)
    memory_id: str
    tenant_id: str
    session_id: str | None
    content: str
    source: str
    trust_level: TrustLevel
    created_vt_ms: float = 0.0
    expires_vt_ms: float | None = None
    source_revision: str | None = None


class MemoryContext(BaseModel):
    tenant_id: str
    session_id: str | None = None
    now_vt_ms: float = 0.0


def is_authoritative(entry: MemoryEntry) -> bool:
    """Only an authoritative_fact may be trusted as true without revalidation."""
    return entry.trust_level == "authoritative_fact"


class MemoryAdapter(Protocol):
    def write(self, entry: MemoryEntry, context: MemoryContext) -> None: ...
    def search(self, query: str, context: MemoryContext) -> list[MemoryEntry]: ...


class MemoryStore:
    def __init__(self) -> None:
        self._entries: list[MemoryEntry] = []

    def write(self, entry: MemoryEntry, context: MemoryContext) -> None:
        # A caller can only write into its own tenant.
        if entry.tenant_id != context.tenant_id:
            raise PermissionError("cross-tenant memory write denied")
        # Security decisions are never accepted from general memory.
        if entry.trust_level == "security_decision":
            raise PermissionError("security decisions are not stored in general memory")
        self._entries.append(entry)

    def search(self, query: str, context: MemoryContext) -> list[MemoryEntry]:
        out: list[MemoryEntry] = []
        for e in self._entries:
            if e.tenant_id != context.tenant_id:          # tenant isolation
                continue
            if e.session_id is not None and e.session_id != context.session_id:  # session isolation
                continue
            if e.expires_vt_ms is not None and context.now_vt_ms >= e.expires_vt_ms:  # expiry
                continue
            if query.lower() in e.content.lower():
                out.append(e)
        return out
