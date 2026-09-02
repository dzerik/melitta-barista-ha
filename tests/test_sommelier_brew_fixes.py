"""Brew-path honesty fixes for the Sommelier WS API.

Covers four audit findings:
1. brew_freestyle's bool result must propagate to the WS reply (no fake
   success, no brewed-counter increment on failure).
2. LLM blend semantics (1=hopper 1, 0=hopper 2) must be translated to the
   BLE Blend enum (BLEND_1=1, BLEND_2=2) at the boundary, same value for
   both phases; favorites/add must record the matching source hopper.
3. Brew-time re-validation: portion_ml clamped to 0..250 on the 5 ml grid,
   unknown enum strings raise ValueError instead of silently defaulting.
4. capabilities/get must expose supports_recipe_writes.
"""

from __future__ import annotations

import inspect
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.melitta_barista import sommelier_api
from custom_components.melitta_barista.sommelier_api import _brew_recipe_components

ws_brew = inspect.unwrap(sommelier_api.ws_brew)
ws_favorites_brew = inspect.unwrap(sommelier_api.ws_favorites_brew)
ws_favorites_add = inspect.unwrap(sommelier_api.ws_favorites_add)
ws_capabilities_get = inspect.unwrap(sommelier_api.ws_capabilities_get)


def _client(brew_result: bool = True) -> MagicMock:
    """Machine client mock with no capability gate and a stubbed brew."""
    client = MagicMock()
    client.capabilities = None  # skip the supports_recipe_writes gate
    client.brew_freestyle = AsyncMock(return_value=brew_result)
    return client


def _phase(**component) -> dict:
    """Build one machine_phases entry around a component dict."""
    return {"component": component, "user_action_before": []}


def _coffee_phase(**overrides) -> dict:
    comp = {
        "process": "coffee",
        "shots": "one",
        "intensity": "medium",
        "aroma": "standard",
        "temperature": "normal",
        "portion_ml": 40,
    }
    comp.update(overrides)
    return _phase(**comp)


def _brew_call_components(client):
    kwargs = client.brew_freestyle.call_args.kwargs
    return kwargs["component1"], kwargs["component2"]


# ── 2. Blend translation ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_llm_blend_1_maps_to_ble_byte_1_both_phases():
    """LLM blend=1 (hopper 1) → BLE byte 1 for phase 1 AND phase 2."""
    client = _client()
    await _brew_recipe_components(
        client, name="X", blend=1,
        phases=[_coffee_phase(), _phase(process="milk", portion_ml=100)],
    )
    c1, c2 = _brew_call_components(client)
    assert c1.blend == 1
    assert c2.blend == 1  # no hard inversion of the second phase


@pytest.mark.asyncio
async def test_llm_blend_0_maps_to_ble_byte_2_both_phases():
    """LLM blend=0 (hopper 2) → BLE byte 2 (Blend.BLEND_2), never raw 0."""
    client = _client()
    await _brew_recipe_components(
        client, name="X", blend=0,
        phases=[_coffee_phase(), _phase(process="milk", portion_ml=100)],
    )
    c1, c2 = _brew_call_components(client)
    assert c1.blend == 2
    assert c2.blend == 2


@pytest.mark.asyncio
async def test_single_phase_synthesized_component2_uses_same_blend():
    client = _client()
    await _brew_recipe_components(client, name="X", blend=0, phases=[_coffee_phase()])
    c1, c2 = _brew_call_components(client)
    assert c1.blend == 2
    assert c2.blend == 2
    assert c2.process == 0  # still the "no second pour" marker


@pytest.mark.asyncio
async def test_unknown_blend_value_raises():
    client = _client()
    with pytest.raises(ValueError, match="blend"):
        await _brew_recipe_components(client, name="X", blend=3, phases=[_coffee_phase()])
    client.brew_freestyle.assert_not_awaited()


# ── 1. Result honesty (_brew_recipe_components returns the bool) ─────


@pytest.mark.asyncio
async def test_brew_recipe_components_returns_true_on_success():
    client = _client(brew_result=True)
    assert await _brew_recipe_components(client, name="X", blend=1, phases=[_coffee_phase()]) is True


@pytest.mark.asyncio
async def test_brew_recipe_components_returns_false_on_machine_refusal():
    client = _client(brew_result=False)
    assert await _brew_recipe_components(client, name="X", blend=1, phases=[_coffee_phase()]) is False


# ── 3. Brew-time re-validation ───────────────────────────────────────


@pytest.mark.asyncio
async def test_portion_ml_clamped_to_250_max():
    client = _client()
    await _brew_recipe_components(
        client, name="X", blend=1, phases=[_coffee_phase(portion_ml=1300)],
    )
    c1, _ = _brew_call_components(client)
    assert c1.portion == 50  # 250 ml / 5


@pytest.mark.asyncio
async def test_portion_ml_clamped_to_0_min():
    client = _client()
    await _brew_recipe_components(
        client, name="X", blend=1, phases=[_coffee_phase(portion_ml=-50)],
    )
    c1, _ = _brew_call_components(client)
    assert c1.portion == 0


