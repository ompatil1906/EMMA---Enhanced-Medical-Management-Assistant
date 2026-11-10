"""LiveKit agent worker (Day 13, repaired Day 17).

Runs as a LiveKit Agents worker that bridges WebRTC audio from the browser
to the existing EMMA VoicePipeline:

    microphone (WebRTC) → STT → emergency guard → LLM → ConversationController
    → (Qdrant RAG when configured) → NestJS backend → TTS → speaker (WebRTC)

The existing ConversationController remains the single source of truth for
conversation state — the agent only transports audio and drives the pipeline.

Day 17 — runs against the installed `livekit` / `livekit-agents` SDK APIs:

* input: `rtc.Room` `track_subscribed` events → `rtc.AudioStream` frames →
  explicit mono/stereo downmix + sample-rate resample (WebRTC publishes Opus,
  typically 48 kHz; the pipeline negotiates 16 kHz) → `pipeline.submit_audio()`;
* output: `pipeline.stream_outbound()` → `LiveKitAgent.publish_audio()` for
  PCM (real-time-paced `rtc.AudioSource`) and `publish_text()` for the
  transport-neutral control messages (transcripts / state / barge-in);
* barge-in: a reliable data message `{"event": "barge-in"}` (sent by the
  browser audio-level monitor or a test harness) cancels in-flight TTS via
  the existing `pipeline.cancel_tts()` — identical semantics to the legacy
  WebSocket route;
* lifecycle: the pipeline session is created only when an actual caller
  (`emma-caller-…` participant, minted server-side) is in the room, so the
  greeting is audible to the caller rather than published to an empty room.

The provider set (STT/TTS/LLM) is selected by the same env vars as the
gateway (`STT_PROVIDER`/`TTS_PROVIDER`/`LLM_PROVIDER` = mock for Day 17).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from app.core.config import Settings
from app.domain.audio import AudioFormat
from app.domain.events import AudioReceived
from app.integrations.livekit import LiveKitAgent
from app.services.caller_identity import (
    CallerIdentity,
    caller_from_participant,
    first_name_from_participant,
)
from app.services.voice_pipeline import VoicePipeline

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=__import__("sys").stderr,
)

_CALLER_IDENTITY_PREFIX = "emma-caller-"

# Real `track.kind` is the protobuf TrackKind enum (KIND_AUDIO == 1); unit
# test fakes pass the literal string "audio". Accept both shapes explicitly.
_TRACK_KIND_AUDIO_VALUES = (1, "audio")


def _is_audio_track(track: Any) -> bool:
    kind = getattr(track, "kind", None)
    if kind is None:
        return False
    try:
        return kind in _TRACK_KIND_AUDIO_VALUES
    except TypeError:  # unhashable enum — fall back to int cast
        return int(kind) == 1


class EmmaAgentSession:
    """One LiveKit agent session, wired to one VoicePipeline.

    Listens for audio from the browser participant, feeds it to the pipeline's
    STT provider, and publishes TTS audio plus control events back to the room.
    """

    def __init__(
        self,
        *,
        ctx: Any,  # livekit.agents.JobContext
        settings: Settings,
        pipeline: VoicePipeline,
    ) -> None:
        self._ctx = ctx
        self._settings = settings
        self._pipeline = pipeline
        self._room = ctx.room
        self._session_id = ctx.job.id
        self._started = False
        self._stopped = False
        self._teardown_requested = False
        self._closed_event: asyncio.Event = asyncio.Event()
        self._input_tasks: set[asyncio.Task[None]] = set()
        self._play_task: asyncio.Task[None] | None = None
        self._agent = LiveKitAgent(
            settings=settings,
            pipeline=pipeline,
            room_name=self._room.name,
        )
        self._sequence = 0
        self._attached_sids: set[Any] = set()
        self._caller_frames = 0
        self._audio_watchdog: asyncio.Task[None] | None = None

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def agent(self) -> LiveKitAgent:
        return self._agent

    async def start(self) -> None:
        """Start the agent session: begin STT input and TTS output loops."""
        if self._started:
            return
        self._started = True

        # Connect the agent's outbound publisher to the room first — the
        # greeting is published after `pipeline.start()`.
        await self._agent.start(self._session_id)
        self._setup_room_handlers()

        # Start the pipeline (emits greeting via TTS).
        await self._pipeline.start()

        # Bridge: pipeline TTS output → room audio publication.
        self._play_task = asyncio.create_task(self._audio_output_loop())
        self._audio_watchdog = asyncio.create_task(self._watch_for_caller_audio())

    async def _watch_for_caller_audio(self, *, timeout: float = 12.0) -> None:
        """Log loudly when no caller audio arrives (mic not published/denied)."""
        try:
            await asyncio.sleep(timeout)
        except asyncio.CancelledError:
            return
        if self._stopped or self._caller_frames > 0:
            return
        logger.warning(
            "no caller audio received: greeting only — check microphone permission, "
            "track publication and subscription",
            extra={"session_id": self._session_id},
        )

    # --- room event bridging ----------------------------------------------------

    def _setup_room_handlers(self) -> None:
        """Subscribe to caller audio, control messages, and room teardown.

        Audio attachment is *reconciled from room state*, not only driven by the
        `track_subscribed` event. When the caller publishes before the agent
        joins (the normal browser flow), that event can fire during
        `room.connect()` — i.e. before these handlers exist — and the track is
        then never read: the greeting plays and the caller is never heard.
        """
        self._room.on("track_subscribed")(self._on_track_subscribed)
        # Late publications / late subscriptions must also be picked up.
        for event_name in ("track_published", "participant_connected", "track_subscription_failed"):
            try:
                self._room.on(event_name)(self._on_room_changed)
            except Exception:  # noqa: BLE001 — SDK event names vary by version
                logger.debug("room event not available", extra={"event": event_name})
        self._room.on("data_received")(self._on_data_received)
        self._room.on("disconnected")(self._on_room_disconnected)
        self._room.on("participant_disconnected")(self._on_participant_disconnected)

        self._attach_existing_caller_audio()
        # One delayed rescan covers the auto-subscribe window right after join.
        self._input_tasks.add(asyncio.create_task(self._rescan_audio(delay=1.0)))

    def _on_room_changed(self, *_: Any) -> None:
        """Room membership/publication changed — reconcile caller audio again."""
        if self._stopped:
            return
        self._attach_existing_caller_audio()

    def _attach_existing_caller_audio(self) -> None:
        """Start an input loop for every caller audio track we can see."""
        attached = 0
        for participant in list(self._room.remote_participants.values()):
            if not self._is_caller(participant):
                continue
            for publication in list(getattr(participant, "track_publications", {}).values()):
                track = getattr(publication, "track", None)
                if track is None or not _is_audio_track(track):
                    continue
                self._start_input_loop(track, publication)
                attached += 1
        if attached:
            logger.info(
                "caller audio attached",
                extra={"session_id": self._session_id, "tracks": attached},
            )

    async def _rescan_audio(self, *, delay: float) -> None:
        """Re-reconcile once after join (auto-subscription may land late)."""
        try:
            await asyncio.sleep(delay)
            if not self._stopped:
                self._attach_existing_caller_audio()
        except asyncio.CancelledError:
            return

    def _is_caller(self, participant: Any) -> bool:
        identity = getattr(participant, "identity", None)
        return isinstance(identity, str) and identity.startswith(_CALLER_IDENTITY_PREFIX)

    def _on_track_subscribed(self, track: Any, publication: Any, participant: Any) -> None:
        if self._stopped or not self._started:
            return
        if not _is_audio_track(track) or not self._is_caller(participant):
            return
        self._start_input_loop(track, publication)

    def _start_input_loop(self, track: Any, publication: Any) -> None:
        """Start (at most once per publication) the reader for one audio track."""
        sid = getattr(publication, "sid", None) or getattr(track, "sid", None) or id(track)
        if sid in self._attached_sids:
            return
        self._attached_sids.add(sid)
        task = asyncio.create_task(self._audio_input_loop(track))
        self._input_tasks.add(task)
        task.add_done_callback(self._input_tasks.discard)

    def _on_data_received(self, event: Any) -> None:
        """Handle control data from the browser (barge-in cancellation)."""
        if self._stopped or not self._started:
            return
        payload = getattr(event, "payload", None)
        if not payload:
            return
        try:
            if isinstance(payload, (bytes, bytearray, memoryview)):
                decoded = bytes(payload).decode("utf-8", errors="replace")
            else:
                decoded = str(payload)
            message = json.loads(decoded)
        except (ValueError, TypeError):
            return
        if message.get("event") == "barge-in" and self._pipeline.should_cancel_for_barge_in:
            logger.info(
                "barge-in received from browser",
                extra={"session_id": self._session_id},
            )
            task = asyncio.create_task(self._cancel_for_barge_in())
            self._input_tasks.add(task)
            task.add_done_callback(self._input_tasks.discard)

    def _on_participant_disconnected(self, participant: Any) -> None:
        """End the session when the caller hangs up.

        Without this the agent stayed in an empty room after the caller left;
        the next browser tab would see the stale `agent-…` participant and show
        "listening" while nobody was actually reading its audio.
        """
        if self._stopped:
            return
        if not self._is_caller(participant):
            return
        logger.info(
            "caller left the room; stopping session",
            extra={"session_id": self._session_id},
        )
        self._request_stop("caller_left")

    def _request_stop(self, reason: str) -> None:
        """Tear the session down from an SDK callback without blocking it.

        Guarded by its own flag — setting `_stopped` here would make `stop()`
        return immediately and leave the pipeline (and the room participant)
        alive, which is exactly the zombie-agent failure this fixes.
        """
        if self._teardown_requested:
            return
        self._teardown_requested = True
        task = asyncio.create_task(self._stop_async(reason))
        self._input_tasks.add(task)
        task.add_done_callback(self._input_tasks.discard)

    async def _stop_async(self, reason: str) -> None:
        try:
            await self.stop(reason=reason)
        except Exception:  # noqa: BLE001 — teardown is best-effort
            logger.exception(
                "session stop failed",
                extra={"session_id": self._session_id, "reason": reason},
            )
        # Leave the room so the `agent-<job>` participant disappears immediately
        # instead of lingering after the caller hung up.
        try:
            await self._room.disconnect()
        except Exception:  # noqa: BLE001 — best-effort
            logger.debug(
                "room disconnect after stop failed",
                extra={"session_id": self._session_id},
            )

    def _on_room_disconnected(self, *_: Any) -> None:
        # Ask `stop()` to run (it owns pipeline teardown); do not pre-set
        # `_stopped` or the teardown would be skipped.
        self._request_stop("room_disconnect")

    async def _cancel_for_barge_in(self) -> None:
        try:
            await self._pipeline.cancel_tts()
        except Exception as exc:  # noqa: BLE001 — cancellation is best-effort
            logger.warning(
                "barge-in cancellation failed",
                extra={"session_id": self._session_id, "detail": type(exc).__name__},
            )

    # --- audio input: caller microphone → pipeline STT ------------------------------

    async def _audio_input_loop(self, track: Any) -> None:
        """Read subscribed caller audio frames and push to STT.

        Frames arrive as decoded PCM16 (WebRTC publishes Opus; the SDK decodes,
        typically at 48 kHz). Stereo frames are downmixed and non-negotiated
        sample rates are resampled explicitly before reaching the pipeline —
        the pipeline never resamples silently (`convert_if_needed` refuses
        unrouted rates).
        """
        from app.integrations.livekit import _import_rtc

        rtc = _import_rtc()
        try:
            stream = rtc.AudioStream(track)
            async for event in stream:
                if self._stopped:
                    break
                frame = event.frame
                payload, sample_rate = _frame_to_mono_pcm16(frame, rtc)
                if not payload:
                    continue
                audio, negotiated_rate = _resample_if_needed(payload, sample_rate)
                if not audio:
                    continue
                self._sequence += 1
                self._caller_frames += 1
                await self._pipeline.submit_audio(
                    AudioReceived(
                        session_id=self._session_id,
                        sequence=self._sequence,
                        payload=audio,
                        audio_format=AudioFormat(sample_rate=negotiated_rate, channels=1),
                    )
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "audio input loop failed",
                extra={"session_id": self._session_id, "detail": type(exc).__name__},
            )

    # --- output: pipeline → assistant audio + control events -------------------------

    async def _audio_output_loop(self) -> None:
        """Read TTS chunks / control events from the pipeline and publish.

        `stream_outbound` yields `ControlEvent`s (transcripts / state / barge-in)
        and `AudioToSend` frames (assistant PCM audio). Both are forwarded to
        the room: control events as reliable data messages, audio via the
        real-time-paced `rtc.AudioSource`.
        """
        try:
            async for item in self._pipeline.stream_outbound():
                if self._stopped:
                    break
                from app.services.voice_pipeline import ControlEvent

                if isinstance(item, ControlEvent):
                    payload = item.payload
                    await self._agent.publish_text(self._session_id, json.dumps(payload))
                    continue

                from app.domain.events import AudioToSend

                if isinstance(item, AudioToSend):
                    await self._agent.publish_audio(self._session_id, item.payload)
                    continue

                assert isinstance(item, bytes)
                await self._agent.publish_audio(self._session_id, item)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "audio output loop failed",
                extra={"session_id": self._session_id, "detail": type(exc).__name__},
            )

    async def wait_closed(self) -> None:
        """Block until this session's teardown has finished."""
        await self._closed_event.wait()

    async def stop(self, *, reason: str = "session_stop") -> None:
        """Stop the agent session and release resources."""
        if self._stopped:
            return
        self._stopped = True

        for task in list(self._input_tasks):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 — teardown must not raise
                pass
        self._input_tasks.clear()

        if self._audio_watchdog is not None:
            self._audio_watchdog.cancel()
            try:
                await self._audio_watchdog
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._audio_watchdog = None

        if self._play_task is not None:
            self._play_task.cancel()
            try:
                await self._play_task
            except asyncio.CancelledError:
                pass
            self._play_task = None

        if self._started:
            try:
                await self._agent.stop(self._session_id, reason=reason)
            except Exception as exc:  # noqa: BLE001 — teardown must not raise
                logger.warning(
                    "agent transport stop failed",
                    extra={"session_id": self._session_id, "detail": type(exc).__name__},
                )

        await self._pipeline.aclose(reason=reason)
        self._closed_event.set()
        logger.info(
            "emma agent session stopped",
            extra={"session_id": self._session_id, "reason": reason},
        )


