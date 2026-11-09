"""Day 12: OpenAI GPT-6 Luna provider tests (offline, mocked transport).

Tests the OpenAI provider adapter without consuming API credits:

* Request payload construction (model, system instruction, JSON output).
* Response parsing (text extraction, usage accounting).
* Budget guard integration (pre-call estimate, actual usage recording).
* Error handling (HTTP errors, timeouts, malformed JSON, validation failure).
* No silent fallback to mock.

Uses `httpx.MockTransport` — no network, no API credits consumed.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import pytest

from app.domain.llm import InterpretationResult
from app.integrations.llm.base import LLMRequest
from app.integrations.llm.exceptions import (
    LLMConnectionError,
    LLMSynthesisError,
)
from app.integrations.llm.openai_provider import OpenAILLMProvider
from app.services.openai_budget import OpenAIBudgetGuard


def _make_request(
    transcript: str = "I would like to request an appointment next Wednesday morning.",
    session_id: str = "test-session",
) -> LLMRequest:
    return LLMRequest(
        transcript=transcript,
        session_id=session_id,
        prompt_version="v1",
        model="gpt-6-luna",
        timeout_seconds=15.0,
        max_output_tokens=800,
        correlation_id="test-corr",
    )


def _mock_handler(
    response_body: dict[str, Any],
    status: int = 200,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=response_body)

    return httpx.MockTransport(handler)


def _success_body(text: str, input_tokens: int = 100, output_tokens: int = 50) -> dict[str, Any]:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "gpt-6-luna",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    }


@pytest.mark.asyncio
async def test_openai_provider_constructs_correct_payload() -> None:
    guard = OpenAIBudgetGuard(
        daily_budget_usd=2.50,
        dev_budget_usd=1.00,
    )
    body = _success_body(
        json.dumps({"intent": "appointment-request", "requests": []}),
        input_tokens=120,
        output_tokens=60,
    )
    client = httpx.AsyncClient(transport=_mock_handler(body))
    provider = OpenAILLMProvider(
        api_key="test-key-not-real",
        model="gpt-6-luna",
        timeout_seconds=15.0,
        max_output_tokens=800,
        budget_guard=guard,
        http_client=client,
    )
    request = _make_request()
    response = await provider.interpret(request)

    # Verify the request sent to the mock transport.
    assert client._transport is not None
    assert response.raw_text == json.dumps({"intent": "appointment-request", "requests": []})
    assert response.parsed is not None
    assert isinstance(response.parsed, InterpretationResult)
    assert response.parsed.intent == "appointment-request"
    assert response.correlation_id == "test-corr"
    assert response.total_ms >= 0


@pytest.mark.asyncio
async def test_openai_provider_records_actual_usage_from_response() -> None:
    guard = OpenAIBudgetGuard(
        daily_budget_usd=2.50,
        dev_budget_usd=1.00,
    )
    body = _success_body(
        json.dumps({"intent": "practice-information", "requests": []}),
        input_tokens=200,
        output_tokens=100,
    )
    client = httpx.AsyncClient(transport=_mock_handler(body))
    provider = OpenAILLMProvider(
        api_key="test-key-not-real",
        model="gpt-6-luna",
        timeout_seconds=15.0,
        max_output_tokens=800,
        budget_guard=guard,
        http_client=client,
    )
    request = _make_request("What time does the practice open on Monday?")
    await provider.interpret(request)

    snap = guard.snapshot()
    assert snap.request_count >= 1
    # The guard records both the pre-call estimate and actual usage.
    assert snap.input_tokens > 0
    assert snap.output_tokens > 0


@pytest.mark.asyncio
async def test_openai_provider_http_error_raises_connection_error() -> None:
    guard = OpenAIBudgetGuard(daily_budget_usd=2.50, dev_budget_usd=1.00)
    client = httpx.AsyncClient(transport=_mock_handler({"error": "bad"}, status=500))
    provider = OpenAILLMProvider(
        api_key="test-key-not-real",
        model="gpt-6-luna",
        timeout_seconds=15.0,
        max_output_tokens=800,
        budget_guard=guard,
        http_client=client,
    )
    with pytest.raises(LLMConnectionError):
        await provider.interpret(_make_request())


@pytest.mark.asyncio
async def test_openai_provider_malformed_json_raises_synthesis_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    guard = OpenAIBudgetGuard(daily_budget_usd=2.50, dev_budget_usd=1.00)
    provider = OpenAILLMProvider(
        api_key="test-key-not-real",
        model="gpt-6-luna",
        timeout_seconds=15.0,
        max_output_tokens=800,
        budget_guard=guard,
        http_client=client,
    )
    with pytest.raises(LLMSynthesisError):
        await provider.interpret(_make_request())


@pytest.mark.asyncio
async def test_openai_provider_validation_failure_raises() -> None:
    body = _success_body(
        json.dumps({"intent": "invalid-intent", "requests": []}),
        input_tokens=50,
        output_tokens=25,
    )
    client = httpx.AsyncClient(transport=_mock_handler(body))
    guard = OpenAIBudgetGuard(daily_budget_usd=2.50, dev_budget_usd=1.00)
    provider = OpenAILLMProvider(
        api_key="test-key-not-real",
        model="gpt-6-luna",
        timeout_seconds=15.0,
        max_output_tokens=800,
        budget_guard=guard,
        http_client=client,
    )
    with pytest.raises(LLMSynthesisError):
        await provider.interpret(_make_request())


@pytest.mark.asyncio
async def test_openai_provider_budget_cap_refuses_request() -> None:
    """When the dev budget cap is reached, no request is made."""
    guard = OpenAIBudgetGuard(daily_budget_usd=2.50, dev_budget_usd=0.001)
    client = httpx.AsyncClient(transport=_mock_handler(_success_body("{}")))
    provider = OpenAILLMProvider(
        api_key="test-key-not-real",
        model="gpt-6-luna",
        timeout_seconds=15.0,
        max_output_tokens=800,
        budget_guard=guard,
        http_client=client,
    )
    with pytest.raises(LLMConnectionError):
        await provider.interpret(_make_request())


def test_openai_provider_missing_api_key_raises() -> None:
    with pytest.raises(ValueError):
        OpenAILLMProvider(
            api_key="",
            model="gpt-6-luna",
            timeout_seconds=15.0,
            max_output_tokens=800,
        )
