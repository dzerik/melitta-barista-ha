"""Nivona native two-cup control and temporary recipe command regression tests."""

import logging
import struct
from contextlib import nullcontext
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.core import State

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.melitta_barista.ble_client import MelittaBleClient
from custom_components.melitta_barista.brands import get_profile
from custom_components.melitta_barista.const import DOMAIN
from custom_components.melitta_barista.number import (
    NivonaBrewOverrideNumber,
    async_setup_entry,
)
from tests import MOCK_ADDRESS, MOCK_CONFIG_DATA


def _client(model="970"):
    client = MelittaBleClient(MOCK_ADDRESS, brand=get_profile("nivona"))
    client._capabilities = client.brand.capabilities_for_model(f"{model}000000000000")
    return client


async def _numbers(hass, client):
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG_DATA)
    entry.add_to_hass(hass)
    entry.runtime_data = client
    numbers = []
    await async_setup_entry(hass, entry, numbers.extend)
    return entry, numbers


async def test_nicr970_exposes_two_cup_input_without_hardware_writes(hass):
    client = _client()
    client._protocol.send_and_wait_ack = AsyncMock(return_value=True)
    assert client.capabilities.family_key == "900-light"
    _, numbers = await _numbers(hass, client)
    two_cups = next((n for n in numbers if n.unique_id.endswith("_brew_two_cups")), None)
    assert two_cups is not None, "The native Nivona two-cup control is missing"
    assert isinstance(two_cups, NivonaBrewOverrideNumber)
    assert (two_cups.native_min_value, two_cups.native_max_value, two_cups.native_step) == (0, 1, 1)
    assert two_cups.native_value == 0
    assert not two_cups.is_user_set
    two_cups.hass = hass
    two_cups.entity_id = "number.brew_two_cups"
    await two_cups.async_set_native_value(1)
    assert hass.states.get(two_cups.entity_id).state == "1"
    assert hass.states.get(two_cups.entity_id).attributes["user_set"] is True
    client._protocol.send_and_wait_ack.assert_not_awaited()


async def _native_controls(hass, client):
    from custom_components.melitta_barista.button import NivonaBrewButton
    from custom_components.melitta_barista.select import NivonaRecipeSelect

    entry, numbers = await _numbers(hass, client)
    overrides = [n for n in numbers if isinstance(n, NivonaBrewOverrideNumber)]
    component = EntityComponent(logging.getLogger(__name__), "number", hass)
    await component.async_add_entities(overrides)
    recipe = NivonaRecipeSelect(client, entry, "Coffee")
    selects = EntityComponent(logging.getLogger(__name__), "select", hass)
    await selects.async_add_entities([recipe])
    await recipe.async_select_option("Coffee")
    button = NivonaBrewButton(client, entry, "Coffee")
    buttons = EntityComponent(logging.getLogger(__name__), "button", hass)
    await buttons.async_add_entities([button])
    return {n.field_name: n for n in overrides}, button


@pytest.mark.parametrize("scale,amounts", [(1, (40, 100, 80)), (10, (400, 1000, 800))])
@pytest.mark.parametrize("value", [0, 1])
async def test_native_two_cup_choice_reaches_unscaled_temp_register_before_brew(hass, value, scale, amounts):
    client = _client()
    client._connected = True
    client._client = MagicMock(is_connected=True)
    client.start_polling = MagicMock()
    client._protocol.send_and_wait_ack = AsyncMock(return_value=True)
    numbers, button = await _native_controls(hass, client)
    await numbers["two_cups"].async_set_native_value(value)
    # Exercise both fluid scales without inventing a new model mapping.
    with patch.object(client.brand, "fluid_write_scale", return_value=scale):
        await button.async_press()
    commands = [(c.args[0], c.args[1]) for c in client._protocol.send_and_wait_ack.call_args_list]
    assert commands == [
        ("HW", struct.pack(">hi", 9001, 1)),
        ("HW", struct.pack(">hi", 9002, 3)),
        ("HW", struct.pack(">hi", 9010, amounts[0])),
        ("HW", struct.pack(">hi", 9011, amounts[1])),
        ("HW", struct.pack(">hi", 9012, amounts[2])),
        ("HW", struct.pack(">hi", 9005, value)),
        ("HE", bytes.fromhex("000b00010001000000000000000000000000")),
    ]


async def test_untouched_two_cup_default_keeps_saved_recipe(hass):
    client = _client()
    client._connected = True
    client._client = MagicMock(is_connected=True)
    client.start_polling = MagicMock()
    client._protocol.send_and_wait_ack = AsyncMock(return_value=True)
    _, button = await _native_controls(hass, client)
    await button.async_press()
    commands = [(c.args[0], c.args[1]) for c in client._protocol.send_and_wait_ack.call_args_list]
    assert commands == [("HE", bytes.fromhex("000b00010000000000000000000000000000"))]


