"""Typed async client for the NestJS practice backend (`apps/practice-api`).

Honesty rules (assignment §11):
* The voice agent NEVER claims persistence unless the API returned a 2xx that
  carried the created record — every call returns a `SubmissionOutcome`, never
  a silent `None` or an optimistic bool.
* Retries only for server-side-idempotent operations: call upsert and
  request submission (which carries an `Idempotency-Key`). Handoffs and audit
  events are submitted at most once (append-only and non-idempotent server-side).
* Payloads/secrets are never logged — attempts log operation + outcome only.
* No practice-API URL configured → `disabled` outcome; nothing is attempted.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Literal

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SubmissionOutcome:
    """Structured result of one practice-API submission attempt sequence."""

    status: Literal[
        "confirmed", "conflict", "rejected", "timeout", "unavailable", "disabled", "skipped"
    ]
    detail: str = ""
    record_id: str | None = None
    http_status: int | None = None

    @property
    def confirmed(self) -> bool:
        return self.status == "confirmed"


@dataclass(frozen=True)
class PracticeCallRecord:
    """Minimal typed projection of the API's `data` envelope for calls."""

    id: str
    status: str | None = None
    external_call_id: str | None = None


def build_request_idempotency_key(
    *, practice_id: str, call_sid: str, sequence: int, request_type: str
) -> str:
    """Deterministic key scoped to (practice, call, utterance, request type)."""
    digest = uuid.uuid5(uuid.NAMESPACE_URL, f"{practice_id}|{call_sid}|{sequence}|{request_type}")
    return str(digest)


