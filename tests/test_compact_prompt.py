"""Compact prompt mode (issue #38 follow-up, local-model prefill case).

The configurable LLM timeout was half of the local-LLM plan; this is the
other half. Local models pay for every prompt token during prefill, so a
``compact_prompt`` setting makes ``_build_prompt`` emit a significantly
shorter prompt that keeps the correctness-critical content (bean names,
machine constraints, anti-repeat recipe names) and drops the verbose
guidance prose. Default stays the full prompt.
"""

from __future__ import annotations

import json
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.melitta_barista.ai_recipes import _build_prompt
from custom_components.melitta_barista.panel_api import _resolve_compact_prompt
from custom_components.melitta_barista.sommelier_api import VALID_SETTING_KEYS


# ── Fixtures ──────────────────────────────────────────────────────────

def _bean1():
    return {
        "brand": "Lavazza",
        "product": "Crema e Aroma",
        "roast": "medium",
        "bean_type": "blend",
        "origin": "blend",
        "origin_country": "Brazil",
        "flavor_notes": ["chocolate", "nutty", "caramel", "honey", "spicy"],
        "composition": "80% arabica, 20% robusta",
    }


def _bean2():
    return {
        "brand": "illy",
        "product": "Decaffeinato",
        "roast": "dark",
        "bean_type": "arabica",
        "origin": "single_origin",
        "flavor_notes": ["floral", "citrus"],
    }


def _existing():
    return [
        {"name": "Morning Latte", "milk": True, "strength": "medium",
         "blend": 1, "extras": ["vanilla syrup"], "recency": "3 days ago"},
        {"name": "Night Cap", "milk": False, "strength": "mild",
         "blend": 0, "extras": [], "recency": "yesterday"},
    ]


def _rich_kwargs():
    """Kwargs exercising every optional prompt section."""
    return dict(
        hopper1_bean=_bean1(),
        hopper2_bean=_bean2(),
        milk_types=["oat", "whole"],
        mode="custom",
        preference="something chocolatey",
        count=3,
        extras={"syrups": ["vanilla", "caramel"], "toppings": ["cinnamon"]},
        ice_available=True,
        cup_size="mug",
        temperature_pref="hot",
        moods=["energizing", "dessert"],
        occasion="guests",
        servings=2,
        dietary=["no_sugar"],
        caffeine_pref="low",
        weather={"temperature": 5, "condition": "rainy"},
        people_home=2,
        cups_today=2,
        language="ru",
        omit_output_format=True,
        existing_recipes=_existing(),
    )


# ── Settings key + resolver ───────────────────────────────────────────

def test_setting_key_registered():
    assert "compact_prompt" in VALID_SETTING_KEYS


def test_resolver_default_false():
    assert _resolve_compact_prompt({}) is False
    assert _resolve_compact_prompt({"compact_prompt": None}) is False
    assert _resolve_compact_prompt(None) is False


def test_resolver_truthy_strings():
    # The WS settings API stores string values ("true"/"false").
    assert _resolve_compact_prompt({"compact_prompt": "true"}) is True
    assert _resolve_compact_prompt({"compact_prompt": "True"}) is True
    assert _resolve_compact_prompt({"compact_prompt": "1"}) is True


def test_resolver_falsy_and_garbage():
    assert _resolve_compact_prompt({"compact_prompt": "false"}) is False
    assert _resolve_compact_prompt({"compact_prompt": "0"}) is False
    assert _resolve_compact_prompt({"compact_prompt": "banana"}) is False
    assert _resolve_compact_prompt({"compact_prompt": 42}) is False


def test_resolver_accepts_native_bool():
    assert _resolve_compact_prompt({"compact_prompt": True}) is True
    assert _resolve_compact_prompt({"compact_prompt": False}) is False


# ── _build_prompt(compact=...) ────────────────────────────────────────

def test_compact_is_substantially_shorter():
    full = _build_prompt(**_rich_kwargs())
    compact = _build_prompt(compact=True, **_rich_kwargs())
    assert len(compact) < 0.6 * len(full), (
        f"compact prompt should be well under 60% of full: "
        f"compact={len(compact)} full={len(full)}"
    )


def test_default_is_full_prompt():
    """Omitting `compact` yields the same prompt as before (verbose markers present)."""
    prompt = _build_prompt(**_rich_kwargs())
    # Verbose steps-guidance prose only exists in the full prompt.
    assert "manual preparation BEFORE the machine starts" in prompt
    assert prompt == _build_prompt(compact=False, **_rich_kwargs())


