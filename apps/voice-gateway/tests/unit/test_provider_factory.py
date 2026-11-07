"""Unit tests for the LLM provider factory (Day 5)."""

from __future__ import annotations

import pytest

from app.integrations.llm.exceptions import LLMProviderNotAvailable
from app.integrations.llm.mock_provider import MockLLMProvider
from app.integrations.llm.provider_factory import LLMProviderFactory


class TestProviderFactory:
    def test_mock_provider_selection(self) -> None:
        factory = self._make_settings_mock("mock")
        provider = factory.create()
        assert isinstance(provider, MockLLMProvider)
        assert provider.provider_name == "mock-llm"

    def test_configuration_ok_mock(self) -> None:
        factory = self._make_settings_mock("mock")
        assert factory.configuration_ok is True
        assert factory.configuration_problem() is None

    def test_gemini_without_key_fails(self) -> None:
        factory = self._make_settings_mock("gemini")
        # Override key to None to simulate missing credentials
        factory._settings.LLM_API_KEY = None  # type: ignore[assignment]
        assert factory.configuration_ok is False
        assert "LLM_API_KEY" in factory.configuration_problem()
        with pytest.raises(LLMProviderNotAvailable):
            factory.create()

    def test_unknown_provider_fails(self) -> None:
        factory = self._make_settings_mock("bogus")
        with pytest.raises(LLMProviderNotAvailable):
            factory.create()

    # --- helper -----------------------------------------------------------

    @staticmethod
    def _make_settings_mock(provider: str) -> LLMProviderFactory:
        """Build a factory with a minimal settings-like object."""

        class Settings:
            LLM_PROVIDER = provider
            LLM_API_KEY = "test-key" if provider == "gemini" else None
            LLM_MODEL = "gemini-1.5-flash"
            LLM_TIMEOUT_SECONDS = 15.0
            LLM_MAX_OUTPUT_TOKENS = 800
            LLM_PROMPT_VERSION = "v1"

        return LLMProviderFactory(Settings())
