"""Explicit LLM-agent pre-flight for the Sommelier (issue #38).

Without a configured conversation agent, generation used to fall through
to the built-in Assist agent, fail to parse its reply, and surface an
opaque error. The pre-flight now tells the user exactly what is missing.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.melitta_barista import sommelier_api as sa
from custom_components.melitta_barista.panel_api import _check_llm_agent

ASSIST = "conversation.home_assistant"


def _state(entity_id: str, name: str | None = None):
    st = MagicMock()
    st.entity_id = entity_id
    st.attributes = {"friendly_name": name or entity_id}
    return st


def _hass(conversation_entities):
    hass = MagicMock()
    hass.states.async_all = MagicMock(
        side_effect=lambda domain=None: list(conversation_entities)
    )
    hass.states.get = MagicMock(
        side_effect=lambda eid: next(
            (s for s in conversation_entities if s.entity_id == eid), None
        )
    )
    return hass


# ── _check_llm_agent unit behavior ─────────────────────────────────────


def test_no_agent_and_no_llm_integrations():
    hass = _hass([_state(ASSIST, "Home Assistant")])
    problem = _check_llm_agent(hass, None)
    assert problem is not None
    code, message = problem
    assert code == "no_llm_agent"
    assert "conversation" in message.lower() or "llm" in message.lower()


def test_no_agent_but_llm_agents_exist():
    hass = _hass([_state(ASSIST), _state("conversation.chatgpt", "ChatGPT")])
    problem = _check_llm_agent(hass, None)
    assert problem is not None
    code, message = problem
    assert code == "no_llm_agent_selected"
    assert "ChatGPT" in message  # actionable: names what to pick


def test_builtin_default_treated_as_unset():
    hass = _hass([_state(ASSIST)])
    problem = _check_llm_agent(hass, "homeassistant")
    assert problem is not None and problem[0] == "no_llm_agent"


def test_existing_conversation_agent_passes():
    hass = _hass([_state(ASSIST), _state("conversation.chatgpt")])
    assert _check_llm_agent(hass, "conversation.chatgpt") is None


def test_missing_conversation_agent_reports_gone():
    hass = _hass([_state(ASSIST)])
    problem = _check_llm_agent(hass, "conversation.removed_llm")
    assert problem is not None
    code, message = problem
    assert code == "llm_agent_missing"
    assert "conversation.removed_llm" in message


def test_non_conversation_agent_id_is_trusted():
    """smartchain.* etc. can't be verified via states — let them through."""
    hass = _hass([_state(ASSIST)])
    assert _check_llm_agent(hass, "smartchain.gpt4o") is None


# ── ws_generate wiring ─────────────────────────────────────────────────


def _unwrap(func):
    while hasattr(func, "__wrapped__"):
        func = func.__wrapped__
        if inspect.iscoroutinefunction(func):
            break
    return func


@pytest.mark.asyncio
async def test_ws_generate_fails_fast_without_agent():
    db = MagicMock()
    db.async_get_settings = AsyncMock(return_value={})

    hass = MagicMock()
    hass.data = {"melitta_barista": {"sommelier_db": db}}
    hass.states.async_all = MagicMock(return_value=[_state(ASSIST)])
    hass.states.get = MagicMock(return_value=None)

    connection = MagicMock()

    structured = AsyncMock()
    msg = {
        "id": 7,
        "type": "melitta_barista/sommelier/generate",
        "mode": "surprise_me",
        "count": 3,
    }
    with patch(
        "custom_components.melitta_barista.sommelier_api._async_get_db",
        new=AsyncMock(return_value=db),
    ), patch(
        "custom_components.melitta_barista.panel_api._async_get_db",
        new=AsyncMock(return_value=db),
    ), patch(
        "custom_components.melitta_barista.panel_api._structured_call",
        new=structured,
    ):
        await _unwrap(sa.ws_generate)(hass, connection, msg)

    connection.send_error.assert_called_once()
    assert connection.send_error.call_args.args[1] == "no_llm_agent"
    structured.assert_not_awaited()
