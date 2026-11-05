# COMPANY_RESEARCH.md

**Project:** EMMA voice-agent prototype — technical assignment for QuantumLoopAI
**Author role:** AI Solutions Architect / Voice AI Engineer candidate
**Research phase:** Day 1 of 10 · All web sources accessed 5 November 2026 unless stated otherwise.
**Evidence labels used throughout:** `[V]` = independently verifiable fact · `[C]` = claim published by the company (not independently audited) · `[I]` = my reasoned interpretation · `[U]` = unverified / could not be verified

---

## 1. What QuantumLoopAI is

QuantumLoopAI is a UK healthtech company that builds **AI-powered patient communication products for NHS GP surgeries (primary care)**. Its flagship product, **EMMA**, is an AI telephone receptionist that answers inbound patient calls, captures administrative requests, and routes or escalates them to the practice team. `[V]` [what-is-quantumloop-ai]

- **Headquarters:** London, England. The company's own careers narrative describes a UK, office-first team; an independent interview (eGPlearning podcast, June 2025) quotes the team as based in Canary Wharf, London. `[V]` [eGPlearning YouTube interview; Tavily company record]
- **Founded:** 2023 (third-party company databases; consistent with the company's own claim of "AI Reception Since 2023"). `[V]` [Remote Rocketship company record; QLAI homepage badge]
- **Legal entity note:** EMMA's G-Cloud 15 procurement listing is published under **QuantumLoop Technologies Ltd** — i.e. the product is sold under a separate legal entity name from the consumer brand "QuantumLoopAI". `[V]` [EMMA helpdesk → procurement; Digital Marketplace listing]
- **Further identity facts that could not be verified in this session:** company registration number, registered office, directors/founders' names on the register, and audited financials. The site does not publish a founders-per-bylines page. `[U]`

## 2. Founders and leadership

- The public careers site explicitly references "direct access to founders" and "founder chat" as the last interview stage, and an **Advisory Board chaired by Sir David Sloman, former COO of NHS England**. `[C]` [careers; compliance-security]
- Founders' names are not published on the pages I fetched. LinkedIn would be the next verification step; the company LinkedIn page lists 38.2k followers (Tavily record). `[U→follow-up]`
- A **Clinical Safety Officer (CSO)** role exists and is publicly documented as owning sign-off on language sets and red-flag evaluation results. `[C]` [who-is-emma helpdesk]

> Interpretation `[I]`: the company's governance story (CSO, hazard log, DCB0129/0160 sign-off, advisory board with a former NHS England COO) is unusually mature for a small startup and is clearly deliberate — the NHS buys clinical assurance, not just software. Any prototype I produce must mirror that evidence-first governance posture.

## 3. Scale, stage and traction

| Item | Claim | Source status |
| --- | --- | --- |
| Serving over 2 million patients across England | `[C]` | Helpdesk footer & what-is page |
| Handles over 500,000 calls per month | `[C]` | what-is-quantumloop-ai |
| 100% of calls answered within three rings | `[C]` | Home FAQ |
| 82% of calls handled automatically, no staff intervention | `[C]` | Home / GP benefits pages (Conisbrough case study: 95% reduction in calls reaching reception) |
| 81% uplift in GP Patient Survey phone-access scores | `[C]` | Home / GP benefits pages |
| Up to 80% reduction in reception staffing costs; 15 work-days/week saved per practice; 4× reception capacity per 10k list size | `[C]` | Home / benefits pages |
| 19 languages signed off via internal CSO process; "10+ clinically tested languages" | `[C]` | who-is-emma helpdesk |
| 41% average re-engagement rate via SOPHIA SMS follow-up after dropped calls | `[C]` | SOPHIA helpdesk page |
| Raised $2M convertible note (Feb 2025); 51–200 employees | `[V]` (third-party) | Remote Rocketship record — not confirmed on company pages `[U]` |
| Preparing for Series A; "backed by leading healthcare investors" | `[C]` | Workable JD intro |
| Named customer: Conisbrough Group Practice (South Yorkshire, ~12,000 list, on Accurx) with detailed before/after metrics | `[C]` (named, verifiable practice) | Case-study page |

Percentages are marketing claims without published methodology — but the company's own blog (Nov 2026) openly teaches buyers to interrogate exactly such numbers ("AI Receptionist Benchmarks: What the Numbers Mean"), which itself signals a metrics-literate culture. `[I]`

## 4. Business model

- **B2B SaaS subscription to GP practices**, sold on outcomes: eliminate phone queues, cut reception cost, improve GP Patient Survey access scores. Pricing is not public ("pricing on request"; InTouchNow competitor page lists per-machine comparators, e.g. £1.00/call). `[C]` + third-party `[V]`
- **Bundled telephony:** EMMA's telephone service includes assistance with a *ported reception line* plus telephony/forwarding costs; the practice keeps overall control of its number and its existing telephony provider pairings. `[C]` [who-is-emma]
- **Procurement routes:** G-Cloud 15 listing (under QuantumLoop Technologies Ltd) and participation in the **RM6200 AI Dynamic Purchasing System** (Crown Commercial Service), enabling ICB-level procurement. `[V]` [who-is-emma → Digital Marketplace; gca.gov.uk agreement page]
- **Interoperability-over-bundling philosophy:** The company states it deliberately does **not** sell telephony or consultation systems, connecting to "any telephony provider and any consultation tool that follows NHS standards" (Accurx named). `[C]` [gp-surgeries-benefits FAQ]

## 5. NHS / healthcare positioning

- Positioned as "the first AI reception deployed in NHS GP surgeries" and "the first DTAC-certified AI reception platform". `[C]`
- Regulatory stack claimed by the company: **DTAC certification; MHRA Class I medical device registration (advancing toward Class IIa); DSPT (Data Security and Protection Toolkit) compliance; Cyber Essentials Plus; ISO 13485 & ISO 37001 alignment (certification in progress); clinical safety standards DCB0129/DCB0160 with a hazard log reviewed per release; DPIA per deployment; UK GDPR compliance with surgery as controller / QuantumLoopAI as processor (Art. 6(1)(e), 9(2)(h))**. `[C]` [compliance-security; data handling helpdesk]
- **Hosting posture:** Microsoft Azure data centres in England ("NHS-approved cloud" per their claim), AES-256 encryption, TLS 1.2+, zero-trust model, immutable audit logs, annual CREST-accredited penetration testing, failover re-routing to the practice's existing phone system. `[C]`
- Independent anchor points that are genuinely checkable: G-Cloud/RM6200 listings, the named case study, and the DTAC/DSPT certificates themselves if requested (available "on request" per company). Everything else above remains `[C]` until documentation is shown. `[I]`

## 6. Market context the company targets

- NHS England's *Delivery Plan for Recovering Access to Primary Care* (2024) requires practices to let patients phone as the primary route to general practice, mandates click-to-dial/GP-level call handling improvements, and pushes modern access — the exact demand EMMA monetises. `[V]` (NHS England policy; published Jan 2024, updated May 2024)
- Known pressure inputs: rising GP appointment demand (NHS England reported record appointment volumes), the "8am rush", reception staff turnover/abuse, NHS telephony metrics reporting (level of unanswered calls, average call wait). The Conisbrough case study quantifies the pre-EMMA pain: 1,523 unanswered calls in one month of 3,488; 18-minute average waits; 50 engaged-tone overflow events. `[V]/[C]`
- NHS England continues to publish AI-in-primary-care guidance, and GPs must still own all clinical decisions — EMMA is marketed strictly as *administrative access infrastructure*, never triage. `[C]`

## 7. Competitive set (reliably public signals only)

| Competitor | Signal (public) | Notes |
| --- | --- | --- |
| **InTouchNow.ai (UK)** | Claims "first Class 1 medical device certified AI voice company", 150+ practices, 85+ languages/200+ accents, DCB0129/0160 lists medical-device registration; raised £2.3M seed (Sept 2026); pricing ~£1.00 per call | Closest direct NHS-GP competitor by public profile `[V]` (their site) |
| **General AI receptionists (US/UK): CureMD, Sully.ai, Emitrr, Smith.ai, BookedSolid, ViveoAI (UK dental)** | Hospital/clinic/private-practice focus, not NHS primary care; none publicise DTAC/DSPT posture | Adjacent, not truly head-to-head `[V]` (their marketing pages; Zeroic/DoctorConnect comparison round-ups) |
| **Paths (clinical/booking platforms): Accurx, AskMyGP, eConsult, Patient Access** | Online/queueing/triage tools; Accurx is EMMA's named integration partner (EMMA streams captured requests into it) | Complements rather than competes on voice `[C+V]` |
| **Telephony suppliers (Gamma/BT etc.) adding "AI features"** | EMMA's own FAQ warns these are resellers entering the space; frames the monee moat as clinical-specialism + interoperability | `[I]` |

**Narrow moat EMMA claims:** DTAC-first regulatory head start, call data quality for clinicians ("better notes than rushed receptionists"), human-handover design, and NHS-specific workflow config per practice — not raw voice quality. `[I]`

## 8. Product philosophy and stated culture

Company-published principles (careers page): **Patient First, Always; Clinical Safety is Non-Negotiable; Build With, Not For (co-designed with NHS staff); Simplicity Wins; Own Your Impact; Evidence Over Opinion; Remember the Waiting Room.** Hiring page values: "Impact over optics… Transparency… Ownership… Learning." The hiring process is deliberately CV-agnostic and skills-assessment-driven — this very assignment is that philosophy in practice. `[C]`

**Expectations of engineers (from JD + careers pages):** own ambiguity end-to-end ("if you see something that needs doing, it's probably your job"); instrument everything; treat evaluation as first-class (the AI/ML JD demands test plans with QA and continuous-learning loops); communicate complex AI concepts to non-technical NHS stakeholders; work asynchronously with a UK team during 9:00–18:00 UK time.

## 9. People/roles visible in hiring (tech-stack evidence)

Open roles at 5 Nov 2026 (careers/open-roles): **AI/ML & Prompt Engineer — LLM, RAG & Voice Agent** (London ×2 + India remote), **Azure DevOps / Infrastructure Engineer** (London + India), **Full Stack Developer**, **Full Stack Engineer (Backend Heavy)**, **Technical Lead** (London + India), **QA Engineer** (London + India), plus generic/ops roles. The AI/ML and Technical Lead JDs are the strongest public evidence of the engineering platform (detailed in JOB_DESCRIPTION_MAPPING.md §2). `[V]`

## 10. Summary judgement for the assignment

1. QuantumLoopAI sells *administrative patient access* to NHS primary care, not clinical AI; EMMA's value is instant answer + accurate request capture + safe escalation.
2. The company'sinternal bar is: evidence, evaluation, governance. A prototype that shows a working voice agent **plus an audit/eval/tracing story** matches the company's actual operating philosophy far better than a demo voice.
3. Its public tech stack (Azure, NestJS, Node/TS microservices, Next.js/React, Python AI, MySQL) is directly stated in two public JDs; voice-stack tools (Deepgram, ElevenLabs, Azure Speech, Whisper, OpenAI/Azure OpenAI) are named in the Technical Lead JD as things they *evaluate/integrate*. `[V]`
4. The assignment audience works UK hours; a demonstrable latency/eval table will be read as seriously as the demo itself.

## 11. Source register

**Official QuantumLoopAI** (accessed 5 Nov 2026): home (`quantumloopai.com/`), `/careers`, `/careers/open-roles`, `/helpdesk/what-is-quantumloop-ai`, `/helpdesk/about-quantumloop-ai`, `/helpdesk/our-products`, `/helpdesk/who-is-emma`, `/helpdesk/who-is-nina`, `/helpdesk/who-is-sofia`, `/helpdesk/how-does-quantumloop-ai-handle-patient-data`, `/compliance-security`, `/gp-surgeries-benefits`, `/case-studies/conisbrough-group-practice-south-yorkshire`, blog posts *Two Requests in One GP Call* (Grant, 3 Nov 2026), *AI Reception: When a Patient Asks for a Person* (Clancy, 2 Nov 2026), *Patient Details: Getting Corrections Right on GP Calls* (Zhudro, 4 Nov 2026), *AI Receptionist Benchmarks: What the Numbers Mean* (Burgess, 5 Nov 2026), `/compare`.

**Job description:** `apply.workable.com/quantumloopai/j/B7F8DA3F6C/` (Workable SPA; text reconstructed via Workable public mirrors `jobs.workable.com/view/tYD5q8zLN9ChDaVJUGEa8H/...` and Remote Rocketship archive; cross-checked with LinkedIn job 4475445319 and Indeed jk=ea9fd4cc724100c8). Related roles: Technical Lead (`jobs.workable.com/view/k4Wq5pXzB77g2vV6jqiTqw/...`), London AI/ML JD D391785516.

**Independent/third-party:** eGPlearning interview (YouTube `ZBxt6fxzfWk`, Jun 2025); Remote Rocketship company record; Tavily company knowledge record; InTouchNow.ai site & Zeroic/DoctorConnect market round-ups; UK Digital Marketplace G-Cloud service 870623262096397; gca.gov.uk RM6200.

**NHS/regulatory context:** NHS England *Delivery Plan for Recovering Access to Primary Care* (2024); NHS England DTAC page (`england.nhs.uk/digitaltechnology/standards-and-frameworks/dtac/` — standard reference; the live page did not render in this session's fetch and is cited as a known public standard); NHS DCB0129/DCB0160 clinical risk management standards; MHRA medical-device registration guidance (gov.uk).

*Not verified anywhere: revenue, ARR, named practice list beyond Conisbrough, investor names, employee count on official pages, founders' names/roles, internal tech beyond JD text.*
