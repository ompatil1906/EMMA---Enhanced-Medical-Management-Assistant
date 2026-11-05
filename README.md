# EMMA — Enhanced Medical Management Assistant (QuantumLoopAI assignment)

A 10-day technical assignment: prototype an EMMA-style AI voice receptionist
for NHS GP practices — an Exotel-compatible voice gateway (Day 2 today), with
streaming STT → LLM → TTS and a practice backend/dashboard following on Days
3–9. Day-1 research lives in `*(research docs)*.md` at the repo root.

> **Prototype ≠ production service.** This repository is a technical prototype
> for an assignment evaluation. It is NOT an approved NHS system, NOT a medical
> device, provides NO clinical advice, and must never process real patient
> data. QuantumLoopAI's regulated production system is a separate, certified
> effort — don't conflate the two.

## Current scope (Day 2 — engineering foundation & echo path)

Working today:

* FastAPI voice gateway with `/ws/exotel` (Exotel AgentStream-compatible media
  streaming) and structured `/health/live` + `/health/ready` endpoints.
* Provider-independent domain events (`CallStarted`, `AudioReceived`,
  `CallEnded`, `ConnectionClosed`, `AudioToSend`); Exotel specifics isolated in
  an adapter (schema tolerance to field drift, strict audio negotiation).
* Local **echo** service: byte-for-byte audio frame return (no transcode).
* Local WebSocket **simulator** that plays the provider role — no phone call,
  no Exotel account, no paid APIs.
* 105 automated tests (unit + real-uvicorn integration incl. close-code
  semantics), dockerised one-command runtime, protocol verification doc.

Not yet (by design): STT, LLM, RAG, TTS, booking, dashboard, real-call proof.

## Architecture snapshot

```
phone → Exotel (PSTN bridge) ──WSS──► /ws/exotel ─► Exotel adapter
                                                        │  (domain events)
                                                        ▼
                    /health/*                      echo service ─► AudioToSend
                     ▲                                        │  (adapter back)
                     └── FastAPI app (lifespan, registry) ◄───┘
```

Day-2-only. The echo service is the seam where VAD → STT → LLM → TTS lands on
Day 3+. Diagrams & policies: `docs/architecture/day-2-implementation.md`.

## Tech stack

Python 3.13 · FastAPI · Uvicorn · Pydantic v2 · pydantic-settings · pytest +
pytest-asyncio (async suite against real uvicorn) · `websockets` (tests &
simulator) · Docker Compose. Dependency workflow: **uv** (locked `uv.lock`).

## Repository layout

```
apps/voice-gateway      the gateway service (app code, tests, Dockerfile)
packages/contracts      cross-service contract placeholder (Day 3+)
infrastructure/docker   gateway-only compose file
docs/                   protocol verification · architecture · runbook
scripts/                websocket_simulator.py (provider-role client)
apps/…research docs…    Day-1: REQUIREMENTS · ADRs · evaluation plan · risks
```

## Quickstart

```bash
cd apps/voice-gateway
uv sync --all-groups                    # venv + locked deps
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000   # gateway
# second shell
uv run python ../../scripts/websocket_simulator.py --frames 10
# → frames=10/10 matched byte-for-byte, closed cleanly (1000)
```

Health checks: `curl http://127.0.0.1:8000/health/live` and `/health/ready`.

## Environment

```bash
cp .env.example .env   # optional; simulation defaults need no credentials
```

Key switches: `EXOTEL_MODE=simulation|live` · `WS_AUTH_TOKEN` (optional
simulator auth) · `EXOTEL_API_KEY/_TOKEN` (live mode only) · WS hardening
limits (see `.env.example`). Secrets stay out of git.

## Tests / lint

```bash
cd apps/voice-gateway
uv run pytest && uv run ruff format . && uv run ruff check .
```

## Docker

```bash
cd infrastructure/docker
docker compose up -d && curl http://127.0.0.1:8000/health/ready
docker compose down
```

## Documentation index

| Doc | Contents |
|---|---|
| `docs/integrations/exotel-protocol.md` | verified protocol facts (URLs + dates) vs gateway policy vs unknowns |
| `docs/architecture/day-2-implementation.md` | components, lifecycle, adapter boundary, Day-3 seam |
| `docs/runbooks/local-development.md` | exact commands incl. troubleshooting |
| `docs/integrations/exotel-onboarding-checklist.md` | KYC/applet/URL config → first real call |
| `REQUIREMENTS.md` / `ARCHITECTURE_DECISION_RECORD.md` / `EVALUATION_PLAN.md` / `RISKS_AND_ASSUMPTIONS.md` | Day-1 research & decisions |

## Current limitations

* **Echo of the caller's voice** is the entire audio "brain" (by design today).
* No real PSTN call has been attempted: Exotel trial/KYC is pending — the
  protocol doc separates verified facts from that open action; simulate first.
* `clear` barge-in is protocol-verified and documented but not wired (no
  playback queue exists yet).
* Health probes, auth gates, close-code policy are ours (assignment policy),
  not Exotel-mandated behaviour — explicitly labelled in the protocol doc.
* Test audio is synthetic (deterministic sine chunks); no recordings, no PII.
