"""NICR 960 identity and native aroma profile regressions."""

import logging
import struct
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from homeassistant.core import State
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_component import EntityComponent
from pytest_homeassistant_custom_component.common import MockConfigEntry, mock_restore_cache

from custom_components.melitta_barista.ble_client import MelittaBleClient
from custom_components.melitta_barista.brands.nivona import NivonaProfile
from custom_components.melitta_barista.button import NivonaBrewButton, NivonaResetOverridesButton
from custom_components.melitta_barista.const import DOMAIN
from custom_components.melitta_barista.number import NivonaBrewOverrideNumber
from custom_components.melitta_barista.select import NivonaRecipeSelect
from tests import MOCK_ADDRESS, MOCK_CONFIG_DATA


@pytest.mark.parametrize("model, tail", [("960", ""), ("965", ""), ("970", ""), ("960", "-----")])
def test_serial_identity_preserves_nine_slots_and_five_strengths(model, tail):
    caps = NivonaProfile().capabilities_for_model(f"NIVONA-{model}698000000000{tail}")

    assert caps.family_key == "900-light"
    assert (caps.my_coffee_slots, caps.strength_levels) == (9, 5)
    assert caps.first_mycoffee_selector == 8


def test_dis_model_identity_refines_960_when_serial_is_missing():
    client = MelittaBleClient(MOCK_ADDRESS, brand=NivonaProfile())
    client._dis_info = {"model": "NICR 960"}

    caps = client._resolve_capabilities()

    assert caps.family_key == "900-light"
    assert (caps.my_coffee_slots, caps.strength_levels) == (9, 5)


def test_unstructured_dis_hint_preserves_conservative_family_defaults():
    caps = NivonaProfile().capabilities_for_model("", {"ad06_ascii": "model family 960"})

    assert caps.family_key == "900-light"
    assert (caps.my_coffee_slots, caps.strength_levels) == (4, 3)


def test_same_lookup_prefers_a_complete_dis_model_over_conflicting_serial():
    caps = NivonaProfile().capabilities_for_model("NIVONA-930254000000000", {"model": "NICR 960"})

    assert caps.family_key == "900-light"


@pytest.mark.parametrize("name", ["960th Coffee Shop", "NIVONA-960698000000000-----"])
def test_advertisement_does_not_mask_exact_dis_model(name):
    client = MelittaBleClient(MOCK_ADDRESS, name, brand=NivonaProfile())
    client._dis_info = {"model": "NICR 930"}

    caps = client._resolve_capabilities()

    assert caps.family_key == "900"
    assert not caps.brew_profile_options


def test_compatible_advertisement_does_not_mask_conflicting_dis_under_family_override():
    client = MelittaBleClient(
        MOCK_ADDRESS, "NIVONA-960698000000000-----", brand=NivonaProfile(), family_override="900-light",
    )
    client._dis_info = {"model": "NICR 930"}

    caps = client._resolve_capabilities()

    assert caps.family_key == "900-light"
    assert (caps.my_coffee_slots, caps.strength_levels) == (4, 3)
    assert not caps.brew_profile_options


def test_ordinary_790_serial_still_beats_an_unstructured_dis_hint():
    client = MelittaBleClient(MOCK_ADDRESS, "NIVONA-790253000000000-----", brand=NivonaProfile())
    client._dis_info = {"model": "family 960"}

    caps = client._resolve_capabilities()

    assert caps.family_key == "79x"
    assert (caps.my_coffee_slots, caps.strength_levels) == (5, 5)


@pytest.mark.parametrize("name", ["960 kitchen", "NIVONA-960-front-office"])
def test_name_prefix_alone_cannot_identify_960(name):
    assert NivonaProfile().capabilities_for_model(name) is None