class PracticeApiClient:
    """Small, bounded HTTP client; every method returns a structured outcome."""

    def __init__(
        self,
        *,
        base_url: str | None,
        timeout: float = 5.0,
        max_attempts: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = (base_url or "").rstrip("/")
        self._timeout = timeout
        self._max_attempts = max(1, max_attempts)
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    def _ensure_client(self) -> httpx.AsyncClient | None:
        """Lazy client (pipeline holds the instance without an `async with`)."""
        if self._client is None and self._base_url:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(self._timeout, connect=min(self._timeout, 5.0)),
                transport=self._transport,
                headers={"user-agent": "emma-voice-gateway/0.3.0"},
            )
        return self._client

    async def __aenter__(self) -> PracticeApiClient:
        self._ensure_client()
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # --- operations ---------------------------------------------------------------

    async def submit_call(self, *, practice_id: str, external_call_id: str) -> SubmissionOutcome:
        """Create-or-fetch the call record (idempotent server-side → retryable)."""
        if not self._base_url:
            return SubmissionOutcome(status="disabled", detail="PRACTICE_API_BASE_URL unset")
        payload: dict[str, Any] = {"practiceId": practice_id, "externalCallId": external_call_id}
        return await self._post("/api/v1/calls", payload, retryable=True, op="submit_call")

    async def submit_request(
        self,
        *,
        practice_id: str,
        idempotency_key: str,
        request_type: str,
        structured_payload: dict[str, Any],
        call_id: str | None = None,
    ) -> SubmissionOutcome:
        """Submit a structured patient request (idempotency enforced server-side)."""
        if not self._base_url:
            return SubmissionOutcome(status="disabled", detail="PRACTICE_API_BASE_URL unset")
        payload: dict[str, Any] = {
            "practiceId": practice_id,
            "requestType": request_type,
            "structuredPayload": structured_payload,
        }
        if call_id:
            payload["callId"] = call_id
        return await self._post(
            "/api/v1/requests",
            payload,
            retryable=True,
            op="submit_request",
            headers={"Idempotency-Key": idempotency_key},
        )

    async def submit_handoff(
        self,
        *,
        practice_id: str,
        reason: str,
        destination: str,
        call_id: str | None = None,
    ) -> SubmissionOutcome:
        """Record a handoff as `requested` — never pre-claims a completed transfer."""
        if not self._base_url:
            return SubmissionOutcome(status="disabled", detail="PRACTICE_API_BASE_URL unset")
        payload: dict[str, Any] = {
            "practiceId": practice_id,
            "reason": reason,
            "destination": destination,
        }
        if call_id:
            payload["callId"] = call_id
        return await self._post("/api/v1/handoffs", payload, retryable=False, op="submit_handoff")

    async def append_audit_event(
        self,
        *,
        practice_id: str,
        event_type: str,
        event_payload: dict[str, Any],
        call_id: str | None = None,
    ) -> SubmissionOutcome:
        """Append-only audit event (single attempt — no retry on append-only ops)."""
        if not self._base_url:
            return SubmissionOutcome(status="disabled", detail="PRACTICE_API_BASE_URL unset")
        payload: dict[str, Any] = {
            "practiceId": practice_id,
            "eventType": event_type,
            "eventPayload": event_payload,
        }
        if call_id:
            payload["callId"] = call_id
        return await self._post("/api/v1/audit-events", payload, retryable=False, op="append_audit")

    async def get_call(self, call_id: str) -> SubmissionOutcome:
        """Read-only lookup (safe → retryable)."""
        if not self._base_url:
            return SubmissionOutcome(status="disabled", detail="PRACTICE_API_BASE_URL unset")
        client = self._ensure_client()
        if client is None:
            return SubmissionOutcome(status="disabled", detail="PRACTICE_API_BASE_URL unset")
        attempt = 0
        while True:
            attempt += 1
            try:
                response = await client.get(f"/api/v1/calls/{call_id}")
            except (TimeoutError, httpx.TimeoutException):
                return SubmissionOutcome(status="timeout", detail=f"get_call({call_id[:8]})")
            except httpx.HTTPError as exc:
                return SubmissionOutcome(status="unavailable", detail=str(exc))
            if response.status_code in (500, 502, 503, 504) and attempt < self._max_attempts:
                await _brief_backoff()
                continue
            record = self._parse_envelope(response)
            if record is None:
                return SubmissionOutcome(
                    status="unavailable",
                    detail=f"get_call unexpected response {response.status_code}",
                )
            return SubmissionOutcome(
                status="confirmed",
                detail="fetched",
                record_id=str(record.get("id", "")) or None,
                http_status=response.status_code,
            )

    # --- internals ------------------------------------------------------------------

    async def _post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        retryable: bool,
        op: str,
        headers: dict[str, str] | None = None,
    ) -> SubmissionOutcome:
        client = self._ensure_client()
        if client is None:
            return SubmissionOutcome(status="disabled", detail="PRACTICE_API_BASE_URL unset")
        attempt = 0
        while True:
            attempt += 1
            try:
                response = await client.post(path, json=payload, headers=headers or {})
            except (TimeoutError, httpx.TimeoutException):
                outcome = SubmissionOutcome(
                    status="timeout", detail=f"{op} attempt {attempt}/{self._max_attempts}"
                )
                logger.warning("practice-api timeout", extra={"op": op, "attempt": attempt})
                return outcome
            except httpx.HTTPError as exc:
                outcome = SubmissionOutcome(status="unavailable", detail=str(exc))
                logger.warning("practice-api unreachable", extra={"op": op, "attempt": attempt})
                return outcome

            if (
                response.status_code in (500, 502, 503, 504)
                and retryable
                and attempt < self._max_attempts
            ):
                logger.warning(
                    "practice-api server error; retrying",
                    extra={"op": op, "attempt": attempt, "status": response.status_code},
                )
                await _brief_backoff()
                continue

            if response.status_code == 409:
                code = self._error_code(response)
                logger.warning("practice-api conflict", extra={"op": op, "code": code})
                return SubmissionOutcome(
                    status="conflict",
                    detail=code or "conflict",
                    http_status=response.status_code,
                )
            if 400 <= response.status_code < 500:
                code = self._error_code(response)
                logger.warning(
                    "practice-api rejected", extra={"op": op, "status": response.status_code}
                )
                return SubmissionOutcome(
                    status="rejected",
                    detail=code or response.reason_phrase or "rejected",
                    http_status=response.status_code,
                )
            if response.status_code >= 500:
                return SubmissionOutcome(
                    status="unavailable",
                    detail=f"{op} server error {response.status_code}",
                    http_status=response.status_code,
                )
            record = self._parse_envelope(response)
            if record is None:
                return SubmissionOutcome(
                    status="unavailable",
                    detail=f"{op} unexpected response body",
                    http_status=response.status_code,
                )
            logger.info("practice-api confirmed", extra={"op": op, "status": response.status_code})
            return SubmissionOutcome(
                status="confirmed",
                detail="confirmed",
                record_id=str(record.get("id", "")) or None,
                http_status=response.status_code,
            )

    @staticmethod
    def _parse_envelope(response: httpx.Response) -> dict[str, Any] | None:
        """Parse `{ data: { ... } }`; returns None when the shape cannot be trusted."""
        try:
            body = response.json()
        except ValueError:
            return None
        data = body.get("data") if isinstance(body, dict) else None
        if isinstance(data, dict) and data.get("id"):
            return data
        return None

    @staticmethod
    def _error_code(response: httpx.Response) -> str | None:
        try:
            body = response.json()
        except ValueError:
            return None
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict):
            code = error.get("code")
            return code if isinstance(code, str) else None
        return None


async def _brief_backoff() -> None:
    await asyncio.sleep(0.05)
