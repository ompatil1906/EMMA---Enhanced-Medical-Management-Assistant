"""Central LLM provider factory (Day 5).

Selects a provider from validated settings. Real providers without credentials
fail clearly — there is NEVER a silent mock fallback.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.integrations.llm.base import LLMProvider
from app.integrations.llm.exceptions import LLMProviderNotAvailable
from app.integrations.llm.gemini_provider import GeminiLLMProvider
from app.integrations.llm.mock_provider import MockLLMProvider

if TYPE_CHECKING:  # pragma: no cover
    from app.core.config import Settings


class LLMProviderFactory:
    """Build one LLM provider instance from validated settings."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def provider_name(self) -> str:
        return str(self._settings.LLM_PROVIDER)

    @property
    def configuration_ok(self) -> bool:
        if self._settings.LLM_PROVIDER == "gemini":
            return bool(self._settings.LLM_API_KEY)
        return True

    def configuration_problem(self) -> str | None:
        if self._settings.LLM_PROVIDER == "gemini" and not self._settings.LLM_API_KEY:
            return (
                "LLM_PROVIDER=gemini but LLM_API_KEY is not configured "
                "(no credentials → degraded, never mock fallback)"
            )
        return None

    def create(self) -> LLMProvider:
        """Build the provider for the configured LLM_PROVIDER (fails fast)."""
        settings = self._settings
        if settings.LLM_PROVIDER == "mock":
            return MockLLMProvider()
        if settings.LLM_PROVIDER == "gemini":
            if not settings.LLM_API_KEY:
                raise LLMProviderNotAvailable(
                    "LLM_PROVIDER=gemini requires LLM_API_KEY; "
                    "refusing to start (no silent fallback)"
                )
            return GeminiLLMProvider(
                api_key=settings.LLM_API_KEY,
                model=settings.LLM_MODEL,
                timeout_seconds=settings.LLM_TIMEOUT_SECONDS,
                max_output_tokens=settings.LLM_MAX_OUTPUT_TOKENS,
            )
        raise LLMProviderNotAvailable(f"unknown LLM_PROVIDER {settings.LLM_PROVIDER!r}")


__all__ = ["LLMProviderFactory", "LLMProviderNotAvailable"]
