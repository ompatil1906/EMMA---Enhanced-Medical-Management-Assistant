"""Bounded, permission-checked tool execution (Day 20).

The executor is the only place a tool runs. It owns:

* argument validation (Pydantic, `extra="forbid"`);
* the permission + confirmation gate for write tools;
* a hard timeout per call;
* idempotent replay (same key → the earlier result, no second side effect);
* a bounded audit trail of executions for `/api/v1/diagnostics`.

It never raises for a tool failure: a failed tool becomes a `ToolResult` the
conversation can speak about truthfully.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections import deque
from typing import Any

from app.services.tools.base import (
    ToolAuditEntry,
    ToolCall,
    ToolContext,
    ToolPermission,
    ToolResult,
    ToolStatus,
)
from app.services.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

_AUDIT_MAX = 200  # bounded: one call's tool usage, not a data lake


class ToolExecutor:
    """Executes tool calls against a registry for one session."""

    def __init__(self, registry: ToolRegistry, *, audit_max: int = _AUDIT_MAX) -> None:
        self._registry = registry
        self._audit: deque[ToolAuditEntry] = deque(maxlen=audit_max)
        # idempotency key → successful result data (replay, never re-execute)
        self._completed: dict[str, dict[str, Any]] = {}

    @property
    def audit(self) -> tuple[ToolAuditEntry, ...]:
        return tuple(self._audit)

    @property
    def completed_keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._completed))

    async def execute(self, call: ToolCall, *, context: ToolContext) -> ToolResult:
        """Validate, authorise, run and record one tool call."""
        started = time.monotonic()
        entry = self._registry.get(call.name)
        if entry is None:
            return self._finish(
                ToolResult(
                    status=ToolStatus.UNKNOWN_TOOL,
                    tool=call.name,
                    error="unknown_tool",
                ),
                started,
                permission=None,
            )

        spec, handler = entry

        if spec.requires_confirmation and not context.confirmed:
            return self._finish(
                ToolResult(
                    status=ToolStatus.CONFIRMATION_REQUIRED,
                    tool=spec.name,
                    error="explicit caller confirmation is required",
                    permission=spec.permission,
                ),
                started,
                permission=spec.permission,
            )

        try:
            arguments = spec.parameters.model_validate(call.arguments)
        except Exception as exc:  # noqa: BLE001 — validation error is a result, not a crash
            return self._finish(
                ToolResult(
                    status=ToolStatus.INVALID_ARGUMENTS,
                    tool=spec.name,
                    error=f"{type(exc).__name__}: invalid arguments",
                    permission=spec.permission,
                ),
                started,
                permission=spec.permission,
            )

        key = self._idempotency_key(spec.name, arguments) if spec.idempotent else None
        if key is not None and key in self._completed:
            return self._finish(
                ToolResult(
                    status=ToolStatus.REPLAYED,
                    tool=spec.name,
                    data=self._completed[key],
                    permission=spec.permission,
                    idempotency_key=key,
                ),
                started,
                permission=spec.permission,
                key=key,
            )

        try:
            data = await asyncio.wait_for(handler(arguments, context), timeout=spec.timeout_seconds)
        except TimeoutError:
            return self._finish(
                ToolResult(
                    status=ToolStatus.TIMEOUT,
                    tool=spec.name,
                    error="tool timed out",
                    permission=spec.permission,
                    idempotency_key=key,
                ),
                started,
                permission=spec.permission,
                key=key,
            )
        except Exception as exc:  # noqa: BLE001 — tool failure must not break the call
            logger.warning(
                "tool failed",
                extra={
                    "tool": spec.name,
                    "permission": spec.permission.value,
                    "detail": type(exc).__name__,
                },
            )
            return self._finish(
                ToolResult(
                    status=ToolStatus.FAILED,
                    tool=spec.name,
                    error=f"{type(exc).__name__}",
                    permission=spec.permission,
                    idempotency_key=key,
                ),
                started,
                permission=spec.permission,
                key=key,
            )

        payload = dict(data or {})
        if key is not None:
            self._completed[key] = payload
        return self._finish(
            ToolResult(
                status=ToolStatus.OK,
                tool=spec.name,
                data=payload,
                permission=spec.permission,
                idempotency_key=key,
            ),
            started,
            permission=spec.permission,
            key=key,
        )

    # --- internals ----------------------------------------------------------

    @staticmethod
    def _idempotency_key(tool: str, arguments: Any) -> str:
        """Deterministic key over (tool, validated arguments)."""
        payload = (
            arguments.model_dump_json() if hasattr(arguments, "model_dump_json") else str(arguments)
        )
        digest = hashlib.sha256(f"{tool}|{payload}".encode()).hexdigest()[:32]
        return f"{tool}:{digest}"

    def _finish(
        self,
        result: ToolResult,
        started: float,
        *,
        permission: ToolPermission | None,
        key: str | None = None,
    ) -> ToolResult:
        latency_ms = round((time.monotonic() - started) * 1000.0, 3)
        final = ToolResult(
            status=result.status,
            tool=result.tool,
            data=result.data,
            error=result.error,
            latency_ms=latency_ms,
            idempotency_key=result.idempotency_key or key,
            permission=permission,
        )
        self._audit.append(
            ToolAuditEntry(
                tool=final.tool,
                permission=final.permission,
                status=final.status,
                latency_ms=final.latency_ms,
                idempotency_key=final.idempotency_key,
                error=final.error,
            )
        )
        logger.info(
            "tool execution",
            extra={
                "tool": final.tool,
                "status": final.status.value,
                "latency_ms": final.latency_ms,
            },
        )
        return final

    def summary(self) -> dict[str, Any]:
        """Bounded, PII-free execution summary for diagnostics."""
        by_status: dict[str, int] = {}
        by_tool: dict[str, int] = {}
        latencies: list[float] = []
        for entry in self._audit:
            by_status[entry.status.value] = by_status.get(entry.status.value, 0) + 1
            by_tool[entry.tool] = by_tool.get(entry.tool, 0) + 1
            latencies.append(entry.latency_ms)
        latencies.sort()
        return {
            "executions": len(self._audit),
            "by_status": by_status,
            "by_tool": by_tool,
            "latency_ms": _percentiles(latencies),
            "idempotent_replays": len(self._completed),
        }


def _percentiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p50": None, "p95": None, "max": None}

    def pick(fraction: float) -> float:
        index = min(len(values) - 1, max(0, int(round(fraction * (len(values) - 1)))))
        return round(values[index], 2)

    return {"p50": pick(0.5), "p95": pick(0.95), "max": round(values[-1], 2)}


__all__ = ["ToolExecutor"]
