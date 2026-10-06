"""Harness-controlled deterministic test double (authored decision script).

The script is an authored callable mapping an Observation to the next
Decision, injected when the harness constructs the adapter. During execution
the adapter forwards only the Observation; it has no access to the scenario or
its expectations, so the SUE cannot see the answer key.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from .base import Decision, Observation, ToolCall

DecisionScript = Callable[[Observation], Decision]


class ScriptedModelAdapter:
    def __init__(self, script: DecisionScript, config_id: str) -> None:
        self._script = script
        self._config_id = config_id
        self._call_count = 0

    @property
    def config_id(self) -> str:
        return self._config_id

    @property
    def call_count(self) -> int:
        return self._call_count

    def decide(self, observation: Observation) -> Decision:
        self._call_count += 1
        return self._script(observation)


def from_sequence(decisions: list[Decision], config_id: str) -> ScriptedModelAdapter:
    """Authored fixed sequence, indexed by call count.

    Exhausting the script yields a safe final answer rather than raising, so a
    misauthored script fails a scenario cleanly instead of crashing the runner.
    """
    state = {"i": 0}

    def _script(_obs: Observation) -> Decision:
        i = state["i"]
        state["i"] += 1
        if i >= len(decisions):
            return Decision(
                kind="final_answer",
                final_answer="(script exhausted with no further authored decision)",
            )
        return decisions[i]

    return ScriptedModelAdapter(_script, config_id)


def from_fn(fn: DecisionScript, config_id: str) -> ScriptedModelAdapter:
    return ScriptedModelAdapter(fn, config_id)


# ---- observation-conditioned scripts ----------------------------------------
# Authored decisions live in JSON fixtures (fixtures/model_scripts/), separate
# from scenario expectations. A rule fires only when the observation matches its
# guard, so the double verifies each result before proceeding and does not march
# through a success path after an error. An unmatched observation yields a
# fixture-mismatch, not a blind next step.

def _match(when: dict, ctx: dict) -> bool:
    for k, v in when.items():
        if k.startswith("result."):
            if ctx["result"].get(k[len("result."):]) != v:
                return False
        elif k in ("step", "last_tool", "last_status"):
            if ctx.get(k) != v:
                return False
        else:
            return False
    return True


def _to_decision(spec: dict) -> Decision:
    if spec["kind"] == "final_answer":
        return Decision(kind="final_answer", final_answer=spec["final_answer"])
    return Decision(kind="tool_call",
                    tool_call=ToolCall(tool=spec["tool"], arguments=spec["arguments"]))


def from_rules(rules: list[dict], on_mismatch: dict, config_id: str) -> ScriptedModelAdapter:
    def _script(obs: Observation) -> Decision:
        results = obs.tool_results
        last = results[-1] if results else None
        ctx = {
            "step": len(results),
            "last_tool": (last.tool if last else None),
            "last_status": (last.status if last else None),
            "result": (last.data if last else {}),
        }
        for rule in rules:
            if _match(rule["when"], ctx):
                return _to_decision(rule["then"])
        return _to_decision(on_mismatch)

    return ScriptedModelAdapter(_script, config_id)


_DEFAULT_MISMATCH = {"kind": "final_answer",
                     "final_answer": "(fixture mismatch: unexpected observation)"}


def from_fixture_file(path, config_id: str | None = None) -> ScriptedModelAdapter:
    spec = json.loads(Path(path).read_text())
    return from_rules(spec["rules"], spec.get("on_mismatch", _DEFAULT_MISMATCH),
                      config_id or spec.get("config_id", "scripted:fixture"))
