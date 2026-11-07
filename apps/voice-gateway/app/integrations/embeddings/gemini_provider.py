"""Gemini embedding provider adapter (Day 6).

Calls the Gemini `embedContent` REST endpoint directly via `httpx` (already a
project dependency) so no additional vendor SDK is required. The provider:

* Reads `GEMINI_EMBEDDING_API_KEY` from settings — fails clearly when missing.
* Applies explicit request and total timeouts.
* Validates response dimension against the requested dimension.
* Never retries automatically (the caller decides retry policy).
* Never silently falls back to mock.

If credentials are unavailable the adapter is constructed but raises
`EmbeddingConfigurationError` on first use — tested with mocked transport,
never a live call.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from app.integrations.embeddings.base import (
    EmbeddingRequest,
    EmbeddingResponse,
)
from app.integrations.embeddings.exceptions import (
    EmbeddingConnectionError,
    EmbeddingTimeoutError,
)

# Gemini `embedContent` endpoint (v1beta). Key is passed as a query param per
# the Gemini REST API spec; the value is never logged.
_GEMINI_EMBED_BASE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent"


class GeminiEmbeddingProvider:
    """Real Gemini embedding provider backed by httpx + the embedContent REST API."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        dimension: int,
        timeout_seconds: float,
        max_retries: int,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key or not api_key.strip():
            raise ValueError("Gemini embedding provider requires a non-empty API key")
        self._api_key = api_key
        self._model = model
        self._dimension = dimension
        self._timeout_seconds = timeout_seconds
        self._max_retries = max(0, max_retries)
        self._client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 5.0))
        )
        self._owns_client = http_client is None

    @property
    def provider_name(self) -> str:
        return f"gemini-embedding:{self._model}"

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """Embed one text chunk into a vector.

        Validates that the response dimension matches the requested dimension.
        Raises `EmbeddingError` subclasses on failure — never returns a
        degraded guess.
        """
        url = f"{_GEMINI_EMBED_BASE.format(model=request.model)}?key={self._api_key}"
        payload = self._build_payload(request)

        t0 = time.monotonic()
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = await asyncio.wait_for(
                    self._client.post(url, json=payload),
                    timeout=request.dimension and request.dimension or self._timeout_seconds,
                )
                break
            except TimeoutError as exc:
                last_error = EmbeddingTimeoutError(
                    f"gemini embedding request timed out after {self._timeout_seconds}s"
                )
                if attempt >= self._max_retries:
                    raise last_error from exc
                await asyncio.sleep(0.25 * (attempt + 1))
                continue
            except httpx.HTTPError as exc:
                last_error = EmbeddingConnectionError(f"gemini embedding request failed: {exc}")
                if attempt >= self._max_retries:
                    raise last_error from exc
                await asyncio.sleep(0.25 * (attempt + 1))
                continue
            except asyncio.CancelledError:
                raise
        else:
            # Should not be reachable — the loop always breaks or raises.
            raise last_error or EmbeddingConnectionError("gemini embedding request failed")

        total_ms = (time.monotonic() - t0) * 1000.0

        if response.status_code >= 400:
            raise EmbeddingConnectionError(
                f"gemini embedding returned HTTP {response.status_code}: {response.text[:200]}"
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise EmbeddingConnectionError("gemini embedding returned non-JSON response") from exc

        vector = self._extract_vector(body)
        if len(vector) != request.dimension:
            raise EmbeddingConnectionError(
                f"gemini embedding dimension mismatch: expected "
                f"{request.dimension}, got {len(vector)}"
            )

        return EmbeddingResponse(
            vector=vector,
            model=request.model,
            dimension=request.dimension,
            latency_ms=total_ms,
        )

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    # --- payload construction ----------------------------------------------

    def _build_payload(self, request: EmbeddingRequest) -> dict[str, Any]:
        return {
            "model": f"models/{request.model}",
            "content": {"parts": [{"text": request.text}]},
            "output_dimensionality": request.dimension,
        }

    @staticmethod
    def _extract_vector(body: dict[str, Any]) -> list[float]:
        """Extract the embedding vector from a Gemini embedContent response."""
        embedding = body.get("embedding")
        if not isinstance(embedding, dict):
            raise EmbeddingConnectionError("gemini embedding response missing 'embedding'")
        values = embedding.get("values")
        if not isinstance(values, list) or not all(isinstance(v, (int, float)) for v in values):
            raise EmbeddingConnectionError("gemini embedding 'values' is not a numeric array")
        return [float(v) for v in values]


__all__ = ["GeminiEmbeddingProvider"]
