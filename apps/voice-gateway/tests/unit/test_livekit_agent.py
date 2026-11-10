"""Day 13/17: LiveKit agent session tests (offline, no credentials).

Tests the `EmmaAgentSession` class using mocked pipeline and LiveKit SDK.
No network calls, no API credits consumed.

Day 17: the bridge is SDK-backed (`rtc.AudioStream` frames → pipeline;
pipeline output → `LiveKitAgent.publish_audio`), so the tests stub
`_import_livekit` with a fake rtc module and verify:

* start/stop lifecycle (pipeline started once, agent transport started/stopped);
* audio input: a subscribed caller audio track (identity `emma-caller-…`)
  pushes frames to `pipeline.submit_audio` as `AudioReceived`;
* non-caller tracks (probe/test instrumentation) are ignored;
* explicit resample: 48 kHz frames are resampled to the negotiated 16 kHz;
* barge-in: a `{"event": "barge-in"}` data message cancels in-flight TTS.
"""

from __future__ import annotations

import asyncio
import json
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings


def _make_settings(**overrides: Any) -> Settings:
    data = {
        "LIVEKIT_API_KEY": "test-key-not-real",
        "LIVEKIT_API_SECRET": "test-secret-not-real",
        "LIVEKIT_URL": "wss://test.livekit.cloud",
        "PRACTICE_ID": "demo-practice-001",
        "STT_PROVIDER": "deepgram",
        "TTS_PROVIDER": "elevenlabs",
        "LLM_PROVIDER": "openai",
        "OPENAI_API_KEY": "test-key-not-real",
        "DEEPGRAM_API_KEY": "test-key-not-real",
        "ELEVENLABS_API_KEY": "test-key-not-real",
        "ELEVENLABS_VOICE_ID": "test-voice-not-real",
        "AZURE_SPEECH_KEY": "test-key-not-real",
        "AZURE_SPEECH_REGION": "eastasia",
        "LLM_API_KEY": "test-key-not-real",
        "GEMINI_EMBEDDING_API_KEY": "test-key-not-real",
    }
    data.update(overrides)
    return Settings(**data)


@pytest.fixture()
def mock_pipeline() -> Any:
    """Mock the VoicePipeline."""
    pipeline = MagicMock()
    pipeline.start = AsyncMock()
    pipeline.aclose = AsyncMock()
    pipeline.submit_audio = AsyncMock()
    pipeline.cancel_tts = AsyncMock()
    pipeline.should_cancel_for_barge_in = False

    async def _empty_aiter():
        if False:
            yield

    pipeline.stream_outbound = _empty_aiter
    return pipeline


def _patched_agent_factory() -> Any:
    """A LiveKitAgent fake whose async methods are awaitable."""
    fake_agent = MagicMock()
    fake_agent.start = AsyncMock()
    fake_agent.stop = AsyncMock()
    fake_agent.publish_audio = AsyncMock()
    fake_agent.publish_text = AsyncMock()
    return fake_agent


def _make_session(settings: Settings, mock_pipeline: Any, agent: Any) -> Any:
    """Build an EmmaAgentSession with the LiveKit transport mocked out."""
    from app.integrations.livekit.agent import EmmaAgentSession

    mock_ctx = MagicMock()
    mock_ctx.job.id = "test-session-001"
    mock_ctx.room = MagicMock()
    mock_ctx.room.name = "test-room"
    # MagicMock supports iteration/membership; the initial-participant scan
    # must see no pre-existing callers.
    mock_ctx.room.remote_participants = {}

    with patch(
        "app.integrations.livekit.agent.LiveKitAgent",
        return_value=agent,
    ):
        return EmmaAgentSession(ctx=mock_ctx, settings=settings, pipeline=mock_pipeline)


def _fake_rtc_module(frames: list[Any]) -> Any:
    """A fake `livekit.rtc` module exposing AudioStream over the given frames."""

    class _FakeAudioStream:
        def __init__(self, track: Any) -> None:
            self._frames = frames
            self.track = track

        def __aiter__(self):  # noqa: ANN102 - part of fake typing
            return self._gen()

        async def _gen(self):
            for frame in self._frames:
                event = MagicMock()
                event.frame = frame
                yield event

    return types.SimpleNamespace(AudioStream=_FakeAudioStream)


