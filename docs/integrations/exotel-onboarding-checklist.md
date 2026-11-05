# Exotel onboarding checklist (Day 2 → first real call)

Goal: reach the state where **a real inbound call reaches
`/ws/exotel` with verified stream events in the logs**. Until then, we
explicitly do NOT claim the telephone integration works.

> Advisory: this project is a technical prototype for QuantumLoopAI's
> assignment. It is not an approved NHS system, not a medical device, and must
> never receive real patient calls or data.

## 1. Account & credentials

- [ ] Create the Exotel trial account for QuantumLoopAI.
- [ ] Submit KYC (entity/individual + address details) — trials require it for
      an Indian Exophone; note actual review turnaround in the log below.
- [ ] Confirm trial credits (≈ ₹1,000 demonstration credit per account docs)
      and the trial's pricing semantics (per-connect/per-minute).
- [ ] Save `EXOTEL_API_KEY` & `EXOTEL_API_TOKEN` from the developer console —
      set as environment variables only (never in git).
- [ ] Record the account subdomain that applies to this account (unknown until
      login; needed only for outbound APIs later).

## 2. Telephony enablement

- [ ] Confirm the trial Exophone number is visible & inbound-capable.
- [ ] Check Voicebot / AgentStream applet availability for the trial plan; if
      restricted, log what support says (this is the biggest schedule risk).
- [ ] Build an inbound call flow: DID → Voicebot applet → "Stream" action.
- [ ] Configure the Stream action:
      - `StreamUrl`: `wss://<public-host>/ws/exotel?sample-rate=8000`
        (secured WSS; 8 kHz is our default until/unless an upgrade is warranted)
      - `CustomParameters` (test only): e.g. `practice=sim` — MUST NOT contain
        secrets; treat values as customer-visible metadata.
- [ ] Confirm the platform version handles Voicebot WebSocket connections for
      trial accounts (record evidence — plan limits are trial-specific).

## 3. Public endpoint requirement

- [ ] Exotel opens the connection **to us** (verified fact [V1]) → the gateway
      must be reachable on a **public, TLS (wss://) endpoint**.
- [ ] Options for the assignment window:
      a) cloudflared / ngrok quick tunnel → `cloudflared tunnel --url http://localhost:8000`
         (HTTP only; may not terminate WSS in all plans — verify);
      b) any small VPS with nginx/Caddy TLS termination → recommended if (a)
         blocks WebSockets — record actual choice & caveats in the log below.
- [ ] Sanity: `curl https://<public-host>/health/ready` returns 200 `ready`.

## 4. Configuration on the gateway side

- [ ] `.env`: `EXOTEL_MODE=live`, `EXOTEL_API_KEY`, `EXOTEL_API_TOKEN`, `WS_AUTH_TOKEN=` unset
      (live-mode auth uses Basic header per the verified protocol)
- [ ] Restart the gateway; check `/health/ready` → `provider_mode: live`,
      `provider_credentials_configured: true`, `status: ready`.
- [ ] Browser/tools side check: attempt a websocket client with a wrong Basic
      header and expect close **1008** (confirms live-mode auth path works).

## 5. First test call (safe procedure)

- [ ] Call the Exophone **from your own phone**; speak/say clearly "test 1".
- [ ] Expect in gateway logs (in order):
      1. `ws session opened` (with 1008 `authentication failed` appearing only
         for deliberate bad-credential probes, never for the real call);
      2. `call started` with real `call_sid`/`media_format`;
      3. repeated `media` frames flow (`frames_in` climbing);
      4. clean `ws session closed` when the call ends.
- [ ] Record: negotiated `media_format` actually sent by Exotel (resolves the
      encoding-spelling open question), apparent frame sizes (resolves chunk-size question).
- [ ] Do NOT loop audio or expose the echo as a "conversation" to a real caller —
      echo of your own voice is confusing/deceptive; use short calls only.

## 6. Definition of "telephone integration works"

Learned earlier is only "simulator-verified". The integration is proven when:

- [ ] A real inbound call produced `call started` + ≥10 `media` frames received.
- [ ] Outbound reply envelopes (`media`) were accepted by the platform without
      protocol complaints (call does not drop/mute on our replies).
- [ ] A clean close arrived (`stop` — `reason` ∈ stopped/callended) plus
      close-code 1000 handshake completion.

## 7. Log of unverified Qs (fill during onboarding)

- Subdomain used: ____________
- KYC submitted: ____________ (date) → approved: ____________
- Applet enabled: ____________ (yes/no; support reference)
- Real call result: ____________ (date, brief result, framing observed)
