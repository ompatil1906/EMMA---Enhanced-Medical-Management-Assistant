"""Process lifecycle state: gateway readiness flags + active-session registry."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.domain.session import VoiceSession


@dataclass
class SessionRegistry:
    """Tracks live WebSocket sessions so health/readiness and cleanup are observable.

    Not a leak: `unregister` is idempotent and the WS route always unregisters in
    its `finally` block, whatever the disconnection path was.
    """

    _sessions: dict[str, VoiceSession] = field(default_factory=dict)

    def register(self, session: VoiceSession) -> str:
        """Insert a session under its current id and return that id."""
        if session.session_id in self._sessions:
            raise RuntimeError(f"session id {session.session_id!r} already registered")
        self._sessions[session.session_id] = session
        return session.session_id

    def adopt(self, session: VoiceSession, previous_id: str) -> str:
        """Re-key a session after `adopt_stream` (returns the id to forget); returns new id."""
        if previous_id != session.session_id:
            self._sessions.pop(previous_id, None)
        self._sessions[session.session_id] = session
        return session.session_id

    def unregister(self, session_id: str) -> None:
        """Idempotent removal — safe to call multiple times on the cleanup path."""
        self._sessions.pop(session_id, None)

    def count(self) -> int:
        return len(self._sessions)

    def ids(self) -> list[str]:
        return list(self._sessions)


@dataclass
class GatewayState:
    """Held on `app.state.gateway`; flipped by the lifespan handler."""

    started_at: float | None = None
    ready: bool = False
    registry: SessionRegistry = field(default_factory=SessionRegistry)

    def mark_started(self) -> None:
        self.started_at = time.monotonic()

    def mark_ready(self) -> None:
        self.ready = True

    def mark_stopped(self) -> None:
        self.ready = False
        self.started_at = None

    @property
    def uptime_seconds(self) -> float | None:
        if self.started_at is None:
            return None
        return round(time.monotonic() - self.started_at, 3)
