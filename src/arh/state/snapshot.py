"""Immutable authoritative-state snapshot.

Evaluators judge evidence they must not be able to alter. A StateSnapshot is a
frozen value object built by canonical serialization: it carries a sequence
number, the virtual-time capture point, a content hash for tamper detection,
and the canonical JSON of the outcome-relevant state. Reads decode a fresh copy
each time, so an evaluator can inspect the state without mutating the canonical
evidence. The live state store is never handed to an evaluator.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict


def canonicalize(state: dict) -> str:
    return json.dumps(state, sort_keys=True, default=str)


class StateSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)
    sequence: int
    captured_vt_ms: float
    state_hash: str
    canonical_json: str

    @classmethod
    def capture(cls, state: dict, *, sequence: int, captured_vt_ms: float) -> "StateSnapshot":
        canonical = canonicalize(state)
        digest = hashlib.sha256(canonical.encode()).hexdigest()[:16]
        return cls(sequence=sequence, captured_vt_ms=captured_vt_ms,
                   state_hash=digest, canonical_json=canonical)

    def decoded(self) -> dict:
        """A fresh, mutable copy for querying. Mutating it cannot affect the
        canonical evidence, which stays in canonical_json/state_hash."""
        return json.loads(self.canonical_json)

    # Convenience read access so callers can treat a snapshot like a mapping
    # without ever holding a mutable reference to the real state.
    def __getitem__(self, key: str) -> Any:
        return self.decoded()[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.decoded().get(key, default)

    def projection_hash(self, exclude: tuple[str, ...] = ()) -> str:
        """Hash of the outcome-relevant state projection, excluding volatile
        collections (e.g. the append-only side-effect journal). Cycle detection
        must use this: if every step's hash changed because the journal grew, no
        cycle would ever repeat and no oscillation could be seen."""
        proj = {k: v for k, v in self.decoded().items() if k not in exclude}
        return hashlib.sha256(canonicalize(proj).encode()).hexdigest()[:16]

    def verify(self) -> bool:
        return hashlib.sha256(self.canonical_json.encode()).hexdigest()[:16] == self.state_hash
