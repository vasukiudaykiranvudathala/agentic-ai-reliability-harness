"""Assertion engine over an immutable state snapshot.

Shared by the runner's step-level invariant checks now and the outcome
verifier in the next increment. Pure function, no side effects, returns a
verdict plus evidence.
"""
from __future__ import annotations

from typing import Any

from .scenario import OutcomeAssertion


def _matches(item: dict, where: dict) -> bool:
    return all(item.get(k) == v for k, v in where.items())


def _filter(collection: list[dict], where: dict) -> list[dict]:
    return [i for i in collection if _matches(i, where)]


def evaluate_assertion(a: OutcomeAssertion, snapshot: dict[str, Any]) -> tuple[bool, dict]:
    collection = snapshot.get(a.collection, [])
    items = _filter(collection, a.where)
    n = len(items)

    if a.kind == "count":
        return n == a.expected, {"matched": n, "expected": a.expected}
    if a.kind == "absent":
        return n == 0, {"matched": n}
    if a.kind == "present":
        return n > 0, {"matched": n}
    if a.kind == "field_match":
        values = [i.get(a.field) for i in items]
        holds = n > 0 and all(v == a.expected for v in values)
        return holds, {"matched": n, "field": a.field, "expected": a.expected, "values": values}
    return False, {"error": f"unknown_kind:{a.kind}"}
