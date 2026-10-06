"""The model boundary used by the agent.

An interface the agent calls to obtain its next decision. The external model
(or a test double) sits behind it. decide() receives only an Observation: the
same observable conversation, tool definitions, and tool results a real model
would see. It never receives scenario expectations, invariants, budgets, or
evaluator configuration. Those live exclusively in the control plane.
"""
from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict


class ToolDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    version: str
    description: str
    args_schema: dict


class ToolCall(BaseModel):
    tool: str
    arguments: dict


class Decision(BaseModel):
    kind: Literal["tool_call", "final_answer"]
    tool_call: ToolCall | None = None
    final_answer: str | None = None


class ObservedToolResult(BaseModel):
    tool: str
    status: str
    data: dict = {}
    error: str | None = None


GatewayOutcomeLike = object  # structural: any gateway outcome (avoids an import cycle)


class Message(BaseModel):
    """A typed conversation message. Optional fields cover user turns
    (role, content) and tool-result turns (role, name, status)."""
    role: str
    content: str | None = None
    name: str | None = None
    status: str | None = None


class Observation(BaseModel):
    messages: list[Message]
    tool_definitions: list[ToolDefinition]
    tool_results: list[ObservedToolResult]


@runtime_checkable
class ModelAdapter(Protocol):
    def decide(self, observation: Observation) -> Decision: ...

    @property
    def config_id(self) -> str: ...