def test_compact_keeps_bean_names_roast_and_capped_notes():
    compact = _build_prompt(compact=True, **_rich_kwargs())
    assert "Lavazza Crema e Aroma" in compact
    assert "illy Decaffeinato" in compact
    assert "medium" in compact and "dark" in compact
    # First 3 flavor notes survive, the rest are dropped.
    assert "chocolate" in compact and "nutty" in compact and "caramel" in compact
    assert "honey" not in compact
    # Verbose bean attributes are dropped.
    assert "Composition" not in compact
    assert "Origin:" not in compact


def test_compact_keeps_blend_mapping_and_hoppers():
    compact = _build_prompt(compact=True, **_rich_kwargs())
    assert "blend=1" in compact
    assert "blend=0" in compact
    assert "1 for hopper 1" in compact or "1 = hopper 1" in compact


def test_compact_keeps_milk_and_extras_inventory():
    compact = _build_prompt(compact=True, **_rich_kwargs())
    assert "oat" in compact and "whole" in compact
    assert "vanilla" in compact and "caramel" in compact and "cinnamon" in compact
    assert "Ice: available" in compact


def test_compact_keeps_machine_constraints():
    compact = _build_prompt(compact=True, **_rich_kwargs())
    assert "Machine Capabilities" in compact
    assert '"very_mild"' in compact and '"very_strong"' in compact
    assert "portion_ml" in compact
    # Cup volume constraint survives.
    assert "250" in compact and "350" in compact


def test_compact_keeps_anti_repeat_names_but_drops_recency():
    compact = _build_prompt(compact=True, **_rich_kwargs())
    assert "Morning Latte" in compact
    assert "Night Cap" in compact
    assert "NOT repeat" in compact
    assert "days ago" not in compact
    assert "yesterday" not in compact


def test_compact_caps_existing_recipes_at_12():
    kwargs = _rich_kwargs()
    kwargs["existing_recipes"] = [
        {"name": f"Recipe {i}", "milk": False, "strength": None,
         "blend": None, "extras": [], "recency": None}
        for i in range(20)
    ]
    compact = _build_prompt(compact=True, **kwargs)
    assert "Recipe 11" in compact
    assert "Recipe 12" not in compact


def test_compact_keeps_count_mode_and_preference():
    compact = _build_prompt(compact=True, **_rich_kwargs())
    assert "exactly 3" in compact
    assert "something chocolatey" in compact


def test_compact_drops_verbose_guidance():
    compact = _build_prompt(compact=True, **_rich_kwargs())
    # Steps phase-tagging prose is condensed.
    assert "manual preparation BEFORE the machine starts" not in compact
    assert "selecting the cup, adding ice" not in compact
    # Weather advice prose is dropped, the observation stays.
    assert "rainy" in compact
    assert "warming, comforting" not in compact
    # Mood spread guidance dropped, moods themselves stay.
    assert "energizing" in compact and "dessert" in compact
    assert "spread variety across the moods" not in compact


def test_compact_keeps_dietary_constraints():
    compact = _build_prompt(compact=True, **_rich_kwargs())
    assert "no_sugar" in compact
    assert "no sugar syrups" in compact


def test_compact_keeps_language_and_schema_contract():
    compact = _build_prompt(compact=True, **_rich_kwargs())
    assert "ru" in compact
    # Enum-stays-English contract must survive so replies validate.
    assert "English" in compact
    # `steps`/`phase`/`reasoning` fields still referenced so the JSON
    # schema response stays compatible.
    assert "steps" in compact
    assert "phase" in compact
    assert "reasoning" in compact


def test_compact_with_caps_keeps_capability_enums():
    from custom_components.melitta_barista.capabilities import LiveCapabilities
    caps = LiveCapabilities(
        schema_version=1,
        family_key="test_family",
        model_name="Test Machine",
        supported_processes=("coffee", "milk"),
        supported_intensities=("mild", "medium", "strong"),
        supported_aromas=("standard",),
        supported_temperatures=("normal",),
        supported_shots=("one", "two"),
        portion_limits={"coffee": {"min": 5, "max": 200, "step": 5}},
        forbidden_combinations=(),
    )
    kwargs = _rich_kwargs()
    compact = _build_prompt(compact=True, caps=caps, **kwargs)
    full = _build_prompt(caps=caps, **kwargs)
    caps_section = compact.split("## Machine Capabilities")[1].split("##")[0]
    assert '"coffee"' in caps_section and '"milk"' in caps_section
    assert '"water"' not in caps_section
    assert '"mild"' in caps_section and '"strong"' in caps_section
    assert "Test Machine" in compact
    assert len(compact) < 0.6 * len(full)


