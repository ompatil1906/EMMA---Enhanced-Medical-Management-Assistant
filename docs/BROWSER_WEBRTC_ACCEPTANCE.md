# Browser WebRTC Acceptance — EMMA Day 17

**Purpose:** Prove, in a **real browser**, that the EMMA browser voice path works
end to end through a **self-hosted** LiveKit server:

```
Browser UI → LiveKit room (WebRTC) → LiveKit agent worker → mock STT →
emergency guard → ConversationController → mock LLM → (Qdrant RAG, mock store) →
mock TTS → LiveKit room → browser playback
```

**Status:** VERIFIED in this environment — 8/8 automated browser scenarios pass,
green on five consecutive full runs (see §7). Verified with **mock** STT/TTS/LLM
and a **synthetic microphone**, not real telephony or real speech recognition.

**Cost:** Zero. No OpenAI / Deepgram / ElevenLabs / Gemini calls and no LiveKit
Cloud. LiveKit Server runs locally (open source, 1.13.11); every speech/LLM
provider is the deterministic mock.

**Security:** Never print, log, or commit credentials. `.env` is git-ignored;
only variable *names* appear in this document.

---

## 1. What this acceptance run is (and is not)

**Is:** an automated, repeatable acceptance test that drives the shipped browser
UI in Chromium (Playwright, full Chromium build with a working WebRTC stack),
against a real self-hosted LiveKit SFU, with a real LiveKit agent worker process
joining the room, and asserts both **UI-visible behaviour** and **server-side
ground truth** (a second LiveKit participant records what actually crossed the
room).

**Is not:** a claim about speech-recognition accuracy, synthesis naturalness, or
telephony. The mock STT derives transcripts from *audio duration*, never from
content (`app/speech/mock_stt.py`). The mock TTS emits a 240 Hz tone, not speech
(`app/speech/mock_tts.py`). Everything below is honestly synthetic.

---

## 2. System under test

| Component | Runtime | Notes |
|---|---|---|
| LiveKit Server | `livekit-server` 1.13.11, `ws://127.0.0.1:7880` | self-hosted, `--dev` config, room `emma-voice` |
| Voice gateway | `uvicorn app.main:app` on `http://localhost:8000` | mint JWTs (`POST /api/voice/token`), readiness at `/health/ready` |
| Agent worker | `python -m app.integrations.livekit.agent dev` | LiveKit Agents worker; joins each new room as `agent-<job_id>` |
| Browser UI | Next.js dev on `http://localhost:3000` | `/voice` route, `NEXT_PUBLIC_VOICE_TRANSPORT=livekit` |
| Room probe (test-only) | `python -m tools.room_probe` | second participant; writes JSONL server-side evidence |
| Browser | Playwright + full Chromium | fake media device fed from a WAV fixture |

The gateway's readiness endpoint must report the mock pipeline before running:

```bash
curl -s http://localhost:8000/health/ready
# {"status":"ready","checks":{"provider_mode":"simulation","stt_provider":"mock",
#  "tts_provider":"mock","speech_provider_configured":true, ...}}
```

---

## 3. Test files

| Path | Role |
|---|---|
| `apps/ui/tests/e2e/webrtc-acceptance.spec.ts` | the acceptance suite (A–G) |
| `apps/ui/tests/e2e/helpers/harness.ts` | per-scenario isolation: agent worker restart, room purge, probe, browser |
| `apps/ui/tests/e2e/helpers/agent-worker.ts` | agent worker lifecycle (SIGTERM + wait for exit, port 8081 reuse) |
| `apps/ui/tests/e2e/helpers/probe.ts` | room probe spawner + JSONL reader (`waitFor`, `eventsOfType`) |
| `apps/ui/tests/e2e/helpers/browser.ts` | Chromium launcher (fake media, WAV-as-microphone, autoplay allowed) |
| `apps/ui/tests/e2e/helpers/env.ts` | loads `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` from the root `.env` |
| `apps/ui/tests/e2e/fixtures/silence.wav` | default synthetic microphone (near-zero amplitude) |
| `apps/ui/tests/e2e/fixtures/tone.wav` | barge-in microphone (amplitude-modulated tone) |
| `apps/voice-gateway/tools/room_probe.py` | server-side ground-truth participant (non-product tool) |
| `apps/voice-gateway/tools/room_purge.py` | deletes the room between scenarios (fresh agent dispatch) |

