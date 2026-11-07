"""Narrow error types for the LLM integration (importable without adapters).

All LLM exceptions live here so that `base.py` (the protocol) and adapters can
import them without creating circular imports between the protocol module and
provider-specific modules.
"""

from __future__ import annotations


class LLMIntegrationError(Exception):
    """Base class for LLM integration failures."""


class LLMConfigurationError(LLMIntegrationError):
    """The configured provider cannot run with the supplied configuration."""


class LLMConnectionError(LLMIntegrationError):
    """Connecting to the provider failed (or exceeded its timeout)."""


class LLMTimeoutError(LLMIntegrationError):
    """An LLM call exceeded its configured timeout."""


class LLMValidationError(LLMIntegrationError):
    """Provider output failed structured-output validation."""


class LLMSynthesisError(LLMIntegrationError):
    """The provider returned malformed or unusable output."""


class LLMProviderNotAvailable(LLMIntegrationError):
    """The selected provider cannot be constructed or used."""


# Backward-compatible alias: LLMError == LLMIntegrationError
LLMError = LLMIntegrationError


__all__ = [
    "LLMIntegrationError",
    "LLMError",
    "LLMConfigurationError",
    "LLMConnectionError",
    "LLMTimeoutError",
    "LLMValidationError",
    "LLMSynthesisError",
    "LLMProviderNotAvailable",
]
