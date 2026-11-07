"""Unit tests for the intent interpreter (Day 5).

Tests the interpreter with a mocked LLM provider — no real API calls.
"""

from __future__ import annotations

import pytest

from app.domain.llm import InterpretationResult
from app.integrations.llm.base import LLMResponse
from app.integrations.llm.exceptions import LLMConnectionError
from app.services.conversation.intent_interpreter import (
    IntentInterpretationError,
    IntentInterpreter,
)


class FakeProvider:
    """Mock LLM provider that returns canned responses."""

    def __init__(self, response: LLMResponse | None = None, exc: Exception | None = None):
        self._response = response
        self._exc = exc
        self.call_count = 0
        self.last_request = None

    @property
    def provider_name(self) -> str:
        return "fake-provider"

    async def interpret(self, request):
        self.call_count += 1
        self.last_request = request
        if self._exc is not None:
            raise self._exc
        return self._response


def make_response(
    raw_text: str = '{"intent": "appointment-request"}',
    parsed: InterpretationResult | None = None,
) -> LLMResponse:
    return LLMResponse(
        raw_text=raw_text,
        parsed=parsed,
        first_token_ms=50.0,
        total_ms=120.0,
        correlation_id="corr-123",
    )


@pytest.mark.asyncio
async def test_interpreter_successful_interpretation() -> None:
    result = InterpretationResult(intent="appointment-request")
    provider = FakeProvider(response=make_response(parsed=result))
    interpreter = IntentInterpreter(
        provider,
        model="gemini-1.5-flash",
        timeout_seconds=15.0,
        max_output_tokens=800,
        prompt_version="v1",
        correlation_id="corr-123",
    )
    outcome = await interpreter.interpret("I'd like to book an appointment", "sess-1")
    assert outcome is result
    assert provider.call_count == 1


@pytest.mark.asyncio
async def test_interpreter_emergency_guard_prevents_llm() -> None:
    provider = FakeProvider(response=make_response())
    interpreter = IntentInterpreter(
        provider,
        model="gemini-1.5-flash",
        timeout_seconds=15.0,
        max_output_tokens=800,
        prompt_version="v1",
        correlation_id="corr-123",
    )
    with pytest.raises(IntentInterpretationError) as excinfo:
        await interpreter.interpret("I have chest pain", "sess-1")
    assert excinfo.value.emergency is True
    assert provider.call_count == 0  # LLM must NOT be called


@pytest.mark.asyncio
async def test_interpreter_provider_failure() -> None:
    provider = FakeProvider(exc=LLMConnectionError("connection refused"))
    interpreter = IntentInterpreter(
        provider,
        model="gemini-1.5-flash",
        timeout_seconds=15.0,
        max_output_tokens=800,
        prompt_version="v1",
        correlation_id="corr-123",
    )
    with pytest.raises(IntentInterpretationError):
        await interpreter.interpret("I'd like to book an appointment", "sess-1")
    assert provider.call_count == 1


@pytest.mark.asyncio
async def test_interpreter_malformed_output() -> None:
    provider = FakeProvider(response=make_response(parsed=None))
    interpreter = IntentInterpreter(
        provider,
        model="gemini-1.5-flash",
        timeout_seconds=15.0,
        max_output_tokens=800,
        prompt_version="v1",
        correlation_id="corr-123",
    )
    with pytest.raises(IntentInterpretationError):
        await interpreter.interpret("I'd like to book an appointment", "sess-1")


@pytest.mark.asyncio
async def test_interpreter_passes_correlation_id() -> None:
    parsed_result = InterpretationResult(intent="appointment-request")
    provider = FakeProvider(response=make_response(parsed=parsed_result))
    interpreter = IntentInterpreter(
        provider,
        model="gemini-1.5-flash",
        timeout_seconds=15.0,
        max_output_tokens=800,
        prompt_version="v1",
        correlation_id="corr-456",
    )
    await interpreter.interpret("book an appointment", "sess-789")
    assert provider.last_request is not None
    assert provider.last_request.correlation_id == "corr-456"
    assert provider.last_request.session_id == "sess-789"
