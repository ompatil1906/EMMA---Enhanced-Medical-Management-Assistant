# REQUIREMENTS.md

**Project:** EMMA-style voice-agent prototype (10-day assignment, QuantumLoopAI AI/ML & Prompt Engineer role)
**Scope basis:** EMMA_PRODUCT_ANALYSIS.md `[DOC]` behaviours + JOB_DESCRIPTION_MAPPING.md; all prototype features are `[REC]` implementations of that essence.
**Data rule:** only fictional patient data will be processed; the prototype never uses real patient data and claims no certification.

---

## 1. Functional requirements (MoSCoW)

### M — Must have (demo breaks without them)
| ID | Requirement |
|---|---|
| F1 | Answer inbound PSTN call within ~3 rings equivalent (<1.5 s post-answer) with a greeting + recording notice (`[DOC]` "100% answered within three rings"; recording is documented EMMA behaviour). |
| F2 | Parse patient intent from streaming speech: enquiry-opening-hours, enquiry-address/services, appointment-request, appointment-change, prescription-request, results-enquiry, registration/admin, human handoff, emergency red-flag, none-of-the-above. |
| F3 | **Practice-KB RAG** over a fictional practice knowledge base (hours, address, registration process, services, Pharmacy First/signposting pages) with cited-only answering + explicit "I can't find that" fallback. |
| F4 | **Appointment request capture** (identity, concern for routing-only, preference, contact) → structured POST to practice backend; optional mock booking against a tiny fake schedule. `[DOC]` "sends to consultation workflow; books within agreed rules". |
| F5 | **Appointment change/capture**, **prescription request capture**, **results-enquiry capture with hard disclosure block**, **registration/admin capture**. |
| F6 | **Multiple requests in one call** — each request recorded as its own item with its own outcome/next step. `[DOC philosophy]` |
| F7 | **Patient-detail corrections** — final-value semantics: spelling, phone change, correction mid-sentence leaves one unambiguous record value; unresolved values flagged provisional. `[DOC philosophy]` |
| F8 | **Human handoff** — request-a-person intent; mock transfer with a transfer summary; unanswered-transfer → message + visit open in Hub. `[DOC philosophy]` "follow the handover to its destination". |
| F9 | **Emergency red-flag handling** — pre-approved short script ("hang up and call 999"), keyword pre-filter + prompt-level guard, call flagged `emergency-detected`, no further workflow. 111 signpost for urgent-but-not-999. |
| F10 | **Telephony audio pipeline** — Exotel trial Exophone (Voicebot bidirectional WebSocket), raw PCM 16 kHz mono, base64 framing; send media audio back on the same socket; support `clear` for barge-in flush. `[V]` Exotel AgentStream docs |
| F11 | **Streaming STT** — Azure Speech (F0) streaming recognition with interim results feeding VAD endpointing; per-turn ASR confidence captures. |
| F12 | **Streaming TTS with low first-chunk latency** — Azure Neural TTS (en-GB voice), streamed as PCM feeding the telephony socket; concurrent play + voice-activity detection for **interruption (barge-in)** handling. |
| F13 | **Turn orchestration FSM** — deterministic per-call state machine (greeting → capture → validate → confirm → next-request/close), with tool/function calls executed by the LLM; timeouts/retries on each external call. |
| F14 | **Call state & persistence** — per-call record (state, turns, ASR transcript, responses, tool calls, outcomes per request, latency per stage, flags) persisted; call ends cleanly including abnormal disconnects (`disconnected → follow-up pending`). |
| F15 | **EMMA-Hub-style dashboard** (Next.js): call list w/ status, transcript viewer, captured requests view, red-flag audit feed (practices as "what reaches your team"). |
| F16 | **Observability** — structured JSONL audit (append-only) + Langfuse traces (per turn: STT latency, LLM tokens/latency, TTS first-chunk latency, tool latency, retrieval trace). |
| F17 | **Practice backend API** — NestJS + MySQL service exposing REST: `POST /requests/{appointments,appointment-changes,prescriptions,results-enquiries,registration}`, `POST /transfers`, `GET /calls/{id}`, `PATCH /requests/{id}` statuses; OpenAPI-documented. |
| F18 | **Failure modes** — graceful degradation: backend unavailable → apologise + leave a pending message; LLM timeout → retry once, then transfer/hold; RAG miss → "I'm not certain; I'll pass this on" (never fabricated answer). |
| F19 | **Evaluation harness** — replay scripted calls (WebSocket test-client from WAV) through the pipeline; compute metrics per EVALUATION_PLAN.md; 15 scenarios green at thresholds. |
| F20 | **Privacy posture** — recording notice; fictional data only; retention window documented; no PII in URLs/logs ([REQ privacy from `[DOC]` posture]). |

