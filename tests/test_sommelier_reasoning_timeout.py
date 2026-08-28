"""Reasoning field on GeneratedRecipe + LLM timeout on the live converse path.

Covers two audit findings:
- "'Why this recipe?' expander is dead UI" — GeneratedRecipe gains a
  ``reasoning`` field so the LLM's justification survives schema validation.
- "Live LLM path has no timeout" — panel_api._llm_call_text must bound
  conversation.async_converse with LLM_TIMEOUT instead of hanging forever.
"""

from __future__ import annotations

import asyncio
import sys
import types
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from custom_components.melitta_barista.panel_api import (
    GeneratedRecipe,
    _llm_call_text,
)


def _recipe_kwargs() -> dict:
    return {
        "name": "Latte",
        "machine_phases": [
            {
                "component": {
                    "process": "coffee",
                    "intensity": "medium",
                    "aroma": "standard",
                    "temperature": "normal",
                    "shots": "two",
                    "portion_ml": 40,
                },
                "user_action_before": [],
            }
        ],
        "steps": [{"order": 1, "action": "Brew"}],
    }


# ── GeneratedRecipe.reasoning ─────────────────────────────────────────


def test_generated_recipe_accepts_reasoning():
    recipe = GeneratedRecipe(reasoning="Fits your rainy morning.", **_recipe_kwargs())
    assert recipe.reasoning == "Fits your rainy morning."


def test_generated_recipe_reasoning_defaults_empty():
    recipe = GeneratedRecipe(**_recipe_kwargs())
    assert recipe.reasoning == ""


def test_generated_recipe_schema_advertises_reasoning():
    """The auto-appended JSON Schema must show the field to the LLM."""
    schema = GeneratedRecipe.model_json_schema()
    assert "reasoning" in schema["properties"]


def test_generated_recipe_model_dump_keeps_reasoning():
    """model_dump must not strip the field (it feeds _validate_recipes)."""
    recipe = GeneratedRecipe(reasoning="why", **_recipe_kwargs())
    assert recipe.model_dump()["reasoning"] == "why"


# ── _llm_call_text timeout ────────────────────────────────────────────


def _make_hass() -> MagicMock:
    hass = MagicMock()
    hass.config.language = "en"
    return hass


@contextmanager
def _stub_conversation(async_converse):
    """Inject a stub `homeassistant.components.conversation` module.

    The real component pulls in `hassil`, which is not installed in the
    test env; production code imports it lazily inside _llm_call_text.
    """
    import homeassistant.components as components

    stub = types.ModuleType("homeassistant.components.conversation")
    stub.async_converse = async_converse
    with patch.dict(
        sys.modules, {"homeassistant.components.conversation": stub}
    ), patch.object(components, "conversation", stub, create=True):
        yield


@pytest.mark.asyncio
async def test_llm_call_text_times_out():
    """A hung conversation agent raises RuntimeError instead of hanging."""

    async def _hang(*args, **kwargs):
        await asyncio.sleep(30)

    with _stub_conversation(_hang), patch(
        "custom_components.melitta_barista.panel_api.LLM_TIMEOUT", 0.05
    ):
        with pytest.raises(RuntimeError, match="timed out"):
            await _llm_call_text(_make_hass(), "prompt", None, None)


@pytest.mark.asyncio
async def test_llm_call_text_returns_speech_within_timeout():
    """Fast path unchanged: speech text is returned as before."""

    result = MagicMock()
    result.response.speech = {"plain": {"speech": "hello"}}

    async def _fast(*args, **kwargs):
        return result

    with _stub_conversation(_fast):
        text = await _llm_call_text(_make_hass(), "prompt", None, None)
    assert text == "hello"
