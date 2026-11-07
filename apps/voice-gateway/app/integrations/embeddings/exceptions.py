"""Narrow error types for the embedding integration (importable without
adapters)."""

from __future__ import annotations


class EmbeddingIntegrationError(Exception):
    """Base class for embedding integration failures."""


class EmbeddingConfigurationError(EmbeddingIntegrationError):
    """The configured provider cannot run with the supplied configuration."""


class EmbeddingConnectionError(EmbeddingIntegrationError):
    """Connecting to the provider failed (or exceeded its timeout)."""


class EmbeddingTimeoutError(EmbeddingIntegrationError):
    """An embedding call exceeded its configured timeout."""


class EmbeddingProviderNotAvailable(EmbeddingIntegrationError):
    """The selected provider cannot be constructed or used."""


# Backward-compatible alias: EmbeddingError == EmbeddingIntegrationError
EmbeddingError = EmbeddingIntegrationError


__all__ = [
    "EmbeddingIntegrationError",
    "EmbeddingError",
    "EmbeddingConfigurationError",
    "EmbeddingConnectionError",
    "EmbeddingTimeoutError",
    "EmbeddingProviderNotAvailable",
]