@pytest.mark.asyncio
async def test_portion_ml_rounds_to_5ml_grid_not_floor():
    client = _client()
    await _brew_recipe_components(
        client, name="X", blend=1,
        phases=[_coffee_phase(portion_ml=42), _phase(process="milk", portion_ml=43)],
    )
    c1, c2 = _brew_call_components(client)
    assert c1.portion == 8  # 42 → 40 ml
    assert c2.portion == 9  # 43 → 45 ml (round, not floor)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("process", "espresso"),
        ("intensity", "extreme"),
        ("temperature", "iced"),
        ("shots", "four"),
        ("aroma", "floral"),
    ],
)
@pytest.mark.asyncio
async def test_unknown_enum_string_raises_descriptive_valueerror(field, value):
    client = _client()
    with pytest.raises(ValueError) as excinfo:
        await _brew_recipe_components(
            client, name="X", blend=1, phases=[_coffee_phase(**{field: value})],
        )
    assert field in str(excinfo.value)
    assert value in str(excinfo.value)
    client.brew_freestyle.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_enum_keys_still_default():
    """Absent keys keep their historic defaults — only bad VALUES error."""
    client = _client()
    await _brew_recipe_components(client, name="X", blend=1, phases=[_phase(portion_ml=40)])
    c1, _ = _brew_call_components(client)
    assert c1.process == 0      # "none"
    assert c1.shots == 0        # "none"
    assert c1.intensity == 2    # "medium"
    assert c1.temperature == 1  # "normal"


# ── 1. WS handlers honor the bool ────────────────────────────────────


def _recipe_row(blend: int = 1) -> dict:
    return {
        "id": "r1",
        "name": "Test Drink",
        "description": "d",
        "blend": blend,
        "component1": {},
        "component2": {},
        "machine_phases": [_coffee_phase()],
    }


@pytest.mark.asyncio
async def test_ws_brew_sends_error_and_skips_marking_when_brew_refused():
    hass = MagicMock()
    db = MagicMock()
    db.async_get_recipe = AsyncMock(return_value=_recipe_row())
    db.async_mark_recipe_brewed = AsyncMock()
    client = _client(brew_result=False)
    connection = MagicMock()

    with patch.object(sommelier_api, "_async_get_db", AsyncMock(return_value=db)), \
         patch.object(sommelier_api, "_find_client", return_value=client):
        await ws_brew(hass, connection, {"id": 5, "recipe_id": "r1"})

    connection.send_error.assert_called_once()
    args = connection.send_error.call_args.args
    assert args[0] == 5
    assert args[1] == "brew_failed"
    assert args[2]  # human-readable message present
    db.async_mark_recipe_brewed.assert_not_awaited()
    connection.send_result.assert_not_called()


@pytest.mark.asyncio
async def test_ws_brew_success_path_still_marks_and_replies():
    hass = MagicMock()
    db = MagicMock()
    db.async_get_recipe = AsyncMock(return_value=_recipe_row())
    db.async_mark_recipe_brewed = AsyncMock()
    client = _client(brew_result=True)
    connection = MagicMock()

    with patch.object(sommelier_api, "_async_get_db", AsyncMock(return_value=db)), \
         patch.object(sommelier_api, "_find_client", return_value=client):
        await ws_brew(hass, connection, {"id": 6, "recipe_id": "r1"})

    connection.send_error.assert_not_called()
    db.async_mark_recipe_brewed.assert_awaited_once_with("r1")
    connection.send_result.assert_called_once()


@pytest.mark.asyncio
async def test_ws_brew_validation_error_message_reaches_client():
    hass = MagicMock()
    db = MagicMock()
    row = _recipe_row()
    row["machine_phases"] = [_coffee_phase(process="espresso")]
    db.async_get_recipe = AsyncMock(return_value=row)
    db.async_mark_recipe_brewed = AsyncMock()
    client = _client()
    connection = MagicMock()

    with patch.object(sommelier_api, "_async_get_db", AsyncMock(return_value=db)), \
         patch.object(sommelier_api, "_find_client", return_value=client):
        await ws_brew(hass, connection, {"id": 7, "recipe_id": "r1"})

    args = connection.send_error.call_args.args
    assert args[1] == "brew_failed"
    assert "espresso" in args[2]  # the descriptive ValueError text, not a generic hint
    db.async_mark_recipe_brewed.assert_not_awaited()


@pytest.mark.asyncio
async def test_ws_favorites_brew_sends_error_and_skips_increment_when_refused():
    hass = MagicMock()
    db = MagicMock()
    db.async_get_favorite = AsyncMock(return_value={
        "id": "f1", "name": "Fav", "blend": 0,
        "machine_phases": [_coffee_phase()],
    })
    db.async_increment_favorite_brew = AsyncMock()
    client = _client(brew_result=False)
    connection = MagicMock()

    with patch.object(sommelier_api, "_async_get_db", AsyncMock(return_value=db)), \
         patch.object(sommelier_api, "_find_client", return_value=client):
        await ws_favorites_brew(hass, connection, {"id": 8, "favorite_id": "f1"})

    args = connection.send_error.call_args.args
    assert args[0] == 8
    assert args[1] == "brew_failed"
    db.async_increment_favorite_brew.assert_not_awaited()
    connection.send_result.assert_not_called()


