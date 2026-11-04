"""Milk/froth selection changes the milk byte without changing coffee shots."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant.core import State
from homeassistant.helpers.entity_component import EntityComponent
from pytest_homeassistant_custom_component.common import mock_restore_cache_with_extra_data

import custom_components.melitta_barista as integration
from custom_components.melitta_barista import select as select_platform
from custom_components.melitta_barista.ble_client import MelittaBleClient
from custom_components.melitta_barista.button import MelittaBrewFreestyleButton
from custom_components.melitta_barista.const import DOMAIN, MachineProcess
from custom_components.melitta_barista.protocol import MachineRecipe, MachineStatus, RecipeComponent
from tests.test_ui_contract_parameters import make_melitta_client, make_nivona_client
from custom_components.melitta_barista.ui_contract import build_capabilities_block, build_ui_contract


def _client():
    client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
    client._connected = True
    client._client = MagicMock(is_connected=True)
    client._status = MachineStatus(process=MachineProcess.READY)
    client._stop_polling = MagicMock()
    client.start_polling = MagicMock()
    client._write_ble = AsyncMock()
    client._protocol.send_and_wait_ack = AsyncMock(return_value=True)
    return client


async def _service(client, name, data):
    hass = MagicMock()
    hass.services.has_service.return_value = False
    handlers = {}
    hass.services.async_register.side_effect = lambda domain, key, handler, **kw: handlers.update({key: handler})
    hass.config_entries.async_get_entry.return_value = SimpleNamespace(domain=DOMAIN, runtime_data=client)
    registry = MagicMock()
    registry.async_get.return_value = SimpleNamespace(config_entry_id="milk-mode")
    schema = integration.BREW_FREESTYLE_SCHEMA if name == "brew_freestyle" else integration.SAVE_DIRECTKEY_SCHEMA
    validated = schema({"entity_id": "button.coffee_brew", **data})
    with patch.object(integration.er, "async_get", return_value=registry), \
         patch("custom_components.melitta_barista._ble_commands.asyncio.sleep", new_callable=AsyncMock):
        integration._async_register_services(hass)
        await handlers[name](SimpleNamespace(data=validated))
    return client._protocol.send_and_wait_ack.call_args_list[0].args[1]


@pytest.mark.parametrize("slot", [1, 2])
@pytest.mark.parametrize("mode,want", [("warm_milk", 1), ("milk_froth", 0)])
async def test_explicit_mode_reaches_actual_freestyle_payload(slot, mode, want):
    client = _client()
    payload = await _service(client, "brew_freestyle", {
        "process1": "coffee", "shots1": "three", "process2": "coffee", "shots2": "two",
        f"process{slot}": "milk", f"milk_mode{slot}": mode,
    })
    assert payload[:4] == bytes.fromhex("01901807")
    assert payload[5 if slot == 1 else 13] == want
    assert payload[13 if slot == 1 else 5] == (2 if slot == 1 else 3)
    client._write_ble.assert_not_called()


@pytest.mark.parametrize("process", ["coffee", "water", "none"])
async def test_milk_mode_does_not_change_another_process(process):
    payload = await _service(_client(), "brew_freestyle", {
        "process1": process, "shots1": "three", "milk_mode1": "milk_froth",
    })
    assert payload[5] == 3


@pytest.mark.parametrize("shots,want", [("none", 0), ("one", 1), ("two", 2), ("three", 3)])
async def test_omitted_mode_preserves_all_legacy_milk_values(shots, want):
    payload = await _service(_client(), "brew_freestyle", {"process1": "milk", "shots1": shots})
    assert payload[4:6] == bytes([2, want])


@pytest.mark.parametrize("slot", [1, 2])
async def test_directkey_mode_preserves_profile_slot_type_and_hoppers(slot):
    client = _client()
    client._protocol.read_recipe = AsyncMock(return_value=MachineRecipe(
        recipe_id=312, recipe_type=2,
        component1=RecipeComponent(blend=2, reserve=17),
        component2=RecipeComponent(blend=1, reserve=23),
    ))
    payload = await _service(client, "save_directkey", {
        "category": "espresso", "profile_id": 1, "process1": "coffee", "shots1": "three",
        "process2": "coffee", "shots2": "two", f"process{slot}": "milk",
        f"milk_mode{slot}": "milk_froth",
    })
    assert payload[:3] == bytes.fromhex("013802")
    assert payload[4 if slot == 1 else 12] == 0
    assert (payload[5], payload[10], payload[13], payload[18]) == (2, 17, 1, 23)


@pytest.mark.parametrize("mode", [None, "", "steam", True, 0])
def test_invalid_explicit_modes_are_rejected(mode):
    with pytest.raises(vol.Invalid):
        integration.BREW_FREESTYLE_SCHEMA({
            "entity_id": "button.coffee_brew", "process1": "milk", "milk_mode1": mode,
        })


def _mode_entity(client, slot):
    cls = getattr(select_platform, "MelittaMilkModeSelect", None)
    assert cls is not None, "Native milk-mode select is missing"
    entity = cls(client, MagicMock(entry_id="milk-mode"), "Coffee", slot)
    entity.entity_id = f"select.coffee_freestyle_milk_mode_{slot}"
    return entity


@pytest.mark.parametrize("slot", [1, 2])
async def test_native_mode_preserves_coffee_shots_and_legacy_raw_control_wins(hass, slot):
    client = _client()
    setattr(client, f"freestyle_process{slot}", "milk")
    setattr(client, f"freestyle_shots{slot}", "three")
    mode = _mode_entity(client, slot)
    shots = select_platform.MelittaFreestyleSelect(
        client, MagicMock(entry_id="milk-mode"), "Coffee", f"shots_{slot}", "Shots",
        "mdi:counter", ["none", "one", "two", "three"], f"freestyle_shots{slot}",
    )
    shots.entity_id = f"select.coffee_shots_{slot}"
    component = EntityComponent(logging.getLogger(__name__), "select", hass)
    await component.async_add_entities([mode, shots])
    await mode.async_select_option("milk_froth")
    assert getattr(client, f"freestyle_shots{slot}") == "three"
    await MelittaBrewFreestyleButton(client, MagicMock(), "Coffee").async_press()
    payload = client._protocol.send_and_wait_ack.call_args_list[0].args[1]
    assert payload[5 if slot == 1 else 13] == 0
    setattr(client, f"freestyle_process{slot}", "coffee")
    client._protocol.send_and_wait_ack.reset_mock()
    await MelittaBrewFreestyleButton(client, MagicMock(), "Coffee").async_press()
    assert client._protocol.send_and_wait_ack.call_args_list[0].args[1][5 if slot == 1 else 13] == 3
    await shots.async_select_option("one")
    await hass.async_block_till_done()
    assert getattr(client, f"freestyle_milk_mode{slot}") is None
    assert mode.current_option == "warm_milk"


@pytest.mark.parametrize("override", [None, "milk_froth", "warm_milk"])
@pytest.mark.parametrize("mode_first", [True, False])
async def test_native_restore_keeps_optional_override_and_derived_label(hass, override, mode_first):
    client = _client()
    mode = _mode_entity(client, 1)
    raw = select_platform.MelittaFreestyleSelect(
        client, MagicMock(entry_id="milk-mode"), "Coffee", "shots_1", "Shots", "mdi:counter",
        ["none", "one", "two", "three"], "freestyle_shots1",
    )
    raw.entity_id = "select.coffee_shots"
    mock_restore_cache_with_extra_data(hass, [
        (State(mode.entity_id, "unavailable"), {"value": override}),
        (State(raw.entity_id, "three"), {"value": "three"}),
    ])
    component = EntityComponent(logging.getLogger(__name__), "select", hass)
    await component.async_add_entities([mode, raw] if mode_first else [raw, mode])
    await hass.async_block_till_done()
    assert getattr(client, "freestyle_milk_mode1") == override
    assert client.freestyle_shots1 == "three"
    assert mode.extra_restore_state_data.as_dict() == {"value": override}
    assert mode.extra_state_attributes["milk_mode"] == override
    assert mode.current_option == (override or "warm_milk")


def test_mode_catalog_is_additive_and_unavailable_for_nivona():
    for client in [make_melitta_client(), make_nivona_client()]:
        document = build_ui_contract(SimpleNamespace(entry_id="milk-mode"), client)
        legacy = document["vocabularies"]["freestyle"]
        assert legacy["process"] == ["none", "coffee", "milk", "water"]
        assert "milk_mode" not in legacy
        if build_capabilities_block(client)["supports_freestyle"]:
            assert document["parameters"]["milk_mode"] == {
                "kind": "enum", "scope": ["freestyle"], "applies_to": ["milk"],
                "tokens": ["warm_milk", "milk_froth"],
            }
        else:
            assert "milk_mode" not in document["parameters"]


async def test_process_changes_refresh_native_mode_availability_without_ble(hass):
    client = _client()
    mode = _mode_entity(client, 1)
    process = select_platform.MelittaFreestyleSelect(
        client, MagicMock(entry_id="milk-mode"), "Coffee", "process_1", "Process", "mdi:coffee",
        ["coffee", "milk", "water"], "freestyle_process1",
    )
    process.entity_id = "select.coffee_process_1"
    component = EntityComponent(logging.getLogger(__name__), "select", hass)
    await component.async_add_entities([mode, process])
    assert hass.states.get(mode.entity_id).state == "unavailable"
    await process.async_select_option("milk")
    await hass.async_block_till_done()
    assert hass.states.get(mode.entity_id).state == "warm_milk"
    await mode.async_select_option("milk_froth")
    await process.async_select_option("coffee")
    await hass.async_block_till_done()
    assert hass.states.get(mode.entity_id).state == "unavailable"
    assert client.freestyle_shots1 == "one"
    client._write_ble.assert_not_called()
