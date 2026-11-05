# Exotel AgentStream WebSocket protocol — verified facts & gateway behaviour

**Verification date: 2026-11-06.** Every claim in Section 1 was read directly
from official Exotel documentation on that date (links listed per claim).
Anything not demonstrably documented is marked **unknown/assumption**; protocol
knowledge is deliberately isolated to `apps/voice-gateway/app/integrations/exotel/*`.

> Summary: Exotel's voice streaming runs over a WebSocket connection with JSON
> control events and base64-encoded linear PCM audio. The gateway acts as the
> WebSocket **server**; the Exotel platform connects **out** to our endpoint and
> bridges the PSTN call audio into that stream.

---

## 1. Verified protocol behaviour (primary sources)

### 1.1 Connection, lifecycle, control events

* **[V1] Connection direction.** Exotel opens a WebSocket connection to the
  bot's configured `StreamUrl` — the bot is the WSS **server**.
  Source: <https://github.com/exotel/Agent-Stream/blob/main/docs/AGENTSTREAM_WSS_PROTOCOL.md>
* **[V2] `connected` event** is sent by Exotel to the bot when the WebSocket is
  accepted. Source: <https://developer.exotel.com/docs/agentstream/websocket-protocol>
* **[V3] `start` event** carries stream/call metadata —
  `stream_sid`, `call_sid`, `account_sid`, `from`, `to`, `custom_parameters` —
  plus nested `media_format {encoding, sample_rate, bit_rate}`.
  Source: websocket-protocol docs + <https://developer.exotel.com/docs/agentstream/stream-voicebot-applet>
* **[V4] `media` events** (both directions) carry a base64 `chunk` and a `timestamp`.
  Source: websocket-protocol docs.
* **[V5] `stop` event** ends the stream: `call_sid`, `account_sid`, `reason` ∈
  {`stopped`, `callended`}. Source: websocket-protocol docs.
* **[V6] `dtmf {digit, duration}` and `mark {name}`** are documented as
  applet-dependent events (may appear; behaviour varies by applet).
  Source: websocket-protocol docs.
* **[V7] Bot→Exotel responses (Voicebot applet):**
  * `media` — exactly **3 top-level fields**: `{event, stream_sid, media:{payload}}`
  * `mark` — `{event, stream_sid, mark:{name}}`
  * `clear` — `{event:"clear", stream_sid}` — the documented queue-flush
    (barge-in) mechanism. NOT an invention by this project.
  Source: stream-voicebot-applet + AgentStream docs.
* **[V8] Field-name tolerance.** Exact field names can vary slightly across
  applets/Connect paths; integrations should tolerate missing optional fields.
  Source: AgentStream WSS protocol doc.
* **[V9] Authentication.** `Authorization: Basic base64(API_KEY:API_TOKEN)` on
  the WebSocket handshake.
  Source: developer.exotel.com authentication reference.
* **[V10] Sample rate via URL query.** `?sample-rate=16000` (also `8000`,
  `24000`) on the connection URL.
  Source: AgentStream WSS protocol docs.

### 1.2 Audio encoding (verified)

* **[V11]** Raw **linear 16-bit PCM**, signed little-endian, **mono** (not μ-law).
* **[V12]** Default sample rate **8 kHz**; supported set **8000 / 16000 / 24000** via `?sample-rate=`.
* **[V13]** Payload rides **base64** inside `media.chunk` (JSON **text** frames).
* **[V14]** Documented chunk guidance: **3,200–100,000 bytes**; a session caps at
  **60 minutes**.

### 1.3 Sources (all fetched 2026-11-06)

1. websocket-protocol — <https://developer.exotel.com/docs/agentstream/websocket-protocol>
2. voicebot applet — <https://developer.exotel.com/docs/agentstream/stream-voicebot-applet>
3. AgentStream WSS protocol (GitHub) — <https://github.com/exotel/Agent-Stream/blob/main/docs/AGENTSTREAM_WSS_PROTOCOL.md>
4. ExoC applet config — <https://docs.exotel.com/exotel-agentstream/voicebot-applet>
5. Trial account & KYC — <https://docs.exotel.com/business-phone-system/create-a-trial-account>

