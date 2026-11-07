"""Provider-independent LLM interpretation contract (Day 5).

Defines the protocol and shared types that every LLM adapter (mock, Gemini,
Azure OpenAI, Groq …) must implement. The voice pipeline never imports a
vendor SDK directly — it only calls into this contract.

Design rules:

* One `interpret()` call per final transcript — never partials, never audio.
* Structured input → structured output; the response is validated against a
  Pydantic schema before the controller sees it.
* Latency is measured and recorded per call (first-token + total).
* Provider failures are surfaced as narrow exceptions; the controller decides
  recovery vs degradation. No silent fallback to mock when a real provider was
  selected.
* Credentials are read from settings only — never hardcoded, never logged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app.integrations.llm.exceptions import (
    LLMConfigurationError,
    LLMConnectionError,
    LLMError,
    LLMSynthesisError,
)


@dataclass(frozen=True)
class LLMRequest:
    """Structured input to one interpretation call."""

    transcript: str  # one final utterance — never partials, never raw audio
    session_id: str
    prompt_version: str
    model: str
    timeout_seconds: float
    max_output_tokens: int
    correlation_id: str
    # Optional Pydantic schema for response validation. When None (default),
    # the provider validates against InterpretationResult. When provided, the
    # provider validates against this schema instead — validation failure
    # returns parsed=None rather than raising, letting the caller handle it.
    response_schema: type[Any] | None = None


@dataclass(frozen=True)
class LLMResponse:
    """Normalized output from one interpretation call.

    `raw_text` is the provider's verbatim response; `parsed` is the Pydantic-
    validated interpretation (None when validation failed). Latency fields are
    wall-clock deltas in milliseconds.
    """

    raw_text: str
    parsed: object | None
    first_token_ms: float | None
    total_ms: float
    correlation_id: str


@runtime_checkable
class LLMProvider(Protocol):
    """LLM interpretation provider contract."""

    @property
    def provider_name(self) -> str: ...

    async def interpret(self, request: LLMRequest) -> LLMResponse:
        """Interpret one final transcript into structured administrative data.

        Raises `LLMError` subclasses on failure — never returns a degraded
        guess when the provider selected was real.
        """
        ...


__all__ = [
    "LLMRequest",
    "LLMResponse",
    "LLMError",
    "LLMConfigurationError",
    "LLMConnectionError",
    "LLMSynthesisError",
    "LLMProvider",
]
