"""LLM provider integration package (Day 5).

Provider-independent interpretation interface with mock and Gemini adapters.
The voice pipeline imports only the protocol — never a vendor SDK directly.
"""

from app.integrations.llm.base import (
    LLMConfigurationError,
    LLMConnectionError,
    LLMError,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMSynthesisError,
)
from app.integrations.llm.exceptions import (
    LLMIntegrationError,
    LLMProviderNotAvailable,
    LLMTimeoutError,
    LLMValidationError,
)
from app.integrations.llm.gemini_provider import GeminiLLMProvider
from app.integrations.llm.mock_provider import MockLLMProvider
from app.integrations.llm.provider_factory import LLMProviderFactory

__all__ = [
    "LLMError",
    "LLMConfigurationError",
    "LLMConnectionError",
    "LLMSynthesisError",
    "LLMIntegrationError",
    "LLMProviderNotAvailable",
    "LLMTimeoutError",
    "LLMValidationError",
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "GeminiLLMProvider",
    "MockLLMProvider",
    "LLMProviderFactory",
]
