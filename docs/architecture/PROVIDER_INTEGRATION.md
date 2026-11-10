# Provider integration

## 1. Contracts (provider-independent)

| Contract | Module | Adapters |
|---|---|---|
| `SpeechToTextProvider` | `app/speech/base.py` | Deepgram Flux, Azure Speech, mock |
| `TextToSpeechProvider` | `app/speech/base.py` | ElevenLabs Flash, Azure Speech, mock |
| `LLMProvider` | `app/integrations/llm/base.py` | Gemini, OpenAI, mock, fallback wrapper |
| `EmbeddingProvider` | `app/integrations/embeddings/base.py` | Gemini, mock |
| `VectorStore` | `app/integrations/vector_store/base.py` | Qdrant (local/remote), mock |
| Practice backend | `app/integrations/practice_api.py` | NestJS REST |
| Task platform | `apps/practice-api/src/integrations/jira/` | Atlassian Cloud REST v3, mock |

Factories (`app/speech/factory.py`, `app/integrations/*/provider_factory.py`)
construct one provider per session with the negotiated audio format; a real
provider without credentials raises — there is no silent mock fallback.

## 2. Selection (environment)

| Variable | Values | Notes |
|---|---|---|
| `STT_PROVIDER` | `mock` \| `azure` \| `deepgram` | Deepgram requires `DEEPGRAM_API_KEY` |
| `TTS_PROVIDER` | `mock` \| `azure` \| `elevenlabs` | ElevenLabs requires key + voice id |
| `LLM_PROVIDER` | `mock` \| `gemini` \| `openai` | Gemini is the preferred existing provider |
| `EMBEDDING_PROVIDER` | `mock` \| `gemini` | 768-dim, matches Qdrant collection |
| `VECTOR_STORE_PROVIDER` | `mock` \| `qdrant` | local persistent or remote |
| `JIRA_MODE` (practice API) | `disabled` \| `mock` \| `live` | `live` fails fast when incomplete |

## 3. Budget guards

Live Deepgram/ElevenLabs construction is gated by the Day-18C ledger guard
(`EMMA_LIVE_GUARD_ENABLED` + operator attestations), and OpenAI spend is capped
by `OPENAI_DAILY_BUDGET_USD` / `OPENAI_DEV_BUDGET_USD`. This milestone made **no
live provider calls**: all verification ran against mock providers, so no cost
was incurred.

## 4. Adding a provider

1. Implement the matching `Protocol` in `app/speech/` or `app/integrations/*/`.
2. Register it in the factory with a new `Literal` value in `config.py`.
3. Add a unit test that pins the request shape against a local fake server
   (see `tests/integration/test_deepgram_protocol.py`).
4. Document the credential names in `.env.example` — never values.

## 5. Task-platform integration (Jira)

The voice gateway never talks to Jira. It posts the end-of-call summary to the
practice API (`POST /api/v1/calls/:id/summary`), which stores it and mirrors one
issue per call. Masking (`JIRA_PII_MODE`), idempotency (`jira_issue_key`) and
failure reporting are documented in `docs/integrations/jira.md`.
