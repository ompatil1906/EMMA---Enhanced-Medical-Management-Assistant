"""Prompt package (Day 5).

Versioned, YAML-stored prompts loaded through a central registry. Prompts are
defence-in-depth only — application-level validation and safety controls remain
mandatory regardless of prompt wording.
"""

from __future__ import annotations

import os


def list_prompts() -> list[str]:
    """Return prompt IDs available under `app/prompts/`."""
    base = os.path.dirname(__file__)
    ids: list[str] = []
    for entry in os.listdir(base):
        full = os.path.join(base, entry)
        if os.path.isdir(full) and any(f.endswith(".yaml") for f in os.listdir(full)):
            ids.append(entry)
    return sorted(ids)


def load_prompt(prompt_id: str, version: str = "v1") -> dict[str, object]:
    """Load a versioned prompt as a dict.

    Reads `<prompt_id>/<version>.yaml` relative to `app/prompts/`. Raises
    `FileNotFoundError` when the prompt or version does not exist.
    """
    import yaml

    path = os.path.join(os.path.dirname(__file__), prompt_id, f"{version}.yaml")
    if not os.path.exists(path):
        raise FileNotFoundError(f"prompt not found: {prompt_id} v{version} ({path})")
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"prompt {prompt_id} v{version} is not a mapping")
    return data


__all__ = ["load_prompt", "list_prompts"]
