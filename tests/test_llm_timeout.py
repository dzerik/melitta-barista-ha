"""Configurable LLM timeout (issue #38, local-model prefill case).

A local LLM can spend minutes prefilling the Sommelier's large prompt
while answering short prompts (bean autofill) in seconds. The fixed 60 s
ceiling made generation impossible on such setups; the timeout is now a
setting (``llm_timeout_s``), clamped to sane bounds.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.melitta_barista.ai_recipes import LLM_TIMEOUT
from custom_components.melitta_barista.panel_api import _resolve_llm_timeout
from custom_components.melitta_barista.sommelier_api import VALID_SETTING_KEYS


def test_setting_key_registered():
    assert "llm_timeout_s" in VALID_SETTING_KEYS


def test_default_when_unset():
    assert _resolve_llm_timeout({}) == LLM_TIMEOUT
    assert _resolve_llm_timeout({"llm_timeout_s": None}) == LLM_TIMEOUT


def test_valid_value_parsed():
    assert _resolve_llm_timeout({"llm_timeout_s": "180"}) == 180.0


def test_clamped_to_bounds():
    assert _resolve_llm_timeout({"llm_timeout_s": "3"}) == 10.0
    assert _resolve_llm_timeout({"llm_timeout_s": "9999"}) == 600.0


def test_garbage_falls_back_to_default():
    assert _resolve_llm_timeout({"llm_timeout_s": "soon"}) == LLM_TIMEOUT


@pytest.mark.asyncio
async def test_structured_call_passes_setting_to_llm_call():
    """_structured_call resolves the timeout once and hands it down."""
    from custom_components.melitta_barista import panel_api

    db = MagicMock()
    db.async_get_settings = AsyncMock(return_value={"llm_timeout_s": "240"})

    captured: dict = {}

    async def fake_llm_call_text(hass, prompt, agent_id, ctx, *, timeout):
        captured["timeout"] = timeout
        return '{"recipes": []}'

    hass = MagicMock()
    with patch.object(panel_api, "_async_get_db", new=AsyncMock(return_value=db)), \
            patch.object(panel_api, "_llm_call_text", new=fake_llm_call_text), \
            patch.object(panel_api, "_resolve_prompt", new=AsyncMock(return_value="p")):
        await panel_api._structured_call(
            hass, "sommelier", {}, "conversation.x", None,
            prebuilt_prompt="hello",
        )
    assert captured.get("timeout") == 240.0
