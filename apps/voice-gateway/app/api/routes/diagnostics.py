"""Operational diagnostics (Day 20).

Two read-only endpoints, no caller data:

* `GET /api/v1/tools` — the tool catalog the assistant may invoke (name,
  description, permission, confirmation requirement, timeout, JSON schema).
  Handlers are never exposed and nothing here executes a tool.
* `GET /api/v1/diagnostics` — provider selection, pipeline tunables, tool
  catalog and the rolling latency percentiles collected from finished sessions.

Content policy: names, booleans, counters and latencies only. No transcripts, no
audio, no caller details, no credentials.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.core.config import get_settings
from app.integrations.jira_client import JiraClient
from app.services.observability import registry as latency_registry
from app.services.tools import build_default_registry

router = APIRouter(prefix="/api/v1", tags=["diagnostics"])

_VERSION = "0.4.0"


def _tool_catalog() -> dict[str, Any]:
    """Catalog built from a spec-only registry (handlers are never invoked)."""
    registry = build_default_registry()
    return {
        "tools": registry.catalog(),
        "function_declarations": registry.function_declarations(),
    }


@router.get("/tools")
async def list_tools() -> dict[str, Any]:
    """Read-only tool catalog (safe for operators and for LLM wiring)."""
    return _tool_catalog()


@router.get("/diagnostics")
async def diagnostics() -> dict[str, Any]:
    """Provider selection, tunables and measured latency percentiles."""
    settings = get_settings()
    latency = latency_registry.snapshot()
    return {
        "version": _VERSION,
        "providers": {
            "stt": str(settings.STT_PROVIDER),
            "tts": str(settings.TTS_PROVIDER),
            "llm": str(settings.LLM_PROVIDER),
            "embeddings": str(settings.EMBEDDING_PROVIDER),
            "vector_store": str(settings.VECTOR_STORE_PROVIDER),
            "llm_configured": settings.llm_provider_configured,
            "livekit_configured": settings.livekit_provider_configured,
            "rag_configured": settings.rag_configured,
        },
        "pipeline": {
            "voice_pipeline": str(settings.VOICE_PIPELINE),
            "voice_transport": str(settings.VOICE_TRANSPORT),
            "turn_assembly_enabled": settings.TURN_ASSEMBLY_ENABLED,
            "turn_hold_ms": settings.TURN_HOLD_MS,
            "turn_interrupt_enabled": settings.TURN_INTERRUPT_ENABLED,
            "vad_enabled": settings.VAD_ENABLED,
            "vad_barge_in_enabled": settings.VAD_BARGE_IN_ENABLED,
            "vad_start_energy_db": settings.VAD_START_ENERGY_DB,
            "vad_end_silence_ms": settings.VAD_END_SILENCE_MS,
            "general_chat_enabled": settings.GENERAL_CHAT_ENABLED,
            "call_summary_enabled": settings.CALL_SUMMARY_ENABLED,
        },
        "call_record": {
            "sink_setting": str(settings.CALL_RECORD_SINK),
            "jira": JiraClient.from_settings(settings).describe(),
            "practice_api_configured": bool(settings.PRACTICE_API_BASE_URL),
        },
        "tools": _tool_catalog()["tools"],
        "latency": latency,
        "recent_sessions": latency_registry.recent(10),
    }


__all__ = ["router"]
