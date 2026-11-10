# Voice troubleshooting

Start here: `GET /health/ready` and `GET /api/v1/diagnostics` on the gateway
(`:8000`). Logs are structured JSON on stderr.

## 1. No audio in the browser

| Check | Command / place | Expected |
|---|---|---|
| UI transport | `apps/ui/.env` → `NEXT_PUBLIC_VOICE_TRANSPORT` | `livekit` for WebRTC |
| Gateway URL | `NEXT_PUBLIC_VOICE_GATEWAY_URL=http://localhost:8000` | matches the running gateway |
| Token endpoint | `POST /api/voice/token` with `{room_name, caller}` | 200 + `livekit_url` |
| LiveKit server | `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:7880` | 200 |
| Agent worker | log line `registered worker` | present |
| Diagnostics | `/api/v1/diagnostics → providers.*` | provider names as intended |

The agent only starts a session when an `emma-caller-*` participant joins; a room
with no caller is silent by design.

## 2. EMMA does not answer / answers the previous question

* `/api/v1/diagnostics → pipeline.turn_assembly_enabled` — if `true`, a final that
  looks mid-sentence **and** arrives with fresh partials is held for
  `TURN_HOLD_MS` then released. That is expected for long utterances.
* If answers lag by one turn, check for `turns_dropped` with reason
  `stale_sequence` (out-of-order finals) in the logs.
* If a caller's quiet voice never triggers: lower `VAD_START_ENERGY_DB` (e.g.
  -40) or raise `VAD_MIN_SPEECH_MS` if the room is noisy.

## 3. EMMA interrupts itself

* Raise `VAD_ECHO_GUARD_DB` (6 → 10).
* Confirm the browser reports `echoCancellation: true` (LiveKit requests it).
* Last resort: `VAD_BARGE_IN_ENABLED=false` — transcript-driven barge-in still
  works, only ~STT-endpointing later.

## 4. Stale audio keeps playing after an interruption

Should be impossible (`_interrupt_in_flight` flushes before cancelling the turn).
If it happens:
* check `provider_errors` for `tts:synthesis_error` (a provider that ignores
  cancellation);
* verify the browser transport received `{"event":"barge-in"}`;
* confirm the client-side player stops on that event.

## 5. Everything sounds like a tone (mock providers)

`STT_PROVIDER=mock TTS_PROVIDER=mock` is the offline demo mode: TTS emits a
synthetic waveform and STT is duration-driven. Text and state are real; audio is
not. Switch to Deepgram/ElevenLabs (with attestations) for real speech.

## 6. Practice API / summary / Jira

| Symptom | Cause | Action |
|---|---|---|
| `call record not registered; call summary will be skipped` | practice API down or `PRACTICE_ID` unknown | start `apps/practice-api`, verify the seeded practice id |
| summary row missing | emergency-guarded call | by design — zero actions on emergency |
| `jira.status = failed` | bad credentials / project / issue type | `docs/integrations/jira.md`; the summary row is still stored |
| `jira.mode = disabled` | `JIRA_MODE` unset | set `JIRA_MODE=mock` (offline) or `live` + credentials |

## 7. Port conflicts

EMMA expects: UI `3001`, gateway `8000`, LiveKit `7880`, practice API `3000` (or
any port with `PRACTICE_API_BASE_URL` overriding the gateway's). If `3000` is
taken by another project, run the practice API elsewhere and pass
`PRACTICE_API_BASE_URL=http://localhost:<port>` to the gateway.

## 8. Test failures that are environmental

`test_health_live_guard.py::test_guard_enabled_but_mock_providers_stay_ready`
assumes mock providers, but the repo `.env` selects live ones. Run it with
`STT_PROVIDER=mock TTS_PROVIDER=mock LLM_PROVIDER=mock` (6 passed).
