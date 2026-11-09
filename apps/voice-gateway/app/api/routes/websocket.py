"""`/ws/exotel` — transport orchestration for the Exotel AgentStream protocol.

Protocol knowledge lives in `app.integrations.exotel` (verified schemas +
adapter); audio behaviour lives in `app.services`. This module coordinates:

    accept → authenticate → register session → message loop → guaranteed cleanup

Message-loop policies are OUR Day-2 decisions (documented in
docs/integrations/exotel-protocol.md) — they are not Exotel-mandated rules:

| situation                        | action            | close code |
|----------------------------------|-------------------|------------|
| authentication failure           | refuse connection | 1008       |
| unsupported media negotiation    | reject stream     | 1008       |
| media renegotiation (speech mode)| reject stream     | 1008       |
| malformed / invalid frames       | tolerate up to MAX_CONSECUTIVE_ERRORS then close | 1003 |
| oversized message                | reject immediately| 1009       |
| provider `stop` or client close  | normal shutdown   | 1000       |
| speech provider misconfiguration | close session     | 1011       |
| server-side unexpected error     | best-effort close | 1011       |
| session cap reached              | refuse new session| 1013       |

Two pipeline modes (`VOICE_PIPELINE`):
* `echo`   — Day-2 byte-for-byte echo path (strict per-frame ping-pong).
* `speech` — full speech pipeline (STT → FSM → router → TTS); inbound `media`
  frames feed the providers and outbound `media` events are produced by a
  dedicated pump task draining the pipeline's queue (turn-shaped flow, not
  per-frame echo).
Configuration is validated at startup; provider construction errors close
1011 rather than ever silently falling back.
"""

from __future__ import annotations

import array
import asyncio
import hmac
import json
import math
import sys

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.core.lifecycle import GatewayState
from app.core.logging import get_logger
from app.domain.audio import SUPPORTED_SAMPLE_RATES, AudioFormat
from app.domain.session import VoiceSession
from app.domain.speech_session import MediaFormatChangedError
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

# Day 10 barge-in policy: a continuously streaming browser microphone sends
# silence too, so "any inbound frame while speaking" would cut off every reply.
# Only genuine caller energy may interrupt assistant output.
_BARGE_IN_RMS_THRESHOLD = 800.0  # PCM16 RMS (~ -32 dBFS); requires clear voice
_BARGE_IN_MIN_FRAMES = 5  # 100 ms of speech at the browser's 20 ms cadence


def _pcm16_rms(payload: bytes) -> float:
    """Root-mean-square of little-endian PCM16 samples (0.0 when too short)."""
    if len(payload) < 2:
        return 0.0
    sample_count = len(payload) // 2
    samples = array.array("h")
    samples.frombytes(payload[: sample_count * 2])
    if sys.byteorder != "little":
        samples.byteswap()
    total = 0
    for sample in samples:
        total += sample * sample
    return math.sqrt(total / sample_count)


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
    state: GatewayState = websocket.scope["app"].state.gateway
    adapter = ExotelAdapter()

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
    # Browser UI opt-in: emit JSON control events alongside `media` envelopes.
    # Provider (Exotel) traffic never sets this, so the verified media protocol
    # stays byte-compatible.
    control_events = websocket.query_params.get("control-events") in ("1", "true", "yes")
    state.registry.register(session)
    logger.info(
        "ws session opened",
        extra=_session_log(session, voice_pipeline=settings.VOICE_PIPELINE),
    )

    if settings.VOICE_PIPELINE == "speech":
        exit_close = await _session_loop_speech(
            websocket, session, state, settings, adapter, control_events=control_events
        )
    else:
        exit_close = await _session_loop_echo(websocket, session, settings, adapter, state)

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


# --- shared receive scaffolding (both modes) ---------------------------------------


# --- echo loop (Day-2 behaviour, byte-for-byte) -------------------------------------


async def _session_loop_echo(
    websocket: WebSocket,
    session: VoiceSession,
    settings: Settings,
    adapter: ExotelAdapter,
    state: GatewayState,
) -> tuple[int, str] | None:
    """Day-2 echo loop — unchanged behaviour, isolated from the speech path."""
    echo = EchoService()
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
                    audio_out = await echo.process(audio_in)
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
                    # `clear` (barge-in flush) is the bot→Exotel answer direction;
                    # inbound marks matter once TTS markers exist (Day 3+).
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

    return exit_close


# --- speech loop (Day-3 pipeline) ---------------------------------------------------