# --- frame conversion helpers (explicit, documented; pipeline does not resample) ---


def _frame_to_mono_pcm16(frame: Any, rtc: Any) -> tuple[bytes, int]:
    """Downmix one AudioFrame to mono PCM16 and return (bytes, sample_rate).

    Stereo/multi-channel frames are averaged channel pairs (explicit adapter
    for the WebRTC publisher; the pipeline's mono invariant is preserved).
    """
    data = bytes(frame.data)
    channels = int(frame.num_channels)
    sample_rate = int(frame.sample_rate)
    if channels == 1:
        return data, sample_rate
    samples = len(data) // 2 // channels
    if samples == 0:
        return b"", sample_rate
    import struct

    source = struct.unpack(f"<{samples * channels}h", data[: samples * channels * 2])
    mono = bytearray()
    for i in range(samples):
        total = 0
        for c in range(channels):
            total += source[i * channels + c]
        mono += struct.pack("<h", max(-32768, min(32767, round(total / channels))))
    return bytes(mono), sample_rate


def _resample_if_needed(payload: bytes, sample_rate: int) -> tuple[bytes, int]:
    """Explicit sample-rate adapter for WebRTC frames (never silent).

    LiveKit WebRTC audio decodes at whatever the publisher chose — commonly
    48 kHz Opus — while EMMA negotiates 16 kHz. Rates inside the pipeline's
    supported set pass through untouched; anything else is resampled to
    16 kHz with the SDK resampler here (loud, explicit, at the bridge —
    matching `app.speech.audio_convert`'s explicit-conversion policy).
    """
    supported = {8000, 16000, 24000}
    if sample_rate in supported:
        return payload, sample_rate
    from app.integrations.livekit import _import_rtc

    rtc = _import_rtc()
    resampler = rtc.AudioResampler(input_rate=sample_rate, output_rate=16000, num_channels=1)
    frames = resampler.push(bytearray(payload))
    flush_frames = resampler.flush()
    out = b"".join(bytes(f.data) for f in [*frames, *flush_frames])
    return out, 16000