@pytest.mark.asyncio
async def test_ws_favorites_brew_success_increments():
    hass = MagicMock()
    db = MagicMock()
    db.async_get_favorite = AsyncMock(return_value={
        "id": "f1", "name": "Fav", "blend": 1,
        "machine_phases": [_coffee_phase()],
    })
    db.async_increment_favorite_brew = AsyncMock()
    client = _client(brew_result=True)
    connection = MagicMock()

    with patch.object(sommelier_api, "_async_get_db", AsyncMock(return_value=db)), \
         patch.object(sommelier_api, "_find_client", return_value=client):
        await ws_favorites_brew(hass, connection, {"id": 9, "favorite_id": "f1"})

    connection.send_error.assert_not_called()
    db.async_increment_favorite_brew.assert_awaited_once_with("f1")


# ── 2b. favorites/add source-hopper mapping ──────────────────────────


async def _run_favorites_add(blend: int) -> dict:
    hass = MagicMock()
    db = MagicMock()
    db.async_get_recipe = AsyncMock(return_value=_recipe_row(blend=blend))
    # 0.91.0b5 duplicate guard: not favorited yet -> proceed to insert.
    db.async_find_favorite_by_source = AsyncMock(return_value=None)
    db.async_get_hoppers = AsyncMock(return_value={
        "hopper1": {"bean": {"id": "bean-h1"}},
        "hopper2": {"bean": {"id": "bean-h2"}},
    })
    db.async_add_favorite = AsyncMock(return_value={"id": "f1"})
    connection = MagicMock()

    with patch.object(sommelier_api, "_async_get_db", AsyncMock(return_value=db)):
        await ws_favorites_add(hass, connection, {"id": 10, "recipe_id": "r1"})

    return db.async_add_favorite.call_args.args[0]


@pytest.mark.asyncio
async def test_favorites_add_blend_1_records_hopper1_bean():
    payload = await _run_favorites_add(blend=1)
    assert payload["source_bean_id"] == "bean-h1"


@pytest.mark.asyncio
async def test_favorites_add_blend_0_records_hopper2_bean():
    payload = await _run_favorites_add(blend=0)
    assert payload["source_bean_id"] == "bean-h2"


# ── 4. capabilities/get exposes supports_recipe_writes ───────────────


@pytest.mark.asyncio
async def test_capabilities_get_cache_path_includes_supports_recipe_writes():
    hass = MagicMock()
    db = MagicMock()
    db.async_get_capabilities = AsyncMock(return_value={
        "entry_id": "e1",
        "json_payload": json.dumps({
            "schema_version": 2,
            "family_key": "nivona_7xx",
            "model_name": "Nivona NICR 779",
            "supported_processes": ["coffee"],
            "supported_intensities": ["mild"],
            "supported_aromas": ["standard"],
            "supported_temperatures": ["normal"],
            "supported_shots": ["one"],
            "portion_limits": {},
            "forbidden_combinations": [],
            "supports_recipe_writes": False,
        }),
        "probed_at": "2026-08-01T10:00:00+00:00",
        "schema_version": 2,
    })
    hass.data = {"melitta_barista": {"sommelier_db": db}}
    connection = MagicMock()

    await ws_capabilities_get(hass, connection, {"id": 11, "entry_id": "e1"})

    result = connection.send_result.call_args.args[1]
    assert result["capabilities"]["supports_recipe_writes"] is False


@pytest.mark.asyncio
async def test_capabilities_get_derive_path_includes_supports_recipe_writes():
    from custom_components.melitta_barista.brands.base import MachineCapabilities

    caps = MachineCapabilities(
        family_key="nivona_7xx",
        model_name="Nivona NICR 779",
        supports_recipe_writes=False,
        supports_stats=True,
        my_coffee_slots=0,
        strength_levels=3,
        has_aroma_balance=False,
        image_transfer=None,
        fluid_scale_factor=1,
        brew_command_mode=0x0B,
        recipe_text_encoding="utf16_le",
        tolerated_brew_manipulations=(),
        recipes=(),
        settings=(),
        stats=(),
    )
    client = MagicMock()
    client.capabilities = caps
    entry = MagicMock()
    entry.entry_id = "e2"
    entry.runtime_data = client

    hass = MagicMock()
    hass.config_entries.async_get_entry = MagicMock(return_value=entry)
    db = MagicMock()
    db.async_get_capabilities = AsyncMock(return_value=None)
    hass.data = {"melitta_barista": {"sommelier_db": db}}
    connection = MagicMock()

    await ws_capabilities_get(hass, connection, {"id": 12, "entry_id": "e2"})

    result = connection.send_result.call_args.args[1]
    assert result["source"] == "derive"
    assert result["capabilities"]["supports_recipe_writes"] is False
