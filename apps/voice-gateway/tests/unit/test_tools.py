"""Day 20: typed tool execution (validation, permissions, timeouts, idempotency).

The tool layer is the Pipecat "function calling" equivalent. These tests pin the
properties the safety invariants depend on: a model-supplied payload can never
smuggle a tenant id, a write tool cannot run without an explicit confirmation, a
slow backend cannot hold a call, and a retried turn cannot double-submit.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field

from app.services.tools import (
    ToolCall,
    ToolContext,
    ToolExecutor,
    ToolPermission,
    ToolRegistry,
    ToolRegistryError,
    ToolSpec,
    ToolStatus,
    build_default_registry,
)


class _EchoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=50)


class _SleepArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seconds: float = Field(gt=0, le=5)


def _context(*, confirmed: bool = False) -> ToolContext:
    return ToolContext(
        session_id="session-1", practice_id="practice-1", confirmed=confirmed, call_id="call-1"
    )


def _registry_with(spec: ToolSpec, handler: Any) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(spec, handler)
    return registry


def _echo_spec(**overrides: Any) -> ToolSpec:
    base: dict[str, Any] = {
        "name": "echo",
        "description": "echo text",
        "parameters": _EchoArgs,
        "permission": ToolPermission.READ,
        "timeout_seconds": 1.0,
    }
    base.update(overrides)
    return ToolSpec(**base)


@pytest.mark.asyncio
async def test_read_tool_runs_and_returns_structured_data() -> None:
    async def handler(args: _EchoArgs, ctx: ToolContext) -> dict[str, Any]:
        return {"echo": args.text, "practice": ctx.practice_id}

    executor = ToolExecutor(_registry_with(_echo_spec(), handler))
    result = await executor.execute(
        ToolCall(name="echo", arguments={"text": "hello"}), context=_context()
    )
    assert result.ok
    assert result.status is ToolStatus.OK
    assert result.data == {"echo": "hello", "practice": "practice-1"}
    assert result.latency_ms >= 0


@pytest.mark.asyncio
async def test_unknown_tool_is_reported_not_raised() -> None:
    executor = ToolExecutor(ToolRegistry())
    result = await executor.execute(ToolCall(name="nope"), context=_context())
    assert result.status is ToolStatus.UNKNOWN_TOOL
    assert result.error == "unknown_tool"


@pytest.mark.asyncio
async def test_arguments_are_validated_and_extra_fields_rejected() -> None:
    async def handler(args: _EchoArgs, ctx: ToolContext) -> dict[str, Any]:
        return {"echo": args.text}

    executor = ToolExecutor(_registry_with(_echo_spec(), handler))

    # A model trying to inject a tenant/identity field must be rejected outright.
    injected = await executor.execute(
        ToolCall(name="echo", arguments={"text": "hi", "practice_id": "evil"}),
        context=_context(),
    )
    assert injected.status is ToolStatus.INVALID_ARGUMENTS

    missing = await executor.execute(ToolCall(name="echo", arguments={}), context=_context())
    assert missing.status is ToolStatus.INVALID_ARGUMENTS


@pytest.mark.asyncio
async def test_write_tool_requires_explicit_confirmation() -> None:
    calls: list[str] = []

    async def handler(args: _EchoArgs, ctx: ToolContext) -> dict[str, Any]:
        calls.append(args.text)
        return {"ok": True}

    executor = ToolExecutor(
        _registry_with(
            _echo_spec(permission=ToolPermission.WRITE, requires_confirmation=True), handler
        )
    )
    denied = await executor.execute(
        ToolCall(name="echo", arguments={"text": "book"}), context=_context(confirmed=False)
    )
    assert denied.status is ToolStatus.CONFIRMATION_REQUIRED
    assert calls == [], "a write tool must not run without confirmation"

    allowed = await executor.execute(
        ToolCall(name="echo", arguments={"text": "book"}), context=_context(confirmed=True)
    )
    assert allowed.ok
    assert calls == ["book"]


@pytest.mark.asyncio
async def test_timeout_is_bounded_and_reported() -> None:
    async def slow(args: _SleepArgs, ctx: ToolContext) -> dict[str, Any]:
        await asyncio.sleep(2.0)
        return {"slept": True}

    executor = ToolExecutor(
        _registry_with(
            ToolSpec(
                name="slow",
                description="slow",
                parameters=_SleepArgs,
                permission=ToolPermission.READ,
                timeout_seconds=0.05,
            ),
            slow,
        )
    )
    result = await executor.execute(
        ToolCall(name="slow", arguments={"seconds": 2.0}), context=_context()
    )
    assert result.status is ToolStatus.TIMEOUT
    assert result.latency_ms < 1000


@pytest.mark.asyncio
async def test_handler_failure_is_a_result_not_a_crash() -> None:
    async def boom(args: _EchoArgs, ctx: ToolContext) -> dict[str, Any]:
        raise RuntimeError("backend down")

    executor = ToolExecutor(_registry_with(_echo_spec(), boom))
    result = await executor.execute(
        ToolCall(name="echo", arguments={"text": "hi"}), context=_context()
    )
    assert result.status is ToolStatus.FAILED
    assert result.error == "RuntimeError"


@pytest.mark.asyncio
async def test_idempotent_tool_replays_without_re_executing() -> None:
    executions = 0

    async def handler(args: _EchoArgs, ctx: ToolContext) -> dict[str, Any]:
        nonlocal executions
        executions += 1
        return {"serial": executions}

    executor = ToolExecutor(_registry_with(_echo_spec(idempotent=True), handler))
    first = await executor.execute(
        ToolCall(name="echo", arguments={"text": "book"}), context=_context(confirmed=True)
    )
    second = await executor.execute(
        ToolCall(name="echo", arguments={"text": "book"}), context=_context(confirmed=True)
    )
    assert first.status is ToolStatus.OK
    assert second.status is ToolStatus.REPLAYED
    assert executions == 1
    assert second.data == first.data
    assert second.idempotency_key == first.idempotency_key


@pytest.mark.asyncio
async def test_non_idempotent_tool_runs_every_time() -> None:
    executions = 0

    async def handler(args: _EchoArgs, ctx: ToolContext) -> dict[str, Any]:
        nonlocal executions
        executions += 1
        return {"serial": executions}

    executor = ToolExecutor(_registry_with(_echo_spec(idempotent=False), handler))
    await executor.execute(ToolCall(name="echo", arguments={"text": "x"}), context=_context())
    await executor.execute(ToolCall(name="echo", arguments={"text": "x"}), context=_context())
    assert executions == 2


def test_registry_rejects_duplicate_names() -> None:
    registry = ToolRegistry()

    async def handler(args: _EchoArgs, ctx: ToolContext) -> dict[str, Any]:
        return {}

    registry.register(_echo_spec(), handler)
    with pytest.raises(ToolRegistryError):
        registry.register(_echo_spec(), handler)


def test_catalog_exposes_specs_but_not_handlers() -> None:
    async def handler(args: _EchoArgs, ctx: ToolContext) -> dict[str, Any]:
        return {}

    registry = _registry_with(_echo_spec(), handler)
    entry = registry.catalog()[0]
    assert entry["name"] == "echo"
    assert entry["permission"] == "read"
    assert "handler" not in entry
    declaration = registry.function_declarations()[0]
    assert declaration["name"] == "echo"
    assert "properties" in declaration["parameters"]


@pytest.mark.asyncio
async def test_executor_summary_is_bounded_and_pii_free() -> None:
    async def handler(args: _EchoArgs, ctx: ToolContext) -> dict[str, Any]:
        return {"echo": args.text}

    executor = ToolExecutor(_registry_with(_echo_spec(), handler))
    await executor.execute(ToolCall(name="echo", arguments={"text": "hi"}), context=_context())
    await executor.execute(ToolCall(name="nope"), context=_context())

    summary = executor.summary()
    assert summary["executions"] == 2
    assert summary["by_status"]["ok"] == 1
    assert summary["by_status"]["unknown_tool"] == 1
    assert summary["latency_ms"]["p50"] is not None
    assert "hi" not in str(summary), "argument values must never appear in diagnostics"


# --- built-in tools -------------------------------------------------------


class _FakeRetriever:
    def __init__(self, chunks: list[Any]) -> None:
        self.queries: list[Any] = []
        self._chunks = chunks

    async def retrieve(self, query: Any) -> Any:
        self.queries.append(query)
        return type("Result", (), {"chunks": self._chunks})()


class _Chunk:
    chunk_id = "chunk-1"
    source_title = "Opening hours"
    content = "Monday to Friday, 8am to 6pm."


class _FakePractice:
    def __init__(self, *, status: str = "confirmed") -> None:
        self.requests: list[dict[str, Any]] = []
        self.handoffs: list[dict[str, Any]] = []
        self._status = status

    async def submit_request(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        return type(
            "Outcome",
            (),
            {"confirmed": self._status == "confirmed", "status": self._status, "record_id": "r1"},
        )()

    async def submit_handoff(self, **kwargs: Any) -> Any:
        self.handoffs.append(kwargs)
        return type("Outcome", (), {"confirmed": True, "status": "confirmed", "record_id": "h1"})()


@pytest.mark.asyncio
async def test_lookup_tool_uses_the_server_practice_scope() -> None:
    retriever = _FakeRetriever([_Chunk()])
    executor = ToolExecutor(build_default_registry(retriever=retriever))
    result = await executor.execute(
        ToolCall(
            name="lookup_practice_information",
            arguments={"question": "What are your opening hours?"},
        ),
        context=_context(),
    )
    assert result.ok
    assert result.data["available"] is True
    # Practice scope is server-authoritative, never model-supplied.
    assert retriever.queries[0].practice_id == "practice-1"


@pytest.mark.asyncio
async def test_appointment_tool_requires_confirmation_and_posts_once() -> None:
    practice = _FakePractice()
    executor = ToolExecutor(build_default_registry(practice_client=practice, practice_id="p1"))
    args = {
        "request_type": "appointment-request",
        "preferred_date": "Tuesday",
        "preferred_time": "morning",
    }
    denied = await executor.execute(
        ToolCall(name="create_appointment_request", arguments=args), context=_context()
    )
    assert denied.status is ToolStatus.CONFIRMATION_REQUIRED
    assert practice.requests == []

    first = await executor.execute(
        ToolCall(name="create_appointment_request", arguments=args),
        context=_context(confirmed=True),
    )
    replay = await executor.execute(
        ToolCall(name="create_appointment_request", arguments=args),
        context=_context(confirmed=True),
    )
    assert first.data["submitted"] is True
    assert replay.status is ToolStatus.REPLAYED
    assert len(practice.requests) == 1


@pytest.mark.asyncio
async def test_appointment_tool_rejects_unsupported_request_types() -> None:
    practice = _FakePractice()
    executor = ToolExecutor(build_default_registry(practice_client=practice, practice_id="p1"))
    result = await executor.execute(
        ToolCall(
            name="create_appointment_request",
            arguments={"request_type": "clinical-advice"},
        ),
        context=_context(confirmed=True),
    )
    assert result.status is ToolStatus.FAILED
    assert practice.requests == []


@pytest.mark.asyncio
async def test_handoff_tool_escalates_through_the_backend() -> None:
    practice = _FakePractice()
    executor = ToolExecutor(build_default_registry(practice_client=practice, practice_id="p1"))
    result = await executor.execute(
        ToolCall(name="request_human_handoff", arguments={"reason": "asked for a person"}),
        context=_context(confirmed=True),
    )
    assert result.ok
    assert result.data["requested"] is True
    assert practice.handoffs[0]["reason"] == "asked for a person"
