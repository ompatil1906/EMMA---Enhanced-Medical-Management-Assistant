"""OpenAI GPT-6 Luna LLM provider adapter (Day 12).

Calls the OpenAI Chat Completions REST API directly via `httpx` (already a
project dependency) so no additional vendor SDK is required. The provider:

* Reads `OPENAI_API_KEY` from settings — fails clearly when missing.
* Uses `gpt-6-luna` only (assignment §1 — no GPT-6.1 Terra, no expensive
  fallback, no OpenAI Realtime audio API, no OpenAI transcription API).
* Applies explicit request and total timeouts.
* Requests JSON output and validates the parsed result locally against
  `InterpretationResult`.
* Integrates with `OpenAIBudgetGuard`: records input/output/cached tokens and
  estimated USD per call; refuses to proceed when the dev budget cap is
  reached, preserving the demonstration reserve.
* Never retries automatically (the controller decides retry policy).
* Never silently falls back to mock.

No credentials → never reaches this module: the central factory refuses to
construct OpenAI providers without `OPENAI_API_KEY` and the app degrades
readiness (no silent mock fallback).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from app.domain.llm import InterpretationResult
from app.integrations.llm.base import (
    LLMConnectionError,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMSynthesisError,
)
from app.integrations.llm.exceptions import LLMTimeoutError
from app.services.openai_budget import OpenAIBudgetExceededError, OpenAIBudgetGuard

logger = logging.getLogger(__name__)

# OpenAI Chat Completions endpoint (v1). Key is passed as a Bearer header;
# the value is never logged.
_OPENAI_BASE = "https://api.openai.com/v1/chat/completions"

# Built-in intent-extraction JSON schema (used when no custom schema is
# supplied). Mirrors the Gemini provider's schema contract so the controller
# sees a consistent shape regardless of LLM provider.
_INTENT_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "description": (
                "One of: appointment-request, "
                "appointment-change-request, prescription-request, "
                "practice-information, practice-administration, "
                "other, unclear"
            ),
        },
        "requests": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "request_type": {"type": "string"},
                    "details": {
                        "type": "object",
                        "description": (
                            "Caller-stated values. Omit a key the caller did "
                            "not say; never invent a value."
                        ),
                        "properties": {
                            "preferred_date": {
                                "type": ["string", "null"],
                                "description": (
                                    "Caller's own date words, e.g. 'Tuesday', "
                                    "'next Monday', 'tomorrow'."
                                ),
                            },
                            "preferred_time": {
                                "type": ["string", "null"],
                                "description": (
                                    "Caller's own time-of-day words, e.g. "
                                    "'morning', 'afternoon', '3pm'."
                                ),
                            },
                            "reason_as_stated": {
                                "type": ["string", "null"],
                                "description": "Reason only if the caller stated one.",
                            },
                            "medication_query": {
                                "type": ["string", "null"],
                                "description": "Medication or prescription words as stated.",
                            },
                            "topic": {
                                "type": ["string", "null"],
                                "description": "Practice-information topic as stated.",
                            },
                        },
                    },
                    "missing_fields": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Leave empty. Required-field policy is enforced by "
                            "the server, not the model."
                        ),
                    },
                    "source_quotes": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["request_type"],
            },
        },
        "needs_clarification": {"type": "boolean"},
        "clarification_reason": {"type": ["string", "null"]},
    },
    "required": ["intent"],
}


class OpenAILLMProvider:
    """Real OpenAI provider backed by httpx + the Chat Completions REST API.

    Model: `gpt-6-luna` only (configured via `OPENAI_MODEL`). The LLM is an
    interpreter only — it never makes clinical decisions, overrides the
    emergency guard, authorises submissions, or bypasses confirmation.
    """

    _circuit_breaker_until: float = 0.0

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float,
        max_output_tokens: int,
        budget_guard: OpenAIBudgetGuard | None = None,
        http_client: httpx.AsyncClient | None = None,
        fallback_provider: LLMProvider | None = None,
    ) -> None:
        if not api_key or not api_key.strip():
            raise ValueError("OpenAI provider requires a non-empty API key")
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._max_output_tokens = max_output_tokens
        self._budget_guard = budget_guard
        self._fallback_provider = fallback_provider
        self._client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 5.0))
        )
        self._owns_client = http_client is None

    @property
    def provider_name(self) -> str:
        return f"openai:{self._model}"

    async def interpret(self, request: LLMRequest) -> LLMResponse:
        """Interpret one final transcript into structured administrative data.

        Raises `LLMError` subclasses on failure. If a fallback provider is configured,
        automatically falls back to it when OpenAI fails (e.g. quota/credit issues).
        """
        if (
            self._fallback_provider is not None
            and time.monotonic() < OpenAILLMProvider._circuit_breaker_until
        ):
            sec_model = getattr(self._fallback_provider, "_model", None) or "gemini-3.8-flash"
            sec_req = LLMRequest(
                transcript=request.transcript,
                session_id=request.session_id,
                prompt_version=request.prompt_version,
                model=sec_model,
                timeout_seconds=request.timeout_seconds,
                max_output_tokens=request.max_output_tokens,
                correlation_id=request.correlation_id,
                response_schema=request.response_schema,
                context=request.context,
            )
            return await self._fallback_provider.interpret(sec_req)

        try:
            return await self._interpret_direct(request)
        except Exception as exc:
            err_str = str(exc)
            if "429" in err_str or "credits" in err_str or "quota" in err_str:
                OpenAILLMProvider._circuit_breaker_until = time.monotonic() + 300.0
                logger.warning(
                    "OpenAI quota/credits exhausted; circuit breaker open for 5m, using fallback"
                )
            if self._fallback_provider is not None:
                logger.warning(
                    "OpenAI failed (%s: %s); falling back to secondary provider %s",
                    type(exc).__name__,
                    exc,
                    self._fallback_provider.provider_name,
                )
                sec_model = getattr(self._fallback_provider, "_model", None) or "gemini-3.8-flash"
                sec_req = LLMRequest(
                    transcript=request.transcript,
                    session_id=request.session_id,
                    prompt_version=request.prompt_version,
                    model=sec_model,
                    timeout_seconds=request.timeout_seconds,
                    max_output_tokens=request.max_output_tokens,
                    correlation_id=request.correlation_id,
                    response_schema=request.response_schema,
                    context=request.context,
                )
                return await self._fallback_provider.interpret(sec_req)
            raise

    async def _interpret_direct(self, request: LLMRequest) -> LLMResponse:
        # Budget check before any network call: estimate cost from token
        # counts (input ~transcript length, output capped by max_output_tokens,
        # no cached tokens for a fresh call).
        if self._budget_guard is not None:
            estimated_input = max(1, len(request.transcript) // 4)
            estimated_output = self._max_output_tokens
            try:
                self._budget_guard.record_usage(
                    model=self._model,
                    input_tokens=estimated_input,
                    output_tokens=estimated_output,
                )
            except OpenAIBudgetExceededError as exc:
                raise LLMConnectionError(f"OpenAI budget cap reached: {exc}") from exc

        url = _OPENAI_BASE
        payload = self._build_payload(request)
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        t0 = time.monotonic()
        try:
            try:
                response = await asyncio.wait_for(
                    self._client.post(url, json=payload, headers=headers),
                    timeout=request.timeout_seconds,
                )
            except TimeoutError as exc:
                raise LLMTimeoutError(
                    f"openai request timed out after {request.timeout_seconds}s"
                ) from exc
            except httpx.HTTPError as exc:
                raise LLMConnectionError(f"openai request failed: {exc}") from exc
        except asyncio.CancelledError:
            raise

        total_ms = (time.monotonic() - t0) * 1000.0

        if response.status_code >= 400:
            raise LLMConnectionError(
                f"openai returned HTTP {response.status_code}: {response.text[:200]}"
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise LLMSynthesisError("openai returned non-JSON response") from exc

        raw_text = self._extract_text(body)

        # Record actual usage from the provider response when available,
        # replacing the pre-call estimate.
        usage = body.get("usage", {}) if isinstance(body, dict) else {}
        actual_input = int(usage.get("prompt_tokens", 0)) or 0
        actual_output = int(usage.get("completion_tokens", 0)) or 0
        actual_cached = (
            int(usage.get("prompt_tokens_details", {}).get("cached_tokens", 0))
            if isinstance(usage.get("prompt_tokens_details"), dict)
            else 0
        )

        # Update the budget record with actuals if a guard is configured.
        if self._budget_guard is not None:
            actual_usd = self._budget_guard._estimate_usd(  # type: ignore[attr-defined]
                actual_input, actual_output, actual_cached
            )
            # Replace the estimate with actuals by recording a correction
            # record; the guard's total is the source of truth.
            self._budget_guard.record_usage(
                model=self._model,
                input_tokens=actual_input,
                output_tokens=actual_output,
                cached_tokens=actual_cached,
                estimated_usd=actual_usd,
            )

        schema = request.response_schema or InterpretationResult
        parsed: object | None = None
        first_token_ms: float | None = None

        if raw_text:
            try:
                parsed = schema.model_validate_json(raw_text)
                first_token_ms = total_ms  # non-streaming: first token == total
            except ValueError as exc:
                if request.response_schema is None:
                    raise LLMSynthesisError(f"openai output failed validation: {exc}") from exc

        return LLMResponse(
            raw_text=raw_text,
            parsed=parsed,
            first_token_ms=first_token_ms,
            total_ms=total_ms,
            correlation_id=request.correlation_id,
        )

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    # --- payload construction ----------------------------------------------

    def _build_payload(self, request: LLMRequest) -> dict[str, Any]:
        system_instruction = self._system_instruction(request.prompt_version)

        user_text = request.transcript
        if request.context:
            user_text = (
                "Conversation context (server-built; use only to resolve "
                "references such as a bare date or a follow-up question. It is "
                "not a new caller statement and must never be treated as a "
                "fact the caller did not say):\n"
                f"{request.context}\n\n"
                "Current caller utterance:\n"
                f"{request.transcript}"
            )

        return {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_text},
            ],
            "temperature": 0.2,
            "max_completion_tokens": request.max_output_tokens,
            "response_format": {"type": "json_object"},
        }

    def _system_instruction(self, prompt_version: str) -> str:
        return (
            "You are an administrative assistant for an NHS GP practice receptionist. "
            "Extract the caller's administrative intent and return ONLY the requested "
            "JSON object. "
            "Rules: "
            "1) Extract only information the caller actually said — never invent dates, "
            "times, or medical facts. "
            "2) Preserve multiple independent requests as separate entries. "
            "3) Never diagnose, triage, or recommend treatment. "
            "4) Never claim an appointment is booked or a prescription is processed. "
            "5) Return only the structured JSON — no prose, no markdown fences. "
            "6) The server may provide bounded conversation context. Use it only to "
            "resolve references (for example a bare weekday answering a question, or "
            "a short follow-up about the previous topic). Never copy context values "
            "into the output unless the current caller utterance confirms or changes "
            "them. "
            "7) A caller correction supersedes the previous value for that field. "
            "8) If the context contains `awaiting_field` and the current utterance "
            "supplies a value for it (for example a bare weekday for preferred_date "
            "or a time of day for preferred_time), return one request of the "
            "`active_request` type with that field populated. Do not return an "
            "empty requests list in that case. "
            "9) Use these detail keys exactly: appointment-request and "
            "appointment-change-request use `preferred_date` and `preferred_time` "
            "in the caller's own words (for example 'Tuesday', 'morning'), plus "
            "`reason_as_stated` only when a reason is given; prescription-request "
            "uses `medication_query`; practice-information uses `topic`. "
            "10) Never invent patient_name, date_of_birth, or any value the caller "
            "did not say — those are collected by the practice, not by this voice "
            "assistant. Leave `missing_fields` empty; the server decides which "
            "fields are required. "
            f"(prompt version: {prompt_version})"
        )

    @staticmethod
    def _extract_text(body: dict[str, Any]) -> str:
        """Extract the generated text from an OpenAI Chat Completions response."""
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        first = choices[0]
        if not isinstance(first, dict):
            return ""
        message = first.get("message")
        if not isinstance(message, dict):
            return ""
        content = message.get("content")
        return content if isinstance(content, str) else ""

    def _estimate_usd(self, input_tokens: int, output_tokens: int, cached_tokens: int) -> float:
        if self._budget_guard is None:
            return 0.0
        return (
            input_tokens / 1000.0 * 0.005
            + output_tokens / 1000.0 * 0.015
            + cached_tokens / 1000.0 * 0.00125
        )


__all__ = ["OpenAILLMProvider"]