`apps/ui/playwright.config.js` ignores the Day 13/17 scratch specs
(`debug*.spec.ts`, `test_f_debug.spec.ts`, `livekit-browser.spec.ts`), so
`npx playwright test` runs exactly the acceptance contract.

---

## 4. Prerequisites

1. Root `.env` present (never committed) with LiveKit server credentials:
   `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`.
   `LIVEKIT_URL` for local testing is `ws://127.0.0.1:7880` — **never** the
   Exotel WebSocket URL.
2. LiveKit Server running on `127.0.0.1:7880`.
3. Gateway running with mock providers (`STT_PROVIDER=mock`, `TTS_PROVIDER=mock`,
   `LLM_PROVIDER=mock`, `VOICE_TRANSPORT=livekit`).
4. UI dev server running with
   `NEXT_PUBLIC_VOICE_GATEWAY_URL=http://localhost:8000` and
   `NEXT_PUBLIC_VOICE_TRANSPORT=livekit`.
5. Python venv at `apps/voice-gateway/.venv` (agent worker + tools use it).
6. Playwright Chromium installed: `cd apps/ui && npx playwright install chromium`.

The harness starts/stops the **agent worker itself** — do not run it manually
while the suite is running (two workers would compete for dispatch and port
8081).

---

## 5. Running the acceptance suite

```bash
cd apps/ui
npx playwright test webrtc-acceptance.spec.ts --reporter=list
```

Expected tail:

```
  8 passed (2.2m)
```

Runtime is ~15–25 s per scenario: a fresh agent worker + room + probe + Chromium
per test. `workers: 1` is required (the suite owns the single room name and the
worker port).

### 5.1 Why the suite is deterministic

Every scenario is isolated and every assertion has a server-side witness:

1. **Fresh agent worker** with the scenario's `MOCK_STT_SCRIPT` /
   `MOCK_STT_UTTERANCE_MS`. The previous worker is SIGTERMed and *awaited* so
   port 8081 and the LiveKit registration are free.
2. **Room purge** (`tools.room_purge`), so LiveKit creates a new room and
   dispatches a new agent job (rooms have `empty_timeout: 12s`).
3. **Probe joins before the browser**, so it observes the agent joining and the
   agent's audio/data from the first frame.
4. **Chromium with a synthetic microphone** (`--use-file-for-fake-audio-capture`
   + a full media stack), so mic permission and audio capture are real WebRTC.
5. **Scripted turns suppressed where not under test**: the mock STT advances on
   accumulated caller-audio *duration* (default `MOCK_STT_UTTERANCE_MS=1000`).
   Because the fake microphone streams continuously, scenarios that only test
   transport/UI pass `mockSttUtteranceMs: 60_000` (`noScriptedTurns`) so the
   session genuinely **settles** in `Listening` instead of oscillating
   LISTENING → PROCESSING → SPEAKING every second.
6. **Bounded dispatch retry**: LiveKit keeps a killed worker registered until
   its heartbeat expires (~14 s), and a job handed to that dead registration
   never joins. If the probe does not see an `agent-…` participant, the harness
   deletes the room, waits 5 s and re-creates it (max 3 attempts) so dispatch
   runs against the worker that is registered *now*. This repairs isolation —
   the test is not allowed to proceed without observing the agent, so no
   assertion is weakened.
7. **Retried, not tolerated, barge-in**: the probe re-sends the barge-in control
   message on each `state: speaking` until the agent echoes `barge-in`, because
   a single send can land before the pipeline enters SPEAKING (where
   `should_cancel_for_barge_in` is false).

---

## 6. Scenarios and assertions