def test_compact_with_output_format_block_keeps_it():
    """When the legacy text Output Format path is used, compact keeps the block."""
    kwargs = _rich_kwargs()
    kwargs["omit_output_format"] = False
    compact = _build_prompt(compact=True, **kwargs)
    assert "## Output Format" in compact


# ── WS-level threading ────────────────────────────────────────────────

def _make_generate_env(settings: dict):
    """Shared MagicMock scaffolding for ws_generate threading tests."""
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
    db.async_get_hoppers = AsyncMock(return_value={"hopper1": {}, "hopper2": {}})
    db.async_get_milk = AsyncMock(return_value=[])
    db.async_get_pantry_extras = AsyncMock(
        return_value={"syrups": [], "toppings": [], "liqueurs": [], "misc": []}
    )
    db.async_get_active_profile = AsyncMock(return_value=None)
    db.async_get_settings = AsyncMock(return_value=settings)
    db.async_get_preferences = AsyncMock(return_value={})
    db.async_create_session = AsyncMock(return_value=MagicMock(id="sess1"))
    db.async_get_panel_prompt = AsyncMock(return_value=None)
    db.async_get_capabilities = AsyncMock(return_value={
        "entry_id": "entry_target",
        "json_payload": cached_caps_json,
        "probed_at": "2026-05-25T00:00:00+00:00",
        "schema_version": 1,
    })
    db.async_list_favorites = AsyncMock(return_value=[])
    db.async_list_history = AsyncMock(return_value=[])

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
    return hass, connection, db


async def _run_generate(settings: dict) -> dict:
    """Run unwrapped ws_generate with mocked deps; return _build_prompt kwargs."""
    from custom_components.melitta_barista import sommelier_api as sa

    captured: dict = {}

    def _spy_build_prompt(**kwargs):
        captured.update(kwargs)
        return "STUB_PROMPT"

    async def _fake_structured_call(hass, **kwargs):
        return {"parsed": {"recipes": []}, "validation_errors": []}

    hass, connection, _db = _make_generate_env(settings)
    ws_generate = inspect.unwrap(sa.ws_generate)
    msg = {
        "id": 1,
        "type": "melitta_barista/sommelier/generate",
        "mode": "surprise_me",
        "agent_id": "smartchain.test",
        "count": 3,
    }
    with patch("custom_components.melitta_barista.panel_api._structured_call",
               new=_fake_structured_call), \
         patch("custom_components.melitta_barista.ai_recipes._build_prompt",
               side_effect=_spy_build_prompt):
        await ws_generate(hass, connection, msg)
    return captured


@pytest.mark.asyncio
async def test_ws_generate_threads_compact_setting_to_build_prompt():
    captured = await _run_generate({"compact_prompt": "true"})
    assert captured.get("compact") is True


@pytest.mark.asyncio
async def test_ws_generate_defaults_to_full_prompt():
    captured = await _run_generate({})
    assert captured.get("compact") is False


@pytest.mark.asyncio
async def test_prompts_preview_honors_compact_setting():
    """The sommelier_intro preview must show what /generate will actually send."""
    from custom_components.melitta_barista import panel_api

    captured: dict = {}

    def _spy_build_prompt(**kwargs):
        captured.update(kwargs)
        return "STUB_PROMPT"

    db = MagicMock()
    db.async_get_hoppers = AsyncMock(return_value={"hopper1": None, "hopper2": None})
    db.async_get_milk = AsyncMock(return_value=[])
    db.async_get_pantry_extras = AsyncMock(return_value={})
    db.async_get_settings = AsyncMock(return_value={"compact_prompt": "true"})
    db.async_list_favorites = AsyncMock(return_value=[])
    db.async_list_history = AsyncMock(return_value=[])

    hass = MagicMock()
    hass.data = {"melitta_barista": {"sommelier_db": db}}
    connection = MagicMock()

    preview = inspect.unwrap(panel_api._ws_prompts_preview)
    msg = {"id": 1, "type": "melitta_barista/prompts/preview", "slot": "sommelier_intro"}
    with patch.object(panel_api, "_async_get_db", new=AsyncMock(return_value=db)), \
         patch.object(panel_api, "_resolve_prompt", new=AsyncMock(return_value=None)), \
         patch("custom_components.melitta_barista.ai_recipes._build_prompt",
               side_effect=_spy_build_prompt):
        await preview(hass, connection, msg)

    assert captured.get("compact") is True
