"""Narrow error types for the vector store integration (importable without
adapters)."""

from __future__ import annotations


class VectorStoreIntegrationError(Exception):
    """Base class for vector store integration failures."""


class VectorStoreConfigurationError(VectorStoreIntegrationError):
    """The configured provider cannot run with the supplied configuration."""


class VectorStoreConnectionError(VectorStoreIntegrationError):
    """Connecting to the provider failed (or exceeded its timeout)."""


class VectorStoreProviderNotAvailable(VectorStoreIntegrationError):
    """The selected provider cannot be constructed or used."""


# Backward-compatible alias: VectorStoreError == VectorStoreIntegrationError
VectorStoreError = VectorStoreIntegrationError


__all__ = [
    "VectorStoreIntegrationError",
    "VectorStoreError",
    "VectorStoreConfigurationError",
    "VectorStoreConnectionError",
    "VectorStoreProviderNotAvailable",
]
