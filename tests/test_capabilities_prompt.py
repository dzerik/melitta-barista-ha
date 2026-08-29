"""R4 — _build_prompt consumes LiveCapabilities and emits capability-driven enums."""

from __future__ import annotations

import pytest

from custom_components.melitta_barista.ai_recipes import _build_prompt
from custom_components.melitta_barista.capabilities import LiveCapabilities


def _make_caps(**overrides) -> LiveCapabilities:
    defaults = dict(
        schema_version=1,
        family_key="test_family",
        model_name="Test Machine",
        supported_processes=("coffee", "milk"),  # no water/none
        supported_intensities=("mild", "medium", "strong"),  # 3-level
        supported_aromas=("standard",),  # no intense
        supported_temperatures=("normal",),  # no cold/high
        supported_shots=("one", "two"),  # no none/three
        portion_limits={
            "coffee": {"min": 5, "max": 200, "step": 5},
            "milk": {"min": 10, "max": 200, "step": 5},
        },
        forbidden_combinations=(),
    )
    defaults.update(overrides)
    return LiveCapabilities(**defaults)


def _common_kwargs():
    return dict(
        mode="surprise_me",
        preference="",
        count=3,
        hopper1_bean=None,
        hopper2_bean=None,
        milk_types=[],
        extras={"syrups": [], "toppings": [], "liqueurs": []},
        ice_available=False,
        cup_size=None,
        temperature_pref=None,
        weather=None,
        moods=[],
        mood=None,
        occasion=None,
        dietary=[],
        caffeine_pref=None,
        servings=1,
        cups_today=0,
        intro=None,
        language=None,
        omit_output_format=True,
    )


def test_build_prompt_without_caps_keeps_legacy_universal_block():
    """When caps=None, the Machine Capabilities section enumerates the universal set."""
    prompt = _build_prompt(caps=None, **_common_kwargs())
    assert "Machine Capabilities" in prompt
    assert '"coffee"' in prompt
    assert '"milk"' in prompt
    assert '"water"' in prompt


def test_build_prompt_with_caps_emits_only_supported_processes():
    """When caps is provided, the section lists ONLY supported_processes."""
    caps = _make_caps()
    prompt = _build_prompt(caps=caps, **_common_kwargs())
    caps_section = prompt.split("## Machine Capabilities")[1].split("##")[0]
    assert '"coffee"' in caps_section
    assert '"milk"' in caps_section
    # water/none NOT supported → must not appear in capabilities section
    assert '"water"' not in caps_section
    assert '"none"' not in caps_section


def test_build_prompt_with_caps_lists_intensities_temperatures_aromas_shots():
    """All five enum dimensions follow capabilities."""
    caps = _make_caps()
    prompt = _build_prompt(caps=caps, **_common_kwargs())
    caps_section = prompt.split("## Machine Capabilities")[1].split("##")[0]
    assert '"mild"' in caps_section and '"medium"' in caps_section and '"strong"' in caps_section
    assert '"very_mild"' not in caps_section and '"very_strong"' not in caps_section
    assert '"standard"' in caps_section
    assert '"intense"' not in caps_section
    assert '"normal"' in caps_section
    assert '"cold"' not in caps_section and '"high"' not in caps_section
    assert '"one"' in caps_section and '"two"' in caps_section


def test_build_prompt_with_caps_emits_instruction_to_ignore_schema_extras():
    """A clear instruction tells LLM to prefer caps over JSON schema when they disagree."""
    caps = _make_caps()
    prompt = _build_prompt(caps=caps, **_common_kwargs())
    caps_section = prompt.split("## Machine Capabilities")[1].split("##")[0]
    section_lower = caps_section.lower()
    assert ("only the values listed" in section_lower
            or "ignore" in section_lower
            or "do not use" in section_lower), \
        f"Expected an override-instruction in caps section, got: {caps_section!r}"


def test_build_prompt_with_caps_uses_per_process_portion_range():
    """portion_ml range comes from caps.portion_limits when set."""
    caps = _make_caps(portion_limits={"coffee": {"min": 10, "max": 180, "step": 10}})
    prompt = _build_prompt(caps=caps, **_common_kwargs())
    caps_section = prompt.split("## Machine Capabilities")[1].split("##")[0]
    assert "10" in caps_section
    assert "180" in caps_section