async def _native_controls(hass, serial):
    client = MelittaBleClient(MOCK_ADDRESS, brand=NivonaProfile())
    client._capabilities = (
        client.brand.capabilities_for_model(serial) if serial
        else client.brand.capabilities_for("900-light")
    )
    client._connected = True
    client._client = MagicMock(is_connected=True)
    client.start_polling = MagicMock()
    client._protocol.send_and_wait_ack = AsyncMock(return_value=True)
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG_DATA)
    entry.add_to_hass(hass)
    entry.runtime_data = client
    selectors = EntityComponent(logging.getLogger(__name__), "select", hass)
    assert await selectors.async_setup_entry(entry)
    numbers = EntityComponent(logging.getLogger(__name__), "number", hass)
    assert await numbers.async_setup_entry(entry)
    await hass.async_block_till_done()
    recipe = next(entity for entity in selectors.entities if isinstance(entity, NivonaRecipeSelect))
    profile = next((entity for entity in selectors.entities if entity.unique_id.endswith("_brew_profile")), None)
    overrides = {n.field_name: n for n in numbers.entities if isinstance(n, NivonaBrewOverrideNumber)}
    brew = NivonaBrewButton(client, entry, "Coffee")
    reset = NivonaResetOverridesButton(client, entry, "Coffee")
    buttons = EntityComponent(logging.getLogger(__name__), "button", hass)
    await buttons.async_add_entities([brew, reset])
    return client, recipe, profile, brew, reset, overrides


@pytest.mark.parametrize(("option", "raw"), [("dynamic", 0), ("constant", 1), ("intense", 2)])
async def test_960_native_aroma_profile_is_sent_as_unscaled_raw_value(hass, option, raw):
    client, recipe, profile, brew, _, _ = await _native_controls(hass, "960698000000000")
    assert profile is not None, "NICR 960 lacks a native aroma profile input"
    assert profile.options == ["default", "dynamic", "constant", "intense"]
    assert client.capabilities.has_aroma_balance is False
    await recipe.async_select_option("Americano")
    await profile.async_select_option(option)
    client._protocol.send_and_wait_ack.assert_not_awaited()

    await brew.async_press()

    assert [call.args[:2] for call in client._protocol.send_and_wait_ack.call_args_list] == [
        ("HW", struct.pack(">hi", 9001, 2)),
        ("HW", struct.pack(">hi", 9002, 2)),
        ("HW", struct.pack(">hi", 9010, 400)),
        ("HW", struct.pack(">hi", 9011, 1000)),
        ("HW", struct.pack(">hi", 9006, 1)),
        ("HW", struct.pack(">hi", 9012, 800)),
        ("HW", struct.pack(">hi", 9005, 0)),
        ("HW", struct.pack(">hi", 9003, raw)),
        ("HE", bytes.fromhex("000b00020001000000000000000000000000")),
    ]


@pytest.mark.parametrize("serial", [None, "930254000000000", "965698000000000", "970698000000000", "795253000000000"])
async def test_profile_input_requires_exact_960_model(hass, serial):
    _, _, profile, _, _, _ = await _native_controls(hass, serial)

    assert profile is None


async def test_profile_is_ignored_for_hot_milk_and_reset_clears_it_for_coffee(hass):
    client, recipe, profile, brew, reset, _ = await _native_controls(hass, "960698000000000")
    assert profile is not None, "NICR 960 lacks a native aroma profile input"
    assert profile.current_option == "default"
    await profile.async_select_option("intense")
    await recipe.async_select_option("Hot Milk")

    await brew.async_press()

    assert [call.args[0] for call in client._protocol.send_and_wait_ack.call_args_list] == ["HE"]
    assert profile.current_option == "intense"
    client._protocol.send_and_wait_ack.reset_mock()
    await reset.async_press()
    await hass.async_block_till_done()
    assert profile.current_option == "default"
    await recipe.async_select_option("Espresso")

    await brew.async_press()

    assert [call.args[0] for call in client._protocol.send_and_wait_ack.call_args_list] == ["HE"]


async def test_profile_rejects_unrecognized_option(hass):
    _, _, profile, _, _, _ = await _native_controls(hass, "960698000000000")
    assert profile is not None, "NICR 960 lacks a native aroma profile input"

    with pytest.raises(ServiceValidationError, match="profile"):
        await profile.async_select_option("standard")


@pytest.mark.parametrize("stored, expected", [("intense", "intense"), ("standard", "default")])
async def test_960_startup_restores_only_valid_profile_choices(hass, stored, expected):
    mock_restore_cache(hass, [State("select.8601abcd1234_brew_aroma_profile", stored)])

    _, _, profile, _, _, _ = await _native_controls(hass, "960698000000000")

    assert profile.current_option == expected