async def test_other_override_keeps_complete_recipe_including_single_cup(hass):
    client = _client()
    client._connected = True
    client._client = MagicMock(is_connected=True)
    client.start_polling = MagicMock()
    client._protocol.send_and_wait_ack = AsyncMock(return_value=True)
    numbers, button = await _native_controls(hass, client)
    await numbers["strength"].async_set_native_value(5)
    await button.async_press()
    commands = [(c.args[0], c.args[1]) for c in client._protocol.send_and_wait_ack.call_args_list]
    assert commands == [
        ("HW", struct.pack(">hi", 9001, 1)),
        ("HW", struct.pack(">hi", 9002, 5)),
        ("HW", struct.pack(">hi", 9010, 40)),
        ("HW", struct.pack(">hi", 9011, 100)),
        ("HW", struct.pack(">hi", 9012, 80)),
        ("HW", struct.pack(">hi", 9005, 0)),
        ("HE", bytes.fromhex("000b00010001000000000000000000000000")),
    ]


@pytest.mark.parametrize("family", ["600", "700", "79x", "900", "900-light", "1030", "1040", "8000"])
async def test_only_published_family_maps_expose_two_cups(hass, family):
    client = _client()
    client._capabilities = client.brand.capabilities_for(family)
    _, numbers = await _numbers(hass, client)
    assert any(n.unique_id.endswith("_brew_two_cups") for n in numbers)


async def test_scanner_resolved_capabilities_create_two_cup_control(hass):
    client = _client()
    caps = client.capabilities
    client._capabilities = None
    with patch("custom_components.melitta_barista.number.resolve_caps_from_scanner", return_value=caps):
        _, numbers = await _numbers(hass, client)
    assert any(n.unique_id.endswith("_brew_two_cups") for n in numbers)


@pytest.mark.parametrize("unsupported", ["unknown", "unmapped", "missing_offset", "disabled", "melitta"])
async def test_two_cups_is_absent_without_supported_capabilities_and_mapping(hass, unsupported):
    from dataclasses import replace

    client = _client()
    if unsupported == "unknown":
        client._capabilities = None
    elif unsupported == "disabled":
        client._capabilities = replace(client.capabilities, supports_brew_overrides=False)
    elif unsupported == "melitta":
        client = MelittaBleClient(MOCK_ADDRESS, brand=get_profile("melitta"))
    elif unsupported != "missing_offset":
        client._capabilities = replace(client.capabilities, family_key="unmapped")
    mapping = (
        patch.object(client.brand, "temp_recipe_register", return_value=None)
        if unsupported == "missing_offset" else nullcontext()
    )
    with mapping, patch("custom_components.melitta_barista.number.resolve_caps_from_scanner", return_value=None):
        _, numbers = await _numbers(hass, client)
    assert not any(n.unique_id.endswith("_brew_two_cups") for n in numbers)


@pytest.mark.parametrize("value,user_set", [(0, False), (0, True), (1, True)])
async def test_two_cup_value_and_explicit_choice_survive_restore(hass, value, user_set):
    client = _client()
    _, numbers = await _numbers(hass, client)
    two_cups = next(n for n in numbers if n.unique_id.endswith("_brew_two_cups"))
    component = EntityComponent(logging.getLogger(__name__), "number", hass)
    previous = State("number.brew_two_cups", str(value), {"user_set": user_set})
    with patch.object(NivonaBrewOverrideNumber, "async_get_last_state", AsyncMock(return_value=previous)):
        await component.async_add_entities([two_cups])
    restored = hass.states.get(two_cups.entity_id)
    assert float(restored.state) == value
    assert restored.attributes["user_set"] is user_set


async def test_reset_two_cups_clears_explicit_choice_and_uses_saved_recipe(hass):
    from custom_components.melitta_barista.button import NivonaResetOverridesButton

    client = _client()
    client._connected = True
    client._client = MagicMock(is_connected=True)
    client.start_polling = MagicMock()
    client._protocol.send_and_wait_ack = AsyncMock(return_value=True)
    numbers, button = await _native_controls(hass, client)
    await numbers["two_cups"].async_set_native_value(1)
    await numbers["coffee_amount"].async_set_native_value(120)
    reset = NivonaResetOverridesButton(client, MagicMock(), "Coffee")
    reset.hass = hass
    await reset.async_press()
    await hass.async_block_till_done()
    state = hass.states.get(numbers["two_cups"].entity_id)
    assert state.state == "0"
    assert state.attributes["user_set"] is False
    for field, default in [("strength", 3), ("coffee_amount", 40), ("water_amount", 100), ("temperature", 1), ("milk_amount", 80)]:
        state = hass.states.get(numbers[field].entity_id)
        assert float(state.state) == default
        assert state.attributes["user_set"] is False
    client._protocol.send_and_wait_ack.assert_not_awaited()
    await button.async_press()
    commands = [(c.args[0], c.args[1]) for c in client._protocol.send_and_wait_ack.call_args_list]
    assert commands == [("HE", bytes.fromhex("000b00010000000000000000000000000000"))]