from unittest.mock import AsyncMock, MagicMock, patch

import json
from custom_components.melitta_barista import sommelier_api as sa


@pytest.mark.asyncio
async def test_ws_generate_passes_caps_from_db_cache_to_build_prompt():
    """ws_generate fetches caps from sommelier_db and threads them into _build_prompt."""
    captured = {}

    def _spy_build_prompt(**kwargs):
        captured.update(kwargs)
        return "STUB_PROMPT"

    async def _fake_structured_call(hass, **kwargs):
        # ws_generate expects a dict-like with .get("parsed") -> {"recipes": [...]}.
        # We return no recipes to short-circuit before session creation; the
        # spy on _build_prompt will already have fired by then.
        return {"parsed": {"recipes": []}, "validation_errors": []}

    cached_caps_json = json.dumps({
        "schema_version": 1,
        "family_key": "test_family",
        "model_name": "Test Machine",
        "supported_processes": ["coffee"],
        "supported_intensities": ["medium"],
        "supported_aromas": ["standard"],
        "supported_temperatures": ["normal"],
        "supported_shots": ["one"],
        "portion_limits": {"coffee": {"min": 5, "max": 200, "step": 5}},
        "forbidden_combinations": [],
    })

    db = MagicMock()
    # ws_generate does `hoppers.get("hopper1", {}).get("bean")`, so empty dicts
    # (not None) are required for the .get chain to short-circuit cleanly.
    db.async_get_hoppers = AsyncMock(return_value={"hopper1": {}, "hopper2": {}})
    db.async_get_milk = AsyncMock(return_value=[])
    db.async_get_pantry_extras = AsyncMock(return_value={"syrups": [], "toppings": [], "liqueurs": [], "misc": []})
    db.async_get_active_profile = AsyncMock(return_value=None)
    db.async_get_settings = AsyncMock(return_value={"llm_agent_id": None})
    db.async_get_preferences = AsyncMock(return_value={})
    db.async_create_session = AsyncMock(return_value=MagicMock(id="sess1"))
    db.async_get_panel_prompt = AsyncMock(return_value=None)
    db.async_get_capabilities = AsyncMock(return_value={
        "entry_id": "entry_target",
        "json_payload": cached_caps_json,
        "probed_at": "2026-05-25T00:00:00+00:00",
        "schema_version": 1,
    })

    fake_entry = MagicMock()
    fake_entry.entry_id = "entry_target"

    hass = MagicMock()
    hass.data = {"melitta_barista": {"sommelier_db": db}}
    hass.config = MagicMock()
    hass.config.language = "en"
    hass.config_entries = MagicMock()
    hass.config_entries.async_entries = MagicMock(return_value=[fake_entry])
    hass.config_entries.async_get_entry = MagicMock(return_value=fake_entry)
    hass.states = MagicMock()
    hass.states.async_all = MagicMock(return_value=[])

    connection = MagicMock()
    connection.context = MagicMock(return_value=None)
    connection.send_result = MagicMock()
    connection.send_error = MagicMock()

    # ws_generate is wrapped by both @websocket_command and @require_admin
    # and @async_response. Use inspect.unwrap to peel back to the actual
    # async coroutine function so we can call it directly.
    import inspect
    ws_generate = inspect.unwrap(sa.ws_generate)

    msg = {
        "id": 1,
        "type": "melitta_barista/sommelier/generate",
        "mode": "surprise_me",
        "agent_id": "smartchain.test",  # pre-flight (issue #38): trusted id
        "count": 3,
    }

    # Note: ws_generate does local `from .panel_api import _structured_call`
    # and `from .ai_recipes import _build_prompt` inside the function body,
    # so we must patch them at their source modules (not at sommelier_api).
    with patch("custom_components.melitta_barista.panel_api._structured_call", new=_fake_structured_call), \
         patch("custom_components.melitta_barista.ai_recipes._build_prompt", side_effect=_spy_build_prompt):
        await ws_generate(hass, connection, msg)

    caps_passed = captured.get("caps")
    assert caps_passed is not None, f"caps was not passed to _build_prompt; got kwargs={list(captured.keys())}"
    assert caps_passed.family_key == "test_family"
    assert "coffee" in caps_passed.supported_processes