### S — Should have (value-adds if the 10 days allow)
| ID | Requirement |
|---|---|
| F21 | **SOPHIA-shaped disconnect follow-up** — simulated "SMS" (stored message + public follow-up web link/guided form) so the request completes without calling back. Mirrors documented SOPHIA scenario-specific messaging. |
| F22 | **DTMF fallback** ("press 1 to book, 2 to be transferred…") after failed ASR attempts. `[V Exotel supports DTMF events; REC flow]` |
| F23 | **One extra language sample** (Hindi or Urdu turn) using ASR auto-detect/multi-lingual voice — evidence of 19-language-coverage ambition. |
| F24 | **Concurrency demo** — 3 simultaneous replay calls sharing one service without state bleed (task-document scenarios like 8am). |
| F25 | **Cost/latency dashboard card** in Hub (per-call: provider latency + tokens/minutes + ₹ estimate). |

### C — Could have (polish)
| ID | Requirement |
|---|---|
| F26 | Red-flag recall micro-benchmark (50 utterance proset incl. negatives) w/ published recall/precision. |
| F27 | Prompt-version history + one A/B run showing refusal/capture delta. |
| F28 | Docker-only startup (`make demo`) so evaluators can replicate in one command. |
| F29 | Small "would a GP receptionist accept this?" walkthrough video script doc. |

### W — Won't have (explicit out-of-scope, per brief)
- Real patient data; real NHS/trial integrations; clinical triage logic; diagnosis or advice; disclosure of test results; production-grade multilingual coverage claims; SMS provider spend; speech-to-speech opaque APIs; Kubernetes; GraphQL; "NHS approved" branding statements.

---

## 2. Non-functional requirements (NFRs)

| ID | Requirement | Target / rationale |
|---|---|---|
| N1 | **Answer & first-audio latency** | First greeting audio ≤1.5 s post-answer; first TTS chunk after end-of-speech p50 ≤1.5 s, p95 ≤2.5 s (prototype-grade; JD explicitly pairs latency with safety) |
| N2 | **Turn latency budget visibility** | Per-stage latency measured & logged (VAD end→STT final →LLM first token →TTS first chunk) target p50 ≤2.5 s end-of-speech→first-audio |
| N3 | **Concurrent calls** | ≥3 simultaneous simulated calls, isolated state; no cross-talk transcripts |
| N4 | **Availability posture** | single-node compose for demo; degrade-to-message on dependency failure; no crash mid-turn |
| N5 | **Reliability of state** | call record durable after every turn; safe on disconnect mid-call (SOPHIA pending state) |
| N6 | **Security** | secrets from env only; provider keys never in repo; public endpoints TLS via Cloudflare Tunnel (no inbound ports opened); logs PII-minimal (no full audio retained; deferred recording only when notice given) |
| N7 | **Auditability** | append-only JSONL per call + trace IDs — mirrors "immutable, auditable" claim `[C]` at prototype fidelity |
| N8 | **Privacy** | fictional data; retain-and-delete command; recording notice; DPIA-lite doc |
| N9 | **Accessibility** | Barge-in; re-ask once; DTMF; slower TTS option; silence tolerance ≥8 s before reassurance prompt |
| N10 | **Cost ceiling** | Total personal cash spend ≤ ₹500; automated per-run cost estimate reported; all provider keys on free/credit tiers with caps |
| N11 | **Portability of providers** | LLM/STT/TTS/telephony behind thin adapters; a new provider swap ≤1 file per adapter (exactly the "modular, replaceable" brief) |
| N12 | **Docs quality** | repo README + this bundle + ADRs; a new engineer cold-starts from docs in <1 h |

---

## 3. Constraints

| Constraint | Detail |
|---|---|
| Time | 10 calendar days from 5 Nov 2026 (Day 1 = research docs, as today) |
| Budget | personal out-of-pocket ≤ ₹500 (≈ $6) — all must-have services on free tiers/credits/third Dev Pack benefits |
| Geography | candidate in India → Indian inbound test number (Exotel trial) appropriate; read-only providers chosen accordingly |
| Hardware | laptop-only; no GPUs; embeddings & VAD CPU-only (MiniLM + Silero VAD) |
| Accounts | GitHub Student Pack first-year benefits (Azure for Students $100, no card required); no new paid subscriptions |
| Provider caps to respect | Azure Speech F0 (5 audio-hours STT & 0.5 M chars TTS monthly; concurrency-1); Gemini free tier (low daily quota, verify on day of use); Qdrant free cluster (suspending after 1-wk idle); Langfuse Hobby (50 k units; 2 users); Exotel trial ₹1,000 credits + KYC |
| Language | en-GB primary; bilingual stub if room (F23) |
| Demo scale | 15 scenarios, live PSTN + scripted-audio replay both exercised |

---

## 4. Acceptance gates (summary — expanded in EVALUATION_PLAN.md)

1. **G1 (line of business):** all M-workflows demonstrable in one scripted 4-minute call incl. multi-request capture.
2. **G2 (safety):** red-flag scenario #14 hard-stops correctly in ≥ every test occurrence; results-disclosure block proven by adversarial prompt attempt logged.
3. **G3 (latency):** N1/N2 targets met on live PSTN test calls.
4. **G4 (integrity):** correction final-value semantics proven on scenario lists; multi-request outcomes complete on Hub.
5. **G5 (observability):** every turn traceable in Langfuse and audit JSONL; eval-run metrics reproducible via one command.