async def test_960_mycoffee_reads_nine_slots_and_preserves_raw_activity_flags(hass):
    client, _, _, _, _, _ = await _native_controls(hass, "960698000000000")
    raw_flags = {20000: 1, 20800: 2}
    client._protocol.read_numerical = AsyncMock(side_effect=lambda _, register: raw_flags.get(register))

    assert await client.read_mycoffee_slots()

    assert len(client.my_coffee_slots) == 9
    assert client.my_coffee_slots[0]["enabled"] == 1
    assert client.my_coffee_slots[8]["enabled"] == 2


async def test_960_default_reloads_saved_profile_when_other_overrides_remain(hass):
    client, recipe, profile, brew, _, numbers = await _native_controls(hass, "960698000000000")
    await recipe.async_select_option("Americano")
    protocol = client._protocol
    protocol.send_and_wait_response = AsyncMock(return_value=struct.pack(">hi", 10202, 0))
    transfers = MagicMock()
    transfers.attach_mock(protocol.send_and_wait_response, "read")
    transfers.attach_mock(protocol.send_and_wait_ack, "write")
    await profile.async_select_option("intense")
    await numbers["strength"].async_set_native_value(5)
    await brew.async_press()
    protocol.send_and_wait_response.assert_not_awaited()
    assert any(c.args[:2] == ("HW", struct.pack(">hi", 9003, 2)) for c in transfers.mock_calls)
    transfers.reset_mock()

    await profile.async_select_option("default")
    await brew.async_press()

    assert transfers.mock_calls[0] == call.read(
        "HR", struct.pack(">h", 10202), client._write_ble, expected_id=10202,
    )
    assert transfers.mock_calls[-2].args[:2] == ("HW", struct.pack(">hi", 9003, 0))
    assert transfers.mock_calls[-1].args[0] == "HE"


@pytest.mark.parametrize("response", [None, struct.pack(">hi", 10002, 3)])
async def test_960_unreadable_saved_profile_prevents_temporary_brew(hass, response):
    client, _, _, brew, _, numbers = await _native_controls(hass, "960698000000000")
    protocol = client._protocol
    protocol.send_and_wait_response = AsyncMock(return_value=response)
    await numbers["strength"].async_set_native_value(5)

    await brew.async_press()

    protocol.send_and_wait_response.assert_awaited_once_with(
        "HR", struct.pack(">h", 10002), client._write_ble, expected_id=10002,
    )
    protocol.send_and_wait_ack.assert_not_awaited()


@pytest.mark.parametrize("name, register", [("Hot Milk", 9008), ("Hot Water", 9007)])
async def test_960_profile_does_not_change_other_overrides_on_drinks_without_coffee(hass, name, register):
    client, recipe, profile, brew, _, numbers = await _native_controls(hass, "960698000000000")
    await recipe.async_select_option(name)
    await profile.async_select_option("intense")
    await numbers["temperature"].async_set_native_value(2)
    protocol = client._protocol
    protocol.send_and_wait_response = AsyncMock()

    await brew.async_press()

    protocol.send_and_wait_response.assert_not_awaited()
    writes = [struct.unpack(">hi", c.args[1]) for c in protocol.send_and_wait_ack.await_args_list if c.args[0] == "HW"]
    assert (register, 2) in writes
    assert all(address != 9003 for address, _ in writes)


@pytest.mark.parametrize("context, selector", [("temporary", 2), ("standard", 2), ("mycoffee", 8)])
def test_960_accepts_only_three_raw_values_for_supported_aroma_profiles(context, selector):
    brand = NivonaProfile()
    caps = brand.capabilities_for_model("960698000000000")

    assert [brand.validate_recipe_param(caps, context, selector, "profile", raw) for raw in range(3)] == [0, 1, 2]
    with pytest.raises(ValueError, match="profile"):
        brand.validate_recipe_param(caps, context, selector, "profile", 3)


@pytest.mark.parametrize("context, selector", [("temporary", 6), ("temporary", 7), ("standard", 7)])
def test_960_rejects_explicit_profile_for_drinks_without_coffee(context, selector):
    brand = NivonaProfile()
    caps = brand.capabilities_for_model("960698000000000")

    with pytest.raises(ValueError, match="profile is unsupported"):
        brand.validate_recipe_param(caps, context, selector, "profile", 2)
