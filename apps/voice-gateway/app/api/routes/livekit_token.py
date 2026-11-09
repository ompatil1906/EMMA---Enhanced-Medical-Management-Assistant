"""LiveKit room token endpoint (Day 13).

Mints short-lived JWT room tokens for browser participants joining a LiveKit
voice session. Credentials never reach the browser; practice identity is
resolved server-side.

Route: POST /api/voice/token
Body: { "room_name": "..." }
Response: { "token": "<jwt>", "room": "...", "practice_id": "...", "livekit_url": "..." }
"""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.integrations.livekit import LiveKitTokenMinter

router = APIRouter(prefix="/api/voice", tags=["voice"])

_ROOM_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class TokenRequest(BaseModel):
    room_name: str = Field(..., min_length=1, max_length=64)


class TokenResponse(BaseModel):
    token: str
    room: str
    practice_id: str | None = None
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

    minter = LiveKitTokenMinter(settings=settings)
    token = minter.mint_participant_token(
        room_name=request.room_name,
        participant_identity=participant_identity,
        practice_id=practice_id,
        ttl_seconds=3600,
    )

    return TokenResponse(
        token=token,
        room=request.room_name,
        practice_id=practice_id,
        livekit_url=settings.LIVEKIT_URL or None,
    )


__all__ = ["router"]
