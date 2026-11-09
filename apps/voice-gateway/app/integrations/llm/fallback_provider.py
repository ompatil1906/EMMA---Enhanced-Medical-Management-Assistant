"""Chained primary/secondary fallback LLM provider.

Attempts the primary provider (e.g. OpenAI) and automatically falls back to
the secondary provider (e.g. Gemini) if the primary fails with quota limits,
timeouts, or vendor connection errors.
"""

from __future__ import annotations

import logging
from app.integrations.llm.base import LLMError, LLMProvider, LLMRequest, LLMResponse

logger = logging.getLogger(__name__)


class FallbackLLMProvider:
    """Wraps a primary and secondary LLM provider with graceful failover."""

    def __init__(self, primary: LLMProvider, secondary: LLMProvider) -> None:
        self._primary = primary
        self._secondary = secondary

    @property
    def provider_name(self) -> str:
        return f"{self._primary.provider_name} (fallback: {self._secondary.provider_name})"

    async def interpret(self, request: LLMRequest) -> LLMResponse:
        """Attempt primary provider first; seamlessly fall back to secondary on failure."""
        try:
            return await self._primary.interpret(request)
        except Exception as exc:
            logger.warning(
                "Primary LLM %s failed (%s: %s); falling back to secondary %s",
                self._primary.provider_name,
                type(exc).__name__,
                exc,
                self._secondary.provider_name,
            )
            try:
                # If the request had a primary-specific model, update model for secondary
                sec_model = getattr(self._secondary, "_model", None) or "gemini-3.8-flash"
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
                return await self._secondary.interpret(sec_req)
            except Exception as sec_exc:
                logger.error(
                    "Secondary LLM %s also failed (%s: %s)",
                    self._secondary.provider_name,
                    type(sec_exc).__name__,
                    sec_exc,
                )
                raise sec_exc from exc

    async def aclose(self) -> None:
        if hasattr(self._primary, "aclose"):
            await self._primary.aclose()
        if hasattr(self._secondary, "aclose"):
            await self._secondary.aclose()

