"""Gemini LLM provider adapter (Day 5).

Calls the Gemini `generateContent` REST endpoint directly via `httpx` (already a
project dependency) so no additional vendor SDK is required. The provider:

* Reads `LLM_API_KEY` from settings — fails clearly when missing.
* Applies explicit request and total timeouts.
* Requests JSON output and validates the parsed result locally.
* Never retries automatically (the controller decides retry policy).
* Never silently falls back to mock.

If credentials are unavailable the adapter is constructed but raises
`LLMConfigurationError` on first use — tested with mocked transport, never a
live call.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from app.domain.llm import InterpretationResult
from app.integrations.llm.base import (
    LLMConnectionError,
    LLMRequest,
    LLMResponse,
    LLMSynthesisError,
)
from app.integrations.llm.exceptions import LLMTimeoutError

# Gemini `generateContent` endpoint (v1beta). Key is passed as a query param per
# the Gemini REST API spec; the value is never logged.
_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Built-in intent-extraction JSON schema (used when no custom schema is supplied).
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
                    "details": {"type": "object"},
                    "missing_fields": {
                        "type": "array",
                        "items": {"type": "string"},
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
        "clarification_reason": {"type": "string"},
    },
    "required": ["intent"],
}


class GeminiLLMProvider:
    """Real Gemini provider backed by httpx + the generateContent REST API."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float,
        max_output_tokens: int,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key or not api_key.strip():
            raise ValueError("Gemini provider requires a non-empty API key")
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._max_output_tokens = max_output_tokens
        self._client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 5.0))
        )
        self._owns_client = http_client is None

    @property
    def provider_name(self) -> str:
        return f"gemini:{self._model}"

    async def interpret(self, request: LLMRequest) -> LLMResponse:
        url = f"{_GEMINI_BASE.format(model=request.model)}?key={self._api_key}"
        payload = self._build_payload(request)

        t0 = time.monotonic()
        try:
            try:
                response = await asyncio.wait_for(
                    self._client.post(url, json=payload), timeout=request.timeout_seconds
                )
            except TimeoutError as exc:
                raise LLMTimeoutError(
                    f"gemini request timed out after {request.timeout_seconds}s"
                ) from exc
            except httpx.HTTPError as exc:
                raise LLMConnectionError(f"gemini request failed: {exc}") from exc
        except asyncio.CancelledError:
            raise

        total_ms = (time.monotonic() - t0) * 1000.0

        if response.status_code >= 400:
            raise LLMConnectionError(
                f"gemini returned HTTP {response.status_code}: {response.text[:200]}"
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise LLMSynthesisError("gemini returned non-JSON response") from exc

        raw_text = self._extract_text(body)
        schema = request.response_schema or InterpretationResult
        parsed: object | None = None
        first_token_ms: float | None = None

        if raw_text:
            try:
                parsed = schema.model_validate_json(raw_text)
                first_token_ms = total_ms  # non-streaming: first token == total
            except ValueError as exc:
                # Intent extraction (no custom schema): validation failure is fatal.
                # Grounded answers (custom schema): return parsed=None, caller parses raw_text.
                if request.response_schema is None:
                    raise LLMSynthesisError(f"gemini output failed validation: {exc}") from exc

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

        # Use the caller-supplied response schema for the API's responseSchema,
        # falling back to the built-in intent-extraction schema.
        if request.response_schema is not None:
            response_schema = request.response_schema.model_json_schema()
        else:
            response_schema = _INTENT_EXTRACTION_SCHEMA

        return {
            "system_instruction": {"parts": [{"text": system_instruction}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": request.transcript}],
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": request.max_output_tokens,
                "responseMimeType": "application/json",
                "responseSchema": response_schema,
            },
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
            f"(prompt version: {prompt_version})"
        )

    @staticmethod
    def _extract_text(body: dict[str, Any]) -> str:
        """Extract the generated text from a Gemini generateContent response."""
        candidates = body.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            return ""
        first = candidates[0]
        if not isinstance(first, dict):
            return ""
        content = first.get("content")
        if not isinstance(content, dict):
            return ""
        parts = content.get("parts")
        if not isinstance(parts, list):
            return ""
        texts: list[str] = []
        for part in parts:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                texts.append(part["text"])
        return "".join(texts)


__all__ = ["GeminiLLMProvider"]
