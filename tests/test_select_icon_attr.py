"""Tests for the UI Contract v1 recipe attributes on select.py (Zone I-C).

Covers spec §7.1 I-C:
- every ``recipes`` entry gains a procedural ``icon`` IconSpec (built by
  ``ui_contract.build_icon_spec`` from the Zone I-A0 raw base-recipe
  cache) and per-component ``blend`` tokens (omitted for wire byte 0);
- recorder guard: the selected recipe's top-level flattened attributes
  stay scalar — structured extras (``icon``) live exclusively inside the
  recorder-excluded ``recipes`` attribute;
- icon determinism/stability across refreshes;
- regression: legacy flattened keys, options and availability unchanged.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.melitta_barista.const import RECIPE_NAMES, RecipeId
from custom_components.melitta_barista.protocol import (
    MachineRecipe,
    RecipeComponent,
)
from custom_components.melitta_barista.select import MelittaRecipeSelect

from . import MOCK_ADDRESS


def _espresso_recipe(blend: int = 1) -> MachineRecipe:
    """Raw espresso: coffee 40 ml strong/one shot, empty component 2."""
    return MachineRecipe(
        recipe_id=int(RecipeId.ESPRESSO),
        recipe_type=0,
        component1=RecipeComponent(
            process=1, shots=1, blend=blend, intensity=3,
            aroma=0, temperature=1, portion=8,
        ),
        component2=RecipeComponent(
            process=0, shots=0, blend=blend, intensity=2,
            aroma=0, temperature=1, portion=0,
        ),
    )


def _latte_macchiato_recipe() -> MachineRecipe:
    """Raw latte macchiato: c1 milk 160 ml, c2 coffee 40 ml strong/one."""
    return MachineRecipe(
        recipe_id=int(RecipeId.LATTE_MACCHIATO),
        recipe_type=0,
        component1=RecipeComponent(
            process=2, shots=0, blend=1, intensity=2,
            aroma=0, temperature=1, portion=32,
        ),
        component2=RecipeComponent(
            process=1, shots=1, blend=1, intensity=3,
            aroma=0, temperature=1, portion=8,
        ),
    )


# §3.7 example payloads, byte-exact.
_ESPRESSO_ICON = {
    "spec_version": 1,
    "glass": "espresso_cup",
    "total_ml": 40,
    "fill_level": 0.67,
    "layers": [
        {"role": "coffee", "ml": 40, "fraction": 1.0, "intensity": 0.68,
         "crema": True},
    ],
    "foam": None,
    "steam": True,
}

_LATTE_MACCHIATO_ICON = {
    "spec_version": 1,
    "glass": "tall_glass",
    "total_ml": 200,
    "fill_level": 0.63,
    "layers": [
        {"role": "milk", "ml": 130, "fraction": 0.65, "intensity": 0.0},
        {"role": "coffee", "ml": 40, "fraction": 0.2, "intensity": 0.68},
    ],
    "foam": {"role": "milk_foam", "ml": 30, "fraction": 0.15},
    "steam": True,
}

# Legacy flattened keys that must survive the token rewire byte-identical.
_ESPRESSO_LEGACY_FLAT = {
    "c1_process": "coffee",
    "c1_intensity": "strong",
    "c1_aroma": "standard",
    "c1_temperature": "normal",
    "c1_shots": "one",
    "c1_portion_ml": 40,
    "c2_process": "none",
    "c2_intensity": "medium",
    "c2_aroma": "standard",
    "c2_temperature": "normal",
    "c2_shots": "none",
    "c2_portion_ml": 0,
}


def _entity():
    """MelittaRecipeSelect wired to a mock client with a real cache dict."""
    client = MagicMock()
    client.address = MOCK_ADDRESS
    client.connected = True
    client.machine_type = None
    client.base_recipes = {}
    client.recipe_cache_generation = 0
    entity = MelittaRecipeSelect(client, MagicMock(), "Test Machine")
    entity.async_write_ha_state = MagicMock()
    return entity, client


def _refresh(entity, client, recipe) -> None:
    client.base_recipes[int(recipe.recipe_id)] = recipe
    entity._on_recipe_refresh(int(recipe.recipe_id), recipe)


class TestRecipeIconAttribute:
    """Icon and blend inside the `recipes` attribute entries."""

    def test_recipes_entry_gains_espresso_icon(self):
        entity, client = _entity()
        _refresh(entity, client, _espresso_recipe())
        entry = entity.extra_state_attributes["recipes"]["Espresso"]
        assert entry["icon"] == _ESPRESSO_ICON

    def test_recipes_entry_gains_latte_macchiato_icon(self):
        entity, client = _entity()
        _refresh(entity, client, _latte_macchiato_recipe())
        name = RECIPE_NAMES[RecipeId.LATTE_MACCHIATO]
        entry = entity.extra_state_attributes["recipes"][name]
        assert entry["icon"] == _LATTE_MACCHIATO_ICON

    def test_recipes_entry_gains_per_component_blend(self):
        entity, client = _entity()
        _refresh(entity, client, _espresso_recipe(blend=1))
        entry = entity.extra_state_attributes["recipes"]["Espresso"]
        assert entry["c1_blend"] == "hopper_1"
        assert entry["c2_blend"] == "hopper_1"

    def test_blend_omitted_for_wire_byte_zero(self):
        """Blend.BARISTA_T (byte 0) has no token — key omitted (§3.2)."""
        entity, client = _entity()
        _refresh(entity, client, _espresso_recipe(blend=0))
        entry = entity.extra_state_attributes["recipes"]["Espresso"]
        assert "c1_blend" not in entry
        assert "c2_blend" not in entry

    def test_icon_stable_across_refreshes(self):
        """Same raw recipe → dict-equal icon on every derivation (§4)."""
        entity, client = _entity()
        _refresh(entity, client, _espresso_recipe())
        first = entity.extra_state_attributes["recipes"]["Espresso"]["icon"]
        _refresh(entity, client, _espresso_recipe())
        second = entity.extra_state_attributes["recipes"]["Espresso"]["icon"]
        assert first == second

    def test_legacy_flat_keys_unchanged(self):
        entity, client = _entity()
        _refresh(entity, client, _espresso_recipe())
        entry = entity.extra_state_attributes["recipes"]["Espresso"]
        for key, value in _ESPRESSO_LEGACY_FLAT.items():
            assert entry[key] == value


class TestRecorderGuard:
    """Top-level flattened attrs stay scalar; icon only inside `recipes`."""

    @pytest.mark.asyncio
    async def test_top_level_attrs_stay_scalar(self):
        entity, client = _entity()
        _refresh(entity, client, _espresso_recipe())
        await entity.async_select_option("Espresso")
        attrs = entity.extra_state_attributes
        for key, value in attrs.items():
            if key == "recipes":
                continue
            assert isinstance(value, (str, int, float, bool)), (
                f"top-level attr {key!r} must stay scalar, got {type(value)}"
            )
        assert "icon" not in attrs

    @pytest.mark.asyncio
    async def test_selected_recipe_flattening_regression(self):
        entity, client = _entity()
        _refresh(entity, client, _espresso_recipe())
        await entity.async_select_option("Espresso")
        attrs = entity.extra_state_attributes
        for key, value in _ESPRESSO_LEGACY_FLAT.items():
            assert attrs[key] == value
        assert attrs["c1_blend"] == "hopper_1"

    def test_recipes_attribute_stays_recorder_excluded(self):
        assert "recipes" in MelittaRecipeSelect._unrecorded_attributes


class TestSelectRegression:
    """Entity-level behaviour unchanged by the contract surface."""

    def test_options_unchanged(self):
        entity, _ = _entity()
        assert len(entity._attr_options) == 24
        assert "Espresso" in entity._attr_options

    def test_available_follows_connection(self):
        entity, client = _entity()
        assert entity.available is True
        client.connected = False
        assert entity.available is False
