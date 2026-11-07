# Qdrant Local Persistence Runbook (Day 8)

This runbook covers configuring and operating Qdrant in local persistent mode
for the EMMA voice gateway's practice knowledge base retrieval system. No
Docker daemon or Qdrant server is required.

---

## 1. Overview

Qdrant local persistent mode uses the `qdrant-client` Python SDK to store
vectors in a local file directory (`QDRANT_LOCAL_PATH`). This is suitable for
development and prototyping but does not prove a production distributed Qdrant
deployment.

| Aspect | Local mode | Remote mode (server) |
|--------|------------|---------------------|
| Docker required | No | Yes |
| Qdrant server required | No | Yes |
| Storage | Local filesystem | Qdrant server |
| Concurrent access | Single process only | Multiple clients |
| Suitable for production | No | Yes |

---

## 2. Configuration

Add the following to `.env` (root directory):

```env
# Day 8: vector store configuration (Qdrant local mode)
VECTOR_STORE_PROVIDER=qdrant
QDRANT_MODE=local
QDRANT_LOCAL_PATH=./data/qdrant
QDRANT_URL=
QDRANT_API_KEY=
QDRANT_COLLECTION=practice_knowledge
```

| Variable | Description | Default |
|----------|-------------|---------|
| `VECTOR_STORE_PROVIDER` | `mock` or `qdrant` | `mock` |
| `QDRANT_MODE` | `local` or `remote` | `local` |
| `QDRANT_LOCAL_PATH` | Filesystem path for persistent storage | `./data/qdrant` |
| `QDRANT_URL` | Qdrant server URL (remote mode only) | (empty) |
| `QDRANT_API_KEY` | Qdrant API key (remote mode only) | (empty) |
| `QDRANT_COLLECTION` | Collection name | `practice_knowledge` |

When `QDRANT_MODE=local`, `QDRANT_URL` is not required. When `QDRANT_MODE=remote`,
`QDRANT_URL` is required and `QDRANT_LOCAL_PATH` is ignored.

---

## 3. How It Works

The `QdrantVectorStoreProvider` supports two connection modes through the same
`VectorStoreProvider` protocol interface:

1. **Local mode** — Uses `QdrantClient(path=QDRANT_LOCAL_PATH)` from the
   `qdrant-client` SDK. All operations (upsert, search, count, etc.) run
   against the local file storage.

2. **Remote mode** — Uses `httpx` + the Qdrant REST API at `QDRANT_URL`
   (original Day-6 implementation).

The provider factory (`VectorStoreProviderFactory`) selects the mode based on
`QDRANT_MODE` and validates that the required configuration is present.

---

## 4. Collection Lifecycle

The collection is created automatically on first use (`_ensure_collection`):

- If the collection does not exist, it is created with the configured
  dimension (768 for `gemini-embedding-2`) and Cosine distance.
- If the collection exists, its dimension is verified against the configured
  dimension. A mismatch raises `VectorStoreConnectionError`.

Collection identity records provider, embedding model, and vector dimension
implicitly through the collection name and dimension configuration.

---

## 5. Operations

### 5.1 Ingest knowledge base

```bash
cd apps/voice-gateway
uv run python ../scripts/ingest_practice_kb.py \
    --practice-id demo-practice-001 \
    --path data/knowledge_base/demo-practice/ \
    --document-version 1
```

### 5.2 Re-ingest (version 2)

```bash
uv run python ../scripts/ingest_practice_kb.py \
    --practice-id demo-practice-001 \
    --path data/knowledge_base/demo-practice/ \
    --document-version 2
```

Re-ingesting the same document version produces no duplicates (deterministic
chunk IDs). Re-ingesting a changed document creates a new version and
deactivates the old one.

---

## 6. Persistence Verification

To verify persistence after application restart:

1. Ingest the knowledge base (creates vectors in `QDRANT_LOCAL_PATH`).
2. Stop the application.
3. Restart the application.
4. Query for the same information — vectors should still be present.

```python
from app.integrations.vector_store.provider_factory import VectorStoreProviderFactory
from app.core.config import get_settings

settings = get_settings()
factory = VectorStoreProviderFactory(settings)
store = factory.create()
count = await store.count(practice_id="demo-practice-001", active_only=True)
print(f"Active chunks: {count}")
```

---

## 7. Constraints

| Constraint | Details |
|------------|---------|
| Single-process access | Qdrant local mode uses file locking; only one process can access the storage path at a time |
| No concurrent writes | Do not run multiple gateway instances pointing to the same `QDRANT_LOCAL_PATH` |
| Storage location | Keep inside `apps/voice-gateway/data/qdrant/` (gitignored) |
| Do not commit vectors | `apps/voice-gateway/data/qdrant/` is in `.gitignore` |
| Model/dimension consistency | All vectors in one collection share the same model and dimension |

---

## 8. Troubleshooting

### Problem: `AlreadyLocked` error

```
RuntimeError: Storage folder ... is already accessed by another instance of Qdrant client
```

**Cause:** Another process has the Qdrant local storage locked.

**Fix:** Stop the other process, or use a different `QDRANT_LOCAL_PATH`.

### Problem: Collection dimension mismatch

```
VectorStoreConnectionError: qdrant collection 'practice_knowledge' has dimension 384, expected 768
```

**Cause:** The collection was created with a different embedding model/dimension.

**Fix:** Use a different `QDRANT_COLLECTION` name, or delete the storage
directory and re-ingest with the correct dimension.

### Problem: `qdrant-client` not installed

```
ImportError: QDRANT_MODE=local requires qdrant-client package
```

**Fix:** `uv add qdrant-client` in `apps/voice-gateway/`.

---

## 9. Security

- Never commit `QDRANT_API_KEY` to version control
- `.env` is gitignored — never commit it
- The Qdrant storage directory (`apps/voice-gateway/data/qdrant/`) is gitignored
- Do not commit generated vectors
