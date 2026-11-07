"""Qdrant vector store provider (Day 6 + Day 8).

Supports two connection modes:

* ``remote`` — talks to an HTTP Qdrant server via ``httpx`` + the REST API
  (original Day-6 implementation).
* ``local``  — persistent file storage via the ``qdrant_client`` Python SDK
  (``QdrantClient(path=…)``), no server or Docker required.

Both modes implement the same ``VectorStoreProvider`` protocol interface.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx

from app.integrations.vector_store.base import (
    VectorDocument,
    VectorSearchResult,
)
from app.integrations.vector_store.exceptions import (
    VectorStoreConnectionError,
)

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import (
        Distance,
        FieldCondition,
        Filter,
        MatchValue,
        PointStruct,
        VectorParams,
    )

    _QDRANT_SDK_AVAILABLE = True
except ImportError:  # pragma: no cover — qdrant-client is a Day-8 dependency
    _QDRANT_SDK_AVAILABLE = False

# Deterministic UUID namespace for converting string chunk IDs to UUIDs
# required by the local Qdrant SDK. Same input always → same UUID.
_QDRANT_UUID_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def _chunk_id_to_uuid(chunk_id: str) -> uuid.UUID:
    """Convert a string chunk ID to a deterministic UUID for the local SDK."""
    return uuid.uuid5(_QDRANT_UUID_NAMESPACE, chunk_id)


class QdrantVectorStoreProvider:
    """Qdrant vector store provider — REST API (remote) or local SDK (file)."""

    def __init__(
        self,
        *,
        url: str | None = None,
        api_key: str | None = None,
        collection: str,
        dimension: int,
        timeout_seconds: float = 15.0,
        http_client: httpx.AsyncClient | None = None,
        local_path: str | None = None,
    ) -> None:
        self._url = url.rstrip("/") if url else None
        self._api_key = api_key
        self._collection = collection
        self._dimension = dimension
        self._timeout_seconds = timeout_seconds
        self._local_path = local_path

        if self._local_path is not None:
            # --- local persistent mode (qdrant_client SDK) ------------------
            if not _QDRANT_SDK_AVAILABLE:
                raise VectorStoreConnectionError(
                    "QDRANT_MODE=local requires qdrant-client package; "
                    "install it (or switch VECTOR_STORE_PROVIDER=mock)"
                )
            self._sdk_client = QdrantClient(path=self._local_path)
            self._http_client: httpx.AsyncClient | None = None
            self._owns_http_client = False
        else:
            # --- remote mode (httpx + REST API) -----------------------------
            if not self._url or not self._url.strip():
                raise ValueError(
                    "Qdrant provider requires either QDRANT_URL (remote) or "
                    "QDRANT_LOCAL_PATH (local)"
                )
            self._sdk_client = None
            self._http_client = http_client or httpx.AsyncClient(
                timeout=httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 5.0))
            )
            self._owns_http_client = http_client is None

        self._collection_checked = False

    @property
    def provider_name(self) -> str:
        mode = "local" if self._sdk_client is not None else "remote"
        return f"qdrant:{self._collection}:{mode}"

    @property
    def dimension(self) -> int:
        return self._dimension

    # --- lifecycle ----------------------------------------------------------

    async def aclose(self) -> None:
        if self._owns_http_client and self._http_client is not None:
            await self._http_client.aclose()
        if self._sdk_client is not None:
            try:
                self._sdk_client.close()
            except Exception:  # noqa: BLE001 — teardown must not raise
                pass

    # --- upsert -------------------------------------------------------------

    async def upsert(self, documents: list[VectorDocument]) -> int:
        """Upsert document chunks into Qdrant."""
        await self._ensure_collection()
        stored = 0
        for doc in documents:
            if doc.vector is None or len(doc.vector) != self._dimension:
                continue
            if self._sdk_client is not None:
                stored += await self._upsert_sdk(doc)
            else:
                stored += await self._upsert_rest(doc)
        return stored

    async def _upsert_sdk(self, doc: VectorDocument) -> int:
        payload = {
            "document_id": doc.document_id,
            "practice_id": doc.practice_id,
            "source_title": doc.source_title,
            "source_filename": doc.source_filename,
            "section": doc.section,
            "page_number": doc.page_number,
            "chunk_index": doc.chunk_index,
            "document_version": doc.document_version,
            "content": doc.content,
            "is_active": doc.is_active,
            "original_chunk_id": doc.chunk_id,
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        if doc.metadata:
            payload.update({k: v for k, v in doc.metadata.items() if v is not None})

        point_uuid = _chunk_id_to_uuid(doc.chunk_id)

        def _upsert_sync() -> None:
            self._sdk_client.upsert(
                collection_name=self._collection,
                points=[
                    PointStruct(
                        id=str(point_uuid),
                        vector=doc.vector,
                        payload=payload,
                    )
                ],
            )

        try:
            await asyncio.get_running_loop().run_in_executor(None, _upsert_sync)
        except Exception as exc:  # noqa: BLE001 — wrap SDK errors
            raise VectorStoreConnectionError(f"qdrant local upsert failed: {exc}") from exc
        return 1

    async def _upsert_rest(self, doc: VectorDocument) -> int:
        points = [
            {
                "id": doc.chunk_id,
                "vector": doc.vector,
                "payload": {
                    "document_id": doc.document_id,
                    "practice_id": doc.practice_id,
                    "source_title": doc.source_title,
                    "source_filename": doc.source_filename,
                    "section": doc.section,
                    "page_number": doc.page_number,
                    "chunk_index": doc.chunk_index,
                    "document_version": doc.document_version,
                    "content": doc.content,
                    "is_active": doc.is_active,
                    **(
                        {k: v for k, v in doc.metadata.items() if v is not None}
                        if doc.metadata
                        else {}
                    ),
                },
            }
        ]
        try:
            response = await asyncio.wait_for(
                self._http_client.post(  # type: ignore[union-attr]
                    f"{self._url}/collections/{self._collection}/points",
                    json={"points": points},
                ),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as exc:
            raise VectorStoreConnectionError(
                f"qdrant upsert timed out after {self._timeout_seconds}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise VectorStoreConnectionError(f"qdrant upsert request failed: {exc}") from exc
        except asyncio.CancelledError:
            raise

        if response.status_code >= 400:
            raise VectorStoreConnectionError(
                f"qdrant upsert returned HTTP {response.status_code}: {response.text[:200]}"
            )
        return 1

    # --- search -------------------------------------------------------------

    async def search(
        self,
        *,
        practice_id: str,
        query_vector: list[float],
        top_k: int = 5,
        min_score: float = 0.0,
        document_version: int | None = None,
    ) -> list[VectorSearchResult]:
        """Search for similar chunks within one practice.

        Every search includes a server-side practice_id filter plus
        document_status=active and optional document_version filter.
        """
        await self._ensure_collection()
        if self._sdk_client is not None:
            return await self._search_sdk(
                practice_id=practice_id,
                query_vector=query_vector,
                top_k=top_k,
                min_score=min_score,
                document_version=document_version,
            )
        return await self._search_rest(
            practice_id=practice_id,
            query_vector=query_vector,
            top_k=top_k,
            min_score=min_score,
            document_version=document_version,
        )

    async def _search_sdk(
        self,
        *,
        practice_id: str,
        query_vector: list[float],
        top_k: int,
        min_score: float,
        document_version: int | None,
    ) -> list[VectorSearchResult]:
        must_conditions = [
            FieldCondition(key="practice_id", match=MatchValue(value=practice_id)),
            FieldCondition(key="is_active", match=MatchValue(value=True)),
        ]
        if document_version is not None:
            must_conditions.append(
                FieldCondition(key="document_version", match=MatchValue(value=document_version))
            )

        def _search_sync() -> Any:
            return self._sdk_client.query_points(
                collection_name=self._collection,
                query=query_vector,
                query_filter=Filter(must=must_conditions),
                limit=top_k,
                score_threshold=min_score if min_score > 0 else None,
                with_payload=True,
                with_vectors=False,
            )

        try:
            result = await asyncio.get_running_loop().run_in_executor(None, _search_sync)
        except Exception as exc:  # noqa: BLE001 — wrap SDK errors
            raise VectorStoreConnectionError(f"qdrant local search failed: {exc}") from exc

        results: list[VectorSearchResult] = []
        for point in result.points or []:
            payload = point.payload or {}
            results.append(
                VectorSearchResult(
                    chunk_id=str(payload.get("original_chunk_id", str(point.id))),
                    document_id=str(payload.get("document_id", "")),
                    practice_id=str(payload.get("practice_id", "")),
                    source_title=str(payload.get("source_title", "")),
                    source_filename=str(payload.get("source_filename", "")),
                    section=payload.get("section"),
                    page_number=payload.get("page_number"),
                    chunk_index=payload.get("chunk_index", 0),
                    content=str(payload.get("content", "")),
                    score=float(point.score or 0.0),
                    metadata={k: v for k, v in payload.items() if v is not None},
                )
            )
        return results

    async def _search_rest(
        self,
        *,
        practice_id: str,
        query_vector: list[float],
        top_k: int,
        min_score: float,
        document_version: int | None,
    ) -> list[VectorSearchResult]:
        filter_conditions: list[dict[str, Any]] = [
            {"key": "practice_id", "match": {"value": practice_id}},
            {"key": "is_active", "match": {"value": True}},
        ]
        if document_version is not None:
            filter_conditions.append(
                {"key": "document_version", "match": {"value": document_version}}
            )

        payload: dict[str, Any] = {
            "vector": query_vector,
            "filter": {"must": filter_conditions},
            "limit": top_k,
            "with_payload": True,
            "with_vectors": False,
        }
        if min_score > 0:
            payload["score_threshold"] = min_score

        try:
            response = await asyncio.wait_for(
                self._http_client.post(  # type: ignore[union-attr]
                    f"{self._url}/collections/{self._collection}/points/search",
                    json=payload,
                ),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as exc:
            raise VectorStoreConnectionError(
                f"qdrant search timed out after {self._timeout_seconds}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise VectorStoreConnectionError(f"qdrant search request failed: {exc}") from exc
        except asyncio.CancelledError:
            raise

        if response.status_code >= 400:
            raise VectorStoreConnectionError(
                f"qdrant search returned HTTP {response.status_code}: {response.text[:200]}"
            )

        body = response.json()
        results: list[VectorSearchResult] = []
        for point in body.get("result", []):
            p_payload = point.get("payload", {}) or {}
            results.append(
                VectorSearchResult(
                    chunk_id=str(point.get("id", "")),
                    document_id=str(p_payload.get("document_id", "")),
                    practice_id=str(p_payload.get("practice_id", "")),
                    source_title=str(p_payload.get("source_title", "")),
                    source_filename=str(p_payload.get("source_filename", "")),
                    section=p_payload.get("section"),
                    page_number=p_payload.get("page_number"),
                    chunk_index=p_payload.get("chunk_index", 0),
                    content=str(p_payload.get("content", "")),
                    score=float(point.get("score", 0.0)),
                    metadata={k: v for k, v in p_payload.items() if v is not None},
                )
            )
        return results

    # --- deactivate_document ------------------------------------------------

    async def deactivate_document(self, *, document_id: str, practice_id: str) -> int:
        """Mark all chunks of a document as superseded (is_active=false)."""
        await self._ensure_collection()
        if self._sdk_client is not None:
            return await self._deactivate_sdk(document_id=document_id, practice_id=practice_id)
        return await self._deactivate_rest(document_id=document_id, practice_id=practice_id)

    async def _deactivate_sdk(self, *, document_id: str, practice_id: str) -> int:
        def _scroll_sync() -> Any:
            return self._sdk_client.scroll(
                collection_name=self._collection,
                scroll_filter=Filter(
                    must=[
                        FieldCondition(key="document_id", match=MatchValue(value=document_id)),
                        FieldCondition(key="practice_id", match=MatchValue(value=practice_id)),
                    ]
                ),
                limit=1000,
                with_payload=False,
                with_vectors=False,
            )

        try:
            result = await asyncio.get_running_loop().run_in_executor(None, _scroll_sync)
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreConnectionError(
                f"qdrant local scroll for deactivation failed: {exc}"
            ) from exc

        # Local SDK scroll returns (points, next_page_offset) tuple
        points = result[0] if isinstance(result, tuple) else getattr(result, "points", [])
        point_ids = [point.id for point in points or []]
        if not point_ids:
            return 0

        def _set_payload_sync() -> None:
            self._sdk_client.set_payload(
                collection_name=self._collection,
                points=point_ids,
                payload={"is_active": False},
            )

        try:
            await asyncio.get_running_loop().run_in_executor(None, _set_payload_sync)
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreConnectionError(
                f"qdrant local update for deactivation failed: {exc}"
            ) from exc
        return len(point_ids)

    async def _deactivate_rest(self, *, document_id: str, practice_id: str) -> int:
        scroll_payload: dict[str, Any] = {
            "filter": {
                "must": [
                    {"key": "document_id", "match": {"value": document_id}},
                    {"key": "practice_id", "match": {"value": practice_id}},
                ]
            },
            "limit": 1000,
            "with_payload": False,
            "with_vectors": False,
        }
        try:
            response = await asyncio.wait_for(
                self._http_client.post(  # type: ignore[union-attr]
                    f"{self._url}/collections/{self._collection}/points/scroll",
                    json=scroll_payload,
                ),
                timeout=self._timeout_seconds,
            )
        except (TimeoutError, httpx.HTTPError, asyncio.CancelledError) as exc:
            raise VectorStoreConnectionError(
                f"qdrant scroll for deactivation failed: {exc}"
            ) from exc

        if response.status_code >= 400:
            raise VectorStoreConnectionError(
                f"qdrant scroll for deactivation returned HTTP {response.status_code}"
            )

        body = response.json()
        point_ids = [point.get("id") for point in body.get("result", {}).get("points", [])]

        if not point_ids:
            return 0

        update_payload: dict[str, Any] = {
            "points": point_ids,
            "payload": {"is_active": False},
        }
        try:
            await asyncio.wait_for(
                self._http_client.post(  # type: ignore[union-attr]
                    f"{self._url}/collections/{self._collection}/points",
                    json=update_payload,
                ),
                timeout=self._timeout_seconds,
            )
        except (TimeoutError, httpx.HTTPError, asyncio.CancelledError) as exc:
            raise VectorStoreConnectionError(
                f"qdrant update for deactivation failed: {exc}"
            ) from exc

        return len(point_ids)

    # --- delete_document ----------------------------------------------------

    async def delete_document(self, *, document_id: str, practice_id: str) -> int:
        """Permanently delete all chunks of a document."""
        await self._ensure_collection()
        if self._sdk_client is not None:
            return await self._delete_sdk(document_id=document_id, practice_id=practice_id)
        return await self._delete_rest(document_id=document_id, practice_id=practice_id)

    async def _delete_sdk(self, *, document_id: str, practice_id: str) -> int:
        def _scroll_sync() -> Any:
            return self._sdk_client.scroll(
                collection_name=self._collection,
                scroll_filter=Filter(
                    must=[
                        FieldCondition(key="document_id", match=MatchValue(value=document_id)),
                        FieldCondition(key="practice_id", match=MatchValue(value=practice_id)),
                    ]
                ),
                limit=1000,
                with_payload=False,
                with_vectors=False,
            )

        try:
            result = await asyncio.get_running_loop().run_in_executor(None, _scroll_sync)
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreConnectionError(
                f"qdrant local scroll for deletion failed: {exc}"
            ) from exc

        # Local SDK scroll returns (points, next_page_offset) tuple
        points = result[0] if isinstance(result, tuple) else getattr(result, "points", [])
        point_ids = [point.id for point in points or []]
        if not point_ids:
            return 0

        def _delete_sync() -> None:
            self._sdk_client.delete(
                collection_name=self._collection,
                points_selector=point_ids,
            )

        try:
            await asyncio.get_running_loop().run_in_executor(None, _delete_sync)
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreConnectionError(f"qdrant local delete failed: {exc}") from exc
        return len(point_ids)

    async def _delete_rest(self, *, document_id: str, practice_id: str) -> int:
        scroll_payload: dict[str, Any] = {
            "filter": {
                "must": [
                    {"key": "document_id", "match": {"value": document_id}},
                    {"key": "practice_id", "match": {"value": practice_id}},
                ]
            },
            "limit": 1000,
            "with_payload": False,
            "with_vectors": False,
        }
        try:
            response = await asyncio.wait_for(
                self._http_client.post(  # type: ignore[union-attr]
                    f"{self._url}/collections/{self._collection}/points/scroll",
                    json=scroll_payload,
                ),
                timeout=self._timeout_seconds,
            )
        except (TimeoutError, httpx.HTTPError, asyncio.CancelledError) as exc:
            raise VectorStoreConnectionError(f"qdrant scroll for deletion failed: {exc}") from exc

        if response.status_code >= 400:
            raise VectorStoreConnectionError(
                f"qdrant scroll for deletion returned HTTP {response.status_code}"
            )

        body = response.json()
        point_ids = [point.get("id") for point in body.get("result", {}).get("points", [])]

        if not point_ids:
            return 0

        delete_payload: dict[str, Any] = {"points": point_ids}
        try:
            await asyncio.wait_for(
                self._http_client.post(  # type: ignore[union-attr]
                    f"{self._url}/collections/{self._collection}/points/delete",
                    json=delete_payload,
                ),
                timeout=self._timeout_seconds,
            )
        except (TimeoutError, httpx.HTTPError, asyncio.CancelledError) as exc:
            raise VectorStoreConnectionError(f"qdrant delete failed: {exc}") from exc

        return len(point_ids)

    # --- count --------------------------------------------------------------

    async def count(self, *, practice_id: str, active_only: bool = True) -> int:
        """Return the number of stored chunks for a practice."""
        await self._ensure_collection()
        if self._sdk_client is not None:
            return await self._count_sdk(practice_id=practice_id, active_only=active_only)
        return await self._count_rest(practice_id=practice_id, active_only=active_only)

    async def _count_sdk(self, *, practice_id: str, active_only: bool) -> int:
        must_conditions = [
            FieldCondition(key="practice_id", match=MatchValue(value=practice_id)),
        ]
        if active_only:
            must_conditions.append(FieldCondition(key="is_active", match=MatchValue(value=True)))

        def _count_sync() -> Any:
            return self._sdk_client.count(
                collection_name=self._collection,
                count_filter=Filter(must=must_conditions),
                exact=True,
            )

        try:
            result = await asyncio.get_running_loop().run_in_executor(None, _count_sync)
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreConnectionError(f"qdrant local count failed: {exc}") from exc
        return int(result.count)

    async def _count_rest(self, *, practice_id: str, active_only: bool) -> int:
        filter_conditions: list[dict[str, Any]] = [
            {"key": "practice_id", "match": {"value": practice_id}}
        ]
        if active_only:
            filter_conditions.append({"key": "is_active", "match": {"value": True}})

        scroll_payload: dict[str, Any] = {
            "filter": {"must": filter_conditions},
            "limit": 1,
            "with_payload": False,
            "with_vectors": False,
        }
        try:
            response = await asyncio.wait_for(
                self._http_client.post(  # type: ignore[union-attr]
                    f"{self._url}/collections/{self._collection}/points/scroll",
                    json=scroll_payload,
                ),
                timeout=self._timeout_seconds,
            )
        except (TimeoutError, httpx.HTTPError, asyncio.CancelledError) as exc:
            raise VectorStoreConnectionError(f"qdrant count scroll failed: {exc}") from exc

        if response.status_code >= 400:
            raise VectorStoreConnectionError(
                f"qdrant count scroll returned HTTP {response.status_code}"
            )

        body = response.json()
        return int(body.get("result", {}).get("total", 0))

    # --- internal -----------------------------------------------------------

    async def _ensure_collection(self) -> None:
        """Ensure the Qdrant collection exists with the correct dimension."""
        if self._collection_checked:
            return
        if self._sdk_client is not None:
            await self._ensure_collection_sdk()
        else:
            await self._ensure_collection_rest()
        self._collection_checked = True

    async def _ensure_collection_sdk(self) -> None:
        def _ensure_sync() -> None:
            if not self._sdk_client.collection_exists(collection_name=self._collection):
                self._sdk_client.create_collection(
                    collection_name=self._collection,
                    vectors_config=VectorParams(size=self._dimension, distance=Distance.COSINE),
                )
                return
            # Collection exists — verify dimension matches
            info = self._sdk_client.get_collection(collection_name=self._collection)
            params = info.config.params
            vectors = params.vectors
            if isinstance(vectors, dict):
                existing_dim = vectors.get("size")
            else:
                existing_dim = getattr(vectors, "size", None)
            if existing_dim is not None and existing_dim != self._dimension:
                raise VectorStoreConnectionError(
                    f"qdrant collection '{self._collection}' has dimension "
                    f"{existing_dim}, expected {self._dimension}"
                )

        try:
            await asyncio.get_running_loop().run_in_executor(None, _ensure_sync)
        except VectorStoreConnectionError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreConnectionError(
                f"qdrant local collection setup failed: {exc}"
            ) from exc

    async def _ensure_collection_rest(self) -> None:
        try:
            response = await asyncio.wait_for(
                self._http_client.get(  # type: ignore[union-attr]
                    f"{self._url}/collections/{self._collection}",
                ),
                timeout=self._timeout_seconds,
            )
        except (TimeoutError, httpx.HTTPError, asyncio.CancelledError) as exc:
            raise VectorStoreConnectionError(f"qdrant collection check failed: {exc}") from exc

        if response.status_code == 404:
            # Create the collection
            create_payload: dict[str, Any] = {
                "vectors": {
                    "size": self._dimension,
                    "distance": "Cosine",
                }
            }
            try:
                await asyncio.wait_for(
                    self._http_client.put(  # type: ignore[union-attr]
                        f"{self._url}/collections/{self._collection}",
                        json=create_payload,
                    ),
                    timeout=self._timeout_seconds,
                )
            except (TimeoutError, httpx.HTTPError, asyncio.CancelledError) as exc:
                raise VectorStoreConnectionError(
                    f"qdrant collection creation failed: {exc}"
                ) from exc
        elif response.status_code >= 400:
            raise VectorStoreConnectionError(
                f"qdrant collection check returned HTTP {response.status_code}"
            )


__all__ = ["QdrantVectorStoreProvider"]