# --- entrypoint -----------------------------------------------------------------


def _first_caller_identity(room: Any) -> str | None:
    """Return the first present caller identity or None."""
    for participant in list(getattr(room, "remote_participants", {}).values()):
        identity = getattr(participant, "identity", None)
        if isinstance(identity, str) and identity.startswith(_CALLER_IDENTITY_PREFIX):
            return identity
    return None


def _caller_identity(room: Any, identity: str | None) -> CallerIdentity | None:
    """Build the sanitised intake identity for the first caller in the room.

    The browser form's full caller data arrives on the participant metadata
    (set server-side by the token endpoint). Absent or unusable -> None.
    """
    target = identity or _first_caller_identity(room)
    if target is None:
        return None
    for participant in list(getattr(room, "remote_participants", {}).values()):
        if getattr(participant, "identity", None) != target:
            continue
        return caller_from_participant(participant)
    return None


async def entrypoint(ctx: Any) -> None:
    """LiveKit agent entrypoint.

    Called by the LiveKit worker for each room job. Builds the VoicePipeline
    from the gateway's factories once an actual caller joins the room, then
    runs one EmmaAgentSession until the room disconnects.

    Requires `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`.
    """
    settings = Settings()  # type: ignore[call-arg] — env loaded via pydantic-settings

    # Build the speech pipeline using the gateway's factories.
    from app.core.lifecycle import GatewayState
    from app.domain.speech_session import SpeechSession
    from app.integrations.embeddings.provider_factory import EmbeddingProviderFactory
    from app.integrations.llm.provider_factory import LLMProviderFactory
    from app.integrations.practice_api import PracticeApiClient
    from app.integrations.vector_store.provider_factory import VectorStoreProviderFactory
    from app.services.response_handler import DeterministicResponseHandler
    from app.services.retrieval import KnowledgeAnswerService, Retriever

    room = ctx.room
    room_disconnected = asyncio.Event()
    room.on("disconnected")(lambda *_: room_disconnected.set())

    # Join the room as the agent participant so callers/browsers can share it
    # with us (livekit-agents requires the entrypoint to invoke connect()).
    await ctx.connect()

    state = GatewayState()
    state.speech_factory = build_speech_factory(settings)
    if settings.PRACTICE_API_BASE_URL:
        state.practice_client = PracticeApiClient(
            base_url=settings.PRACTICE_API_BASE_URL,
            timeout=settings.PRACTICE_API_TIMEOUT,
            max_attempts=settings.PRACTICE_API_MAX_ATTEMPTS,
        )
    state.embedding_factory = EmbeddingProviderFactory(settings)
    state.vector_store_factory = VectorStoreProviderFactory(settings)
    state.llm_factory = LLMProviderFactory(settings)

    session: EmmaAgentSession | None = None
    caller_identity: str | None = None

    async def _launch_session() -> None:
        nonlocal session
        if session is not None:
            return
        audio_format = AudioFormat(sample_rate=16000, channels=1)
        caller = _caller_identity(room, caller_identity)
        speech_session = SpeechSession(
            session_id=ctx.job.id,
            call_sid=ctx.job.id,
            practice_id=settings.PRACTICE_ID or "demo-practice-001",
            audio_format=audio_format,
            caller=caller,
        )

        retriever = None
        knowledge_answer_service = None
        if (
            state.embedding_factory is not None
            and state.vector_store_factory is not None
            and settings.rag_configured
        ):
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

        pipeline = VoicePipeline(
            session=speech_session,
            stt=state.speech_factory.create_stt(audio_format),
            tts=state.speech_factory.create_tts(audio_format),
            response_handler=DeterministicResponseHandler(),
            practice_client=state.practice_client,
            practice_id=settings.PRACTICE_ID,
            settings=settings,
            retriever=retriever,
            knowledge_answer_service=knowledge_answer_service,
            llm_factory=state.llm_factory,
            control_events=True,
            caller=caller,
        )

        session = EmmaAgentSession(ctx=ctx, settings=settings, pipeline=pipeline)
        await session.start()
        logger.info(
            "emma agent session started for caller",
            extra={"session_id": ctx.job.id},
        )

    # Launch for a caller that already exists; otherwise wait for the first.
    first = _first_caller_identity(room)
    if first is not None:
        caller_identity = first
        logger.info(
            "caller already in room at dispatch",
            extra={"session_id": ctx.job.id, "caller": first},
        )
        await _launch_session()
    else:
        waiter: asyncio.Future[str] = asyncio.get_running_loop().create_future()

        def _on_participant(participant: Any) -> None:
            identity = getattr(participant, "identity", None)
            if (
                not waiter.done()
                and isinstance(identity, str)
                and identity.startswith(_CALLER_IDENTITY_PREFIX)
            ):
                waiter.set_result(identity)

        room.on("participant_connected")(_on_participant)
        existing = _first_caller_identity(room)
        if existing is not None:
            first = existing
        else:
            logger.info(
                "waiting for caller participant",
                extra={"session_id": ctx.job.id},
            )
            first = await asyncio.wait_for(waiter, timeout=None)
        caller_identity = first
        await _launch_session()

    # Keep the job alive until the session ends (caller left, error, or room
    # gone) — waiting on the room alone kept the job process busy after the
    # caller hung up, so the next call had no free worker slot.
    try:
        if session is not None:
            await session.wait_closed()
        else:  # pragma: no cover - defensive: no session was ever created
            await room_disconnected.wait()
    finally:
        if session is not None:
            await session.stop(reason="session_closed")
        try:
            await room.disconnect()
        except Exception:  # noqa: BLE001 — best-effort
            logger.debug("job room disconnect failed")


