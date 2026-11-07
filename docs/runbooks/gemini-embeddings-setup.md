# Gemini Embeddings Setup Runbook (Day 6 + Day 8)

This runbook covers configuring the Gemini embedding provider for the EMMA
voice gateway's practice knowledge base retrieval system.

## 1. Overview

The embedding provider converts text chunks into vector embeddings for
semantic retrieval. The EMMA voice gateway supports two embedding providers:

- **mock** — Deterministic hash-based vectors (default, no credentials needed)
- **gemini** — Google Gemini `gemini-embedding-2` via REST API (768 dimensions)

## 2. Configuration

### Environment Variables

Add the following to `.env` (root directory):

```env
# Day 6: embedding provider configuration
EMBEDDING_PROVIDER=gemini          # mock | gemini
GEMINI_EMBEDDING_MODEL=gemini-embedding-2
GEMINI_EMBEDDING_DIMENSION=768
GEMINI_EMBEDDING_API_KEY=        # Get from Google Cloud Console
GEMINI_EMBEDDING_TIMEOUT_SECONDS=15
GEMINI_EMBEDDING_MAX_RETRIES=2
```

### Vector Store Configuration (Day 8 — Qdrant local mode)

```env
# Day 8: vector store configuration
VECTOR_STORE_PROVIDER=qdrant       # mock | qdrant
QDRANT_MODE=local                  # local | remote
QDRANT_LOCAL_PATH=./data/qdrant    # filesystem path (local mode only)
QDRANT_URL=                        # e.g. http://localhost:6333 (remote mode only)
QDRANT_API_KEY=                    # Optional Qdrant API key (remote mode only)
QDRANT_COLLECTION=practice_knowledge
```

When `QDRANT_MODE=local`, `QDRANT_URL` is not required. Vectors are stored in
the local filesystem at `QDRANT_LOCAL_PATH` — no Docker or Qdrant server
needed. See `docs/runbooks/qdrant-local-persistence.md` for details.

### Retrieval Configuration

```env
# Day 6: retrieval configuration
RETRIEVAL_TOP_K=5
RETRIEVAL_MIN_SCORE=0.3
RETRIEVAL_CHUNK_SIZE=600
RETRIEVAL_CHUNK_OVERLAP=80
```

## 3. Getting a Gemini API Key

1. Go to the [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select an existing one
3. Enable the "Generative Language API" for your project
4. Go to "Credentials" → "Create credentials" → "API key"
5. Copy the API key and add it to `.env` as `GEMINI_EMBEDDING_API_KEY`
6. Never commit the API key to version control — `.env` is gitignored

## 4. Configuration Validation

The voice gateway validates configuration at startup:

- If `EMBEDDING_PROVIDER=gemini` and `GEMINI_EMBEDDING_API_KEY` is empty,
  readiness is degraded and the embedding provider will fail clearly on first
  use (never silently falls back to mock)
- If `VECTOR_STORE_PROVIDER=qdrant`:
  - `QDRANT_MODE=local`: `QDRANT_LOCAL_PATH` must be set
  - `QDRANT_MODE=remote`: `QDRANT_URL` must be set

Check readiness at:

```bash
curl http://localhost:8000/health/ready
```

Look for `rag_configured: true` in the response.

## 5. Testing with Mock Provider

By default, `EMBEDDING_PROVIDER=mock` uses deterministic hash-based
embeddings. This is suitable for testing the pipeline structure without
real credentials:

```bash
# No configuration needed — mock provider works out of the box
uv run python scripts/ingest_practice_kb.py \
    --practice-id demo-practice-001 \
    --path apps/voice-gateway/data/knowledge_base/demo-practice/
```

The mock embedding provider:
- Generates deterministic vectors from SHA-256 hashes
- Same input → same vector every time
- Vectors are unit-normalized (cosine similarity = dot product)
- **Not suitable for semantic search** — use for pipeline testing only

## 6. Testing with Real Gemini Provider

To test with the real Gemini embedding API:

1. Get a Gemini API key from Google Cloud Console
2. Add `GEMINI_EMBEDDING_API_KEY` to `.env`
3. Set `EMBEDDING_PROVIDER=gemini`
4. Set `VECTOR_STORE_PROVIDER=qdrant` and `QDRANT_MODE=local`
5. Restart the voice gateway
6. Run ingestion:
   ```bash
   cd apps/voice-gateway
   uv run python ../scripts/ingest_practice_kb.py \
       --practice-id demo-practice-001 \
       --path data/knowledge_base/demo-practice/
   ```
7. Test a practice-information query through the voice UI

## 7. Troubleshooting

### Problem: Embedding provider fails with HTTP 403

**Cause:** The Gemini API key is not being sent as a query parameter.
This was fixed in Day 8 — the embedContent URL now includes `?key=API_KEY`.

### Problem: `GeminiEmbeddingProvider` raised `ValueError: Gemini embedding provider requires a non-empty API key`

Check that `GEMINI_EMBEDDING_API_KEY` is set in `.env` and the gateway was
restarted after changing `.env`.

### Problem: Embeddings all return the same vector

This is expected with the mock provider — it uses deterministic hash-based
vectors. Use the Gemini provider for real semantic embeddings.

### Problem: Vector store not configured

If `VECTOR_STORE_PROVIDER=qdrant` but neither `QDRANT_LOCAL_PATH` (local)
nor `QDRANT_URL` (remote) is set, readiness is degraded. Either:
- Set `QDRANT_LOCAL_PATH` for local mode, or
- Set `QDRANT_URL` for remote mode, or
- Switch to `VECTOR_STORE_PROVIDER=mock` for testing

### Problem: `AlreadyLocked` error from Qdrant local

Another process has the Qdrant storage locked. Stop the other process or
use a different `QDRANT_LOCAL_PATH`. See `docs/runbooks/qdrant-local-persistence.md`.

## 8. Security

- Never commit `GEMINI_EMBEDDING_API_KEY` or `QDRANT_API_KEY` to version control
- `.env` is gitignored — never commit it
- API keys are never logged by the application
- API keys are never sent to the frontend or browser
- The Qdrant storage directory (`apps/voice-gateway/data/qdrant/`) is gitignored