UI contract used by every scenario: intake placeholders `Sarah` / `Jenkins` /
`7700 900077`, submit button **Call Now**, notice
`Connected to EMMA voice agent`, status text `Ready` / `Connecting…` /
`Listening` / `Processing` / `Speaking` / `Disconnected`, mute button title
toggling `Mute microphone` ↔ `Unmute microphone`, and **End Conversation**.
Participant identities: agent `agent-<job_id>`, browser caller
`emma-caller-<hex8>`, probe `emma-probe-<n>-<pid>` (probe audio is ignored by
the agent, which only binds `emma-caller-…`).

| # | Scenario | Asserted evidence |
|---|---|---|
| A | Startup | call connects; notice shown; UI settles to **Listening**; probe saw an `agent-…` participant; **zero** console/page errors |
| B | Auth / room connect | probe saw the caller (`emma-caller-…`) **and** the agent (`agent-…`) in the same room; token minted by the gateway (browser never sees API keys) |
| C | Mic transmission + WebRTC stats | probe observes `track_subscribed` for the caller's **audio** track; `RTCPeerConnection` exposes `getStats`/`getSenders` in the test Chromium |
| D | Agent audio → browser playback | probe observes audible (`nonzero_frames > 0`) agent frames in the room; in-page check finds the `<audio data-emma-remote-audio>` element with a live audio track on its `srcObject` and `paused === false` (not merely "published") |
| E | Full round trip | mock STT line 1 (`"What are your opening hours?"`) appears as a final transcript in the UI; the deterministic RAG-abstain answer ("I don't have information about that in the practice records…") appears; probe sees both `transcript` and `assistant` control events on the data channel |
| F1 | Mute / unmute / disconnect / recovery | button title toggles `Mute microphone` ↔ `Unmute microphone`; **End Conversation** returns to the intake form; a second call re-connects and settles to Listening (fresh room + dispatch) |
| F2 | Invalid token | the token endpoint is intercepted with a bogus JWT; the call must not stay in `Connecting…`; the 401/Unauthorized failure is surfaced (console error captured), never silently swallowed |
| G | Software barge-in | probe sends `{"event":"barge-in"}` while the agent is SPEAKING; the agent echoes `barge-in` on the data channel (cancellation accepted) and the UI leaves **Speaking** and returns to **Listening** |

Barge-in is **data-level, never acoustic**: the browser monitor trips at a local
mic RMS ≥ 0.12 for 2 consecutive 60 ms ticks and publishes the same control
message the probe sends. `Test G` uses `tone.wav` (amplitude-modulated) so the
browser's own detector also fires; the agent's
`pipeline.should_cancel_for_barge_in` gate decides whether cancellation applies.

### 6.1 Emergency guard (unchanged, still enforced)

The emergency guard runs **before** any LLM/RAG call. Inputs such as
"chest pain", "heart attack", "stroke", "999" produce the fixed script
`This may be a medical emergency. Please hang up and call 999 now.` with no
administrative action, no practice-API call, and no duplicate request. Day 17
does not weaken or bypass it; the browser path reuses the same pipeline.

---

## 7. Results (this environment)

Five consecutive full-suite runs green on real Chromium + real self-hosted
LiveKit (three before the dispatch-retry hardening, two after):

| Run | Result |
|---|---|
| 1 | 8 passed (2.2 m) |
| 2 | 8 passed (2.2 m) |
| 3 | 8 passed (2.3 m) — A 15.2 s, B 13.1 s, C 18.4 s, D 18.6 s, E 22.4 s, F1 19.3 s, F2 12.9 s, G 18.2 s |
| 4 (after §5.1 item 6) | 8 passed (2.8 m) — F1 42.4 s: the dispatch retry fired once on the second call inside that test |
| 5 | 8 passed (2.4 m) — A 14.8 s, B 13.4 s, C 19.4 s, D 19.8 s, E 22.9 s, F1 19.9 s, F2 14.4 s, G 18.6 s |

(The intermediate run that failed scenario A with *agent participant never
joined the room* is what exposed the stale-registration race fixed in §4 item 9
of `docs/architecture/day-17-engineering-report.md`; the retry was added rather
than weakening the assertion.)

