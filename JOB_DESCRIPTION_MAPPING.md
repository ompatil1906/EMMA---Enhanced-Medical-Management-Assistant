# JOB_DESCRIPTION_MAPPING.md

**Target role:** AI/ML & Prompt Engineer — LLM, RAG & Voice Agent (India · Remote · Engineering)
**Official JD:** https://apply.workable.com/quantumloopai/j/B7F8DA3F6C/
**Text source method:** The Workable SPA page drops no static text to a plain fetch; the full text was recovered from Workable's public job mirror (jobs.workable.com, India-remote and London postings of the same role D391785516/B7F8DA3F6C) and cross-checked against Remote Rocketship and LinkedIn copies. Sections quoted verbatim; preserved in brackets where fragments were truncated in the mirrors. Access 5 Nov 2026.

---

## 1. The job description, verbatim-categorised

> **ABOUT QUANTUMLOOPAI** — "QuantumLoopAI is transforming how patients access healthcare. Our AI-powered reception system, EMMA, serves millions of patients, helping GP surgeries manage demand, reduce phone queues, and ensure patients get the right care at the **right time.** … As we prepare for our Series A and expand our team, we are looking for exceptional people who want to make a genuine difference to healthcare."
>
> **ABOUT THE ROLE** — "As our AI/ML & Prompt Engineer, you will **own the intelligence behind EMMA** — the LLM and RAG systems that power our chatbot, voice agent and telephony services. You will design, build and optimise the AI models and prompt architectures that allow EMMA to understand patient needs, retrieve accurate clinical information, and respond naturally across text and voice channels. This is a hands-on, high-impact role where your work will directly shape patient outcomes across hundreds of GP surgeries."

### C. Daily responsibilities — "WHAT YOU WILL DO"
1. **Architect and deploy LLM- and RAG-based systems** that ingest data sources, generate accurate responses, and power both **chatbot and voice agent** interactions.
2. **Design, iterate and optimise prompts and voice-agent dialogue flows** to maximise response relevance, **reduce latency and ensure clinical safety** across text and voice channels.
3. **Partner with frontend (Next.js, React) and backend (NestJS, Python) teams** to integrate AI and voice agent components into **our Azure-hosted microservices architecture**.
4. **Instrument AI and voice-agent pipelines**, analyse logs and user feedback, troubleshoot edge cases, and implement **continuous-learning improvements**.
5. **Stay abreast of the latest advances in LLMs, RAG, conversational AI frameworks, and (GDPR, DTAC)** to inform the technical roadmap.
6. **Define and execute rigorous test plans in collaboration with QA** to validate model accuracy, voice-agent performance and **compliance with healthcare standards**.
7. **Mentor junior engineers and advocate for best practices in MLOps, prompt engineering, voice-agent design and model governance.**

### A. Essential technical skills — "REQUIREMENTS / ESSENTIAL"
1. Demonstrable hands-on experience with **prompt engineering, LLMs (e.g. GPT, LLaMA, Mistral), RAG frameworks, and voice-agent or dialogue-system design**, with shipped projects or open-source contributions.
2. **Advanced proficiency in Python** for ML/AI development.
3. Experience building **APIs in NestJS** and familiarity with **Next.js/React** for frontend integration.
4. Strong understanding of **Azure services (App Services, Functions, Cognitive Services)**, **containerisation (Docker)**, and **relational databases (MySQL)**.
5. Experience designing **microservices, distributed architectures and RESTful or GraphQL APIs**.
6. **Excellent written and verbal communication** — articulate complex AI and voice-agent concepts to technical and non-technical stakeholders.
7. Comfortable working **remotely & asynchronously with a UK-based team during UK business hours (9:00–18:00 GMT/BST)**.

### B. Desirable skills
1. Contributions to **open-source LLM or RAG tooling**.
2. **MLOps pipelines, model monitoring and automated evaluation frameworks**.
3. **Speech-to-text, text-to-speech, or telephony integration** background.

### Logistics / culture (JD "character" signals)
- Education: "We value **skills and experience over formal qualifications**."
- Working: fully remote from India, aligned to UK hours; flexible start.
- Company culture listed around the JD: *"Impact over optics… Transparency… Ownership… Learning"*; there is a note: *"If you are excited about this role but do not meet every requirement, we would still love to hear from you."*
- **Recruitment safety notice:** QuantumLoopAI will never ask candidates for payment; all official comms come from @quantumloopai.com email addresses. Contact: careers@quantumloopai.com.

### I. Compliance and healthcare knowledge (explicit)
- GDPR and **DTAC** are named in a daily-responsibility bullet — compliance is not a nice-to-have in this job.
- "Compliance with healthcare standards" appears inside the QA/test-plan responsibility too.

