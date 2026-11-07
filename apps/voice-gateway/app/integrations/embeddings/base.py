"""Provider-independent embedding contract (Day 6).

Defines the protocol and shared types that every embedding adapter (mock,
Gemini, …) must implement. The knowledge-base and retrieval services import
only the protocol — never a vendor SDK directly.

Design rules:

* One `embed()` call per text chunk — never batches across documents.
* Structured input → structured output (typed vector + model metadata).
* Latency is measured and recorded per call.
* Provider failures are surfaced as narrow exceptions; the caller decides
  recovery vs degradation. No silent fallback to mock when a real provider was
  selected.
* Credentials are read from settings only — never hardcoded, never logged.
* All vectors returned by one provider instance share the same model and
  dimension; mixing vectors from different models/dimensions in one collection
  is forbidden.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.integrations.embeddings.exceptions import (
    EmbeddingConfigurationError,
    EmbeddingConnectionError,
    EmbeddingError,
)


@dataclass(frozen=True)
class EmbeddingRequest:
    """Structured input to one embedding call."""

    text: str  # one document chunk or query — never raw audio
    model: str
    dimension: int  # expected output dimension; validated against provider output


@dataclass(frozen=True)
class EmbeddingResponse:
    """Normalized output from one embedding call."""

    vector: list[float]  # embedding vector; len(vector) == dimension
    model: str
    dimension: int
    latency_ms: float  # wall-clock delta in milliseconds


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Embedding provider contract."""

    @property
    def provider_name(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """Embed one text chunk into a vector.

        Raises `EmbeddingError` subclasses on failure — never returns a
        degraded guess when the provider selected was real.
        """
        ...


__all__ = [
    "EmbeddingRequest",
    "EmbeddingResponse",
    "EmbeddingError",
    "EmbeddingConfigurationError",
    "EmbeddingConnectionError",
    "EmbeddingProvider",
]
