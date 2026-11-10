"""Tool contract for EMMA's function-calling layer (Day 20).

Pipecat lets the LLM call annotated async functions ("tools") and feeds the
result back into the conversation. EMMA had three hard-coded backend actions
inside the pipeline (submit request, handoff, knowledge lookup) with no shared
validation, permission model, timeout, audit trail or idempotency rule.

This module defines that shared contract. The rules it enforces are the ones
the assignment's safety invariants depend on:

* **Typed arguments only.** Every tool declares a Pydantic parameter model; the
  executor validates the model-supplied JSON before anything runs. Unknown
  fields are rejected (`extra="forbid"` in each model), so an LLM cannot smuggle
  a `practice_id` into a write.
* **Permissions, not capabilities.** `read` tools may run any time; `write`
  tools additionally *require* an explicit caller confirmation that the
  controller recorded; `escalate` tools may run but are audited separately.
* **Bounded.** Every call has a timeout; a slow backend can never hold a call.
* **Idempotent where it matters.** Write tools declare an idempotency key so a
  retried turn cannot create a second request.
* **Auditable.** Each execution produces a `ToolAuditEntry` (tool name,
  permission, outcome, latency, idempotency key) — never arguments or payloads.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class ToolPermission(StrEnum):
    """What kind of side effect a tool has."""

    READ = "read"  # no state change (knowledge lookup, status read)
    WRITE = "write"  # changes practice state (request, booking, message)
    ESCALATE = "escalate"  # hands the caller to a human / another queue


class ToolStatus(StrEnum):
    OK = "ok"
    INVALID_ARGUMENTS = "invalid_arguments"
    UNKNOWN_TOOL = "unknown_tool"
    PERMISSION_DENIED = "permission_denied"
    CONFIRMATION_REQUIRED = "confirmation_required"
    TIMEOUT = "timeout"
    FAILED = "failed"
    REPLAYED = "replayed"  # idempotent replay of an earlier identical call


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Static description of one tool (also the LLM-facing JSON schema source)."""

    name: str
    description: str
    parameters: type[BaseModel]
    permission: ToolPermission
    timeout_seconds: float = 5.0
    # Write tools only: the controller must have recorded an explicit yes.
    requires_confirmation: bool = False
    # True → the executor derives and remembers a key from the arguments.
    idempotent: bool = False
    # Shown in `GET /api/v1/tools`; never contains secrets.
    tags: tuple[str, ...] = ()

    def json_schema(self) -> dict[str, Any]:
        """OpenAI/Gemini-compatible function declaration (no handler, no secrets)."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters.model_json_schema(),
        }

    def catalog_entry(self) -> dict[str, Any]:
        """Safe, operator-facing description of the tool."""
        return {
            "name": self.name,
            "description": self.description,
            "permission": self.permission.value,
            "requires_confirmation": self.requires_confirmation,
            "idempotent": self.idempotent,
            "timeout_seconds": self.timeout_seconds,
            "tags": list(self.tags),
        }


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One model-requested tool invocation (validated by the executor)."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    call_id: str | None = None


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Structured outcome — never an exception, so the conversation can continue."""

    status: ToolStatus
    tool: str
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    latency_ms: float = 0.0
    idempotency_key: str | None = None
    permission: ToolPermission | None = None

    @property
    def ok(self) -> bool:
        return self.status in (ToolStatus.OK, ToolStatus.REPLAYED)


@dataclass(frozen=True, slots=True)
class ToolAuditEntry:
    """Audit record for one execution (counts/outcomes only, never payloads)."""

    tool: str
    permission: ToolPermission | None
    status: ToolStatus
    latency_ms: float
    idempotency_key: str | None = None
    error: str | None = None
    at: float = field(default_factory=time.time)


class ToolContext(BaseModel):
    """Server-authoritative execution context (never supplied by the model)."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True, extra="forbid")

    session_id: str
    practice_id: str
    confirmed: bool = False
    call_id: str | None = None


# A handler receives validated arguments + the server context and returns data.
ToolHandler = Callable[[BaseModel, ToolContext], Awaitable[dict[str, Any] | None]]


__all__ = [
    "ToolAuditEntry",
    "ToolCall",
    "ToolContext",
    "ToolHandler",
    "ToolPermission",
    "ToolResult",
    "ToolSpec",
    "ToolStatus",
]