def _build_speech_pipeline(
    session: VoiceSession,
    state: GatewayState,
    settings: Settings,
    *,
    control_events: bool = False,
) -> object:
    """Construct one VoicePipeline from the validated provider factory."""
    from app.domain.speech_session import SpeechSession as _SpeechSession
    from app.services.response_handler import DeterministicResponseHandler
    from app.services.retrieval import KnowledgeAnswerService, Retriever
    from app.services.voice_pipeline import VoicePipeline

    factory = state.speech_factory
    if factory is None:  # lifespan guarantee; defensive close path only
        raise RuntimeError("speech factory missing (lifespan did not initialise app.state)")

    speech_session = _SpeechSession(
        session_id=session.session_id,
        call_sid=session.call_sid,
        practice_id=settings.PRACTICE_ID,
        audio_format=session.audio_format,
    )

    # Build Day-6 retrieval components if configured
    retriever: Retriever | None = None
    knowledge_answer_service: KnowledgeAnswerService | None = None

    if state.embedding_factory is not None and state.vector_store_factory is not None:
        if settings.rag_configured:
            embedding_provider = state.embedding_factory.create()
            vector_store = state.vector_store_factory.create()
            retriever = Retriever(
                embedding_provider=embedding_provider,
                vector_store=vector_store,
                default_top_k=settings.RETRIEVAL_TOP_K,
                default_min_score=settings.RETRIEVAL_MIN_SCORE,
            )
            knowledge_answer_service = KnowledgeAnswerService(
                llm_provider=state.llm_factory.create(),
                model=settings.LLM_MODEL,
                timeout_seconds=settings.LLM_TIMEOUT_SECONDS,
                max_output_tokens=settings.LLM_MAX_OUTPUT_TOKENS,
                prompt_version=settings.LLM_PROMPT_VERSION,
            )

    return VoicePipeline(
        session=speech_session,
        stt=factory.create_stt(session.audio_format),
        tts=factory.create_tts(session.audio_format),
        response_handler=DeterministicResponseHandler(),
        practice_client=state.practice_client,
        practice_id=settings.PRACTICE_ID,
        settings=settings,
        retriever=retriever,
        knowledge_answer_service=knowledge_answer_service,
        llm_factory=state.llm_factory,
        control_events=control_events,
    )


