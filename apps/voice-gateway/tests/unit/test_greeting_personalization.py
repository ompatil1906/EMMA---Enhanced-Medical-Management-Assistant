"""Day 19: the greeting starts with "Hello, <FirstName>".

The name reaches the call through the browser form → token request (LiveKit) or
query parameter (WebSocket fallback) → agent → pipeline. These tests pin the
user-visible contract at each seam, and pin the fallback: a missing or unusable
name must produce the generic greeting, never a wrong or raw one.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.core.config import Settings
from app.services.caller_identity import CallerIdentity
from app.services.response_handler import DeterministicResponseHandler
from tests.evaluation.day9 import close, make_pipeline, run_turn


def _make_settings(**overrides: Any) -> Settings:
    data = {
        "LIVEKIT_API_KEY": "test-key-not-real",
        "LIVEKIT_API_SECRET": "test-secret-not-real",
        "PRACTICE_ID": "demo-practice-001",
    }
    data.update(overrides)
    return Settings(**data)


@pytest.mark.asyncio
async def test_greeting_uses_the_intake_first_name() -> None:
    handler = DeterministicResponseHandler()
    decision = await handler.greeting(first_name="Sarah")
    assert decision.response_text.startswith("Hello, Sarah.")
    assert "EMMA" in decision.response_text


@pytest.mark.asyncio
async def test_greeting_without_a_name_is_unchanged() -> None:
    handler = DeterministicResponseHandler()
    generic = (await handler.greeting()).response_text
    assert generic.startswith("Hello, I am EMMA")
    assert (await handler.greeting(first_name=None)).response_text == generic


@pytest.mark.asyncio
async def test_greet_again_uses_the_name() -> None:
    handler = DeterministicResponseHandler()
    assert (
        await handler.greet_again("Sarah")
    ).response_text == "Hello again, Sarah. How can I help?"
    assert (await handler.greet_again()).response_text.startswith("Hello! How can I help")


@pytest.mark.asyncio
async def test_pipeline_speaks_the_named_greeting_first() -> None:
    harness = await make_pipeline(
        responses={},
        caller=CallerIdentity(first_name="Sarah", last_name="Jenkins"),
    )
    try:
        first_assistant = next(
            turn for turn in harness.state.turns if turn.speaker.value == "assistant"
        )
        assert first_assistant.content.startswith("Hello, Sarah.")
    finally:
        await close(harness)


@pytest.mark.asyncio
async def test_pipeline_greets_again_with_the_name_mid_call() -> None:
    harness = await make_pipeline(
        responses={},
        caller=CallerIdentity(first_name="Sarah"),
    )
    try:
        await run_turn(harness, "Hello")
        assert harness.state.turns[-1].content.startswith("Hello again, Sarah.")
    finally:
        await close(harness)


@pytest.mark.asyncio
async def test_pipeline_without_caller_keeps_the_generic_greeting() -> None:
    harness = await make_pipeline(responses={})
    try:
        first_assistant = next(
            turn for turn in harness.state.turns if turn.speaker.value == "assistant"
        )
        assert first_assistant.content.startswith("Hello, I am EMMA")
    finally:
        await close(harness)


# --- token endpoint -------------------------------------------------------


@pytest.fixture()
def patched_minter() -> Any:
    with patch("app.api.routes.livekit_token.LiveKitTokenMinter") as mock_minter_class:
        mock_minter = MagicMock()
        mock_minter.mint_participant_token.return_value = "mock-jwt-token-12345"
        mock_minter_class.return_value = mock_minter
        yield mock_minter


def _client() -> Any:
    from fastapi.testclient import TestClient

    from app.main import create_app

    return TestClient(create_app())


def test_token_endpoint_accepts_and_sanitises_the_caller(patched_minter: Any) -> None:
    settings = _make_settings()
    client = _client()
    with patch("app.api.routes.livekit_token.get_settings", return_value=settings):
        response = client.post(
            "/api/voice/token",
            json={
                "room_name": "emma-voice",
                "caller": {
                    "firstName": "  sarah ",
                    "lastName": "jenkins",
                    "mobile": "+44 7700 900077",
                    "reason": "Book Appointment",
                },
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert body["caller_first_name"] == "Sarah"
    kwargs = patched_minter.mint_participant_token.call_args.kwargs
    assert kwargs["caller_first_name"] == "Sarah"
    # Only the first name is tokenised; the rest stays server-side.
    assert "jenkins" not in str(kwargs).lower()
    assert "7700" not in str(kwargs)


def test_token_endpoint_without_caller_still_works(patched_minter: Any) -> None:
    settings = _make_settings()
    client = _client()
    with patch("app.api.routes.livekit_token.get_settings", return_value=settings):
        response = client.post("/api/voice/token", json={"room_name": "emma-voice"})
    assert response.status_code == 200
    assert response.json()["caller_first_name"] is None
    assert patched_minter.mint_participant_token.call_args.kwargs["caller_first_name"] is None


def test_token_endpoint_rejects_an_oversized_name(patched_minter: Any) -> None:
    settings = _make_settings()
    client = _client()
    with patch("app.api.routes.livekit_token.get_settings", return_value=settings):
        response = client.post(
            "/api/voice/token",
            json={"room_name": "emma-voice", "caller": {"firstName": "a" * 200}},
        )
    assert response.status_code == 422
    patched_minter.mint_participant_token.assert_not_called()


# --- token minting --------------------------------------------------------


@pytest.mark.parametrize("first_name", ["Sarah", None])
def test_minter_carries_the_name_in_name_and_metadata(first_name: str | None) -> None:
    """The agent reads the name from `participant.name` / metadata JSON."""
    import json

    import jwt as pyjwt

    from app.integrations.livekit import LiveKitTokenMinter

    settings = _make_settings(LIVEKIT_URL="ws://127.0.0.1:7880")
    token = LiveKitTokenMinter(settings=settings).mint_participant_token(
        room_name="emma-voice",
        participant_identity="emma-caller-abc123",
        practice_id="demo-practice-001",
        caller_first_name=first_name,
    )
    claims = pyjwt.decode(token, options={"verify_signature": False})

    assert claims["sub"] == "emma-caller-abc123"  # identity stays PII-free
    assert claims["name"] == (first_name or "EMMA Caller")
    assert json.loads(claims["metadata"]) == {
        "practice_id": "demo-practice-001",
        "first_name": first_name,
    }


# --- LiveKit agent name resolution ----------------------------------------


class _FakeParticipant:
    def __init__(
        self, identity: str, *, name: str | None = None, metadata: str | None = None
    ) -> None:
        self.identity = identity
        self.name = name
        self.metadata = metadata


class _FakeRoom:
    def __init__(self, participants: dict[str, Any]) -> None:
        self.remote_participants = participants


def test_agent_resolves_the_caller_name_from_the_room() -> None:
    from app.integrations.livekit.agent import _caller_identity

    room = _FakeRoom({"emma-caller-abc": _FakeParticipant("emma-caller-abc", name="sarah")})
    identity = _caller_identity(room, None)
    assert identity is not None
    assert identity.first_name == "Sarah"


def test_agent_falls_back_to_token_metadata() -> None:
    import json

    from app.integrations.livekit.agent import _caller_identity

    room = _FakeRoom(
        {
            "emma-caller-abc": _FakeParticipant(
                "emma-caller-abc",
                metadata=json.dumps({"practice_id": "demo-practice-001", "first_name": "jenny"}),
            )
        }
    )
    identity = _caller_identity(room, None)
    assert identity is not None
    assert identity.first_name == "Jenny"


def test_agent_ignores_unusable_names_and_non_callers() -> None:
    from app.integrations.livekit.agent import _caller_identity

    assert _caller_identity(_FakeRoom({}), None) is None
    assert (
        _caller_identity(
            _FakeRoom(
                {
                    "emma-caller-abc": _FakeParticipant("emma-caller-abc", name="1234"),
                    "probe-1": _FakeParticipant("probe-1", name="probe"),
                }
            ),
            None,
        )
        is None
    )
