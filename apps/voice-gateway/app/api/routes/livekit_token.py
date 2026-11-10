"""LiveKit room token endpoint (Day 13).

Mints short-lived JWT room tokens for browser participants joining a LiveKit
voice session. Credentials never reach the browser; practice identity is
resolved server-side.

Route: POST /api/voice/token
Body: { "room_name": "...", "caller": { "firstName": "...", ... } }
Response: {
  "token": "<jwt>", "room": "...", "practice_id": "...",
  "livekit_url": "...", "caller_first_name": "Sarah" | null
}

Day 19: the optional `caller` block is the browser intake form. It is sanitised
server-side (`app.services.caller_identity`) and only the first name reaches the
token (participant name + metadata) so the agent can greet the caller by name.
The rest of the form is never trusted from the client for anything else.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.integrations.livekit import LiveKitTokenMinter
from app.services.caller_identity import parse_caller

router = APIRouter(prefix="/api/voice", tags=["voice"])

_ROOM_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class CallerDto(BaseModel):
    """Intake identity from the browser form (all fields optional).

    Values are re-normalised server-side; length caps here only bound the
    payload so an oversized field is rejected before it reaches the sanitiser.
    """

    model_config = {"extra": "ignore", "populate_by_name": True}

    firstName: str | None = Field(default=None, max_length=80)
    lastName: str | None = Field(default=None, max_length=80)
    email: str | None = Field(default=None, max_length=120)
    mobile: str | None = Field(default=None, max_length=40)
    reason: str | None = Field(default=None, max_length=300)


class TokenRequest(BaseModel):
    model_config = {"extra": "ignore"}

    room_name: str = Field(..., min_length=1, max_length=64)
    caller: CallerDto | None = None


class TokenResponse(BaseModel):
    token: str
    room: str
    practice_id: str | None = None
    # Sanitised first name the greeting will use (echoed for UI/debug only).
    caller_first_name: str | None = None
    # The LiveKit WebSocket URL the browser connects to (non-secret, local dev
    # e.g. ws://127.0.0.1:7880). livekit-client's `Room.connect(url, token)`
    # requires the URL explicitly; the JWT does not carry it.
    livekit_url: str | None = None


@router.post("/token", response_model=TokenResponse)
async def mint_token(request: TokenRequest) -> TokenResponse:
    """Mint a LiveKit access token for one browser participant.

    The practice ID is resolved server-side from configuration — the client
    cannot choose another practice's identity. Room names are validated to
    prevent injection or traversal.
    """
    settings = get_settings()

    if not settings.LIVEKIT_API_KEY or not settings.LIVEKIT_API_SECRET:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LiveKit is not configured on the server",
        )

    # Validate the room name.
    if not _ROOM_NAME_RE.match(request.room_name):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid room name",
        )

    # Server-side practice identity — never trusted from the client.
    practice_id = settings.PRACTICE_ID or "demo-practice-001"

    # Participant identity: deterministic but opaque; no PII.
    import uuid

    participant_identity = f"emma-caller-{uuid.uuid4().hex[:8]}"

    # Intake identity: sanitised, and only the first name is tokenised.
    caller = parse_caller(request.caller.model_dump() if request.caller else None)

    minter = LiveKitTokenMinter(settings=settings)
    token = minter.mint_participant_token(
        room_name=request.room_name,
        participant_identity=participant_identity,
        practice_id=practice_id,
        ttl_seconds=3600,
        caller_first_name=caller.first_name,
        caller=caller,
    )

    return TokenResponse(
        token=token,
        room=request.room_name,
        practice_id=practice_id,
        livekit_url=settings.LIVEKIT_URL or None,
        caller_first_name=caller.first_name,
    )


__all__ = ["router"]
