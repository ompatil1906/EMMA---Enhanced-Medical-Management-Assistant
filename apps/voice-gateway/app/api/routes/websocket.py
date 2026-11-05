"""`/ws/exotel` — transport orchestration for the Exotel AgentStream protocol.

Protocol knowledge lives in `app.integrations.exotel` (verified schemas +
adapter); audio behaviour lives in `app.services`. This module coordinates:

    accept → authenticate → register session → message loop → guaranteed cleanup

Message-loop policies are OUR Day-2 decisions (documented in
docs/integrations/exotel-protocol.md) — they are not Exotel-mandated rules:

| situation                     | action                          | close code    |
|-------------------------------|---------------------------------|---------------|
| authentication failure        | refuse connection               | 1008          |
| unsupported media negotiation | reject stream                   | 1008          |
| malformed / invalid frames    | tolerate up to MAX_CONSECUTIVE_ERRORS, then close | 1003 |
| oversized message             | reject immediately              | 1009          |
| provider `stop` or client close | normal shutdown               | 1000          |
| server-side unexpected error  | best-effort close               | 1011          |
| session cap reached           | refuse new session              | 1013          |
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.domain.audio import SUPPORTED_SAMPLE_RATES, AudioFormat
from app.domain.session import VoiceSession
from app.integrations.exotel.adapter import (
    ExotelAdapter,
    build_auth_token,
    build_outbound_media_envelope,
)
from app.integrations.exotel.errors import ExotelProtocolError
from app.integrations.exotel.schemas import (
    ExotelConnected,
    ExotelDtmf,
    ExotelMarkIn,
    ExotelMedia,
    ExotelStart,
    ExotelStop,
    ExotelUnknownEvent,
)
from app.services.echo_service import EchoService

logger = get_logger(__name__)
router = APIRouter()

# Close codes (our policy layer above — ordinary RFC 6455 codes, 1013 in use):
_CLOSE_NORMAL = 1000
_CLOSE_PROTOCOL_ERROR = 1003
_CLOSE_NEGOTIATION = 1008
_CLOSE_TOO_BIG = 1009
_CLOSE_INTERNAL = 1011
_CLOSE_SESSIONS_EXHAUSTED = 1013


class _NegotiationRejection(Exception):
    """Route-level: stream cannot be negotiated → close with (code, reason)."""

    def __init__(self, code: int, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


def _audio_format_from_query(declared_rate: str) -> AudioFormat:
    """Parse the verified `?sample-rate=` connection option (8000|16000|24000)."""
    try:
        rate = int(declared_rate)
    except ValueError as exc:
        raise _NegotiationRejection(_CLOSE_NEGOTIATION, "unsupported sample rate") from exc
    if rate not in SUPPORTED_SAMPLE_RATES:
        raise _NegotiationRejection(_CLOSE_NEGOTIATION, "unsupported sample rate")
    return AudioFormat(encoding="PCM_L16", sample_rate=rate, channels=1)


async def _safe_close(ws: WebSocket, *, code: int, reason: str) -> None:
    """Best-effort close that never breaks the cleanup path if the socket is already gone."""
    try:
        await ws.close(code=code, reason=reason)
    except Exception:
        logger.debug("ws close skipped (socket already gone)", extra={"close_code": code})


def _session_log(session: VoiceSession, **extra: object) -> dict[str, object]:
    """Standard session-side log context (ids/counters only; no payloads, no PII)."""
    ctx: dict[str, object] = {
        "session_id": session.session_id,
        "frames_in": session.frames_received,
        "frames_out": session.frames_sent,
    }
    for key, value in extra.items():
        if value is not None:
            ctx[key] = value
    return ctx


def _connection_authorized(ws: WebSocket, settings: Settings) -> bool:
    """Mode-split authentication; simulation and provider paths never share logic.

    * live mode  : `Authorization: Basic base64(EXOTEL_API_KEY:EXOTEL_API_TOKEN)` —
      as verified for AgentStream WSS. NOTE (blocker, see protocol doc): whether
      Exotel performs additional server-side signing on connections it *opens*
      is not fully verifiable from public docs; this check covers connections
      we receive and is documented as our implementation choice.
    * simulation : optional `X-WS-Token` shared secret. Unset → open local dev.
    """
    if settings.EXOTEL_MODE == "live":
        if not settings.exotel_live_credentials_configured:
            return False
        auth_header = ws.headers.get("authorization")
        if not auth_header:
            return False
        scheme, _, token = auth_header.partition(" ")
        if scheme.lower() != "basic" or not token:
            return False
        expected_body = build_auth_token(
            settings.EXOTEL_API_KEY or "", settings.EXOTEL_API_TOKEN or ""
        )
        return hmac.compare_digest(token.strip().encode("ascii"), expected_body.encode("ascii"))
    expected_token = settings.WS_AUTH_TOKEN
    if expected_token is None:
        return True  # open local development (readiness exposes this decision)
    supplied_token = ws.headers.get("x-ws-token")
    if supplied_token is None:
        return False
    return hmac.compare_digest(supplied_token, expected_token)


@router.websocket("/ws/exotel")
async def exotel_media_stream(websocket: WebSocket) -> None:
    """Coordinate one Exotel(‑compatible) media-stream session (see module docstring)."""
    settings = get_settings()
    state = websocket.scope["app"].state.gateway
    adapter, echo = ExotelAdapter(), EchoService()

    await websocket.accept()

    if not _connection_authorized(websocket, settings):
        logger.warning(
            "ws connection refused: authentication failed",
            extra={"provider_mode": settings.EXOTEL_MODE},
        )
        await _safe_close(websocket, code=_CLOSE_NEGOTIATION, reason="authentication failed")
        return

    if state.registry.count() >= settings.MAX_CONCURRENT_SESSIONS:
        logger.warning(
            "ws connection refused: session limit reached",
            extra={"active_sessions": state.registry.count()},
        )
        await _safe_close(websocket, code=_CLOSE_SESSIONS_EXHAUSTED, reason="session limit reached")
        return

    # Verified connection option: sample rate via `?sample-rate=` query parameter.
    query_format: AudioFormat | None = None
    declared_rate = websocket.query_params.get("sample-rate")
    if declared_rate is not None:
        try:
            query_format = _audio_format_from_query(declared_rate)
        except _NegotiationRejection as rejection:
            logger.warning(
                "ws connection refused: unsupported sample rate", extra={"declared": declared_rate}
            )
            await _safe_close(websocket, code=rejection.code, reason=rejection.reason)
            return

    session = VoiceSession(provider=settings.EXOTEL_MODE)
    if query_format is not None:
        session.audio_format = query_format
    state.registry.register(session)
    logger.info("ws session opened", extra=_session_log(session))

    consecutive_errors = 0
    exit_close: tuple[int, str] | None = None  # the single close frame the server emits at exit

    def protocol_rejected(detail: str) -> bool:
        """Tolerance-budget hook: log a malformed frame; True → close now with 1003."""
        nonlocal consecutive_errors
        consecutive_errors += 1
        logger.warning(
            "ws frame rejected (tolerated)",
            extra=_session_log(
                session,
                detail=detail,
                consecutive_errors=consecutive_errors,
                error_budget=settings.MAX_CONSECUTIVE_ERRORS,
            ),
        )
        return consecutive_errors >= settings.MAX_CONSECUTIVE_ERRORS

    try:
        while True:
            try:
                raw = await websocket.receive()
            except WebSocketDisconnect as exc:
                logger.info(
                    "ws client disconnected unexpectedly",
                    extra=_session_log(session, peer_close_code=exc.code),
                )
                break
            if raw.get("type") == "websocket.disconnect":
                logger.info(
                    "ws client disconnect received",
                    extra=_session_log(session, peer_close_code=raw.get("code")),
                )
                break

            text = raw.get("text")
            event = None
            detail: str | None = None

            if text is not None and len(text) > settings.MAX_WS_MESSAGE_BYTES:
                logger.warning(
                    "ws message rejected: oversized",
                    extra=_session_log(
                        session, received_bytes=len(text), limit=settings.MAX_WS_MESSAGE_BYTES
                    ),
                )
                exit_close = (_CLOSE_TOO_BIG, "message size limit exceeded")
                break

            if text is None:
                detail = "non-text WebSocket frame (text JSON expected)"
            else:
                try:
                    event = adapter.parse_inbound(text)
                except ExotelProtocolError as exc:
                    detail = str(exc)

            if detail is not None:
                if protocol_rejected(detail):
                    exit_close = (_CLOSE_PROTOCOL_ERROR, "protocol error tolerance exhausted")
                    break
                continue
            consecutive_errors = 0  # one valid frame resets the tolerance budget

            match event:
                case ExotelConnected():
                    logger.debug("stream connected event", extra=_session_log(session))
                case ExotelStart():
                    try:
                        call_started = adapter.handle_start(event)
                    except ValidationError as exc:
                        # Strict negotiation (our policy): unusable stream parameters
                        # (e.g. unsupported sample rate) → reject, do not guess.
                        logger.warning(
                            "ws start rejected: unsupported media negotiation",
                            extra=_session_log(session, detail=str(exc.errors()[0]["msg"])),
                        )
                        exit_close = (_CLOSE_NEGOTIATION, "unsupported media format")
                        break
                    previous_id = session.adopt_stream(
                        stream_sid=event.stream_sid, call_sid=event.call_sid
                    )
                    state.registry.adopt(session, previous_id)
                    session.audio_format = call_started.audio_format or session.audio_format
                    logger.info(
                        "call started",
                        extra=_session_log(
                            session,
                            call_sid=session.call_sid,
                            media_format=session.audio_format.model_dump(),
                        ),
                    )
                case ExotelMedia():
                    if session.stream_sid is None:
                        # Verified flow starts with `start`; tolerate provider drift,
                        # outbound envelopes will omit stream_sid until it arrives.
                        logger.debug(
                            "media frame before start (no stream_sid yet)",
                            extra=_session_log(session),
                        )
                    try:
                        audio_in = adapter.handle_media(
                            event,
                            session_id=session.session_id,
                            audio_format=session.audio_format,
                            sequence=session.frames_received + 1,
                            max_chunk_bytes=settings.EXOTEL_MAX_CHUNK_BYTES,
                        )
                    except ExotelProtocolError as exc:  # invalid base64 / oversized chunk
                        if protocol_rejected(str(exc)):
                            exit_close = (_CLOSE_PROTOCOL_ERROR, "audio payload rejected")
                            break
                        continue
                    session.frames_received += 1
                    session.bytes_received += len(audio_in.payload)
                    audio_out = await echo.process(audio_in)  # Day 3: VAD → STT → LLM → TTS here
                    outbound_json = build_outbound_media_envelope(
                        audio_out, stream_sid=session.stream_sid
                    )
                    try:
                        await websocket.send_text(outbound_json)
                    except (WebSocketDisconnect, RuntimeError):
                        logger.warning(
                            "ws echo send failed (peer gone)", extra=_session_log(session)
                        )
                        break
                    session.frames_sent += 1
                    session.bytes_sent += len(audio_out.payload)
                case ExotelStop():
                    logger.info(
                        "stream stopped by provider",
                        extra=_session_log(session, stop_reason=event.reason),
                    )
                    exit_close = (_CLOSE_NORMAL, "stop")
                    break
                case ExotelDtmf():
                    # DTMF digits are NOT logged (could carry IVR codes/pins).
                    logger.debug("dtmf event tolerated", extra=_session_log(session))
                case ExotelMarkIn():
                    # Day 3 note: `clear` (barge-in flush) is the bot→Exotel answer
                    # direction; inbound marks matter once TTS markers exist.
                    logger.debug("mark event tolerated", extra=_session_log(session))
                case ExotelUnknownEvent():
                    logger.debug(
                        "unknown event tolerated (no crash)",
                        extra=_session_log(session, event=event.event),
                    )
                case _:
                    # Parse stage guarantees one of the above; defensive ack only.
                    logger.debug("unmodelled event acked", extra=_session_log(session))

    except Exception:
        logger.exception("ws session failed unexpectedly", extra=_session_log(session))
        exit_close = (_CLOSE_INTERNAL, "internal error")

    if exit_close is not None:
        await _safe_close(websocket, code=exit_close[0], reason=exit_close[1])

    state.registry.unregister(session.session_id)
    logger.info(
        "ws session closed",
        extra=_session_log(
            session,
            frames_received=session.frames_received,
            frames_sent=session.frames_sent,
            bytes_in=session.bytes_received,
            bytes_out=session.bytes_sent,
        ),
    )