@pytest.mark.asyncio
async def test_emma_agent_session_start_stop(mock_pipeline: Any) -> None:
    """The EmmaAgentSession starts and stops the pipeline + transport."""
    agent = _patched_agent_factory()
    session = _make_session(_make_settings(), mock_pipeline, agent)

    assert session.session_id == "test-session-001"

    await session.start()
    agent.start.assert_awaited_once_with("test-session-001")
    mock_pipeline.start.assert_awaited_once()

    await session.stop(reason="test_stop")
    agent.stop.assert_awaited_once_with("test-session-001", reason="test_stop")
    mock_pipeline.aclose.assert_awaited_once_with(reason="test_stop")


@pytest.mark.asyncio
async def test_emma_agent_session_stop_without_start(mock_pipeline: Any) -> None:
    """Stopping a session that was never started is a no-op."""
    agent = _patched_agent_factory()
    session = _make_session(_make_settings(), mock_pipeline, agent)

    await session.stop(reason="never_started")
    mock_pipeline.start.assert_not_awaited()
    mock_pipeline.aclose.assert_awaited_once_with(reason="never_started")
    agent.stop.assert_not_awaited()


@pytest.mark.asyncio
async def test_emma_agent_session_caller_audio_pushed_to_stt(mock_pipeline: Any) -> None:
    """A subscribed caller audio track's frames are pushed to the pipeline."""
    frame = MagicMock()
    frame.data = memoryview(b"\x10\x00\x20\x00" * 160)  # 160 samples of 16 kHz mono
    frame.sample_rate = 16000
    frame.num_channels = 1
    agent = _patched_agent_factory()
    settings = _make_settings()
    session = _make_session(settings, mock_pipeline, agent)

    with patch(
        "app.integrations.livekit._import_rtc",
        return_value=_fake_rtc_module([frame]),
    ):
        await session.start()
        caller = MagicMock()
        caller.identity = "emma-caller-abcdef01"
        track = MagicMock()
        track.kind = "audio"

        session._on_track_subscribed(track, MagicMock(), caller)
        # Let the input loop drain the frames.
        await asyncio.sleep(0.2)
        await session.stop(reason="done")

    mock_pipeline.submit_audio.assert_awaited()
    call = mock_pipeline.submit_audio.await_args
    received = call.args[0] if call.args else call.kwargs.get("event")
    assert received.payload == b"\x10\x00\x20\x00" * 160
    assert received.audio_format.sample_rate == 16000
    assert received.audio_format.channels == 1


@pytest.mark.asyncio
async def test_emma_agent_session_ignores_non_caller_tracks(mock_pipeline: Any) -> None:
    """Tracks from non-caller participants (e.g. test probes) are not STT input."""
    frame = MagicMock()
    frame.data = memoryview(b"\x10\x00" * 160)
    frame.sample_rate = 16000
    frame.num_channels = 1
    agent = _patched_agent_factory()
    session = _make_session(_make_settings(), mock_pipeline, agent)

    with patch(
        "app.integrations.livekit._import_rtc",
        return_value=_fake_rtc_module([frame]),
    ):
        await session.start()
        probe = MagicMock()
        probe.identity = "test-probe"
        track = MagicMock()
        track.kind = "audio"

        session._on_track_subscribed(track, MagicMock(), probe)
        await asyncio.sleep(0.2)
        await session.stop(reason="done")

    mock_pipeline.submit_audio.assert_not_awaited()


@pytest.mark.asyncio
async def test_emma_agent_session_ignores_non_audio_tracks(mock_pipeline: Any) -> None:
    """Video tracks are not forwarded to the pipeline."""
    agent = _patched_agent_factory()
    session = _make_session(_make_settings(), mock_pipeline, agent)

    with patch(
        "app.integrations.livekit._import_rtc",
        return_value=_fake_rtc_module([]),
    ):
        await session.start()
        caller = MagicMock()
        caller.identity = "emma-caller-abcdef01"
        track = MagicMock()
        track.kind = "video"

        session._on_track_subscribed(track, MagicMock(), caller)
        await asyncio.sleep(0.1)
        await session.stop(reason="done")

        mock_pipeline.submit_audio.assert_not_awaited()