Regression alongside this work:

| Gate | Result |
|---|---|
| `pytest` (apps/voice-gateway) | **440 passed, 4 skipped** |
| `ruff check app tests tools` | clean |
| `ruff format --check app tests tools` | 137 files formatted |
| `pnpm lint` (apps/ui) | 0 errors (3 warnings, scratch specs only) |
| `pnpm build` (apps/ui) | compiled + type-checked; routes `/`, `/voice` |
| `pnpm test` (apps/practice-api) | **8 suites, 49 tests passed** |

---

## 8. Debugging the acceptance suite

Server-side truth is written as one JSON object per line:

| Field | Meaning |
|---|---|
| `probe_connected` | probe joined the room |
| `participant` | participant identity joined/left |
| `track_published` / `track_subscribed` | track name/kind, plus `isAudio` on subscription |
| `data` | control event received: `data_event` = `transcript` / `assistant` / `state` / `barge-in` (+`text`, `state`) |
| `audio_progress` | every 100 frames **and on the first audible frame**: `frames`, `nonzero_frames`, `rms`, `peak_rms`, `rate` |
| `barge_in_sent` | probe published a barge-in control message |
| `probe_summary` | totals at exit (`audio_frames`, `nonzero_audio_frames`, `agent_identity`) |

Probe logs live in the system temp directory as
`emma-probe-<pid>-<n>-<timestamp>.jsonl` (unique per test *and* per Playwright
worker process, so a restarted worker can never interleave writes into another
test's evidence). The agent worker log is `emma-agent-suite.log` in the same
temp directory and is truncated per worker start.

```bash
# Follow the live agent worker log (names only — never dump .env)
tail -f "$TMPDIR/emma-agent-suite.log"
```

### Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `agent participant never joined the room after 3 attempts` | agent worker crashed, or LiveKit still dispatches to a dead registration | read `emma-agent-suite.log`; confirm the worker printed `registered worker`; re-run (the harness retries dispatch 3×) |
| `Listening` never appears | a scripted turn keeps firing (mock STT default is 1 s) | pass `mockSttUtteranceMs: 60_000` for transport-only scenarios |
| `probe never connected` | LiveKit server down or token secret mismatch | `nc -z 127.0.0.1 7880`, restart server and gateway |
| `track_subscribed` never for `emma-caller-…` | microphone permission/fake device missing | launch Chromium with `--use-fake-ui-for-media-stream` + `--use-file-for-fake-audio-capture` |
| Test D: `<audio>` present but paused | autoplay blocked | keep `--autoplay-policy=no-user-gesture-required` (the harness sets it) |
| Barge-in test times out | agent was not SPEAKING when the message arrived | the probe retries on each `state: speaking` until the agent echoes `barge-in` |
| `Failed to construct 'MediaStream'` in console | a LiveKit `RemoteAudioTrack` was passed to `new MediaStream` | use the SDK's `track.attach()` (fixed in `livekit-transport.ts`) |

---

## 9. What is NOT verified (do not claim otherwise)

* **Real speech recognition** — the mock STT is duration-driven; no audio is
  decoded. Transcript accuracy is not measurable here.
* **Real speech synthesis** — the mock TTS emits a 240 Hz tone (~ −18 dBFS).
  Playback is verified; naturalness is not.
* **Real LLM reasoning** — the mock LLM returns deterministic strings; RAG
  abstains because the mock vector store is empty.
* **Acoustic barge-in from a human voice** — barge-in is asserted at the data
  level only.
* **PSTN / Exotel / SIP telephony** — out of scope.
* **Production providers** (Deepgram, ElevenLabs, OpenAI, Gemini) — configured
  by name only; no keys are used, so no paid or live-provider path is exercised.
* **Load / multi-room concurrency** — the suite is serial by design.
* **Latency SLOs** — wall-clock timings above include per-test process startup;
  they are not speech-end→first-audio measurements.

Live-provider, real-microphone and real-telephony acceptance still require a
separate run with real credentials and a human speaker.

---

# Appendix — Day 18E-A: "connects but never answers" diagnosis

**Status:** root cause identified and fixed (root cause was *operational*, plus a
UI truthfulness defect). Mock-only round trip re-verified: **11/11 browser
scenarios green** in an isolated stack with a throw-away LiveKit keypair.
No paid provider was contacted.

## 1. What the user saw

The browser connected, the status showed the call was up, and no assistant
reply ever arrived.

## 2. Root cause

`pgrep -f app.integrations.livekit.agent` → **0 processes**; agent HTTP port
8081 closed. The LiveKit room existed and the browser was in it, but **no EMMA
agent worker was running**, so nothing ever subscribed to the caller's audio,
transcribed it, or synthesised a reply.

A browser↔LiveKit connection is a *transport* success only. The EMMA voice
session additionally needs: `livekit-server` → gateway (token minting) → **agent
worker** (`python -m app.integrations.livekit.agent dev`) → mock/live providers.

The second, code-level defect: the UI announced **"Connected to EMMA voice
agent"** the moment the *room* connected, with no check that an agent
participant existed. That is why the failure looked like a hang rather than an
error.

Contributing environment facts (all confirmed): the running gateway predated
Day 18D (its `/health/ready` had no `attestation` block), `.env` selects
`azure/azure/gemini`, and the running LiveKit server still used the previously
exposed development keypair — so the "live" configuration was neither mock nor
safe.

## 3. Fix (smallest change, no architecture change)

`apps/ui/src/lib/voice/livekit-transport.ts`:

| Before | After |
|---|---|
| `connected` → `setState("listening")` + "Connected to EMMA voice agent" unconditionally | agent presence is checked (`agent-…` / `emma-agent-…` identity) before claiming readiness |
| no agent case | `connecting` + "Connected to EMMA — waiting for the voice assistant to join…" |
| agent joins later | `participantConnected` promotes the session to `listening` + the ready notice |
| agent leaves mid-call | `participantDisconnected` → `error` state + "The voice assistant disconnected before finishing…" |

Also added, dev-only and non-secret: `window.__emmaVoiceDiag`
(`attached`, `playing`, `frames`, `peakRms`, `lastRms`) sampled from an
`AnalyserNode` on the remote track, so browser playback can be asserted by
**measured audible samples** instead of "an `<audio>` element exists".

`apps/voice-gateway/app/core/config.py`: a blank flag placeholder
(`EMMA_LIVE_CREDENTIALS_ROTATED=`) now means *off* instead of aborting startup
with an opaque pydantic bool error (hit while building the isolated stack).

## 4. Tests added

`apps/ui/tests/e2e/voice-no-response.spec.ts` (3 scenarios, mock providers):

| Scenario | Assertion |
|---|---|
| A — room connected, no agent | shows "waiting for the voice assistant", never the ready notice, never `Listening`/`Speaking`; probe confirms caller present and **no** `agent-…` |
| B — microphone denied | actionable microphone error, no `Listening`, no ready notice |
| C — agent leaves mid-call | degraded/disconnected message within 30 s, no stale `Listening` |

`webrtc-acceptance.spec.ts` test D now also asserts
`__emmaVoiceDiag.peakRms > 0` and `frames > 0` (measurable browser audio).

## 5. Isolated-stack procedure used (no `.env`, no shared keypair)

1. Generate an ephemeral LiveKit keypair **in memory**; pass it to the server via
   `LIVEKIT_KEYS` (not argv) and to the gateway/agent/harness via the process
   environment (the harness now prefers `LIVEKIT_*` from the environment over
   `.env`, matching the gateway's own precedence).
2. `livekit-server --config <temp yaml>` on alternate ports (WS 17880, UDP 17882).
3. Gateway: `uvicorn app.main:app --port 18000` with
   `VOICE_TRANSPORT=livekit`, `STT_PROVIDER=mock`, `TTS_PROVIDER=mock`,
   `LLM_PROVIDER=mock`, `EMMA_LIVE_GUARD_ENABLED=0`, `EMMA_RUN_LIVE_TESTS=0`.
4. UI: a temporary copy of `apps/ui` (source + `cp -al node_modules`, so the
   developer's `next dev` lock and `.next` are untouched) on port 13000 with
   `NEXT_PUBLIC_VOICE_GATEWAY_URL=http://localhost:18000`.
5. `PLAYWRIGHT_BASE_URL=http://localhost:13000 npx playwright test …`.
6. Tear everything down; the keypair dies with the processes.

Next 16 refuses a second dev server per directory, and Turbopack mis-handles a
symlinked `node_modules` — hardlink-copying `node_modules` into a temp copy is
the working combination.

## 6. Cleanup backlog additions

* `voice-screen.tsx` still derives the visible status from a single `VoiceState`;
  a first-class `agentPresent` field would make the diagnostics panel trivial.
* `window.__emmaVoiceDiag` is a global; move it behind a dev flag or a
  `VoiceTransport.diagnostics()` accessor when the panel is built.
* The isolated-stack recipe (temp keypair + UI copy) should become a documented
  script rather than an ad-hoc orchestrator.
* `tests/e2e/helpers/harness.ts` now has two room-join paths (with/without
  agent); fold them into one parameterised function.

---

# Appendix 2 — Day 18E-A (cont.): why the agent worker never started

## The second root cause (a real bug)

`livekit.agents.AgentServer.run()` reads `LIVEKIT_URL`, `LIVEKIT_API_KEY` and
`LIVEKIT_API_SECRET` from **`os.environ`**, but `main()` never populated them.
The documented start command therefore died instantly:

```
$ python -m app.integrations.livekit.agent dev
ValueError: ws_url is required, or set LIVEKIT_URL environment variable
```

Nothing in the UI or gateway reported this: the browser simply joined a room
that never had an agent — "connects but never answers".

**Fix** (`app/integrations/livekit/agent.py`): `seed_livekit_env(settings)`
copies the three values from the app's `Settings` (`.env` or process env) into
`os.environ` before the worker starts, never logs them, and lets an explicit
process override win. `main()` also fails with a clear message when
`LIVEKIT_URL` is genuinely absent. Regression tests:
`tests/unit/test_livekit_agent.py::test_seed_livekit_env_*`.

## Real-port verification (not the isolated stack)

Real services on their usual ports — LiveKit `127.0.0.1:7880`, gateway `:8000`
(mock providers), Next UI `:3000`, and a **persistent agent worker** started
with the documented command — driven by a real Chromium:

| Evidence | Value |
|---|---|
| Agent participates / UI notice | `Connected to EMMA voice agent` ✅ |
| Remote track attached | `__emmaVoiceDiag.attached = true`, `playing = true` |
| Measured browser audio | `peakRms = 0.0763`, `frames 5 → 142` |
| Greeting (mock TTS) | "Hello, this is the practice's automated assistant. How can I help you today?" |
| Mock STT transcript | "I would like to book an appointment" |
| Mock LLM reply (spoken) | "Please tell me your preferred date and time. Could you tell me more?" |
| Console errors | none |

## Starting the real stack (mock providers)

```bash
# 1. LiveKit (self-hosted)
livekit-server --dev --bind 127.0.0.1 --node-ip 127.0.0.1

# 2. Gateway (current code)
cd apps/voice-gateway
STT_PROVIDER=mock TTS_PROVIDER=mock LLM_PROVIDER=mock VOICE_TRANSPORT=livekit \
  .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 3. Agent worker — the step that was silently missing
cd apps/voice-gateway
STT_PROVIDER=mock TTS_PROVIDER=mock LLM_PROVIDER=mock VOICE_TRANSPORT=livekit \
  .venv/bin/python -m app.integrations.livekit.agent dev

# 4. UI
cd apps/ui
NEXT_PUBLIC_VOICE_GATEWAY_URL=http://localhost:8000 \
NEXT_PUBLIC_VOICE_TRANSPORT=livekit pnpm dev
```

A healthy worker logs `registered worker` and listens on `:8081`. If the UI says
"waiting for the voice assistant to join…", step 3 is missing or crashed.
