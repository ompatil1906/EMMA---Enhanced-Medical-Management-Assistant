"""Day 20: direct Jira sink (live product path).

The gateway can own the call record without the prototype practice API. These
tests pin the three things that make that safe to run live:

* `mock` mode is deterministic and offline; `disabled` sends nothing;
* `live` mode sends an Atlassian REST v3 payload with masked caller details and
  reports failures as data (never as success, never as an exception);
* the call-record sink selector decides where the record goes, and an
  emergency-guarded call sends nothing at all.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.integrations.jira_client import (
    JiraClient,
    caller_lines,
    mask_mobile,
    mock_issue_key,
    to_adf,
)
from app.services.call_summary import CallSummary
from app.services.caller_identity import CallerIdentity
from tests.evaluation.day9 import close, make_pipeline, run_turn


def _summary() -> CallSummary:
    return CallSummary(
        headline="Appointment request — Sarah J.",
        bullets=(
            "Caller: Sarah J. (mobile ending 077).",
            "Reason given on the intake form: Book Appointment.",
            "Requests captured: Appointment request (submitted).",
        ),
        tasks=(
            {
                "type": "appointment",
                "requestType": "appointment-request",
                "title": "Appointment request — preferred date: Tuesday",
                "status": "submitted",
            },
        ),
        outcome="completed",
        duration_seconds=42,
        turns=4,
        caller={
            "firstName": "Sarah",
            "lastName": "Jenkins",
            "mobile": "+44 7700 900077",
            "reason": "Book Appointment",
        },
    )


# --- pure helpers ---------------------------------------------------------


def test_mock_key_is_deterministic_and_opaque() -> None:
    assert mock_issue_key("call-1") == mock_issue_key("call-1")
    assert mock_issue_key("call-1") != mock_issue_key("call-2")
    assert mock_issue_key("call-1").startswith("MOCK-")


@pytest.mark.parametrize(
    ("mobile", "expected"),
    [("+44 7700 900077", "*******077"), ("07700900077", "*******077"), ("123", "123")],
)
def test_mobile_is_masked_to_the_last_three_digits(mobile: str, expected: str) -> None:
    assert mask_mobile(mobile) == expected


def test_caller_block_respects_the_pii_policy() -> None:
    caller = {"firstName": "Sarah", "lastName": "Jenkins", "mobile": "+44 7700 900077"}
    assert caller_lines(caller, "none") == []
    masked = caller_lines(caller, "masked")
    assert any("Sarah J." in line for line in masked)
    assert not any("Jenkins" in line for line in masked)
    assert not any("7700 900077" in line for line in masked)
    full = caller_lines(caller, "full")
    assert any("Sarah Jenkins" in line for line in full)
    assert any("+44 7700 900077" in line for line in full)


def test_adf_body_is_well_formed() -> None:
    adf = to_adf("line one\n\nline two")
    assert adf["type"] == "doc" and adf["version"] == 1
    assert len(adf["content"]) == 3
    assert adf["content"][0]["content"][0]["text"] == "line one"


# --- modes ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_mode_sends_nothing() -> None:
    client = JiraClient(mode="disabled")
    record = await client.create_call_issue(_summary(), practice_id="p1", call_id="c1")
    assert record.status == "disabled"
    assert record.issue_key is None


@pytest.mark.asyncio
async def test_mock_mode_makes_no_network_call() -> None:
    def _boom(request: httpx.Request) -> httpx.Response:  # pragma: no cover - guard
        raise AssertionError("mock mode must not perform network I/O")

    client = JiraClient(
        mode="mock", base_url="https://example.atlassian.net", transport=httpx.MockTransport(_boom)
    )
    record = await client.create_call_issue(_summary(), practice_id="p1", call_id="c1")
    assert record.status == "mock"
    assert record.issue_key == mock_issue_key("c1")
    assert record.issue_url == f"mock://jira/{record.issue_key}"


@pytest.mark.asyncio
async def test_live_mode_posts_a_rest_v3_issue() -> None:
    seen: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization", "")
        seen["body"] = request.read().decode()
        return httpx.Response(201, json={"key": "EMMA-42"})

    client = JiraClient(
        mode="live",
        base_url="https://example.atlassian.net",
        email="reception@example.com",
        api_token="token-not-real",
        project_key="EMMA",
        issue_type_map={"appointment-request": "Appointment"},
        transport=httpx.MockTransport(_handler),
    )
    record = await client.create_call_issue(_summary(), practice_id="p1", call_id="c1")

    assert record.status == "synced"
    assert record.issue_key == "EMMA-42"
    assert record.issue_url == "https://example.atlassian.net/browse/EMMA-42"
    assert seen["url"].endswith("/rest/api/3/issue")
    assert seen["auth"].startswith("Basic ")
    body = seen["body"]
    assert '"key":"EMMA"' in body
    assert '"name":"Appointment"' in body
    assert "[EMMA] Appointment request" in body
    # Masked by default: the full surname/mobile must not leave the process.
    assert "Jenkins" not in body
    assert "7700 900077" not in body
    await client.aclose()


@pytest.mark.asyncio
async def test_live_mode_refuses_when_not_configured() -> None:
    client = JiraClient(mode="live", base_url="https://example.atlassian.net")
    record = await client.create_call_issue(_summary(), practice_id="p1", call_id="c1")
    assert record.status == "failed"
    assert record.error == "jira is not fully configured"


@pytest.mark.asyncio
async def test_http_error_is_reported_not_raised() -> None:
    client = JiraClient(
        mode="live",
        base_url="https://example.atlassian.net",
        email="reception@example.com",
        api_token="token-not-real",
        project_key="EMMA",
        transport=httpx.MockTransport(lambda _request: httpx.Response(401, json={})),
    )
    record = await client.create_call_issue(_summary(), practice_id="p1", call_id="c1")
    assert record.status == "failed"
    assert record.error == "jira returned 401"


@pytest.mark.asyncio
async def test_existing_issue_key_is_replayed_without_a_request() -> None:
    def _boom(request: httpx.Request) -> httpx.Response:  # pragma: no cover - guard
        raise AssertionError("a replay must not create a second issue")

    client = JiraClient(
        mode="live",
        base_url="https://example.atlassian.net",
        email="reception@example.com",
        api_token="token-not-real",
        project_key="EMMA",
        transport=httpx.MockTransport(_boom),
    )
    record = await client.create_call_issue(
        _summary(), practice_id="p1", call_id="c1", existing_issue_key="EMMA-7"
    )
    assert record.status == "synced"
    assert record.issue_key == "EMMA-7"


def test_describe_is_secret_free() -> None:
    client = JiraClient(
        mode="live",
        base_url="https://example.atlassian.net",
        email="reception@example.com",
        api_token="token-not-real",
        project_key="EMMA",
    )
    described = client.describe()
    assert described["mode"] == "live"
    assert described["project_key"] == "EMMA"
    assert "token" not in str(described).lower()


# --- pipeline sink selection ---------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_jira_sink_records_the_call_without_the_practice_api() -> None:
    harness = await make_pipeline(
        responses={},
        caller=CallerIdentity(first_name="Sarah", reason="Book Appointment"),
        settings_overrides={
            "CALL_RECORD_SINK": "jira",
            "JIRA_MODE": "mock",
            "JIRA_PROJECT_KEY": "EMMA",
        },
    )
    try:
        await run_turn(harness, "thanks")
    finally:
        await close(harness)

    assert harness.practice.summaries == [], "jira sink must not use the practice API"
    assert harness.pipeline._jira_record is not None
    assert harness.pipeline._jira_record["status"] == "mock"
    assert str(harness.pipeline._jira_record["issue_key"]).startswith("MOCK-")


@pytest.mark.asyncio
async def test_pipeline_emergency_sends_nothing_to_jira() -> None:
    harness = await make_pipeline(
        responses={},
        settings_overrides={"CALL_RECORD_SINK": "jira", "JIRA_MODE": "mock"},
    )
    try:
        await run_turn(harness, "I have chest pain")
    finally:
        await close(harness)
    assert harness.pipeline._jira_record is None
    assert harness.practice.summaries == []


@pytest.mark.asyncio
async def test_pipeline_sink_none_keeps_the_record_in_process() -> None:
    harness = await make_pipeline(
        responses={},
        settings_overrides={"CALL_RECORD_SINK": "none", "JIRA_MODE": "mock"},
    )
    try:
        await run_turn(harness, "thanks")
    finally:
        await close(harness)
    assert harness.practice.summaries == []
    assert harness.pipeline._jira_record is None


@pytest.mark.asyncio
async def test_auto_sink_writes_jira_without_any_practice_backend() -> None:
    """Live product: with no practice API, `auto` records straight into Jira."""
    harness = await make_pipeline(
        responses={},
        caller=CallerIdentity(first_name="Sarah"),
        settings_overrides={"CALL_RECORD_SINK": "auto", "JIRA_MODE": "mock"},
    )
    # Simulate the deployed live product: no prototype backend configured.
    harness.pipeline._practice_client = None
    harness.pipeline._practice_id = None
    try:
        assert harness.pipeline._resolved_record_sink() == "jira"
        await run_turn(harness, "thanks")
    finally:
        await close(harness)
    assert harness.practice.summaries == []
    assert harness.pipeline._jira_record is not None
    assert str(harness.pipeline._jira_record["issue_key"]).startswith("MOCK-")


@pytest.mark.asyncio
async def test_auto_sink_prefers_jira_even_when_a_practice_backend_exists() -> None:
    """Live product: `auto` never records implicitly in the prototype backend."""
    harness = await make_pipeline(
        responses={},
        settings_overrides={"CALL_RECORD_SINK": "auto", "JIRA_MODE": "mock"},
    )
    try:
        assert harness.pipeline._resolved_record_sink() == "jira"
        await run_turn(harness, "thanks")
    finally:
        await close(harness)
    assert harness.practice.summaries == []
    assert harness.pipeline._jira_record is not None


@pytest.mark.asyncio
async def test_both_sink_records_in_practice_api_and_jira() -> None:
    harness = await make_pipeline(
        responses={},
        settings_overrides={"CALL_RECORD_SINK": "both", "JIRA_MODE": "mock"},
    )
    try:
        await run_turn(harness, "thanks")
    finally:
        await close(harness)
    assert len(harness.practice.summaries) == 1
    assert harness.pipeline._jira_record is not None


@pytest.mark.asyncio
async def test_auto_sink_is_none_when_nothing_is_configured() -> None:
    harness = await make_pipeline(
        responses={}, settings_overrides={"CALL_RECORD_SINK": "auto", "JIRA_MODE": "disabled"}
    )
    harness.pipeline._practice_client = None
    harness.pipeline._practice_id = None
    try:
        assert harness.pipeline._resolved_record_sink() == "none"
    finally:
        await close(harness)
