"""Structured response contract.

Deterministic grounding of a response's claims is only possible when the claims
are structured. When an agent emits a ResponseEnvelope, each claimed action can
be compared mechanically against the action journal. When it emits free prose,
extracting the claims is itself a semantic task, and the deterministic guarantee
does not apply.
"""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ClaimedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str
    status: Literal["completed", "partial", "blocked", "failed"]
    resource_id: str | None = None


class ResponseEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str
    workflow_status: Literal["completed", "partial", "blocked", "failed"]
    claimed_actions: list[ClaimedAction] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


def parse_envelope(final_response: str | None) -> ResponseEnvelope | None:
    """Return a ResponseEnvelope if the response is structured, else None.

    None means 'no structured contract present', not 'invalid'. Free-text
    responses return None and are grounded by other means, not here.
    """
    if not final_response:
        return None
    try:
        obj = json.loads(final_response)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    try:
        return ResponseEnvelope(**obj)
    except Exception:  # noqa: BLE001 -- a malformed envelope is not a valid contract
        return None
