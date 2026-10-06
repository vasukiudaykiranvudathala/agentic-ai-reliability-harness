"""Caller identity and authority context.

Identity is a trusted, request-scoped context. In the reference harness it is
created as a test fixture and BOUND TO THE GATEWAY at construction, not supplied
by the agent on each call. The agent submits only the action; it can neither
forge nor expand the identity. In production this context must come from a
validated session, workload identity, or signed token, never from
model-generated arguments. frozen=True prevents accidental mutation; it does not
authenticate the identity.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Identity(BaseModel):
    model_config = ConfigDict(frozen=True)
    caller: str
    authority: tuple[str, ...]  # granted scopes, e.g. ("read:*", "write:ticket")