def build_speech_factory(settings: Settings) -> Any:
    """Build the provider factory used by the agent (Day 18C).

    Extracted so the *actual* agent construction path can be exercised by tests:
    when the live budget guard is enabled this returns a factory whose Deepgram
    and ElevenLabs providers are budget-guarded, and which fails closed if the
    ledger is unusable.
    """
    from app.speech.factory import SpeechProviderFactory

    factory = SpeechProviderFactory(settings)
    if factory.live_guard_enabled:
        guard_status = factory.live_guard_status()
        if factory.live_guard_ready:
            logger.info(
                "live provider budget guard active",
                extra={
                    "run_id": guard_status.get("run_id"),
                    "usage": guard_status.get("usage"),
                    "limits": guard_status.get("limits"),
                },
            )
        else:
            logger.error(
                "live provider budget guard unavailable; live providers will refuse",
                extra={"detail": guard_status.get("error")},
            )
    return factory


def create_worker_options() -> Any:
    """Build `livekit.agents.WorkerOptions` for the Emma agent entrypoint.

    Returns None if livekit-agents is not installed.
    """
    from app.integrations.livekit import _import_livekit_agents

    agents = _import_livekit_agents()
    try:
        # Two idle processes: a new call is served immediately while the previous
        # one is still tearing down (one job per process otherwise serialises).
        return agents.WorkerOptions(entrypoint_fnc=entrypoint, num_idle_processes=2)
    except TypeError:  # older SDK without the option
        return agents.WorkerOptions(entrypoint_fnc=entrypoint)