async def _session_loop_speech(
    websocket: WebSocket,
    session: VoiceSession,
    state: GatewayState,
    settings: Settings,
    adapter: ExotelAdapter,
    *,
    control_events: bool = False,
) -> tuple[int, str] | None:
    """Speech-pipeline loop: pipeline owns audio; the route stays transport-only."""
    from app.services.voice_pipeline import ControlEvent, PipelineFatallyFailed, VoicePipeline
    from app.speech.base import ProviderConfigurationError

    pipeline: VoicePipeline | None = None
    pump_task: asyncio.Task[None] | None = None
    consecutive_errors = 0
    barge_in_frames = 0
    exit_close: tuple[int, str] | None = None

    def protocol_rejected(detail: str) -> bool:
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

    async def _pump() -> None:
        """Drain the pipeline's outbound queue → `media` envelopes (turn-shaped)."""
        assert pipeline is not None
        try:
            async for item in pipeline.stream_outbound():
                if isinstance(item, ControlEvent):
                    await websocket.send_text(json.dumps(item.payload, separators=(",", ":")))
                    continue
                outbound_json = build_outbound_media_envelope(item, stream_sid=session.stream_sid)
                await websocket.send_text(outbound_json)
                session.frames_sent += 1
                session.bytes_sent += len(item.payload)
        except (WebSocketDisconnect, RuntimeError):
            logger.warning("ws outbound send failed (peer gone)", extra=_session_log(session))
        except Exception:  # noqa: BLE001 — pump crash surfaces at teardown
            logger.exception("outbound pump crashed", extra=_session_log(session))

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
            consecutive_errors = 0

            match event:
                case ExotelConnected():
                    logger.debug("stream connected event", extra=_session_log(session))
                case ExotelStart():
                    try:
                        call_started = adapter.handle_start(event)
                    except ValidationError as exc:
                        logger.warning(
                            "ws start rejected: unsupported media negotiation",
                            extra=_session_log(session, detail=str(exc.errors()[0]["msg"])),
                        )
                        exit_close = (_CLOSE_NEGOTIATION, "unsupported media format")
                        break
                    negotiated = call_started.audio_format or session.audio_format
                    if pipeline is None:
                        previous_id = session.adopt_stream(
                            stream_sid=event.stream_sid, call_sid=event.call_sid
                        )
                        state.registry.adopt(session, previous_id)
                        session.audio_format = negotiated
                        logger.info(
                            "call started",
                            extra=_session_log(
                                session,
                                call_sid=session.call_sid,
                                media_format=session.audio_format.model_dump(),
                            ),
                        )
                        try:
                            pipeline = _build_speech_pipeline(
                                session, state, settings, control_events=control_events
                            )
                        except ProviderConfigurationError as exc:
                            logger.error(
                                "speech provider misconfigured (no silent fallback)",
                                extra=_session_log(session, detail=str(exc)),
                            )
                            exit_close = (_CLOSE_INTERNAL, "speech provider not configured")
                            break
                        pump_task = asyncio.create_task(_pump(), name="outbound-pump")
                        try:
                            await pipeline.start()
                        except PipelineFatallyFailed as exc:
                            logger.error(
                                "speech pipeline failed to start",
                                extra=_session_log(session, detail=str(exc)),
                            )
                            exit_close = (_CLOSE_INTERNAL, "speech pipeline failed")
                            break
                        logger.info(
                            "speech pipeline started",
                            extra=_session_log(
                                session,
                                stt=pipeline.session.stt_state.value,
                                tts=pipeline.session.tts_state.value,
                            ),
                        )
                    else:
                        # Renegotiation policy (speech mode): the audio format was
                        # locked when the pipeline was built; a different format
                        # afterward is rejected (never resampled).
                        if negotiated != session.audio_format:
                            logger.warning(
                                "media renegotiation rejected in speech mode",
                                extra=_session_log(
                                    session,
                                    locked=session.audio_format.sample_rate,
                                    requested=negotiated.sample_rate,
                                ),
                            )
                            exit_close = (_CLOSE_NEGOTIATION, "unsupported media renegotiation")
                            break
                        logger.debug(
                            "redundant start event tolerated",
                            extra=_session_log(session),
                        )
                case ExotelMedia():
                    if pipeline is None:
                        # Speech mode requires `start` first; pre-start frames are
                        # dropped (countered below, tolerated as provider drift).
                        logger.debug(
                            "media frame before start (dropped in speech mode)",
                            extra=_session_log(session),
                        )
                        continue
                    try:
                        audio_in = adapter.handle_media(
                            event,
                            session_id=session.session_id,
                            audio_format=session.audio_format,
                            sequence=session.frames_received + 1,
                            max_chunk_bytes=settings.EXOTEL_MAX_CHUNK_BYTES,
                        )
                    except ExotelProtocolError as exc:
                        if protocol_rejected(str(exc)):
                            exit_close = (_CLOSE_PROTOCOL_ERROR, "audio payload rejected")
                            break
                        continue
                    session.frames_received += 1
                    session.bytes_received += len(audio_in.payload)
                    try:
                        await pipeline.submit_audio(audio_in)
                    except MediaFormatChangedError as exc:
                        logger.warning(
                            "audio format changed after lock",
                            extra=_session_log(session, detail=str(exc)),
                        )
                        exit_close = (_CLOSE_NEGOTIATION, "unsupported media renegotiation")
                        break
                    # --- Day 9/10: barge-in -----------------------------------
                    # If genuine caller speech arrives while the assistant is
                    # speaking (or outbound audio is still queued), cancel the
                    # in-flight/queued TTS. Silence and low-level noise must
                    # not interrupt a reply (the browser mic streams always).
                    if pipeline is not None and pipeline.should_cancel_for_barge_in:
                        if _pcm16_rms(audio_in.payload) >= _BARGE_IN_RMS_THRESHOLD:
                            barge_in_frames += 1
                        else:
                            barge_in_frames = 0
                        if barge_in_frames >= _BARGE_IN_MIN_FRAMES:
                            barge_in_frames = 0
                            try:
                                await pipeline.cancel_tts()
                            except Exception:  # noqa: BLE001 — barge-in is best-effort
                                logger.exception(
                                    "barge-in cancel failed",
                                    extra=_session_log(session),
                                )
                    else:
                        barge_in_frames = 0
                case ExotelStop():
                    logger.info(
                        "stream stopped by provider",
                        extra=_session_log(session, stop_reason=event.reason),
                    )
                    exit_close = (_CLOSE_NORMAL, "stop")
                    break
                case ExotelDtmf():
                    logger.debug("dtmf event tolerated", extra=_session_log(session))
                case ExotelMarkIn():
                    logger.debug("mark event tolerated", extra=_session_log(session))
                case ExotelUnknownEvent():
                    if event.event == "barge-in" and pipeline is not None:
                        # Browser-only control event: explicit client-side
                        # acoustic interruption. Exotel never sends this.
                        try:
                            await pipeline.cancel_tts()
                        except Exception:  # noqa: BLE001 — barge-in is best-effort
                            logger.exception(
                                "barge-in event cancel failed",
                                extra=_session_log(session),
                            )
                    else:
                        logger.debug(
                            "unknown event tolerated (no crash)",
                            extra=_session_log(session, event=event.event),
                        )
                case _:
                    logger.debug("unmodelled event acked", extra=_session_log(session))

    except Exception:
        logger.exception("ws session failed unexpectedly", extra=_session_log(session))
        exit_close = (_CLOSE_INTERNAL, "internal error")

    finally:
        fatal_session = pipeline is not None and pipeline.failed
        internal_exit = exit_close == (_CLOSE_INTERNAL, "internal error")
        if pipeline is not None:
            try:
                await pipeline.aclose(reason="session_stop", fatal=fatal_session or internal_exit)
            except Exception:  # noqa: BLE001 — teardown must not mask the close
                logger.exception("speech pipeline teardown failed", extra=_session_log(session))
        if pump_task is not None and not pump_task.done():
            pump_task.cancel()
            try:
                await pump_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    return exit_close
