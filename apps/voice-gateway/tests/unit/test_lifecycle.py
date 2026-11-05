"""Lifecycle state + session bookkeeping tests."""

import pytest

from app.core.lifecycle import GatewayState, SessionRegistry
from app.domain.session import VoiceSession


class TestSessionRegistry:
    def test_register_count_unregister(self) -> None:
        registry = SessionRegistry()
        session = VoiceSession(provider="simulation")
        registry.register(session)
        assert registry.count() == 1
        registry.unregister(session.session_id)
        assert registry.count() == 0

    def test_register_duplicate_is_a_bug_signal(self) -> None:
        registry = SessionRegistry()
        session = VoiceSession(provider="simulation")
        registry.register(session)
        with pytest.raises(RuntimeError, match="already registered"):
            registry.register(session)

    def test_unregister_is_idempotent(self) -> None:
        registry = SessionRegistry()
        session = VoiceSession(provider="simulation")
        registry.register(session)
        registry.unregister(session.session_id)
        registry.unregister(session.session_id)  # must not raise
        assert registry.count() == 0

    def test_adopt_rekeys_session(self) -> None:
        registry = SessionRegistry()
        session = VoiceSession(provider="exotel")
        registry.register(session)
        pending_id = session.session_id  # registry key before adoption
        session.adopt_stream(stream_sid="stream-provider-1", call_sid=None)
        assert session.session_id == "stream-provider-1"
        assert registry.adopt(session, pending_id) == "stream-provider-1"
        assert registry.ids() == ["stream-provider-1"]
        registry.unregister(pending_id)  # stale id must be a no-op
        assert registry.count() == 1


class TestGatewayState:
    def test_uptime_and_ready_transitions(self) -> None:
        state = GatewayState()
        assert state.started_at is None
        assert state.uptime_seconds is None
        state.mark_started()
        assert state.uptime_seconds is not None
        state.mark_ready()
        assert state.ready is True
        state.mark_stopped()
        assert state.started_at is None
        assert state.uptime_seconds is None
        assert state.ready is False


class TestVoiceSession:
    def test_defaults_use_verified_exotel_audio(self) -> None:
        session = VoiceSession(provider="exotel")
        assert session.state == "pending"
        assert session.session_id.startswith("pending-")
        assert session.audio_format.sample_rate == 8000
        assert session.audio_format.encoding == "PCM_L16"

    def test_adopt_stream_updates_state(self) -> None:
        session = VoiceSession(provider="exotel")
        previous_id = session.adopt_stream(stream_sid="stream-abc", call_sid="call-abc")
        assert previous_id.startswith("pending-")  # caller re-keys the registry with it
        assert session.session_id == "stream-abc"
        assert session.stream_sid == "stream-abc"
        assert session.call_sid == "call-abc"
        assert session.state == "active"
        assert session.uptime_seconds >= 0
