# Voice observability

## 1. Surfaces

| Endpoint | Content | PII |
|---|---|---|
| `GET /health/live` | process liveness | none |
| `GET /health/ready` | providers, session count, live-spend guard | none |
| `GET /api/v1/tools` | tool catalog + LLM function declarations | none |
| `GET /api/v1/diagnostics` | providers, tunables, latency percentiles, recent sessions | none |

## 2. What is measured

Per session (`app/services/metrics.py`, emitted once at teardown):

| Group | Fields |
|---|---|
| STT | first partial/final timestamps, counts, low-confidence count, empty finals |
| Processing | turn start, per-turn totals |
| LLM | first-token/total latency (`llm_ms`) |
| TTS | first chunk, completion, chunks, bytes |
| Latency | `final_to_processing_ms`, `processing_to_first_audio_ms`, `turn_total_ms`, `tts_ms` |
| Turns | dropped (+ reasons), held, interrupted, `interruption_latency_ms` |
| VAD | speech starts/ends, speech duration samples, early barge-ins, `barge_in_latency_ms` |
| Tools | executions, statuses, p50/p95 latency, idempotent replays |
| Incident | provider errors, typed error strings |

Aggregated (`app/services/observability.py`, bounded to 100 sessions):
`GET /api/v1/diagnostics → latency_ms.{metric}.{p50,p95,max,count}`.

## 3. Content policy

No transcript text, no audio, no caller details, no tool arguments and no
credentials are recorded. Session ids are opaque. Tool audit entries carry
`tool · permission · status · latency · idempotency key`, never payloads. This is
asserted by `tests/unit/test_observability.py::test_diagnostics_endpoint_reports_latency_and_config`.

## 4. Measuring real provider latency (not yet done)

1. Rotate provider keys and set the operator attestations
   (`EMMA_LIVE_CREDENTIALS_ROTATED=1`, `EMMA_LIVE_PROVIDER_SPEND_APPROVED=1`).
2. Start the gateway with `STT_PROVIDER=deepgram TTS_PROVIDER=elevenlabs
   LLM_PROVIDER=openai` and run the browser acceptance suite.
3. Read `GET /api/v1/diagnostics` — `latency_ms.llm_ms`, `tts_ms` and
   `processing_to_first_audio_ms` then contain real provider numbers.
4. Keep the spend guard enabled: it refuses to construct live providers without
   the ledger.

## 5. Why no OpenTelemetry yet

OTel adds a dependency, an exporter endpoint and a collector to operate. The
snapshot shape above is stable and small, so wiring OTel is a decorator over
`record_from_metrics()` when an operator actually runs a collector. Adding it
now would be infrastructure the prototype does not need.
