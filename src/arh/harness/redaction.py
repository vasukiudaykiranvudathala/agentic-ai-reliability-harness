"""Redaction seam for persisted arguments and outputs.

Sensitive values are removed before anything is persisted to the execution
record or emitted as a span attribute. A flat key blacklist is too shallow, so
redaction is policy-driven and recursive: each key maps to a policy (omit,
tokenize, or record), nested objects and arrays are walked, and unlisted keys
default to record (the reference tools carry no secrets). The digest used for
correlation is computed over the redacted, non-secret values only.

Real systems should prefer schema annotations over a global key map and apply
source-side sanitization before telemetry leaves the trust boundary, with the
collector's redaction as a second layer.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

_REDACTED = "[REDACTED]"

# key -> policy. "omit" drops the value, "tokenize" replaces it with a stable
# non-reversible token, "record" keeps it. The seeded secret-bearing keys prove
# the control works even though the reference tools do not use them.
TELEMETRY_POLICY: dict[str, str] = {
    "api_key": "omit",
    "password": "omit",
    "authorization": "omit",
    "secret": "omit",
    "token": "tokenize",
}


def _apply(policy: str, value: Any) -> Any:
    if policy == "omit":
        return _REDACTED
    if policy == "tokenize":
        return "tok_" + hashlib.sha256(str(value).encode()).hexdigest()[:8]
    return value


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: (_apply(TELEMETRY_POLICY[k], v) if k in TELEMETRY_POLICY else _redact_value(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(v) for v in value]
    return value


def redact_arguments(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return _redact_value(arguments)


def arguments_hash(arguments: dict[str, Any]) -> str:
    """A stable correlation digest over the REDACTED (non-secret) values. This
    is a high-cardinality correlation id, not a low-cardinality grouping
    dimension, and it belongs in the record rather than as an indexed span
    attribute a query would group on."""
    redacted = redact_arguments("", arguments)
    canonical = json.dumps(redacted, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:12]
