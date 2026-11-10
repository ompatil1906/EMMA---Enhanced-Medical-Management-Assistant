# Voice evaluation

**Principle: no feature is claimed verified unless a test exercised it in this
tree.** Everything below was produced by running the suites; unverified items are
listed explicitly.

## 1. Suites and current results

| Suite | Command | Result |
|---|---|---|
| Gateway unit + integration | `cd apps/voice-gateway && uv run pytest -o addopts="" -q` | **729 passed, 6 skipped, 1 failed\*** |
| Lint | `uv run ruff check app tests tools` | clean |
| Format | `uv run ruff format --check app tests tools` | clean (174 files) |
| Practice API | `cd apps/practice-api && pnpm test` | **74 passed, 10 suites** |
| Practice API build | `pnpm build` | clean |
| UI lint | `cd apps/ui && pnpm lint` | 0 errors (3 pre-existing warnings) |
| UI build | `pnpm build` | clean |

\* `tests/integration/test_health_live_guard.py::test_guard_enabled_but_mock_providers_stay_ready`
asserts "mock providers" but the repository `.env` selects Deepgram/ElevenLabs,
so its premise does not hold in this environment. With the providers pinned to
mock it passes:

```bash
STT_PROVIDER=mock TTS_PROVIDER=mock LLM_PROVIDER=mock \
  uv run pytest -o addopts="" -q tests/integration/test_health_live_guard.py   # 6 passed
```

## 2. Conversation coverage

`tests/evaluation/day9/test_corpus.py` — 34 dialogue cases, including:
greeting, appointment slot filling, confirmation (yes/no/maybe), correction,
cancellation, multi-request independence, backend failure/timeout truthfulness,
practice-information grounded answers + follow-ups, abstention, emergency-first
(pre-empting everything), out-of-order finals, repeated confirmation, and
practice-id injection attempts.

`tests/unit/test_general_conversation.py` — small talk, clinical refusal,
practice-scope guard, general-knowledge fallback, emergency precedence.

`tests/unit/test_greeting_personalization.py` — "Hello, {FirstName}" at the
handler, pipeline, token-claim and room-resolution seams, plus the WebSocket
end-to-end greeting (`tests/integration/test_speech_pipeline.py`).

## 3. Audio and interruption

| Area | Tests |
|---|---|
| VAD (hysteresis, adaptive floor, echo guard, click rejection) | `tests/unit/test_vad.py` (14) |
| Turn assembly (hold/merge/release/dedupe/stale) | `tests/unit/test_turn_manager.py` (38) |
| Control-event ordering + audio flush | `tests/unit/test_day10_control_events.py` |
| Bounded context / digest | `tests/unit/test_context_manager.py` (7) |
| Tool validation, permissions, timeout, idempotency | `tests/unit/test_tools.py` (15) |
| Latency percentile aggregation | `tests/unit/test_observability.py` (7) |
| Browser WebRTC round trip (mock providers) | `apps/ui` Playwright acceptance (Day 17, 8 scenarios) |

## 4. Measured numbers (deterministic mock path)

These are **pipeline overhead** measurements, not provider latency. They were
read from `GET /api/v1/diagnostics` after driving the evaluation corpus:

| Metric | p50 | p95 | How measured |
|---|---|---|---|
| final transcript → processing start | ~0.1 ms | ~0.3 ms | `latency.final_to_processing_ms` |
| processing start → first audio chunk | ~1.5 ms | ~4 ms | `latency.processing_to_first_audio_ms` |
| turn total (mock LLM + mock TTS) | ~2 ms | ~6 ms | `latency.turn_total_ms` |
| interruption latency (mock) | ~0.2 ms | ~0.5 ms | `turns.interruption_latency_ms` |

Real STT/LLM/TTS latency is **not** claimed: no live credentials were used, so
provider TTFB/TTFA remain unmeasured in this milestone (see
`docs/operations/VOICE_OBSERVABILITY.md` for how to measure them).

## 5. Explicitly unverified

* Acoustic barge-in with a real microphone/speaker (echo path).
* Live Deepgram / ElevenLabs / OpenAI / Gemini behaviour and latency.
* Live Atlassian Jira sync (implemented; exercised in `mock` only).
* Load/concurrency beyond the serial acceptance suite.
* Mobile browser behaviour.
