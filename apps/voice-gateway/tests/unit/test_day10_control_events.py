"""Day 10: opt-in UI control events and energy-gated barge-in helpers.

The browser voice UI consumes `transcript` / `assistant` / `state` / `barge-in`
JSON messages alongside the verified `media` envelopes. These tests protect:

* control events are only queued when the connection opts in;
* assistant text is queued before its audio (ordering);
* a final transcript is surfaced exactly once;
* barge-in emits a control event after queued audio is discarded;
* PCM16 RMS distinguishes silence from speech.
"""

from __future__ import annotations

import pytest

from app.api.routes.websocket import _pcm16_rms
from app.domain.fsm import TransitionReason, VoiceState
from app.domain.speech import FinalTranscript, PartialTranscript
from app.services.response_handler import ConversationDecision
from app.services.voice_pipeline import ControlEvent
from tests.evaluation.day9 import close, drain_outbound, make_pipeline

SESSION_ID = "day10-control-events"


def _controls(pipeline) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    while not pipeline._outbound_queue.empty():
        item = pipeline._outbound_queue.get_nowait()
        if isinstance(item, ControlEvent):
            payloads.append(item.payload)
    return payloads


def test_pcm16_rms_separates_silence_from_speech() -> None:
    assert _pcm16_rms(b"") == 0.0
    assert _pcm16_rms(b"\x00\x00" * 320) == 0.0
    # 16-bit sample 0x0101 = 257 → RMS 257 (above the barge-in floor).
    assert _pcm16_rms(b"\x01\x01" * 320) == pytest.approx(257.0)
    # Full-scale square wave is far above the threshold.
    assert _pcm16_rms(b"\xff\x7f" * 320) > 30_000


@pytest.mark.asyncio
async def test_control_events_disabled_by_default() -> None:
    harness = await make_pipeline(responses={})
    try:
        drain_outbound(harness)
        harness.pipeline.session.fsm.transition(
            VoiceState.PROCESSING, TransitionReason.UTTERANCE_RECEIVED
        )
        await harness.pipeline._speak(
            ConversationDecision(response_text="Default mode reply."),
            enter_reason=TransitionReason.RESPONSE_STARTED,
            exit_reason=TransitionReason.RESPONSE_COMPLETED,
        )
        items = []
        while not harness.pipeline._outbound_queue.empty():
            items.append(harness.pipeline._outbound_queue.get_nowait())
        assert [i for i in items if isinstance(i, ControlEvent)] == []
        assert [i for i in items if isinstance(i, bytes)], "audio should still be queued"
    finally:
        await close(harness)


@pytest.mark.asyncio
async def test_assistant_text_precedes_audio_and_listening_follows() -> None:
    harness = await make_pipeline(responses={})
    try:
        pipeline = harness.pipeline
        pipeline._control_events = True
        drain_outbound(harness)

        pipeline.session.fsm.transition(VoiceState.PROCESSING, TransitionReason.UTTERANCE_RECEIVED)
        await pipeline._speak(
            ConversationDecision(response_text="Hello from EMMA."),
            enter_reason=TransitionReason.RESPONSE_STARTED,
            exit_reason=TransitionReason.RESPONSE_COMPLETED,
        )

        items = []
        while not pipeline._outbound_queue.empty():
            items.append(pipeline._outbound_queue.get_nowait())
        first_control = next(i for i in items if isinstance(i, ControlEvent))
        first_audio = next(i for i in items if isinstance(i, bytes))
        assert items.index(first_control) < items.index(first_audio)

        controls = [i.payload for i in items if isinstance(i, ControlEvent)]
        assert {"event": "assistant", "text": "Hello from EMMA."} in controls
        assert {"event": "state", "state": "speaking"} in controls
        assert controls[-1] == {"event": "state", "state": "listening"}
    finally:
        await close(harness)


@pytest.mark.asyncio
async def test_partial_and_final_transcripts_are_emitted_once() -> None:
    harness = await make_pipeline(responses={})
    try:
        pipeline = harness.pipeline
        pipeline._control_events = True
        drain_outbound(harness)
        session_id = pipeline.session.session_id

        await pipeline._handle_stt_event(
            PartialTranscript(
                session_id=session_id,
                provider="eval-stt",
                sequence=1,
                text="i would like",
                confidence=0.4,
            )
        )
        # Emergency text short-circuits before the LLM, so this needs no script.
        await pipeline._handle_stt_event(
            FinalTranscript(
                session_id=session_id,
                provider="eval-stt",
                sequence=2,
                text="I am having severe chest pain",
                confidence=0.99,
            )
        )

        controls = _controls(pipeline)
        transcript_events = [c for c in controls if c.get("event") == "transcript"]
        assert transcript_events == [
            {"event": "transcript", "kind": "partial", "text": "i would like"},
            {
                "event": "transcript",
                "kind": "final",
                "text": "I am having severe chest pain",
            },
        ]
        assert {"event": "state", "state": "thinking"} in controls
        assert any(c.get("event") == "assistant" for c in controls)
    finally:
        await close(harness)


@pytest.mark.asyncio
async def test_barge_in_emits_control_event_after_discarding_audio() -> None:
    harness = await make_pipeline(responses={})
    try:
        pipeline = harness.pipeline
        pipeline._control_events = True
        pipeline._current_tts_utterance_id = "utterance-day10"
        pipeline._outbound_queue.put_nowait(b"\x00\x01" * 160)

        await pipeline.cancel_tts()

        assert pipeline._outbound_queue.qsize() == 1
        item = pipeline._outbound_queue.get_nowait()
        assert isinstance(item, ControlEvent)
        assert item.payload == {"event": "barge-in"}
    finally:
        await close(harness)
