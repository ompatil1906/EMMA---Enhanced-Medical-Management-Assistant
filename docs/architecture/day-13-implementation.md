# Day 13 — Implementation

**Report date:** 2026-11-08 (Day 13 close-out).
**Git branch:** `main`
**Git HEAD:** `3d40b9409b635623f22ce1cd0220f1f23f647572`
**Status:** All work uncommitted; no commits or pushes made.
**Scope:** Browser voice interface integration — extend the existing Next.js UI with
LiveKit WebRTC transport, microphone capture, assistant audio playback, and
control event rendering. Preserves all Day 1–12 work.

---

## 1. What changed

### 1.1 `apps/ui/src/lib/voice/livekit-transport.ts` (new)

LiveKit WebRTC transport implementing the `VoiceTransport` interface:

* Connects to LiveKit room via JWT token from `/api/voice/token`
* Captures microphone audio via `getUserMedia` and publishes to LiveKit room
* Subscribes to assistant audio track and plays via `<audio>` element
* Renders `transcript`, `assistant`, `state` control events from data messages
* Browser barge-in: mic level threshold → stop mic, publish `barge-in` control event
* Dynamic import of `livekit-client` — imports cleanly without the package
* Reports errors if WebSocket transport or LiveKit client is unavailable

### 1.2 `apps/ui/src/components/voice/voice-screen.tsx` (modified)

Transport selection based on `VOICE_TRANSPORT` env var:

* `VOICE_TRANSPORT=websocket` (default) — existing FastAPI WebSocket path
* `VOICE_TRANSPORT=livekit` — new LiveKit WebRTC path

### 1.3 `apps/ui/src/lib/voice/types.ts` (modified)

Updated transport docstring to document the LiveKit transport option.

### 1.4 `apps/ui/package.json` (modified)

Added `livekit-client@^2.0.0` as a dependency.

### 1.5 `apps/ui/package-lock.json` (modified)

Updated lockfile with `livekit-client` dependency.

---

## 2. What was preserved (unchanged)

| Component | Status |
|---|---|
| FastAPI WebSocket `/ws/exotel` | Preserved — fallback transport |
| Azure Speech STT/TTS | Preserved — fallback provider |
| Gemini `gemini-3.8-flash` | Preserved — fallback LLM |
| Deterministic ConversationController | Preserved — single source of truth |
| Emergency guard | Preserved — runs before all providers |
| VoicePipeline | Preserved — transport-agnostic orchestrator |

---

## 3. What was NOT implemented (no browser automation available)

| Item | Reason |
|---|---|
| Live browser WebRTC session | No Playwright/browser automation installed |
| Browser audio playback | Not tested — no browser automation |
| Microphone capture in browser | Not tested — no browser automation |
| Acoustic barge-in | Not tested — no real microphone |

The `livekit-transport.ts` is implemented and type-checks, but has not been executed
in a real browser.

---

## 4. Test results

| Suite | Result |
|---|---|
| Python gateway | 401 passed, 4 skipped |
| NestJS practice API | 49 passed, build clean |
| Frontend UI build | Clean |
| Ruff | Clean |
| Ruff format | Clean |

No new Playwright E2E tests were successfully executed due to browser automation
limitations in this environment.

---

## 5. Known limitations

| Limitation | Detail |
|---|---|
| Browser automation | Playwright not installed; `playwright.config.ts` exists but tests not executed |
| Live browser WebRTC session | Not tested — no browser automation |
| Microphone capture | Not tested — no browser automation |
| Audio playback | Not tested — no browser automation |
| Acoustic barge-in | Not tested — no real microphone |

---

## 6. Deliverables status

| Deliverable | Status |
|---|---|
| `apps/ui/src/lib/voice/livekit-transport.ts` | Implemented, type-checks, build clean |
| `apps/ui/src/components/voice/voice-screen.tsx` | Updated with transport selection |
| `apps/ui/src/lib/voice/types.ts` | Updated docstring |
| `apps/ui/package.json` | Added `livekit-client@^2.0.0` |

---

## 7. Environment variables

| Variable | Purpose | Example |
|---|---|---|
| `NEXT_PUBLIC_VOICE_GATEWAY_URL` | Voice gateway URL | `http://localhost:8000` |
| `NEXT_PUBLIC_VOICE_TRANSPORT` | Transport selection | `websocket` (default) or `livekit` |

No credentials reach the browser. LiveKit tokens are minted server-side via
`/api/voice/token`.