def seed_livekit_env(settings: Settings) -> None:
    """Copy LiveKit credentials from the app settings into `os.environ`.

    `livekit.agents.AgentServer.run()` reads `LIVEKIT_URL` / `LIVEKIT_API_KEY` /
    `LIVEKIT_API_SECRET` straight from the process environment and crashes with
    "ws_url is required" when they are absent — even though the gateway loads the
    same values from `.env`. That made the documented start command
    (`python -m app.integrations.livekit.agent dev`) fail instantly, which is how
    a browser ends up in a room with no agent.

    Values are never logged. Existing environment variables win, so an operator
    can still override per process.
    """
    for name, value in (
        ("LIVEKIT_URL", settings.LIVEKIT_URL),
        ("LIVEKIT_API_KEY", settings.LIVEKIT_API_KEY),
        ("LIVEKIT_API_SECRET", settings.LIVEKIT_API_SECRET),
    ):
        if value and not os.environ.get(name):
            os.environ[name] = value


def main() -> None:
    """Run the LiveKit agent worker.

    Entry point for `python -m app.integrations.livekit.agent dev`.
    LiveKit credentials come from the app settings (`.env` or the process
    environment) and are seeded into the environment before the worker starts.

    Runs with devmode=False so the worker's LiveKit WebSocket connection
    stays stable (devmode causes the connection to degrade after ~7s,
    breaking agent dispatch between tests).
    """
    from app.integrations.livekit import _import_livekit_agents

    _import_livekit_agents()
    import asyncio

    from livekit.agents.cli.log import setup_logging
    from livekit.agents.worker import AgentServer

    # The worker needs LIVEKIT_* in os.environ; Settings is the documented source.
    settings = Settings()  # type: ignore[call-arg] — env loaded via pydantic-settings
    seed_livekit_env(settings)
    if not os.environ.get("LIVEKIT_URL"):
        raise SystemExit(
            "LIVEKIT_URL is not configured (set it in .env or the environment); "
            "refusing to start an agent worker that could never register"
        )

    setup_logging("INFO", devmode=False, console=True)

    server = AgentServer.from_server_options(create_worker_options())
    asyncio.run(server.run(devmode=False, unregistered=False))


__all__ = [
    "EmmaAgentSession",
    "build_speech_factory",
    "create_worker_options",
    "entrypoint",
    "main",
]


if __name__ == "__main__":
    main()
