# Runbook — Multi-Turn Conversation Testing (Day 9)

This runbook explains how to test the stateful administrative voice assistant
offline and how to run the optional live Gemini/Qdrant smoke tests. It assumes
you are in `apps/voice-gateway`.

---

## 1. What is covered

The Day 9 tests exercise:

- slot filling across turns,
- corrections and confirmation invalidation,
- multiple independent requests,
- explicit confirmation before submission,
- targeted cancellation,
- practice-information RAG and follow-ups,
- emergency ordering,
- duplicate/out-of-order final transcripts,
- session isolation,
- barge-in (mock providers),
- truthful backend failure handling,
- tenant-field stripping and trusted retrieval scope.

No test uses real patient data.

---

## 2. Prerequisites

- Python environment managed by `uv`.
- No credentials are required for the offline suite.
- Live tests require the Gemini and Qdrant configuration already used on
  Day 8 (`LLM_PROVIDER=gemini`, `EMBEDDING_PROVIDER=gemini`,
  `VECTOR_STORE_PROVIDER=qdrant`).

Never print, paste, or commit API keys. The tests read credentials from
settings only.

---

## 3. Offline deterministic suite

Run the whole offline suite:

```bash
uv run pytest -o addopts="" -q
```

Expected (Day 9 baseline):

```
338 passed, 4 skipped
```

The 4 skips are the live tests, which are skipped unless
`EMMA_RUN_LIVE_TESTS=1`.

Run only the Day 9 corpus:

```bash
uv run pytest tests/evaluation/day9 -q
```

Run only the focused Day 9 unit tests:

```bash
uv run pytest tests/unit/test_day9_dialogue.py -q
```

Run lint and format checks:

```bash
uv run ruff check .
uv run ruff format --check .
```

---

## 4. Day 9 corpus structure

Files:

- `tests/evaluation/day9/__init__.py` — harness and test doubles.
- `tests/evaluation/day9/test_corpus.py` — 34 scenarios plus isolation,
  bounded-context, and RAG-answer tests.

The harness uses:

- a **scripted LLM** (explicit per-utterance interpretations),
- a **fake practice backend** (records submissions, scripted outcomes),
- a **fake retriever** (records queries, returns configured chunks),
- a **fake answer service** (fixed grounded answer with citations),
- the **real `VoicePipeline`** and **real `ConversationController`**.

This isolates deterministic controller behaviour from any provider's keyword
heuristics. The context-aware mock provider is tested separately in
`tests/unit/test_day9_dialogue.py`.

### Adding a scenario

Add an `EvalCase` to `CASES` in `test_corpus.py`:

```python
EvalCase(
    name="my scenario",
    turns=(
        TurnSpec("I'd like an appointment Tuesday morning", appointment()),
        TurnSpec("yes", unclear("confirmation response")),
    ),
    expected_statuses=(("appointment-request", RequestStatus.SUBMITTED),),
    expected_backend_calls=1,
    expected_llm_calls=2,
)
```

Useful expectations:

- `expected_statuses`
- `expected_backend_calls`
- `expected_llm_calls`
- `expected_retrieval_calls`
- `expected_awaiting_field`
- `expected_confirmation_pending`
- `expected_submitted_types`
- `expected_failed_types`
- `expected_cancelled_types`
- `expected_last_assistant_contains`
- `forbidden_assistant_contains`
- `expected_backend_payload_subset`
- `forbidden_backend_payload_keys`

Remember: a short explicit date/time answer to an awaited slot is filled
deterministically, so it does not call the LLM. Adjust `expected_llm_calls`
accordingly.

---

## 5. Live Gemini/Qdrant smoke tests (opt-in)

Live tests are marked `live` and skipped by default. Enable them explicitly:

```bash
EMMA_RUN_LIVE_TESTS=1 uv run pytest tests/live/test_day9_live_smoke.py -q -s
```

What they do:

1. **Retrieval + isolation + latency** — embeds four practice questions,
   retrieves from local Qdrant, checks all chunks belong to the live practice,
   and checks a different practice returns zero chunks.
2. **Gemini interpretation** — sends a full appointment utterance and asserts a
   date and time are extracted.
3. **Live pipeline turn** — drives one real Gemini turn through the pipeline
   with no practice client and asserts the request stops at
   `READY_FOR_CONFIRMATION`.
4. **Grounded answer** — retrieves opening-hours context and asserts a cited,
   non-abstained answer.

Notes:

- The live practice id defaults to `demo-practice-001`. Override with
  `EMMA_LIVE_PRACTICE_ID` if needed.
- Local Qdrant allows one client per storage path. Close the store before
  reopening it; the tests do this in `finally` blocks.
- Live latency varies. Measured Day 9 runs were:
  - retrieval p50 662–806 ms, p95 852–975 ms,
  - Gemini interpretation ≈ 3.3–3.9 s.

---

## 6. Azure speech smoke checks

Azure credentials are configured in `.env` for the development environment.
Never echo them.

### 6.1 TTS

A live synthesis smoke check should produce raw PCM chunks and a completed
event. A measured run produced 33 chunks / 104,832 bytes at 16 kHz.

### 6.2 STT

The adapter uses Azure's push-stream continuous recognition. In local testing:

- the same PCM recognized correctly from a 16 kHz WAV file via
  `recognize_once()`,
- the push-stream continuous path returned no transcript.

Do not claim real-time streaming STT works until that path is fixed and
re-verified. This is recorded as a known limitation in the Day 9 engineering
report.

### 6.3 Microphone / barge-in

No microphone or browser automation is part of this repository's test suite.
Barge-in is verified with mock providers only. Real acoustic barge-in remains
unverified.

---

## 7. Troubleshooting

### Live tests are skipped

Set `EMMA_RUN_LIVE_TESTS=1` and confirm the live settings are present:

```bash
uv run python -c "from app.core.config import Settings; s=Settings(); print(s.rag_configured)"
```

Do not print key values.

### Qdrant file lock

If a test fails to open the local store, another process still holds the lock.
Close the previous client or stop the process using `./data/qdrant`.

### Unexpected LLM call count in the corpus

Check whether the turn is a short explicit slot answer. Those are filled by
the deterministic backstop and skip the LLM.

### A model returns empty details

The Gemini response schema declares the allowed detail keys explicitly. If a
model still returns an empty `details` object, do not weaken the controller's
required-field policy; treat it as a model/prompt regression and inspect the
raw response.

### A stale turn overwrites a correction

The pipeline rejects final transcripts whose sequence is less than or equal to
the last processed sequence. Check that the STT provider emits monotonically
increasing sequence numbers per session.

---

## 8. Safety checks before shipping

- Emergency guard still runs before the LLM, embeddings, Qdrant, and backend.
- No backend submission without an explicit lexical confirmation.
- No "booked", "confirmed appointment", or "prescription fulfilled" claim
  without backend confirmation.
- Model-supplied `practice_id` / `tenant_id` fields are stripped.
- Retrieval uses the trusted server-side `practice_id`.
- No full transcripts in logs.
