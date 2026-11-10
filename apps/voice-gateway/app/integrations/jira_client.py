"""Direct Jira sink for the live product (Day 20).

The prototype path posted the call record to the NestJS practice API, which
mirrored it to Jira. For a **live product** the gateway must be able to own that
record itself, without requiring the prototype backend to be deployed.

This module writes one Jira issue per call containing:

* the intake form (masked per `JIRA_PII_MODE`),
* the deterministic call summary,
* the tasks derived from captured requests,
* call metadata (opaque call id, outcome, duration, turns).

Safety rules (unchanged from the backend adapter):

* `disabled` → nothing is sent; `mock` → deterministic offline key, no network;
  `live` → Atlassian Cloud REST v3 with Basic auth. `live` without configuration
  refuses to send instead of silently degrading.
* Create is never retried automatically (a retried POST could duplicate the
  issue); idempotency is the caller's stored issue key.
* Failures are returned as data (`status="failed"`, sanitised error) — never
  raised into call teardown and never reported as success.
* The transcript is only included when the operator opted in.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from app.services.call_summary import CallSummary

logger = logging.getLogger(__name__)

JiraMode = Literal["disabled", "mock", "live"]
JiraPiiMode = Literal["full", "masked", "none"]
JiraStatus = Literal["disabled", "mock", "synced", "failed", "skipped"]

_DEFAULT_ISSUE_TYPE = "Task"


@dataclass(frozen=True, slots=True)
class JiraRecord:
    """Outcome of mirroring one call into Jira."""

    status: JiraStatus
    issue_key: str | None = None
    issue_url: str | None = None
    error: str | None = None
    mode: str = "disabled"

    @property
    def ok(self) -> bool:
        return self.status in ("mock", "synced")


def mock_issue_key(call_id: str) -> str:
    """Deterministic offline key (same call → same key, no network)."""
    digest = hashlib.sha256(call_id.encode()).hexdigest()
    return f"MOCK-{int(digest[:8], 16) % 100000:05d}"


def mask_mobile(mobile: str) -> str:
    """Keep only the last three digits of a contact number."""
    digits = [ch for ch in mobile if ch.isdigit()]
    if len(digits) <= 3:
        return "".join(digits)
    return f"{'*' * min(len(digits) - 3, 7)}{''.join(digits[-3:])}"


def caller_lines(caller: dict[str, Any], pii_mode: JiraPiiMode) -> list[str]:
    """Render the caller block under the configured PII policy."""
    if pii_mode == "none":
        return []
    first = caller.get("firstName")
    last = caller.get("lastName")
    email = caller.get("email")
    mobile = caller.get("mobile")
    reason = caller.get("reason")

    lines: list[str] = []
    if first:
        if pii_mode == "full" and last:
            lines.append(f"Name: {first} {last}")
        elif last:
            lines.append(f"Name: {first} {str(last)[:1]}.")
        else:
            lines.append(f"Name: {first}")
    else:
        lines.append("Name: not captured.")
    if email:
        lines.append(f"Email: {email}")
    if mobile:
        lines.append(
            f"Mobile: {mobile}" if pii_mode == "full" else f"Mobile: {mask_mobile(str(mobile))}"
        )
    if reason:
        lines.append(f"Reason for call: {reason}")
    return lines


def to_adf(text: str) -> dict[str, Any]:
    """Minimal Atlassian Document Format body (plain paragraphs)."""
    content = []
    for line in text.split("\n"):
        content.append(
            {"type": "paragraph", "content": []}
            if not line
            else {"type": "paragraph", "content": [{"type": "text", "text": line}]}
        )
    return {"type": "doc", "version": 1, "content": content}


class JiraClient:
    """One HTTP client per session (owns no state across calls)."""

    def __init__(
        self,
        *,
        mode: JiraMode = "disabled",
        base_url: str | None = None,
        email: str | None = None,
        api_token: str | None = None,
        project_key: str | None = None,
        issue_type_map: dict[str, str] | None = None,
        epic_key: str | None = None,
        pii_mode: JiraPiiMode = "masked",
        timeout_seconds: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._mode: JiraMode = mode
        self._base_url = (base_url or "").rstrip("/")
        self._email = email
        self._api_token = api_token
        self._project_key = project_key
        self._issue_type_map = issue_type_map or {}
        self._epic_key = epic_key
        self._pii_mode = pii_mode
        self._timeout = timeout_seconds
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    @classmethod
    def from_settings(
        cls, settings: Any, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> JiraClient:
        """Build from validated gateway settings (names only; values never logged)."""
        raw_map = getattr(settings, "JIRA_ISSUE_TYPE_MAP", "") or ""
        issue_map: dict[str, str] = {}
        for entry in str(raw_map).split(","):
            request_type, _, issue_type = entry.partition(":")
            if request_type.strip() and issue_type.strip():
                issue_map[request_type.strip()] = issue_type.strip()
        return cls(
            mode=str(getattr(settings, "JIRA_MODE", "disabled")),  # type: ignore[arg-type]
            base_url=getattr(settings, "JIRA_BASE_URL", None),
            email=getattr(settings, "JIRA_EMAIL", None),
            api_token=getattr(settings, "JIRA_API_TOKEN", None),
            project_key=getattr(settings, "JIRA_PROJECT_KEY", None),
            issue_type_map=issue_map,
            epic_key=getattr(settings, "JIRA_EPIC_KEY", None),
            pii_mode=str(getattr(settings, "JIRA_PII_MODE", "masked")),  # type: ignore[arg-type]
            timeout_seconds=float(getattr(settings, "JIRA_TIMEOUT_MS", 5000)) / 1000.0,
            transport=transport,
        )

    # --- introspection ------------------------------------------------------

    @property
    def mode(self) -> JiraMode:
        return self._mode

    @property
    def configured(self) -> bool:
        """True when a live send could actually be attempted."""
        if self._mode != "live":
            return True
        return bool(self._base_url and self._email and self._api_token and self._project_key)

    def describe(self) -> dict[str, Any]:
        """Secret-free status for `/api/v1/diagnostics`."""
        return {
            "mode": self._mode,
            "configured": self.configured,
            "project_key": self._project_key,
            "pii_mode": self._pii_mode,
        }

    # --- issue construction -------------------------------------------------

    def issue_type_for(self, request_type: str | None) -> str:
        if request_type and request_type in self._issue_type_map:
            return self._issue_type_map[request_type]
        return _DEFAULT_ISSUE_TYPE

    def describe_issue(
        self,
        summary: CallSummary,
        *,
        practice_id: str | None,
        call_id: str,
    ) -> str:
        """Human-readable issue body (masked per `JIRA_PII_MODE`)."""
        lines: list[str] = ["Caller (intake form)"]
        caller = caller_lines(summary.caller, self._pii_mode)
        lines.append("\n".join(caller) if caller else "Not captured.")
        lines.extend(["", "Call summary", "\n".join(summary.bullets)])
        if summary.tasks:
            lines.extend(["", "Tasks"])
            for task in summary.tasks:
                title = task.get("title") or "Task"
                status = task.get("status") or "captured"
                lines.append(f"- [{status}] {title}")
        lines.extend(
            [
                "",
                "Call record",
                f"Call id: {call_id}",
                f"Practice id: {practice_id or 'unset'}",
                f"Outcome: {summary.outcome}",
                f"Duration: {summary.duration_seconds}s",
                f"Turns: {summary.turns}",
            ]
        )
        if summary.transcript_excerpt:
            lines.extend(["", "Transcript excerpt", summary.transcript_excerpt])
        lines.extend(["", "Generated by EMMA — no clinical advice, no diagnosis."])
        return "\n".join(lines)

    def build_payload(
        self,
        summary: CallSummary,
        *,
        practice_id: str | None,
        call_id: str,
    ) -> dict[str, Any]:
        """REST v3 create-issue body."""
        primary_request = next(
            (str(task.get("requestType")) for task in summary.tasks if task.get("requestType")),
            None,
        )
        labels = ["emma", f"outcome-{summary.outcome}"]
        for task in summary.tasks:
            request_type = task.get("requestType")
            if isinstance(request_type, str) and request_type not in labels:
                labels.append(request_type)
        fields: dict[str, Any] = {
            "project": {"key": self._project_key},
            "summary": f"[EMMA] {summary.headline}"[:250],
            "description": to_adf(
                self.describe_issue(summary, practice_id=practice_id, call_id=call_id)
            ),
            "issuetype": {"name": self.issue_type_for(primary_request)},
            "labels": labels,
        }
        if self._epic_key:
            fields["parent"] = {"key": self._epic_key}
        return {"fields": fields}

    # --- send ---------------------------------------------------------------

    async def create_call_issue(
        self,
        summary: CallSummary,
        *,
        practice_id: str | None,
        call_id: str,
        idempotency_key: str | None = None,
        existing_issue_key: str | None = None,
    ) -> JiraRecord:
        """Create (or replay) the issue for one call. Never raises."""
        if existing_issue_key:
            return self._synced(existing_issue_key)
        if self._mode == "disabled":
            return JiraRecord(status="disabled", mode=self._mode)
        if self._mode == "mock":
            key = mock_issue_key(call_id)
            logger.info("jira mock issue", extra={"issue_key": key, "call_id": call_id})
            return JiraRecord(
                status="mock", issue_key=key, issue_url=f"mock://jira/{key}", mode="mock"
            )
        if not self.configured:
            return JiraRecord(
                status="failed",
                error="jira is not fully configured",
                mode=self._mode,
            )

        payload = self.build_payload(summary, practice_id=practice_id, call_id=call_id)
        try:
            client = self._ensure_client()
            response = await client.post("/rest/api/3/issue", json=payload)
        except (TimeoutError, httpx.TimeoutException):
            return JiraRecord(status="failed", error="jira request timed out", mode=self._mode)
        except httpx.HTTPError:
            return JiraRecord(status="failed", error="jira request failed", mode=self._mode)

        if response.status_code >= 400:
            logger.warning("jira create failed", extra={"status": response.status_code})
            return JiraRecord(
                status="failed", error=f"jira returned {response.status_code}", mode=self._mode
            )
        try:
            body = response.json()
        except ValueError:
            return JiraRecord(status="failed", error="jira response was not JSON", mode=self._mode)
        key = body.get("key") if isinstance(body, dict) else None
        if not isinstance(key, str) or not key:
            return JiraRecord(
                status="failed", error="jira response contained no issue key", mode=self._mode
            )
        return self._synced(key, idempotency_key=idempotency_key)

    async def add_comment(self, issue_key: str, text: str) -> JiraRecord:
        """Append the call summary to an existing issue (never raises)."""
        if self._mode == "disabled":
            return JiraRecord(status="disabled", mode=self._mode)
        if self._mode == "mock":
            logger.info("jira mock comment", extra={"issue_key": issue_key})
            return JiraRecord(status="mock", issue_key=issue_key, mode="mock")
        if not self.configured:
            return JiraRecord(
                status="failed", error="jira is not fully configured", mode=self._mode
            )
        try:
            client = self._ensure_client()
            response = await client.post(
                f"/rest/api/3/issue/{issue_key}/comment", json={"body": to_adf(text)}
            )
        except (TimeoutError, httpx.TimeoutException):
            return JiraRecord(status="failed", error="jira comment timed out", mode=self._mode)
        except httpx.HTTPError:
            return JiraRecord(status="failed", error="jira comment failed", mode=self._mode)
        if response.status_code >= 400:
            return JiraRecord(
                status="failed",
                error=f"jira returned {response.status_code}",
                mode=self._mode,
            )
        return self._synced(issue_key)

    # --- internals ----------------------------------------------------------

    def _synced(self, key: str, *, idempotency_key: str | None = None) -> JiraRecord:
        base = self._base_url or ""
        url = f"{base}/browse/{key}" if base else None
        logger.info(
            "jira issue synced",
            extra={"issue_key": key, "idempotency": idempotency_key},
        )
        return JiraRecord(status="synced", issue_key=key, issue_url=url, mode=self._mode)

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            credentials = base64.b64encode(
                f"{self._email or ''}:{self._api_token or ''}".encode()
            ).decode()
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(self._timeout, connect=min(self._timeout, 5.0)),
                transport=self._transport,
                headers={
                    "authorization": f"Basic {credentials}",
                    "accept": "application/json",
                    "content-type": "application/json",
                },
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def payload_fingerprint(self, payload: dict[str, Any]) -> str:
        """Stable digest of a payload (idempotency/debugging; never logged whole)."""
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


__all__ = [
    "JiraClient",
    "JiraMode",
    "JiraPiiMode",
    "JiraRecord",
    "caller_lines",
    "mask_mobile",
    "mock_issue_key",
    "to_adf",
]
