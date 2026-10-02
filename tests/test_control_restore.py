"""Local brew choices survive HA reloads, including offline shutdowns (#51)."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import State
from homeassistant.helpers import restore_state
from homeassistant.helpers.entity_component import EntityComponent
from pytest_homeassistant_custom_component.common import (
    mock_restore_cache,
    mock_restore_cache_with_extra_data,
)

from custom_components.melitta_barista.ble_client import MelittaBleClient
from custom_components.melitta_barista.button import (
    MelittaBrewButton,
    MelittaBrewFreestyleButton,
)
from custom_components.melitta_barista.const import MachineProcess, RecipeId
from custom_components.melitta_barista.number import MelittaFreestyleNumber
from custom_components.melitta_barista.protocol import MachineStatus
from custom_components.melitta_barista.select import (
    MelittaFreestyleSelect,
    MelittaProfileSelect,
    MelittaRecipeSelect,
    _AROMA_OPTIONS,
    _BLEND_OPTIONS,
    _INTENSITY_OPTIONS,
    _PROCESS_OPTIONS,
    _PROCESS_OPTIONS_WITH_NONE,
    _SHOTS_OPTIONS,
    _TEMPERATURE_OPTIONS,
)
from custom_components.melitta_barista.text import MelittaFreestyleNameText


def _client():
    client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
    client._client = MagicMock(is_connected=True)
    client._connected = True
    client._status = MachineStatus(process=MachineProcess.READY)
    client.read_recipe = AsyncMock(return_value=None)
    client.brew_recipe = AsyncMock(return_value=True)
    client.brew_freestyle = AsyncMock(return_value=True)
    return client


def _controls(client, prefix="coffee"):
    entry = MagicMock()
    entities = {
        "select": [MelittaRecipeSelect(client, entry, "Coffee"),
                   MelittaProfileSelect(client, entry, "Coffee")],
        "number": [],
        "text": [MelittaFreestyleNameText(client, entry, "Coffee")],
    }
    for n in (1, 2):
        for key, options in (
            ("process", _PROCESS_OPTIONS if n == 1 else _PROCESS_OPTIONS_WITH_NONE),
            ("intensity", _INTENSITY_OPTIONS), ("aroma", _AROMA_OPTIONS),
            ("temperature", _TEMPERATURE_OPTIONS), ("shots", _SHOTS_OPTIONS),
            ("blend", _BLEND_OPTIONS),
        ):
            entities["select"].append(MelittaFreestyleSelect(
                client, entry, "Coffee", f"{key}_{n}", key, "mdi:coffee",
                options, f"freestyle_{key}{n}",
            ))
        entities["number"].append(MelittaFreestyleNumber(
            client, entry, "Coffee", f"portion_{n}", f"Portion {n}",
            "mdi:cup", 5 if n == 1 else 0, 250, 5, f"freestyle_portion{n}_ml",
        ))
    for domain, group in entities.items():
        for index, entity in enumerate(group):
            entity.entity_id = f"{domain}.{prefix}_{index}"
    return entities


@pytest.mark.parametrize("offline", [False, True])
@pytest.mark.parametrize("restart", [False, True], ids=["reload", "restart"])
async def test_local_controls_survive_lifecycle(hass, hass_storage, offline, restart):
    """Exercise registration, serialization, removal and a fresh client."""
    old = _client()
    groups = _controls(old)
    components = {}
    for domain, entities in groups.items():
        component = EntityComponent(logging.getLogger(__name__), domain, hass)
        components[domain] = component
        await component.async_add_entities(entities)
    await groups["select"][0].async_select_option("Café Crème Doppio")
    await groups["select"][1].async_select_option("Profile 2")
    for entity in groups["select"][2:]:
        await entity.async_select_option(entity.options[-1])
    await groups["number"][0].async_set_native_value(120)
    await groups["number"][1].async_set_native_value(65)
    await groups["text"][0].async_set_value("Morning coffee")
    expected = {key: value for key, value in vars(old).items()
                if key.startswith("freestyle_") or key in ("selected_recipe", "active_profile")}
    old._connected = not offline
    for group in groups.values():
        for entity in group:
            entity.async_write_ha_state()
    if restart:
        await restore_state.async_get(hass).async_dump_states()
    for domain, group in groups.items():
        for entity in group:
            await components[domain].async_remove_entity(entity.entity_id)
    if restart:
        restore_state.async_get.cache_clear()
        data = restore_state.RestoreStateData(hass)
        hass.data[restore_state.DATA_RESTORE_STATE] = data
        await data.async_load()
    new = _client()
    new._connected = not offline
    # A profile is an identity, not a display name cached before startup.
    new._profile_names[2] = "Renamed profile"
    restored = _controls(new)
    for domain, group in restored.items():
        await components[domain].async_add_entities(group)
    assert {key: getattr(new, key) for key in expected} == expected
    assert restored["select"][0].current_option == "Café Crème Doppio"
    assert restored["select"][1].current_option == "Renamed profile"
    new.read_recipe.assert_not_awaited()
    new.brew_recipe.assert_not_awaited()
    new.brew_freestyle.assert_not_awaited()
    new._connected = True
    brew = MelittaBrewButton(new, MagicMock(), "Coffee")
    assert brew.available
    await brew.async_press()
    new.brew_recipe.assert_awaited_once_with(RecipeId.CAFE_CREME_DOPIO)
    await MelittaBrewFreestyleButton(new, MagicMock(), "Coffee").async_press()
    kwargs = new.brew_freestyle.call_args.kwargs
    assert kwargs["name"] == "Morning coffee"
    assert kwargs["component1"].aroma == 1
    assert kwargs["component1"].shots == 3
    assert kwargs["component1"].portion == 24
    assert kwargs["component2"].portion == 13


async def test_post17_legacy_states_restore_without_ble(hass):
    """The original report is reproducible even with a populated HA cache."""
    client = _client()
    entities = _controls(client)["select"]
    recipe, profile = entities[:2]
    aroma, shots = entities[4], entities[6]
    saved = [(recipe, "Café Crème Doppio", {}), (profile, "Old profile name", {"active_profile": 2}),
             (aroma, "intense", {}), (shots, "two", {})]
    mock_restore_cache(hass, [State(e.entity_id, value, attrs) for e, value, attrs in saved])
    for entity, _, _ in saved:
        entity.hass = hass
        await entity.async_added_to_hass()
    assert client.selected_recipe == RecipeId.CAFE_CREME_DOPIO
    assert client.active_profile == 2
    assert client.freestyle_aroma1 == "intense"
    assert client.freestyle_shots1 == "two"
    client.read_recipe.assert_not_awaited()


@pytest.mark.parametrize("value", ["unknown", "unavailable", "obsolete", "", "nan", "9999"])
async def test_invalid_legacy_states_keep_defaults(hass, value):
    client = _client()
    groups = _controls(client)
    entities = [entity for group in groups.values() for entity in group]
    mock_restore_cache(hass, [State(entity.entity_id, value) for entity in entities])
    for entity in entities:
        entity.hass = hass
        await entity.async_added_to_hass()
    assert client.selected_recipe is None
    assert client.active_profile == 0
    assert client.freestyle_aroma1 == "standard"
    assert client.freestyle_shots1 == "one"
    assert client.freestyle_portion1_ml == 40
    assert client.freestyle_portion2_ml == 0
    if value in ("unknown", "unavailable"):
        assert client.freestyle_name == "Custom"


async def test_controls_do_not_restore_another_machine(hass):
    mock_restore_cache(hass, [State("select.other_0", "Café Crème Doppio")])
    client = _client()
    entity = _controls(client)["select"][0]
    entity.hass = hass
    await entity.async_added_to_hass()
    assert client.selected_recipe is None


@pytest.mark.parametrize("value", [None, True, {}, [], "obsolete", -5, 9999])
async def test_invalid_native_choices_keep_defaults(hass, value):
    client = _client()
    entities = _controls(client)["select"]
    mock_restore_cache_with_extra_data(hass, [
        (State(e.entity_id, "unavailable"), {"value": value}) for e in entities
    ])
    for entity in entities:
        entity.hass = hass
        await entity.async_added_to_hass()
    assert client.selected_recipe is None
    assert client.active_profile == 0
    assert client.freestyle_aroma1 == "standard"


@pytest.mark.parametrize("value", [True, None, "nan", "inf", -5, 251, 122.5, {}])
async def test_invalid_native_portions_keep_defaults(hass, value):
    client = _client()
    entity = _controls(client)["number"][0]
    mock_restore_cache_with_extra_data(hass, [(State(entity.entity_id, "unavailable"), {
        "native_value": value, "native_min_value": 5, "native_max_value": 250,
        "native_step": 5, "native_unit_of_measurement": "ml",
    })])
    entity.hass = hass
    await entity.async_added_to_hass()
    assert client.freestyle_portion1_ml == 40


async def test_text_restore_rejects_overlong_names(hass):
    client = _client()
    entity = _controls(client)["text"][0]
    mock_restore_cache_with_extra_data(hass, [
        (State(entity.entity_id, "unavailable"), {"value": "a" * 31}),
    ])
    entity.hass = hass
    await entity.async_added_to_hass()
    assert client.freestyle_name == "Custom"
