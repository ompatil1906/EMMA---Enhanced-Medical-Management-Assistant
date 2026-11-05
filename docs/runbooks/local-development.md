# Local development runbook — EMMA voice gateway (Day 2)

Every command below was executed as written on macOS 27.2, Python 3.13.5,
uv 0.9.x. Working directory notes first.

## 0. Prerequisites

```sh
python3 --version   # 3.13.x expected (3.13.5 used)
command -v uv || echo "install uv: https://docs.astral.sh/uv/"
```

No paid APIs, no microphone, no Exotel account needed for anything below —
the default mode is `simulation`.

## 1. Install dependencies (one-time)

```bash
cd apps/voice-gateway
uv sync --all-groups          # creates .venv from uv.lock (incl. dev deps)
```

## 2. Environment setup (optional)

```bash
cp ../../.env.example ../../.env    # adjust if you need auth/limits
# defaults: EXOTEL_MODE=simulation · WS_AUTH_TOKEN unset → open local dev
```
Settings read `../../.env` or local `.env` (app dir) plus real environment
variables (env wins). Never commit `.env`.

## 3. Start the gateway

```bash
cd apps/voice-gateway
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Checks:

```bash
curl -s http://127.0.0.1:8000/health/live
# {"status":"alive","uptime_seconds":…}
curl -s http://127.0.0.1:8000/health/ready
# {"status":"ready","checks":{"lifespan_complete":true,"provider_mode":"simulation",…}}
```
Interactive API docs (dev convenience): <http://127.0.0.1:8000/docs>

## 4. Run the simulator (the Day-2 milestone)

```bash
cd apps/voice-gateway
uv run python ../../scripts/websocket_simulator.py --frames 10
```

Success looks like:

```
[simulation] connecting → ws://127.0.0.1:8000/ws/exotel?sample-rate=8000
[simulation] audio: frame=320B rate=8000 frames=10/10 matched byte-for-byte
[simulation] round-trip: avg=0.18ms max=0.24ms
[simulation] closed cleanly (1000)
NOTICE: simulation success ≠ a real Exotel PSTN call working.
```

Add `--json` for machine-readable output; `--sample-rate 16000|24000` to test
rate negotiation; `--frames 200 --interval 0` for a higher-rate soak.

With auth enabled (set `WS_AUTH_TOKEN` on the gateway) run:

```bash
uv run python ../../scripts/websocket_simulator.py --ws-token <token>
```

`--mode exotel` requires a real live-mode URL + `--live-key/--live-token`
(assignment constraint: only used after Exotel onboarding).

## 5. Tests / lint

```bash
cd apps/voice-gateway
uv run pytest                 # full suite (105 tests)
uv run pytest -q tests/unit   # unit only
uv run pytest -v tests/integration/test_websocket_protocol.py
uv run ruff format .          # formatting
uv run ruff check .           # lint (0 = clean)
```

## 6. Docker

```bash
cd infrastructure/docker
docker compose build voice-gateway
docker compose up -d
docker compose logs -f voice-gateway
curl -s http://127.0.0.1:8000/health/ready
uv run python ../../scripts/websocket_simulator.py   # from apps/voice-gateway
docker compose down
```

## 7. Troubleshooting quick refs

| Symptom | Likely cause / fix |
|---|---|
| cURL 404 on `/health/live` | server not running or wrong port (`--port 8000`). |
| Simulator exit 3 `payload mismatch` | gateway version mismatch or a non-echo pipeline deployed. |
| Close 1008 on connect | auth: wrong/missing `X-WS-Token`, or (live mode) wrong Basic header. |
| Close 1009 on connect | test payload exceeds `MAX_WS_MESSAGE_BYTES` (tighten test or raise limit). |
| Close 1003 during session | too many malformed frames already sent (budget cleared by a valid frame). |
| `uv` resolves but tests fail to import `app` | run all commands from `apps/voice-gateway` (path contains `pythonpath=["."]`). |
| Address already in use | previous uvicorn; `lsof -ti:8000` then kill. |

## 8. What to change on Day 3

* Add `AZURE_SPEECH_KEY/REGION` style settings in `app/core/config.py`.
* Replace `echo_service.process` internals with VAD/STT (streaming), keep the
  `AudioReceived → AudioToSend` signature so tests/adapter don't change.
* Recheck Azure/Gemini free-tier caps before firing the first streaming call.