### Corroborating artefacts from other QuantumLoopAI JDs (public) — [`V` jobs.workable.com Technical Lead / London postings]
- Backend: **Node.js, TypeScript, microservice architectures**, Node/NestJS APIs, **MySQL**, distributed systems.
- AI: prompt engineering across **OpenAI, Azure OpenAI and open-source models**; evaluation frameworks measuring **LLM output quality, hallucination rates, latency and cost**; **multi-agent architectures**; RAG pipelines.
- Voice: **"Evaluate and integrate providers such as Deepgram, ElevenLabs, Azure Speech and Whisper"**; **"Build and optimise end-to-end voice agent flows: ASR to LLM to TTS with low-latency streaming and graceful fallback handling"**.
- Infra: **Azure-hosted** services; **Docker**; CI/CD; AI coding tooling (Cursor, GitHub Copilot, Claude).

---

## 2. Requirement → demonstration matrix

Legend — *Demonstrate* states what I will actually build in the 10-day EMMA prototypearchitecture. Nothing is added just to look impressive; each row ties directly to the JD.

| # | JD requirement | 1) What the company likely expects | 2) Why it matters for EMMA | 3) What I will implement | 4) How I will test it | 5) Evidence to show |
|---|---|---|---|---|---|---|
| R1 | **Prompt engineering, LLMs (GPT/LLaMA/Mistral) + dialogue-system design** | Someone who can write versioned, safety-constrained prompts for a stateful voice agent, not a chat toy | A mis-prompted EMMA gives clinical advice or invents slots — unacceptable | Prompt registry (system prompt + tool contract + safety preamble), per-turn FSM, model-agnostic LLM client (Gemini primary; Azure OpenAI/Groq/Llama fallback adapters) | Scenario suite (15 cases); prompt-version A/B on refusal & capture accuracy; hallucination probes (unavailable slot tests) | Prompt diff history, refusal-rate table, side-by-side transcripts |
| R2 | **RAG frameworks** | Retrieval grounded in *practice-specific* content with citations, not general LLM lore | Wrong opening hours = patient walks to closed surgery | Practice-KB RAG: sentence-transformers MiniLM embeddings → Qdrant; grounded-response template; source chunk id recorded | 20 nasty KB questions (in-KB vs out-of-KB); answer-must-match-KB assertions in eval script | Retrieval traces (retrieved chunks + score), groundedness score, "cannot-find" demo |
| R3 | **Advanced Python** | Production-grade async Python (streaming, WS) under latency pressure | Voice agents are all async I/O | FastAPI async pipeline: WS gateway, streaming STT consumer, orchestrator, TTS producer, turn-state machine | pytest on packet/state handling; latency profiling under distractor audio | Clean async code, typed, profiled flame/latency table |
| R4 | **NestJS APIs + Next.js/React familiarity** | Integrates into an existing TS stack; AI engineer isn't language-siloed | Practice-side request intake must expose clean REST contract to AI team | **NestJS "practice backend"** (MySQL) exposing requests/transfer/calls endpoints + minimal **Next.js EMMA-Hub dashboard** consuming it | Contract tests (pytest/curl/fixtures against NestJS) + Playwright-less manual click-through + API capture |
| R5 | **Azure (App Services, Functions, Cognitive Services), Docker, MySQL** | Understands their Azure estate + can containerise and use relational data | All data & AI services sit in Azure in their production | Docker Compose local stack; Azure Speech (F0) STT+TTS; MySQL Flexible Server via Azure for Students ($100 credit) for the practice backend; optional Azure App Service deploy of gateway | docker compose up parity test; Azure resource health; MySQL migrations run in CI | Repo's compose file, Azure portal screenshot w/ cost cap, CDC/migration logs |
| R6 | **Microservices, REST/GraphQL, distributed design** | Can reason across service boundaries without turning a 10-day task into vendor soup | EMMA splits into: telephony gateway / orchestrator / practice backend | Exactly 2 services (FastAPI voice-agent gateway+orchestrator as one; NestJS practice backend second) + Redis-free in-memory session registry + MySQL; crisp REST contract between them | Sequence-level integration tests; failure-injection tests (backend down → shapeful degradation) | Architecture diagram, ADRs, failure-matrix run log |
| R7 | **Communication skills** | Can write docs a non-technical NHS practice manager would accept | Their sales cycle is GP practices; JD demands both-ways explanation | Repo READMEs, this bundle of MDs, a 5-min demo script with a "trainee receptionist" explanation section | Peer-readability check (a non-engineer can narrate it back) | Documents themselves + demo narration |
| R8 | **Remote & async UK hours** | Trustworthy delivery without supervision, UK-time overlap | Distributed team builds EMMA | Every commit has an ADR-first trail; screen-recorded demo replicable async | playback of demo; timestamped evolution (GitHub) | Daily commit trail & dated docs |
| R9 *(desirable)* | **Open-source LLM/RAG tooling contributions** | Habits of contributing upstream, public engineering voice | Tech Lead JD mentions community/mentoring | Ship eval harness as small open-sourceable repo layer (documented, MIT) — *contribution-readiness*, even if merged contribution exceeds 10 days | Public repo hygiene: tests, README, LICENSE | Repo link + contribution-ready module brief |
| R10 *(desirable)* | **MLOps, monitoring, automated eval** | Continuous-quality mindset, LLM metric discipline | They measure hallucination, latency, cost explicitly | Langfuse traces on every turn + JSONL audit + eval-runner + latency/cost dashboards on Hub | Re-run 15 scenarios post-prompt-change and diff metrics automatically (regression table) | Langfuse trace screenshots, metric diff table |
| R11 *(desirable)* | **STT/TTS/telephony background** | Speech pipeline literacy – VAD, streams, 8k/16k trade-offs, barge-in | The raw material of EMMA | Exotel AgentStream (bidirectional WS, 16 kHz PCM pathway), Silero VAD, Azure Speech streaming STT, Azure TTS streaming + `clear`-based barge-in | Scripted WAV replays through WS + live PSTN trial calls; interruption scenario #9; 3 concurrent call soak | WS logs, barge-in capture, latency table, demo video |
| R12 | **Explicit: GDPR & DTAC awareness** | Security/clinical governance reflexes | Non-negotiable for NHS deployment | Privacy plan artifact: data minimisation (fictional data only), retention note, recording notice in greeting, no 3rd-party PII transfers, and a mini-DPIA + hazard log (DCB0129-style) | Checklist audit; doc review | DPIA-lite + hazard log + security section in README |
| R13 | **Test plans with QA; healthcare-standard compliance testing** | Rigorous verification culture | 99.6% red-flag recall is *their* bar | EVALUATION_PLAN.md (this bundle) with scripted + live suites; red-flag test set; acceptance thresholds per scenario | Automated regression in GitHub Actions on eval corpus; threshold gates | Eval report + CI green + failure cases annotated |