@pytest.mark.asyncio
async def test_emma_agent_session_resamples_48khz_to_16khz(mock_pipeline: Any) -> None:
    """48 kHz frames (browser Opus) are explicitly resampled to 16 kHz."""
    from app.integrations.livekit.agent import _resample_if_needed

    # A 48 kHz frame: 480 samples (10 ms).
    payload_48k = b"\x01\x00" * 480
    resampled, rate = _resample_if_needed(payload_48k, 48000)
    assert rate == 16000
    # 480 samples @48 kHz ≈ 160 samples @16 kHz.
    assert abs(len(resampled) // 2 - 160) <= 2


@pytest.mark.asyncio
async def test_emma_agent_session_barge_in_data_cancels_tts(mock_pipeline: Any) -> None:
    """A barge-in data message from the browser cancels in-flight TTS."""
    agent = _patched_agent_factory()
    settings = _make_settings()
    session = _make_session(settings, mock_pipeline, agent)

    await session.start()
    mock_pipeline.start.assert_awaited_once()

    mock_pipeline.should_cancel_for_barge_in = True
    event = MagicMock()
    event.payload = json.dumps({"event": "barge-in"}).encode("utf-8")
    session._on_data_received(event)

    # Let the cancellation task run.
    await asyncio.sleep(0.1)
    mock_pipeline.cancel_tts.assert_awaited_once()

    await session.stop(reason="done")


@pytest.mark.asyncio
async def test_emma_agent_session_ignores_unknown_data_events(mock_pipeline: Any) -> None:
    """Non-barge-in control messages do not touch the pipeline's TTS."""
    agent = _patched_agent_factory()
    session = _make_session(_make_settings(), mock_pipeline, agent)

    await session.start()

    event = MagicMock()
    event.payload = json.dumps({"event": "state", "state": "speaking"}).encode("utf-8")
    session._on_data_received(event)

    await asyncio.sleep(0.1)
    mock_pipeline.cancel_tts.assert_not_awaited()

    await session.stop(reason="done")


@pytest.mark.asyncio
async def test_emma_agent_session_output_loop_publishes_audio_and_controls(
    mock_pipeline: Any,
) -> None:
    """Pipeline outaudio bytes are published via transport; controls as JSON."""
    agent = _patched_agent_factory()

    from app.services.voice_pipeline import ControlEvent

    async def _aiter():
        yield b"\x01\x02" * 8  # one audio chunk
        yield ControlEvent(payload={"event": "state", "state": "speaking"})

    mock_pipeline.stream_outbound = _aiter

    session = _make_session(_make_settings(), mock_pipeline, agent)
    await session.start()

    await asyncio.sleep(0.2)
    await session.stop(reason="done")

    agent.publish_audio.assert_awaited_once()
    audio_args = agent.publish_audio.await_args
    assert audio_args.args[0] == "test-session-001" or audio_args.kwargs.get("session_id") == (
        "test-session-001"
    )
    agent.publish_text.assert_awaited()
    text_arg = agent.publish_text.await_args.args[1]
    assert json.loads(text_arg) == {"event": "state", "state": "speaking"}


__all__ = [
    "test_emma_agent_session_start_stop",
    "test_emma_agent_session_stop_without_start",
    "test_emma_agent_session_caller_audio_pushed_to_stt",
    "test_emma_agent_session_ignores_non_caller_tracks",
    "test_emma_agent_session_ignores_non_audio_tracks",
    "test_emma_agent_session_resamples_48khz_to_16khz",
    "test_emma_agent_session_barge_in_data_cancels_tts",
    "test_emma_agent_session_ignores_unknown_data_events",
    "test_emma_agent_session_output_loop_publishes_audio_and_controls",
]


# --- Day 18E-A: worker startup must seed LIVEKIT_* from the app settings ----------
#
# `livekit.agents.AgentServer.run()` reads LIVEKIT_URL/API_KEY/API_SECRET straight
# from os.environ. `main()` used to leave them unset, so the documented start
# command (`python -m app.integrations.livekit.agent dev`) died instantly with
# "ws_url is required" — leaving the browser in a room with no agent.


def test_seed_livekit_env_copies_settings_into_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import Settings
    from app.integrations.livekit.agent import seed_livekit_env

    for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(
        LIVEKIT_URL="ws://127.0.0.1:7880",
        LIVEKIT_API_KEY="test-key-not-real",
        LIVEKIT_API_SECRET="test-secret-not-real",
    )
    seed_livekit_env(settings)

    import os

    assert os.environ["LIVEKIT_URL"] == "ws://127.0.0.1:7880"
    assert os.environ["LIVEKIT_API_KEY"] == "test-key-not-real"


def test_seed_livekit_env_keeps_an_explicit_process_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operator's exported value must win over the .env-derived one."""
    from app.core.config import Settings
    from app.integrations.livekit.agent import seed_livekit_env

    monkeypatch.setenv("LIVEKIT_URL", "ws://127.0.0.1:9999")

    settings = Settings(
        LIVEKIT_URL="ws://127.0.0.1:7880",
        LIVEKIT_API_KEY="test-key-not-real",
        LIVEKIT_API_SECRET="test-secret-not-real",
    )
    seed_livekit_env(settings)

    import os

    assert os.environ["LIVEKIT_URL"] == "ws://127.0.0.1:9999"


@pytest.mark.asyncio
async def test_caller_audio_published_before_handlers_is_still_attached() -> None:
    """Regression: caller published before the agent joined → no audio was read.

    The `track_subscribed` event fires during `room.connect()`, before handlers
    exist. Attachment must be reconciled from room state, not event-only.
    """
    from app.integrations.livekit.agent import EmmaAgentSession

    settings = _make_settings()
    pipeline = MagicMock()
    pipeline.start = AsyncMock()
    pipeline.aclose = AsyncMock()
    pipeline.submit_audio = AsyncMock()
    pipeline.cancel_tts = AsyncMock()
    pipeline.should_cancel_for_barge_in = False

    async def _empty_aiter():
        if False:  # pragma: no cover
            yield

    pipeline.stream_outbound = _empty_aiter

    class _Publication:
        sid = "pub-1"

        def __init__(self, track: Any) -> None:
            self.track = track

    class _Track:
        sid = "track-1"
        kind = 1  # KIND_AUDIO

    class _Participant:
        identity = "emma-caller-abc123"

        def __init__(self, track: Any) -> None:
            self.track_publications = {"pub-1": _Publication(track)}

    track = _Track()
    participant = _Participant(track)

    mock_ctx = MagicMock()
    mock_ctx.job.id = "test-session-reconcile"
    mock_ctx.room = MagicMock()
    mock_ctx.room.name = "test-room"
    mock_ctx.room.remote_participants = {participant.identity: participant}

    agent = _patched_agent_factory()
    with patch("app.integrations.livekit.agent.LiveKitAgent", return_value=agent):
        session = EmmaAgentSession(ctx=mock_ctx, settings=settings, pipeline=pipeline)
    session._started = True  # already past start(); handlers attach tracks

    session._setup_room_handlers()
    await asyncio.sleep(0.05)

    assert "pub-1" in session._attached_sids, "existing caller track must be attached"
    assert session._input_tasks, "an input loop must be running"

    # A second reconciliation must not attach the same publication twice.
    before = len(session._attached_sids)
    session._attach_existing_caller_audio()
    assert len(session._attached_sids) == before

    await session.stop(reason="test")


class _FakeAudioStreamReconcile:
    def __init__(self, track: Any) -> None:
        self.track = track

    def __aiter__(self):  # pragma: no cover - no frames in this test
        return self._gen()

    async def _gen(self):
        if False:
            yield None
