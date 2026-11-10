"""Central LLM provider factory (Day 5).

Selects a provider from validated settings. Real providers without credentials
fail clearly — there is NEVER a silent mock fallback.

Day 12: adds OpenAI `gpt-6-luna` support with application-level budget guard.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.integrations.llm.base import LLMProvider
from app.integrations.llm.exceptions import LLMProviderNotAvailable
from app.integrations.llm.gemini_provider import GeminiLLMProvider
from app.integrations.llm.mock_provider import MockLLMProvider
from app.integrations.llm.openai_provider import OpenAILLMProvider
from app.services.openai_budget import OpenAIBudgetGuard

if TYPE_CHECKING:  # pragma: no cover
    from app.core.config import Settings


class LLMProviderFactory:
    """Build one LLM provider instance from validated settings."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._budget_guard: OpenAIBudgetGuard | None = None
        if settings.LLM_PROVIDER == "openai":
            self._budget_guard = OpenAIBudgetGuard(
                daily_budget_usd=settings.OPENAI_DAILY_BUDGET_USD,
                dev_budget_usd=settings.OPENAI_DEV_BUDGET_USD,
                input_usd_per_1k_tokens=settings.OPENAI_INPUT_USD_PER_1K_TOKENS,
                output_usd_per_1k_tokens=settings.OPENAI_OUTPUT_USD_PER_1K_TOKENS,
                cached_usd_per_1k_tokens=settings.OPENAI_CACHED_USD_PER_1K_TOKENS,
            )

    @property
    def provider_name(self) -> str:
        return str(self._settings.LLM_PROVIDER)

    @property
    def budget_guard(self) -> OpenAIBudgetGuard | None:
        """Application-level OpenAI budget guard (None when OpenAI not selected)."""
        return self._budget_guard

    @property
    def configuration_ok(self) -> bool:
        if self._settings.LLM_PROVIDER == "gemini":
            return bool(self._settings.LLM_API_KEY)
        if self._settings.LLM_PROVIDER == "openai":
            return bool(self._settings.OPENAI_API_KEY)
        return True

    def configuration_problem(self) -> str | None:
        if self._settings.LLM_PROVIDER == "gemini" and not self._settings.LLM_API_KEY:
            return (
                "LLM_PROVIDER=gemini but LLM_API_KEY is not configured "
                "(no credentials → degraded, never mock fallback)"
            )
        if self._settings.LLM_PROVIDER == "openai" and not self._settings.OPENAI_API_KEY:
            return (
                "LLM_PROVIDER=openai but OPENAI_API_KEY is not configured "
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
        if settings.LLM_PROVIDER in ("openai", "primary_openai"):
            if not settings.OPENAI_API_KEY:
                raise LLMProviderNotAvailable(
                    "LLM_PROVIDER=openai requires OPENAI_API_KEY; "
                    "refusing to start (no silent fallback)"
                )
            gemini_secondary = None
            if settings.LLM_API_KEY:
                gemini_secondary = GeminiLLMProvider(
                    api_key=settings.LLM_API_KEY,
                    model=settings.LLM_MODEL,
                    timeout_seconds=settings.LLM_TIMEOUT_SECONDS,
                    max_output_tokens=settings.LLM_MAX_OUTPUT_TOKENS,
                )
            return OpenAILLMProvider(
                api_key=settings.OPENAI_API_KEY,
                model=settings.OPENAI_MODEL,
                timeout_seconds=settings.LLM_TIMEOUT_SECONDS,
                max_output_tokens=settings.LLM_MAX_OUTPUT_TOKENS,
                budget_guard=self._budget_guard,
                fallback_provider=gemini_secondary,
            )
        raise LLMProviderNotAvailable(f"unknown LLM_PROVIDER {settings.LLM_PROVIDER!r}")


__all__ = ["LLMProviderFactory", "LLMProviderNotAvailable"]
