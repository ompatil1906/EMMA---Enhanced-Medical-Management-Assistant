"""LiveKit WebRTC voice runtime integration (Day 12/13).

Bridges LiveKit WebRTC audio (browser) to the existing EMMA voice pipeline
(Deepgram Flux STT → emergency guard → OpenAI GPT-6 Luna → deterministic
ConversationController → Qdrant RAG → NestJS backend → ElevenLabs TTS).

Design rules:

* The existing deterministic controller is the single source of truth for
  administrative request state — no second conversation state machine.
* The existing VoicePipeline orchestrates the full path; the LiveKit agent
  only bridges WebRTC audio in/out to the pipeline's STT/TTS interfaces.
* LiveKit credentials (API key/secret) never reach the browser; room tokens
  are minted server-side with short expiry and trusted practice identity.
* All LiveKit SDK imports are lazy — the module imports cleanly without the
  `livekit` package installed (live provider tests are opt-in).
* Azure fallback path (existing FastAPI WebSocket) remains fully available.

No credentials → the module is importable but agent construction raises
`ProviderConfigurationError` until `LIVEKIT_URL`, `LIVEKIT_API_KEY` and
`LIVEKIT_API_SECRET` are configured.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from app.speech.base import ProviderConfigurationError

if TYPE_CHECKING:  # pragma: no cover - type hints only; runtime import is lazy
    from app.core.config import Settings
    from app.services.voice_pipeline import VoicePipeline

logger = logging.getLogger(__name__)


def _import_livekit() -> Any:
    """Import the livekit package lazily.

    Raises `ProviderConfigurationError` with a helpful message when the
    package is not installed.
    """
    try:
        import livekit  # type: ignore[import-not-found]

        return livekit
    except ImportError as exc:
        raise ProviderConfigurationError(
            "LiveKit transport requires the `livekit` package; not installed. "
            "Install it or keep VOICE_TRANSPORT=websocket."
        ) from exc


def _import_livekit_api() -> Any:
    """Import the livekit.api package lazily (server-side token minting)."""
    try:
        from livekit import api  # type: ignore[import-not-found]

        return api
    except ImportError as exc:
        raise ProviderConfigurationError(
            "LiveKit transport requires the `livekit` package; not installed."
        ) from exc


def _import_rtc() -> Any:
    """Import the `livekit.rtc` module lazily (Room/AudioSource/frames)."""
    try:
        import livekit.rtc  # type: ignore[import-not-found]

        return livekit.rtc
    except ImportError as exc:
        raise ProviderConfigurationError(
            "LiveKit transport requires the `livekit` package; not installed. "
            "Install it or keep VOICE_TRANSPORT=websocket."
        ) from exc


def _import_livekit_agents() -> Any:
    """Import the livekit.agents package lazily (agent worker runtime)."""
    try:
        from livekit import agents  # type: ignore[import-not-found]

        return agents
    except ImportError as exc:
        raise ProviderConfigurationError(
            "LiveKit transport requires the `livekit-agents` package; "
            "not installed. Install it or keep VOICE_TRANSPORT=websocket."
        ) from exc


class LiveKitTokenMinter:
    """Server-side LiveKit room token minting (credentials never reach browser).

    Mints short-lived JWTs for browser participants joining a LiveKit room.
    Practice identity is trusted server-side — the browser cannot choose
    another practice's identity.
    """

    def __init__(self, *, settings: Settings) -> None:
        self._settings = settings
        if not self._settings.LIVEKIT_API_KEY or not self._settings.LIVEKIT_API_SECRET:
            raise ProviderConfigurationError(
                "LIVEKIT_API_KEY/LIVEKIT_API_SECRET not configured; cannot mint tokens"
            )

    def _api(self) -> Any:
        """Import and return the livekit.api module lazily."""
        return _import_livekit_api()

    def mint_participant_token(
        self,
        *,
        room_name: str,
        participant_identity: str,
        practice_id: str,
        ttl_seconds: int = 3600,
        participant_name: str | None = None,
        caller_first_name: str | None = None,
        caller: Any = None,
    ) -> str:
        """Mint a LiveKit access token for a browser participant.

        The `practice_id` is bound server-side and embedded in the token
        metadata; the browser cannot alter it.

        Day 19: the intake identity travels in the metadata JSON so the agent
        can greet the caller by name and fulfill downstream requests.
        The participant *identity* stays opaque (`emma-caller-<hex>`) — no PII
        in identities, room names or logs.

        Returns a JWT string that the browser passes to `LiveKitClient.connect()`.
        """
        api = self._api()
        first_name = getattr(caller, "first_name", None) or caller_first_name
        metadata_dict: dict[str, Any] = {
            "practice_id": practice_id,
            "first_name": first_name,
        }
        if caller is not None:
            if getattr(caller, "last_name", None):
                metadata_dict["last_name"] = caller.last_name
            if getattr(caller, "email", None):
                metadata_dict["email"] = caller.email
            if getattr(caller, "mobile", None):
                metadata_dict["mobile"] = caller.mobile
            if getattr(caller, "reason", None):
                metadata_dict["reason"] = caller.reason

        token = (
            api.AccessToken(
                self._settings.LIVEKIT_API_KEY,
                self._settings.LIVEKIT_API_SECRET,
            )
            .with_identity(participant_identity)
            .with_name(participant_name or first_name or "EMMA Caller")
            .with_metadata(
                json.dumps(
                    metadata_dict,
                    separators=(",", ":"),
                )
            )
            .with_ttl(timedelta(seconds=ttl_seconds))
            .with_grants(
                api.VideoGrants(
                    room=room_name,
                    room_join=True,
                    can_publish=True,
                    can_subscribe=True,
                    can_publish_data=True,
                )
            )
        )
        return token.to_jwt()


class LiveKitAgent:
    """LiveKit voice agent bridging WebRTC audio to the EMMA pipeline.

    One agent instance per LiveKit room session. It:

    1. Connects to a LiveKit room using a server-minted agent JWT
       (`LiveKitTokenMinter` — credentials never reach the browser).
    2. Exposes `publish_audio` for pipeline outbound PCM (speaker side).
    3. Exposes `publish_text` for transport-neutral control events
       (transcripts / state / barge-in) as reliable data messages.
    4. Disconnects cleanly on `stop`.

    The agent never makes administrative decisions — it only transports audio
    between the browser and the existing pipeline.

    Day 17 note: implemented against the installed `livekit` SDK API
    (`rtc.Room.connect(url, token)`, `rtc.AudioSource.capture_frame` for
    real-time-paced playback).
    """

    _AGENT_PREFIX: str = "emma-agent"

    def __init__(
        self,
        *,
        settings: Settings,
        pipeline: VoicePipeline | None = None,
        room_name: str,
    ) -> None:
        self._settings = settings
        self._pipeline = pipeline
        self._room_name = room_name
        self._livekit = _import_rtc()

        self._room: Any = None
        self._session_id: str | None = None
        self._identity: str | None = None
        self._started = False
        self._stopped = False
        self._audio_source: Any = None
        self._publish_lock = asyncio.Lock()

    @property
    def provider_name(self) -> str:
        return "livekit-webrtc"

    @property
    def identity(self) -> str | None:
        return self._identity

    async def start(self, session_id: str) -> None:
        """Connect to the LiveKit room and join as an agent participant."""
        if self._session_id is not None and self._session_id != session_id:
            raise ProviderConfigurationError(f"agent already bound to session {self._session_id!r}")
        if self._started:
            return

        url = self._settings.LIVEKIT_URL
        if not url or not self._settings.LIVEKIT_API_KEY or not self._settings.LIVEKIT_API_SECRET:
            raise ProviderConfigurationError(
                "LIVEKIT_URL/LIVEKIT_API_KEY/LIVEKIT_API_SECRET are not "
                "configured; refusing to start (no silent fallback)"
            )

        self._session_id = session_id
        self._identity = f"{self._AGENT_PREFIX}-{uuid.uuid4().hex[:8]}"
        self._started = True
        logger.info(
            "livekit agent connecting",
            extra={"session_id": session_id, "room": self._room_name, "identity": self._identity},
        )

        minter = LiveKitTokenMinter(settings=self._settings)
        token = minter.mint_participant_token(
            room_name=self._room_name,
            participant_identity=self._identity,
            practice_id=self._settings.PRACTICE_ID or "demo-practice-001",
            ttl_seconds=3600,
        )
        self._room = await self._connect_room(url, token)
        logger.info(
            "livekit agent connected",
            extra={"session_id": session_id, "room": self._room_name},
        )

    async def _connect_room(self, url: str, token: str) -> Any:
        """Connect to the LiveKit room.

        Uses the installed SDK's `rtc.Room.connect(url, jwt)` — the URL is the
        configured `LIVEKIT_URL`; the JWT carries the agent identity/grants.
        """
        rtc = self._livekit
        room = rtc.Room()
        try:
            await room.connect(url, token)
        except Exception as exc:
            logger.error(
                "livekit room connect failed",
                extra={"room": self._room_name, "detail": type(exc).__name__},
            )
            raise
        return room

    @property
    def room(self) -> Any:
        """The connected rtc.Room (None before start / after stop)."""
        return self._room

    async def publish_audio(self, session_id: str, audio: bytes) -> None:
        """Publish one PCM16 mono chunk to the room (assistant output).

        Chunks are converted to `AudioFrame`s and fed through an
        `rtc.AudioSource` for real-time-paced playback. The source and audio
        track are created lazily on first chunk.
        """
        if self._session_id != session_id or self._stopped or self._room is None:
            return
        if not audio:
            logger.debug("publish_audio: empty audio, skipping")
            return
        async with self._publish_lock:
            try:
                rtc = self._livekit
                source = self._audio_source
                if source is None:
                    source = rtc.AudioSource(sample_rate=16000, num_channels=1, queue_size_ms=200)
                    track = rtc.LocalAudioTrack.create_audio_track("emma-agent", source)
                    await self._room.local_participant.publish_track(track)
                    self._audio_source = source
                samples = len(audio) // 2
                frame = rtc.AudioFrame(audio, 16000, 1, samples)
                logger.debug(
                    "publish_audio: %d bytes, samples=%d",
                    len(audio),
                    samples,
                )
                await source.capture_frame(frame)
            except Exception as exc:  # noqa: BLE001 — publish is best-effort per chunk
                logger.warning(
                    "livekit audio publish failed",
                    extra={"session_id": session_id, "detail": type(exc).__name__},
                )

    async def publish_text(self, session_id: str, message_json: str) -> None:
        """Send a reliable data message (control event) to the room."""
        if self._session_id != session_id or self._stopped or self._room is None:
            return
        try:
            await self._room.local_participant.publish_data(
                message_json.encode("utf-8"),
            )
        except Exception as exc:  # noqa: BLE001 — control events are best-effort
            logger.debug(
                "control event publish failed",
                extra={"session_id": session_id, "detail": type(exc).__name__},
            )

    async def stop(self, session_id: str, *, reason: str = "session_stop") -> None:
        """Disconnect from the room and release resources."""
        if self._session_id != session_id or self._stopped:
            return
        self._stopped = True
        if self._room is not None:
            try:
                await self._room.disconnect()
            except Exception:  # noqa: BLE001 — best-effort disconnect
                pass
            self._room = None
        self._started = False
        logger.info(
            "livekit agent disconnected",
            extra={"session_id": session_id, "reason": reason},
        )


__all__ = ["LiveKitAgent", "LiveKitTokenMinter"]
