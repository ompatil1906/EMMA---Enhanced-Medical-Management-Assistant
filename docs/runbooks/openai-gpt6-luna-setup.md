# OpenAI GPT-6 Luna Setup — EMMA Runbook

**Purpose:** Configure and verify OpenAI GPT-6 Luna LLM for EMMA Day 12 with
strict budget controls. Credentials live in `.env` (git-ignored) and are never
printed or committed.

**CRITICAL:** Total OpenAI budget is **$2.50** for all development, testing, and
the live judge demonstration. This is a hard constraint.

**Status:** Not live-tested — no `OPENAI_API_KEY` configured, `gpt-6-luna` model
availability unverified (no web access). This runbook documents the configuration
and budget control procedure for when credentials become available.

---

## 1. Required environment variables

Set in `apps/voice-gateway/.env` (values redacted):

| Variable | Purpose | Example |
|---|---|---|
| `OPENAI_API_KEY` | OpenAI API key | present |
| `OPENAI_MODEL` | OpenAI model | `gpt-6-luna` |
| `LLM_PROVIDER` | `openai` to enable | `openai` |

## 2. OpenAI budget allocation

| Purpose | Maximum allocation |
|---|---:|
| Integration and development testing | $0.50 |
| Regression and accuracy testing | $0.50 |
| Final demonstration reserve | $0.75 |
| Emergency reserve | $0.75 |
| **Total** | **$2.50** |

## 3. Application-level budget guard (`app/services/openai_budget.py`)

The `OpenAIBudgetGuard` enforces a configurable dev budget cap (default $1.00):

* `record_usage()` records input/output/cached tokens and estimated USD per
  call. Raises `OpenAIBudgetExceededError` when the dev cap would be exceeded.
* `snapshot()` returns cumulative state (never exposes credentials).
* `block()` / `unblock()` for emergency stop.

**Important:** Application-side accounting is NOT a replacement for the
provider's billing controls. The user should also configure a usage limit in the
OpenAI platform. This guard does NOT claim a technical hard cap.

## 3. Budget rules (non-negotiable)

* **GPT-6 Luna only** — no GPT-6.1 Terra, no expensive fallback.
* **No OpenAI Realtime audio API.**
* **No OpenAI transcription API.**
* **No unnecessary repeated calls.** One interpretation call per final caller
  turn.
* **Keep prompts short and structured.** Limit output tokens and conversation
  history. Do not send full historical transcripts.
* **Cache static instructions where supported.**
* **Track input, output and cached token usage.** Record estimated USD per
  request and cumulatively.
* **Stop OpenAI requests when the development allocation is exhausted.**
* **Preserve the final demonstration reserve ($0.75).**
* **Never automatically add credits or change billing settings.**

## 4. Verification (when credentials available)

```bash
cd apps/voice-gateway
# Confirm OpenAI provider imports
uv run python -c "from app.integrations.llm.openai_provider import OpenAILLMProvider; print('OK')"

# Run offline tests (no API credits)
uv run pytest tests/unit/test_openai_provider.py tests/unit/test_openai_budget.py -q

# Confirm settings load
uv run python -c "
from app.core.config import get_settings
s = get_settings()
print('LLM_PROVIDER:', s.LLM_PROVIDER)
print('OPENAI_MODEL:', s.OPENAI_MODEL)
print('OpenAI configured:', bool(s.OPENAI_API_KEY))
print('Dev budget:', s.OPENAI_DEV_BUDGET_USD)
print('Daily budget:', s.OPENAI_DAILY_BUDGET_USD)
"
```

## 5. Model verification

Before using `gpt-6-luna`:

1. Confirm the model ID exists at https://developers.openai.com/api/docs/pricing
2. Confirm `OPENAI_API_KEY` has access to the model
3. If the model is inaccessible, **stop and report** — do not silently switch
   to a more expensive model.

## 6. Security

* Never print, log, or commit `OPENAI_API_KEY`.
* Real providers fail fast on missing credentials — no silent mock fallback.
* Budget guard tracks tokens and estimated cost only — no prompt content.
