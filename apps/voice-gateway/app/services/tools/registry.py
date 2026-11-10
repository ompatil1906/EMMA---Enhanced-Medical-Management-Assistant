"""Tool registry: registration, lookup and LLM-facing catalog (Day 20)."""

from __future__ import annotations

from typing import Any

from app.services.tools.base import ToolHandler, ToolSpec


class ToolRegistryError(Exception):
    """Registration error (duplicate name, missing handler)."""


class ToolRegistry:
    """Name → (spec, handler). Registration is explicit; nothing is implicit."""

    def __init__(self) -> None:
        self._tools: dict[str, tuple[ToolSpec, ToolHandler]] = {}

    def register(self, spec: ToolSpec, handler: ToolHandler) -> None:
        if spec.name in self._tools:
            raise ToolRegistryError(f"tool already registered: {spec.name!r}")
        if not callable(handler):
            raise ToolRegistryError(f"tool {spec.name!r} has no callable handler")
        self._tools[spec.name] = (spec, handler)

    def get(self, name: str) -> tuple[ToolSpec, ToolHandler] | None:
        return self._tools.get(name)

    def spec(self, name: str) -> ToolSpec | None:
        entry = self._tools.get(name)
        return entry[0] if entry else None

    def names(self) -> list[str]:
        return sorted(self._tools)

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._tools

    def catalog(self) -> list[dict[str, Any]]:
        """Operator-facing catalog (`GET /api/v1/tools`) — no handlers, no secrets."""
        return [self._tools[name][0].catalog_entry() for name in self.names()]

    def function_declarations(self) -> list[dict[str, Any]]:
        """LLM-facing function declarations (for provider-level tool calling)."""
        return [self._tools[name][0].json_schema() for name in self.names()]


__all__ = ["ToolRegistry", "ToolRegistryError"]