Also reviewed the same day: `github.com/exotel/Agent-Stream-echobot` (reference
echobot) and `github.com/exotel/voice-streaming` (Exotel's client simulator).

---

## 2. Gateway behaviour — OUR Day-2 policy (not Exotel rules)

Enforced in `app/api/routes/websocket.py` and `app/integrations/exotel/*`,
with corresponding adversarial tests. Close codes are RFC 6455 codes we chose:

| Situation | Gateway behaviour | Resulting close |
|---|---|---|
| Unsupported sample rate / encoding in `start.media_format` | Strict negotiation rejects the stream (no guessing). `media_format` absent → session keeps its default (8 kHz or `?sample-rate=` value). | 1008 `unsupported media format` |
| Invalid `?sample-rate=` query (non-numeric or outside 8000/16000/24000) | Reject at handshake time. | 1008 `unsupported sample rate` |
| Authentication failure | Simulation mode: optional `X-WS-Token` (unset = open local dev). Live mode: verified Basic header (V9). | 1008 `authentication failed` |
| Malformed JSON · missing `event` · non-text frame · invalid base64 · decoded chunk > `EXOTEL_MAX_CHUNK_BYTES` | Tolerated up to `MAX_CONSECUTIVE_ERRORS` (default 5); any valid frame resets the counter. | 1003 `protocol error tolerance exhausted` after budget |
| Single WS message larger than `MAX_WS_MESSAGE_BYTES` (default 256 KiB) | Hard reject, no tolerance. | 1009 `message size limit exceeded` |
| Unknown but well-formed `event` | Logged (debug only), session continues. | — |
| Provider `stop` (either reason) | Cleanup, then a normal close we initiate. | 1000 `stop` |
| Client disconnects without `stop` | Session registry cleanup guaranteed in `finally` (observable via `/health/ready`). | — |
| Unexpected internal error | Best-effort close; session always unregistered. | 1011 `internal error` |
| `MAX_CONCURRENT_SESSIONS` reached (default 200) | Refuse the new session. | 1013 `session limit reached` |

Ordering contract: every inbound `media` is answered with exactly one outbound
`media` envelope **before** the next inbound frame is processed (strict
ping-pong, verified by an ordering integration test).

Audio math: `media.chunk` → `AudioReceived.payload` → `AudioToSend.payload` →
outbound `media` envelope. **Byte-for-byte echo; no transcode, no resample.**
Sequence/order metadata is kept in session counters.

Documented deviation (deliberate): the verified outbound chunk **minimum**
(3,200 bytes, [V14]) is NOT enforced inbound — our 20 ms test frames (320 B @
8 kHz) are intentionally sub-minimum to exercise the transport. This is
metadata-only today; the STT/TTS pipeline will impose real chunk-size policy.

---

## 3. Simulation mode vs Exotel mode

| Aspect | `EXOTEL_MODE=simulation` | `EXOTEL_MODE=live` |
|---|---|---|
| Auth mechanism | `X-WS-Token` header (optional; unset = open local dev) | `Authorization: Basic base64(API key:token)` per [V9] |
| Connecting side | tests / `scripts/websocket_simulator.py` | Exotel platform (unverified until onboarding) |
| Event flow emitted | connected → start → media×N → stop | start → media×N → stop (the platform owns `connected`) |
| What success proves | transport + adapter + echo loop work locally | **NOT proven yet** — requires a real inbound Exotel call |

**Guardrail:** a green suite/simulator run never implies a working PSTN call.
The onboarding checklist must be executed and a real inbound call must be
visible in gateway logs before any such claim.

---

## 4. Unknowns / open questions

1. **Subdomain configuration** — `EXOTEL_SUBDOMAIN` was dropped from Day-2
   settings on purpose: it is only needed when WE open connections to Exotel
   (no outbound API use exists yet). The [V1] direction (Exotel → bot) needs
   only our public WSS URL configured in the applet.
2. **Additional stream validation** — whether Exotel applies extra
   verification beyond the Basic header ([V9]) on the connections it opens is
   **unverified**. Documented as an onboarding blocker, not assumed.
3. **DTMF / mark payloads on our applet** — tolerated and logged, never
   depended upon (see [V6]); first real stream will confirm shapes.
4. **`media_format.encoding` spellings** emitted by the live backend
   ("PCM" vs "linear16" vs others) — unknown; the adapter normalises known
   aliases and rejects unknown encodings strictly. First real call decides.
5. **Chunk-size behaviour in live streaming** — 3,200–100,000 B ([V14]) is
   guidance; actual per-frame sizes on a real call are unmeasured until
   onboarding completes.

Implementation rule (do-not-invent guardrail): every field implemented in
`app/integrations/exotel/schemas.py` must trace to Section 1; unknowns stay
unknown (logged, tolerated, never fabricated).
