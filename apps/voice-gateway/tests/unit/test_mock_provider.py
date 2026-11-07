"""Unit tests for the mock LLM provider (Day 5)."""

from __future__ import annotations

import pytest

from app.integrations.llm.base import LLMRequest
from app.integrations.llm.exceptions import LLMConnectionError, LLMTimeoutError
from app.integrations.llm.mock_provider import MockLLMProvider


def make_request(transcript: str = "I'd like to book an appointment") -> LLMRequest:
    return LLMRequest(
        transcript=transcript,
        session_id="sess-1",
        prompt_version="v1",
        model="gemini-1.5-flash",
        timeout_seconds=15.0,
        max_output_tokens=800,
        correlation_id="corr-123",
    )


@pytest.mark.asyncio
async def test_mock_appointment_request() -> None:
    provider = MockLLMProvider()
    result = await provider.interpret(make_request("I'd like to book an appointment"))
    assert result.parsed is not None
    assert result.parsed.intent == "appointment-request"
    assert len(result.parsed.requests) == 1
    assert result.parsed.requests[0].request_type == "appointment-request"


@pytest.mark.asyncio
async def test_mock_prescription_request() -> None:
    provider = MockLLMProvider()
    result = await provider.interpret(make_request("I need a repeat prescription"))
    assert result.parsed is not None
    assert result.parsed.intent == "prescription-request"


@pytest.mark.asyncio
async def test_mock_practice_information() -> None:
    provider = MockLLMProvider()
    result = await provider.interpret(make_request("What are your opening hours?"))
    assert result.parsed is not None
    assert result.parsed.intent == "practice-information"


@pytest.mark.asyncio
async def test_mock_multiple_requests() -> None:
    provider = MockLLMProvider()
    result = await provider.interpret(
        make_request("I'd like an appointment and also need a prescription")
    )
    assert result.parsed is not None
    assert len(result.parsed.requests) == 2


@pytest.mark.asyncio
async def test_mock_caller_correction() -> None:
    provider = MockLLMProvider()
    first = await provider.interpret(make_request("I'd like an appointment on tuesday morning"))
    assert first.parsed is not None
    assert first.parsed.requests[0].details.get("preferred_date") == "tuesday"

    second = await provider.interpret(make_request("book an appointment on wednesday afternoon"))
    assert second.parsed is not None
    assert second.parsed.requests[0].details.get("preferred_date") == "wednesday"
    assert second.parsed.requests[0].details.get("preferred_time") == "afternoon"


@pytest.mark.asyncio
async def test_mock_unclear_utterance() -> None:
    provider = MockLLMProvider()
    result = await provider.interpret(make_request("what is the meaning of life"))
    assert result.parsed is not None
    assert result.parsed.intent == "unclear"
    assert result.parsed.needs_clarification is True


@pytest.mark.asyncio
async def test_mock_empty_transcript() -> None:
    provider = MockLLMProvider()
    result = await provider.interpret(make_request(""))
    assert result.parsed is not None
    assert result.parsed.intent == "unclear"


@pytest.mark.asyncio
async def test_mock_deterministic() -> None:
    provider = MockLLMProvider()
    first = await provider.interpret(make_request("book an appointment"))
    second = await provider.interpret(make_request("book an appointment"))
    assert first.raw_text == second.raw_text


@pytest.mark.asyncio
async def test_mock_timeout_simulation() -> None:
    provider = MockLLMProvider(simulate_timeout=True)
    req = LLMRequest(
        transcript="book an appointment",
        session_id="sess-1",
        prompt_version="v1",
        model="gemini-1.5-flash",
        timeout_seconds=0.01,  # extremely short to force timeout
        max_output_tokens=800,
        correlation_id="corr-123",
    )
    with pytest.raises(LLMTimeoutError):
        await provider.interpret(req)


@pytest.mark.asyncio
async def test_mock_failure_simulation() -> None:
    provider = MockLLMProvider(simulate_failure=True)
    with pytest.raises(LLMConnectionError):
        await provider.interpret(make_request("book an appointment"))


@pytest.mark.asyncio
async def test_mock_malformed_simulation() -> None:
    provider = MockLLMProvider(simulate_malformed=True)
    result = await provider.interpret(make_request("book an appointment"))
    assert result.raw_text == "{not valid json"
    assert result.parsed is None


@pytest.mark.asyncio
async def test_mock_provider_name() -> None:
    assert MockLLMProvider().provider_name == "mock-llm"


@pytest.mark.asyncio
async def test_mock_call_counting() -> None:
    provider = MockLLMProvider()
    await provider.interpret(make_request("book an appointment"))
    await provider.interpret(make_request("prescription request"))
    assert provider.call_count == 2