---

## 3. Skills named in "special attention" list — where they actually map

| JD keyword | Present in which requirement(s) | My implementation hook |
|---|---|---|
| Python | R2, R3 | Voice agent + RAG + eval harness |
| LLMs | R1 | Gemini/Groq/Azure-OpenAI interchangeable client |
| RAG | R2 | Qdrant + MiniLM over practice KB |
| Prompt engineering | R1 | Versioned prompt registry w/ safety preamble |
| Voice agents | R1, R11 | FSM + streaming pipeline on Exotel WS |
| NestJS | R4 | Practice backend service |
| Next.js / React | R4 | Hub dashboard (Next.js App Router + TS) |
| Azure / Cognitive Services | R5 | Azure Speech; (OpenAI-on-Azure as optional LLM move); Azure for Students resources |
| Docker | R5 | docker compose for full stack |
| MySQL | R5, R6 | Practice backend DB (Azure Flexible Server / local container) |
| Microservices | R6 | 2-service contract, not 7-service circus |
| REST APIs | R6 | NestJS REST (+ documented OpenAPI) |
| GraphQL | R6 (alternative) | *Not implemented* — REST deliberately chosen; one-line justification in ADR |
| MLOps | R10 | Langfuse + eval harness + CI |
| Evaluation & monitoring | R10, R13 | Turn metrics (latency, intent accuracy, groundedness, red-flag recall) |
| GDPR | R12 | privacy plan + minimisation + retention + notice |
| DTAC | R12 | hazard log + clinical-safety framing + "what EMMA never does" list |

---

## 4. What I will deliberately NOT do (JD-honesty)

- **No GraphQL server** — R6 allows REST; a second API style adds surface area a 10-day safe-scope doesn't earn. Documented in ADR with the trade-off named.
- **No Kubernetes** — Docker Compose only; K8s is not in the JD.
- **No Nas** microservice zoo — JD asks microservice *capability*; two well-defined services with a strict contract demonstrate it better in 10 days than 6 undercooked ones.
- **No speech-to-speech opaque API as the pipeline** — the JD profile is a *component* owner ("LLM and RAG systems", "instrument pipelines", "speech-to-text/tts/telephony background"); a modular pipeline with visible metrics matches it exactly.
- **No shortcuts around the JD's compliance line** — even a prototype must be demonstrably not-processing-real-patient-data.

## 5. Requirement → riskier signals to pre-empt in interview

1. **"Own the intelligence" language implies end-to-end accountability** — my demo must show *ownership* evidence: ADRs, choices, fallback behaviour, not just what "the vendor calls".
2. **"Reduce latency" is in the same sentence as clinical safety** in the JD — my eval table must show latency *and* safety metrics side by side; they explicitly care that speed did not buy safety down.
3. **Instrumentation bullet is a real filter** — a voice agent without traces/logs/eval is a silent reject in this team's observable-evidence culture.
4. **Mentoring bullet** — the README-quality bar is therefore actually an interview signal; write docs as if a junior will onboard from them.
