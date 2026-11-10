"""In-process observability registry (Day 20).

Pipecat reports per-turn TTFB/TTFA and per-service processing time. EMMA's
`SessionMetrics` already collects those numbers per session; this module keeps a
bounded, PII-free window of recent sessions so an operator can ask
`GET /api/v1/diagnostics` for real p50/p95 latencies instead of guessing.

Deliberately in-process (no Prometheus/OTel dependency): the acceptance criteria
ask for *meaningful measurements*, and adding a metrics backend is an
infrastructure decision this prototype should not make for the operator. The
snapshot shape is stable, so exporting it to OTel later is a decorator, not a
rewrite.

Content policy: session ids, counters and latencies only. Never transcripts,
audio, caller details or provider payloads.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

_RECENT_MAX = 100

_LATENCY_KEYS = (
    "final_to_processing_ms",
    "processing_to_first_audio_ms",
    "turn_total_ms",
    "llm_ms",
    "tts_ms",
    "interruption_latency_ms",
    "tool_ms",
    "barge_in_latency_ms",
)


@dataclass(frozen=True, slots=True)
class SessionRecord:
    """One finished session's measurements (already PII-free)."""

    session_id: str
    call_sid: str | None
    started_at: float
    ended_at: float
    providers: dict[str, str] = field(default_factory=dict)
    latencies: dict[str, tuple[float, ...]] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)
    errors: tuple[str, ...] = ()

    @property
    def duration_seconds(self) -> float:
        return round(max(0.0, self.ended_at - self.started_at), 3)


def percentiles(values: list[float]) -> dict[str, float | None]:
    """p50 / p95 / max for a sample (None when empty)."""
    if not values:
        return {"p50": None, "p95": None, "max": None, "count": 0}
    ordered = sorted(values)

    def pick(fraction: float) -> float:
        index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
        return round(ordered[index], 2)

    return {
        "p50": pick(0.5),
        "p95": pick(0.95),
        "max": round(ordered[-1], 2),
        "count": len(ordered),
    }


class LatencyRegistry:
    """Bounded rolling window of session records + aggregation."""

    def __init__(self, *, maxlen: int = _RECENT_MAX) -> None:
        self._records: deque[SessionRecord] = deque(maxlen=maxlen)

    def record(self, record: SessionRecord) -> None:
        self._records.append(record)

    def record_from_metrics(
        self,
        *,
        metrics_record: dict[str, Any],
        started_at: float,
        tool_summary: dict[str, Any] | None = None,
    ) -> SessionRecord:
        """Adapt a `SessionMetrics.build_record()` payload and record it."""
        latency = metrics_record.get("latency") or {}
        turns = metrics_record.get("turns") or {}
        vad = metrics_record.get("vad") or {}
        collected: dict[str, tuple[float, ...]] = {
            key: tuple(float(v) for v in (latency.get(key) or [])) for key in _LATENCY_KEYS
        }
        collected["interruption_latency_ms"] = tuple(
            float(v) for v in (turns.get("interruption_latency_ms") or [])
        )
        collected["barge_in_latency_ms"] = tuple(
            float(v) for v in (vad.get("barge_in_latency_ms") or [])
        )
        if tool_summary is not None:
            tool_latency = (tool_summary.get("latency_ms") or {}).get("p50")
            collected["tool_ms"] = (float(tool_latency),) if tool_latency is not None else ()

        counters = {
            "turns_total": int(len(latency.get("turn_total_ms") or [])),
            "turns_dropped": int(turns.get("dropped") or 0),
            "turns_held": int(turns.get("held") or 0),
            "interruptions": int(turns.get("interrupted") or 0),
            "early_barge_ins": int(vad.get("early_barge_ins") or 0),
            "speech_starts": int(vad.get("speech_starts") or 0),
            "provider_errors": int(len(metrics_record.get("provider_errors") or [])),
        }
        record = SessionRecord(
            session_id=str(metrics_record.get("session_id") or ""),
            call_sid=metrics_record.get("call_sid"),
            started_at=started_at,
            ended_at=time.time(),
            providers=dict(metrics_record.get("providers") or {}),
            latencies=collected,
            counters=counters,
            errors=tuple(str(e) for e in (metrics_record.get("errors") or []))[:10],
        )
        self.record(record)
        return record

    @property
    def size(self) -> int:
        return len(self._records)

    def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        """Last N session summaries (no ids beyond the opaque session id)."""
        limited = list(self._records)[-max(1, min(limit, 50)) :]
        return [
            {
                "session_id": record.session_id,
                "duration_seconds": record.duration_seconds,
                "providers": record.providers,
                "counters": record.counters,
                "p95_turn_total_ms": percentiles(list(record.latencies.get("turn_total_ms", ())))[
                    "p95"
                ],
            }
            for record in limited
        ]

    def snapshot(self) -> dict[str, Any]:
        """Aggregated latency percentiles + provider/counter totals."""
        latencies: dict[str, list[float]] = {key: [] for key in _LATENCY_KEYS}
        counters: dict[str, int] = {}
        providers: dict[str, str] = {}
        for record in self._records:
            for key, values in record.latencies.items():
                latencies.setdefault(key, []).extend(values)
            for key, value in record.counters.items():
                counters[key] = counters.get(key, 0) + value
            providers.update(record.providers)
        return {
            "sessions_recorded": len(self._records),
            "providers": providers,
            "counters": counters,
            "latency_ms": {key: percentiles(values) for key, values in latencies.items()},
        }

    def reset(self) -> None:
        self._records.clear()


# Process-wide registry (one gateway process; bounded).
registry = LatencyRegistry()


__all__ = ["LatencyRegistry", "SessionRecord", "percentiles", "registry"]
