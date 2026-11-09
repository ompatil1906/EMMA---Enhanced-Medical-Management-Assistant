# Final Local Acceptance Testing — EMMA Runbook

**Purpose:** Reproduce the Day 10 final acceptance checks locally. All checks
use existing providers and credentials already present in `.env` (never printed
or committed). Nothing is committed or pushed.

**Prerequisites:** Python 3.13 + `uv` (voice-gateway venv), Node + `pnpm` (UI
and practice-api). Qdrant local persistent mode (no server required).

---

## 1. Python voice gateway — full regression

```bash
cd apps/voice-gateway

# Lint + format
uv run ruff check .
uv run ruff format --check .

# Full test suite (offline, mock providers)
uv run pytest -o addopts="" -q

# Expected: 350 passed, 4 skipped (the 4 skips are opt-in live tests)
```

Result observed: `350 passed, 4 skipped in ~15s`, Ruff clean.

## 2. Live Azure STT + TTS + Gemini + Qdrant end-to-end

Requires `EMMA_RUN_LIVE_TESTS=1` and real credentials in `.env`.

### 2.1 Azure STT lifecycle (fake SDK, no network)

```bash
uv run pytest tests/unit/test_azure_stt_lifecycle.py -q
# Expected: 6 passed
```

### 2.2 Real Azure STT streaming (live)

Live-verified phrase: `"I would like to request an appointment next Wednesday morning."`
Expected: exact final transcript delivered exactly once, ~3 s first-final,
partials present, no errors.

### 2.3 Real Azure TTS (live)

Four response types synthesized at PCM_L16 16 kHz mono; first chunk ~1.5–2.3 s;
`SynthesisCompleted` totals match chunk/byte counts; `cancelled=False`.

### 2.4 Full e2e loop over WebSocket

The temporary e2e driver (`/tmp/day10_live_e2e.py`, not committed) starts the
gateway on an ephemeral port, sends real Azure-TTS-generated PCM16 as "mic"
input, and verifies:

* Turn 1: exact final transcript + confirmation read-back ("preferred date:
  next Wednesday, preferred time: morning").
* Turn 2: "What time does the practice open on Monday?" → grounded Qdrant
  answer spoken via Azure TTS.
* Clean WebSocket close (1000).

## 3. Live smoke tests (real Gemini + Qdrant)

```bash
cd apps/voice-gateway
EMMA_RUN_LIVE_TESTS=1 uv run pytest tests/live/test_day9_live_smoke.py -q
# Expected: 4 passed
```

Covers practice-isolated retrieval, grounded answer, practice-info flow, and
Qdrant persistence.

## 4. NestJS practice API

```bash
cd apps/practice-api
pnpm test       # 49 passed, 8 suites
pnpm build      # clean
```

Uses SQLite (dev/test); MySQL runtime unavailable. Covers idempotency,
duplicate-submission prevention, rejection/timeout, audit events, constraints.

## 5. Browser UI

```bash
cd apps/ui
pnpm lint       # clean (1 pre-existing warning)
pnpm build      # clean, Next.js 16.4.5
```

The UI implements PCM16 playback and control events, but no live browser
session was executed against the gateway (no browser automation/mic access).

## 6. What is deliberately NOT acceptance-tested

| Item | Reason |
|---|---|
| Real microphone capture | No browser automation / mic access in this environment |
| Acoustic barge-in | Not tested; server-side energy-gated cancel + browser playback cancel are implemented only |
| Live browser end-to-end session | UI builds but was not driven against the gateway |
| Exotel/PSTN call | Out of scope |
| Docker/MySQL runtime | Unavailable |

## 7. Success criteria (from Day 10 run)

* Ruff check + format: clean
* Gateway pytest: 350 passed, 4 skipped
* Live smoke: 4/4 passed
* Azure STT: exact final transcript, exactly once
* Azure TTS: valid PCM_L16 16 kHz mono, all 4 response types
* Gemini + Qdrant: grounded practice-scoped answer
* E2e loop: confirmation read-back + practice-info answer over WebSocket
* NestJS: 49 passed, build clean
* UI: lint + build clean

## 8. Security reminders

* Never print or log `AZURE_SPEECH_KEY`, `LLM_API_KEY`, `GEMINI_EMBEDDING_API_KEY`,
  `EXOTEL_API_KEY/_TOKEN`, or `QDRANT_API_KEY`.
* Never commit `.env`.
* Metrics/logs carry counts and latencies only — no transcript text, no audio
  payloads, no secrets.
* No real patient data; no clinical triage claims.
