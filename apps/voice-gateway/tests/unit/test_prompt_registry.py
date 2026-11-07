"""Unit tests for the prompt registry (Day 5)."""

from __future__ import annotations

import os

import pytest

from app.prompts import list_prompts, load_prompt


class TestPromptRegistry:
    def test_list_prompts(self) -> None:
        ids = list_prompts()
        assert "intent_extraction" in ids

    def test_load_prompt_v1(self) -> None:
        prompt = load_prompt("intent_extraction", "v1")
        assert prompt["prompt_id"] == "intent_extraction"
        assert prompt["version"] == "v1"
        assert "system_instruction" in prompt
        assert "safety_constraints" in prompt
        assert "input_schema" in prompt
        assert "output_schema" in prompt

    def test_load_prompt_missing(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_prompt("nonexistent", "v1")

    def test_load_prompt_wrong_version(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_prompt("intent_extraction", "v99")

    def test_safety_constraints_present(self) -> None:
        prompt = load_prompt("intent_extraction", "v1")
        constraints = prompt["safety_constraints"]
        assert isinstance(constraints, list)
        assert len(constraints) >= 5
        joined = " ".join(constraints)
        assert "invent" in joined.lower()
        assert "diagnos" in joined.lower()
        assert "book" in joined.lower()

    def test_prompt_yaml_exists_on_disk(self) -> None:
        """Verify the prompt YAML file is present on disk (not just importable)."""
        here = os.path.dirname(__file__)
        voice_gateway = os.path.dirname(os.path.dirname(here))
        prompt_path = os.path.join(
            voice_gateway,
            "app",
            "prompts",
            "intent_extraction",
            "v1.yaml",
        )
        assert os.path.exists(prompt_path)
