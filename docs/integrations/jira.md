# Jira integration — call records, summaries and tasks

**Status:** implemented and tested offline (mock mode). Live Atlassian Cloud sync
is implemented against the documented REST v3 API and is **not** exercised in
this environment (no credentials). Prototype only — no real patient data.

## 1. What it does

Every completed call produces one deterministic record:

| Section | Source | Notes |
|---|---|---|
| Caller (intake form) | Browser form → gateway (sanitised) | Masked by policy (§4) |
| Call summary | `app/services/call_summary.py` (deterministic) | Built from conversation state, never a model by default |
| Tasks | Captured requests + statuses | e.g. `appointment-request` → `submitted` |
| Call record | Call id, practice id, outcome, duration, turns | Operational metadata |

The voice gateway never talks to Jira. It posts the summary to the practice API,
which owns the credentials and the idempotency record:

```
browser form ─► gateway (greeting + conversation)
                    │  POST /api/v1/calls                    (call record)
                    │  POST /api/v1/calls/:id/summary        (form + summary + tasks)
                    ▼
              practice API ──► Jira REST v3 POST /rest/api/3/issue
```

## 2. Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/calls/:id/summary` | Store the call summary and mirror it to Jira. One summary per call: a replay returns `200` + `meta.existing = true` and never creates a second issue. |
| `GET` | `/api/v1/calls/:id/summary` | Read the record back: form, summary, tasks, Jira status/key/URL. |
| `POST` | `/api/v1/integrations/jira/sync` | Staff-initiated re-sync (`{ "callId": "…" }`); replay-safe. |
| `GET` | `/api/v1/health` | Exposes `jira: { mode, ready }` — no secrets. |

Response `jira` block:

```json
{ "status": "synced", "issueKey": "EMMA-42",
  "issueUrl": "https://example.atlassian.net/browse/EMMA-42", "error": null }
```

`status` is `disabled | mock | synced | failed`. A failure is always reported —
never presented as success, and it never affects the voice call (which has
already ended).

## 3. Modes

| `JIRA_MODE` | Behaviour |
|---|---|
| `disabled` (default) | No issue is created; the summary is still stored. |
| `mock` | Offline/demo: a deterministic `MOCK-#####` key is returned, no network call. Used by the test suite and demos. |
| `live` | Atlassian Cloud REST v3 with Basic auth (`JIRA_EMAIL:JIRA_API_TOKEN`). Missing configuration fails fast at startup (no silent fallback to mock). |

## 4. Personal data policy (`JIRA_PII_MODE`)

| Mode | What reaches Jira |
|---|---|
| `masked` (default) | First name + last initial (`Sarah J.`), mobile ending in 3 digits (`*******077`), stated reason for calling |
| `full` | Every captured field, verbatim |
| `none` | No caller block at all |

The practice database always stores the normalised form; only the external copy
is masked. Transcript excerpts are **opt-in** (`CALL_SUMMARY_INCLUDE_TRANSCRIPT`)
and off by default — the summary is derived from state, not from the transcript.

## 5. Issue shape

* **Summary:** `[EMMA] <headline>` (e.g. `[EMMA] Appointment request — Sarah J.`)
* **Issue type:** `JIRA_ISSUE_TYPE_MAP` lookup by request type, else `Task`
* **Labels:** `emma`, `outcome-<outcome>`, plus each request type
* **Description:** Atlassian Document Format paragraphs — caller block, summary,
  task checklist (`- [submitted] …`), call record, prototype disclaimer
* **Parent:** `JIRA_EPIC_KEY` when set

## 6. Configuration

Gateway (`apps/voice-gateway/.env`):

```bash
CALL_SUMMARY_ENABLED=true
CALL_SUMMARY_PROVIDER=deterministic   # deterministic | llm
CALL_SUMMARY_INCLUDE_TRANSCRIPT=false
CALL_SUMMARY_TIMEOUT=2                # hard ceiling on teardown delay (seconds)
GENERAL_CHAT_ENABLED=true             # everyday conversation
GENERAL_CHAT_MAX_SENTENCES=2
```

Practice API (`apps/practice-api/.env`):

```bash
JIRA_MODE=disabled                    # disabled | mock | live
JIRA_BASE_URL=https://your-site.atlassian.net
JIRA_EMAIL=reception@your-practice.example
JIRA_API_TOKEN=                       # ignored .env only
JIRA_PROJECT_KEY=EMMA
JIRA_ISSUE_TYPE_MAP=appointment-request:Appointment,prescription-request:Prescription
JIRA_EPIC_KEY=
JIRA_PII_MODE=masked                  # masked | full | none
JIRA_TIMEOUT_MS=5000
```

Never commit a token. Only variable **names** appear in this document.

## 7. Idempotency and failure behaviour

* `call_summaries.call_id` is unique: a retried submission returns the stored
  row (no second summary).
* `call_summaries.jira_issue_key` is written once and replayed: a retry, restart
  or manual re-sync returns the same issue.
* Jira failures are stored in `jira_sync_error` (status code / transport class
  only) and surfaced in the response; the summary row is still persisted.
* The end-of-call submission is bounded (`CALL_SUMMARY_TIMEOUT`, max 10 s) and
  never blocks teardown; a failed submission is not spoken during the call.
* **Emergency calls submit nothing** — the emergency guard's "zero
  administrative actions" invariant covers the summary too.

## 8. Verification

```bash
# Practice API: adapter + endpoint tests (mock mode, no network)
cd apps/practice-api && pnpm test        # includes jira.spec.ts + call-summary.spec.ts

# Voice gateway: summary + submission behaviour
cd apps/voice-gateway && uv run pytest -o addopts="" -q tests/unit/test_call_summary.py
```

Live Atlassian verification is out of scope for this prototype: it requires a
real site, project and API token, and must never be exercised with patient data.
