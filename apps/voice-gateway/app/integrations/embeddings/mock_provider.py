"""Deterministic mock embedding provider (Day 6).

Scenario-based, zero network, zero randomness. Same input text always produces
the same vector. Used for local development and the regression suite — never a
substitute for real-provider testing.

Determinism rules:
* Same text → same vector every time.
* Vector dimension matches the requested dimension.
* Vectors are unit-normalized (cosine similarity == dot product).
* Timeout/failure scenarios are opt-in via constructor flags.
* Mock embeddings are marked as test-only — never mixed with real Gemini
  vectors in the same production collection.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import time

from app.integrations.embeddings.base import (
    EmbeddingRequest,
    EmbeddingResponse,
)
from app.integrations.embeddings.exceptions import EmbeddingConnectionError, EmbeddingTimeoutError


class MockEmbeddingProvider:
    """Deterministic mock embedding provider implementing EmbeddingProvider.

    Vectors are derived from a stable hash of the input text plus the model
    name and dimension, so identical inputs always produce identical vectors.
    """

    def __init__(
        self,
        *,
        dimension: int = 768,
        simulate_timeout: bool = False,
        simulate_failure: bool = False,
    ) -> None:
        self.dimension = dimension
        self.simulate_timeout = simulate_timeout
        self.simulate_failure = simulate_failure
        self.call_count = 0
        self.last_request: EmbeddingRequest | None = None

    @property
    def provider_name(self) -> str:
        return f"mock-embedding:{self.dimension}"

    @property
    def model_name(self) -> str:
        return "mock-embedding"

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        self.call_count += 1
        self.last_request = request

        if self.simulate_timeout:
            await asyncio.sleep(request.dimension / 1000.0 + 0.1)
            raise EmbeddingTimeoutError(
                f"mock embedding provider timed out after {request.dimension}s"
            )

        if self.simulate_failure:
            raise EmbeddingConnectionError("mock embedding provider connection failed")

        vector = self._deterministic_vector(request.text, request.model, request.dimension)

        t0 = time.monotonic()
        # Mock is instantaneous: latency is ~0.
        total_ms = (time.monotonic() - t0) * 1000.0

        return EmbeddingResponse(
            vector=vector,
            model=request.model,
            dimension=request.dimension,
            latency_ms=total_ms,
        )

    # --- deterministic vector generation -----------------------------------

    @staticmethod
    def _deterministic_vector(text: str, model: str, dimension: int) -> list[float]:
        """Generate a deterministic unit-normalized vector from text.

        Uses SHA-256 of (text + model) seeded into a deterministic PRNG.
        Vectors are unit-normalized so cosine similarity == dot product.
        """
        seed_material = f"{model}:{text}".encode()
        seed = int.from_bytes(hashlib.sha256(seed_material).digest()[:8], "big")
        rng = __import__("random").Random(seed)

        raw = [rng.gauss(0, 1) for _ in range(dimension)]
        norm = math.sqrt(sum(v * v for v in raw)) or 1.0
        return [v / norm for v in raw]

    async def aclose(self) -> None:
        """No-op close for mock provider (no resources to release)."""
        pass


__all__ = ["MockEmbeddingProvider"]
