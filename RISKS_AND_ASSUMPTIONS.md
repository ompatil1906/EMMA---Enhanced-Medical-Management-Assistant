# RISKS_AND_ASSUMPTIONS.md

**Purpose:** everything I believe but could not confirm; every integration that can break; privacy/safety residual risks; and the open questions I would take to Saima Khan (QuantumLoopAI contact) before Day 3 locks the architecture.

---

## 1. Assumptions ranked by consequence-if-wrong

| # | Assumption | Basis | Consequence if wrong | Mitigation |
|---|---|---|---|---|
| A1 | The prototype is a **capability demonstration**, not a deliverable to EMMA production systems | Brief says "working voice-agent prototype aligned with EMMA"; assignment context | Wrong scope (building too small / too big) | Covered in §3 clarifying questions |
| A2 | Telephony provider Exotel's trial supports **bidirectional AgentStream media WS** for an *inbound*/test line without paid plan (trial = ₹1,000 credits; Voicebot applet documented) | Official docs; trial FAQ | Live PSTN demo cannot happen | Replay harness is co-equal (deterministic, provider-safe); Plan B ngrok+Twilio for *audio only*; Plan C software-call bridging (agent calls out via trial as a demo). Assignments are usually demo'd via video, so risk is moderate |
| A3 | Azure for Students ($100) **can deploy** Azure Speech + (optional) MySQL Flexible Server (Basic B-class) without extra verification. Speech F0 doesn't require a card | Azure for Students official terms (no card, student email) | Fallback = Deepgram ($200 free credit, no card) + local Docker MySQL; same adapters, no code change (ADR-002/ADR-007) |
| A4 | Gemini free quota is usable on demo days (RPD-like free tier exists; daily/Quota may change) | Official pricing page + community reports of 2025's free-tier cuts | Swap LLM adapter to Azure OpenAI (student $100 credit) or Groq free; 45-turn rehearsal needs ~1-2 calls/min, easily under any plausible RPD |
| A5 | JWT/token not needed for returning WS in trial (Unauthenticated WSS beyond TLS is enough for prototype security) | Exotel trial semantics | Replay/harness token check supplies an equivalent check; document production-gap in README |
| A6 | Qdrant cloud free cluster will survive during demo; suspension/restart is acceptable | Official docs (1-week suspension) | `make qdrant-up` restart runbook; local docker Qdrant identical code path |
| A7 | A live, real-time phone demo recorded on video (screen+audio) is what evaluators watch; no production deployment requested | Assignment pattern and 10-day/budget limits | Docs-first deliverable everywhere; repo runs `make demo` on any laptop |

## 2. Integration risk register (technically actionable, ranked)

| # | Risk | Likelihood | Impact | Mitigation / fallback |
|---|---|---|---|---|
| I1 | **WS audio-format mismatch** (chunk sizes must be multiples of 320; base64 framing; `?sample-rate=16`) leads to audio distortion | High if untested early; Medium | Speaks wrong/robotic audio | **Day 2**: echo-bot test with official Exotel AgentStream echo/repo + audio harness; strict chunking logic + streamed assertions in pytest |
| I2 | **Barge-in mis-timing** (clear event sent too late → double speech) | Medium-High | Bad patient experience; metric fail in S9 | VAD gate fully in-process; param `Track=inbound`; tuning tests with real recordings; graceful degrade (still-allowed overlap po) |
| I3 | **Azure F0 concurrency-1 limit** collides with 3-call soak test | High | Soak stalls | Soak on replay audio with a *mock-STT provider* for concurrency assertions; live concurrency capped to 1 F0 stream for the live video; documented honestly |
| I4 | **LLM latency spikes** (free tier throttling, provider failover) | Medium | N1 p95 blowout | LLM adapter carries a full retry ladder + fallback provider switch mid-session; pre-canned worst-case replies for emergencies (never block red-flag path on a slow model — **red flag is pre-LLM by design**) |
| I5 | **Red-flag guard false-negatives** on unusual phrasings | Low-ish but catastrophic | Safety | Corpus of ~50 emergency-variance utterances required; pre-LLM regex layer + prompt layer; a single miss = release blocker ([S] gates in EVALUATION_PLAN) |
| I6 | **Qdrant cluster suspension** before demo days | Medium | Demo shows RAG-down | Post deployment `ping-job` (cron via GitHub Actions) or restart-kernel runbook; local fallback image |
| I7 | **Exotel KYC/approval delay** during trial activation | Medium | Live PSTN slipped by 1–3 days | Sign up Day 2 morning; develop replay path in parallel; PSTN demo recorded last |
| I8 | **Cloudflare Tunnel blocked** on evaluator's network | Low | Public WSS failing | Keep local `ws://` + recorded demo as backup; ngrok alt documented |
| I9 | **Cost creep** (TTS/STT beyond free tier via repeated takes) | Low-Medium | Budget breach | Run-level cost print; ~2 h-call budget precounted to stay << free allowance; no paid tier ever activated; card never attached |
| I10 | **NestJS/MySQL deliverable swallowed by infra friction in 10 days** | Medium | JD-mismatch on TS/SQL story | Time-box: Day 3 fixed; minimal entity set (5 tables, 6 routes); tests via `curl`-script; no auth flow beyond health |
| I11 | **Fragmented repo reduced evaluation clarity** (too many components) | Medium | Reviewer time wasted | Monorepo via pnpm/npm workspaces; docs-first README; one-command demo; 3 services with strict interface contracts (ADR-006/007/008) |

