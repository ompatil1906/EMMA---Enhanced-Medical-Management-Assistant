"""Unit tests for the LLM provider interface and shared types (Day 5)."""

from __future__ import annotations

import pytest

from app.domain.llm import InterpretationResult, RequestDetail
from app.integrations.llm.base import LLMRequest, LLMResponse
from app.integrations.llm.exceptions import (
    LLMConfigurationError,
    LLMConnectionError,
    LLMError,
    LLMSynthesisError,
    LLMTimeoutError,
    LLMValidationError,
)


class TestLLMRequest:
    def test_request_fields(self) -> None:
        req = LLMRequest(
            transcript="I'd like to book an appointment",
            session_id="sess-1",
            prompt_version="v1",
            model="gemini-1.5-flash",
            timeout_seconds=15.0,
            max_output_tokens=800,
            correlation_id="corr-123",
        )
        assert req.transcript == "I'd like to book an appointment"
        assert req.session_id == "sess-1"
        assert req.prompt_version == "v1"
        assert req.timeout_seconds == 15.0


class TestLLMResponse:
    def test_response_with_parsed(self) -> None:
        result = InterpretationResult(intent="unclear", needs_clarification=True)
        resp = LLMResponse(
            raw_text='{"intent": "unclear"}',
            parsed=result,
            first_token_ms=50.0,
            total_ms=120.0,
            correlation_id="corr-123",
        )
        assert resp.raw_text == '{"intent": "unclear"}'
        assert resp.parsed is result
        assert resp.first_token_ms == 50.0
        assert resp.total_ms == 120.0

    def test_response_without_parsed(self) -> None:
        resp = LLMResponse(
            raw_text="invalid json",
            parsed=None,
            first_token_ms=None,
            total_ms=200.0,
            correlation_id="corr-123",
        )
        assert resp.parsed is None
        assert resp.first_token_ms is None


class TestLLMExceptions:
    def test_exception_hierarchy(self) -> None:
        assert issubclass(LLMConfigurationError, LLMError)
        assert issubclass(LLMConnectionError, LLMError)
        assert issubclass(LLMTimeoutError, LLMError)
        assert issubclass(LLMValidationError, LLMError)
        assert issubclass(LLMSynthesisError, LLMError)

    def test_exception_messages(self) -> None:
        err = LLMConfigurationError("gemini", "missing API key")
        assert "gemini" in str(err)
        assert "missing API key" in str(err)


class TestInterpretationResult:
    def test_valid_intent_categories(self) -> None:
        for intent in (
            "appointment-request",
            "appointment-change-request",
            "prescription-request",
            "practice-information",
            "practice-administration",
            "other",
            "unclear",
        ):
            result = InterpretationResult(intent=intent)
            assert result.intent == intent

    def test_invalid_intent_rejected(self) -> None:
        with pytest.raises(ValueError):
            InterpretationResult(intent="clinical-diagnosis")

    def test_multiple_requests(self) -> None:
        result = InterpretationResult(
            intent="appointment-request",
            requests=[
                RequestDetail(
                    request_type="appointment-request",
                    details={"preferred_date": "tuesday"},
                    missing_fields=["preferred_time"],
                    source_quotes=["tuesday"],
                ),
                RequestDetail(
                    request_type="prescription-request",
                    details={},
                    missing_fields=[],
                    source_quotes=[],
                ),
            ],
        )
        assert len(result.requests) == 2
        assert result.requests[0].request_type == "appointment-request"
        assert result.requests[1].request_type == "prescription-request"

    def test_source_quotes_validation(self) -> None:
        with pytest.raises(ValueError):
            InterpretationResult(
                intent="unclear",
                requests=[
                    RequestDetail(
                        request_type="unclear",
                        source_quotes=["", "  "],  # empty quotes not allowed
                    )
                ],
            )


class TestRequestDetail:
    def test_missing_fields_default(self) -> None:
        detail = RequestDetail(request_type="appointment-request")
        assert detail.missing_fields == []
        assert detail.source_quotes == []
        assert detail.details == {}