## 3. Privacy risks and controls (prototype-grade honesty)

- **No real patient data**: every dataset string must be synthetic; corpus sourced from open speech corpora + TTS-generated recordings; the repo will ship with a `data/SYNTHETIC_README` warning and an intake checklist.
- **Call recording notice**: greeting includes a one-line disclosure ("this call is recorded") — mirrors documented EMMA recording practice and UK call-recording norms `[REC consistent with DOC]`.
- **Provider substrate risks:** audio/text may transit UK/US cloud (Azure, Gemini, etc.). For the prototype this must be disclosed, not hidden (production EMMA runs everything in UK `[C]`); README documents this limitation as *prototype-only, not fit for live patient traffic*.
- **Retention:** default retention ≤ 1 day for audio; transcripts retained for demo review and deleteable via one command; no PII keys in prompts or long-lived traces except synthetic ones.
- **What can leak:** raw transcripts + voice — treat as sensitive even for synthetic data; storage folder encrypted at rest only if laptop is encrypted (FileVault note), cloud buckets not used.
- **Access:** single-user local demo; no public UI auth (localhost/Vercel token-gated where free). Endpoints expect a session token from env; no open internet exposure beyond the WSS media path of the gateway.

## 4. Safety risks (the non-negotiables)

1. **The prototype is not a medical device, not DTAC-certified, not trained/CSO-reviewed, and must never be presented as "NHS approved".** Every artifact states this. (Even the footer claim "NHS Approved" on QuantumLoopAI's own site is their marketing; my artifact will not import it.)
2. **Red-flag detection is probabilistic** — even EMMA's 99.6% is internal `[C]`. Prototype must include the pre-LLM guard layer; corpus-gated recall target = 100% in the test set only.
3. **No clinical content anywhere**: prompts feature an immutable "never advise" preamble; LLM tool schema excludes any diagnostic/medication API; response templates are grounded to KB or tool outputs only.
4. **Emergency script is by-design the *only* clinical-shaped output** of the system (documented 999 instruction; 111 for urgent-not-emergency). No follow-on questioning, no "describe your symptoms".
5. **Hazard-log discipline at prototype scale:** a `ccu/hazards.md` (DCB0129-style) where each risk gets: hazard → mitigation → evidence → test → owner → status. This is my nod to a governance process, not a certification claim.
6. **Escalation honesty** at all times: if the agent is unsure **whether** it's an emergency or clinical, the safe default is offering the human/111 path — under-capture is recoverable; over-confidence isn't.

## 5. Unresolved questions for Saima Khan (day-1 clarification list)

1. **Deliverable format:** should the final output be (a) a live video demo call, (b) a screen-recorded demo + repo link, (c) a public-brief presentation? Should the repo be public or privately shared with you during review?
2. **Telephony constraints:** is evaluating over a **real PSTN trial call (Exotel trial)** an acceptable path in your review, or would you prefer a scripted-audio replay demo where budget/KYC friction is eliminated? (Both are built.)
3. **Stack latitude anywhere?** Given the JD says Azure/NestJS etc., how strictly should I mirror their *production* stack choices where they'd raise complexity meetings (e.g., running fewer microservices for the 10-day scope), versus demonstrating the exactJD keywords? (My current pick: FastAPI voice agent + NestJS practice backend + Azure-aligned deployment, alternatives documented.)
4. **LLM choice:** is there a preferred model family in your review for prototype-level work (Azure OpenAI vs Gemini vs open-weights)? That'll let me pick the fallback ladder with production-plausible names, not generic.
5. **Assignment timeline:** [dates]. Is the 10-day window a hard cap (submission Day 10 evening IST), and are there interim checkpoints you'd like (e.g., a Day-5 architecture review)?
6. **Data guidance:** my corpus is fully synthetic; confirm no real-patient/PII requirement is expected even as "would look more real."

## 6. Things I could NOT verify (open to scrutiny, listed for honesty)

- Configuration details of EMMA's internal language list, prompt architecture, provider names or ASR/TTS vendors live in production — none of this is public; **none** of the prototype's choices claim to be EMMA's real stack.
- Company revenue/ARR, investor names, employee headcount beyond third-party job-site estimates, founders by name.
- Whether "QuantumLoop Technologies Ltd" (G-Cloud entity) is the same legal entity behind this hiring process.
- Exact current Gemini free-tier daily quotas (Google changed them mid-2025; will be checked fresh each running day).
