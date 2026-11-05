"""Literal Ef1109 contracts: status, storage, independent remote schema and ACK order."""

import struct
from unittest.mock import AsyncMock

import pytest

from custom_components.melitta_barista.coffee_platform.domain import (
    MachineProcess,
    SubProcess,
)
from tests.test_nivona_units_domains import _client, _writes


def packets(client):
    return [
        (c.args[0], c.args[1])
        for c in client._protocol.send_and_wait_ack.await_args_list
    ]


def ready_client():
    c = _client("9101")
    c._status = c.brand.parse_status(
        c.capabilities.family_key, struct.pack(">hhhh", 3, 0, 0, 0)
    )
    c._protocol.read_status = AsyncMock(return_value=c._status)
    # Literal saved Lungo fields, used to seed a complete remote recipe.
    saved = {10103: 3, 10104: 2, 10105: 2, 10111: 0, 10112: 120}
    c._protocol.read_numerical = AsyncMock(side_effect=lambda _, r: saved.get(r))
    return c


def test_dedicated_family_catalog_and_slots():
    c = _client("9101")
    caps = c.capabilities
    assert caps.family_key == "9101"
    assert caps.status_profile_key == "ef1109"
    assert caps.my_coffee_slots == 16
    assert caps.first_mycoffee_selector == 13
    assert [r.recipe_id for r in caps.recipes] == list(range(13))
    assert caps.recipe_amount_unit == "mL"
    assert caps.recipe_text_encoding == "utf16_le"
    assert caps.saved_recipe_only_firmwares == ()
    assert caps.verified_maintenance_processes == ()
    assert c.brand.detect_family("91990000000000") is None


@pytest.mark.parametrize(
    "raw,sub,expected",
    [
        (3, 0, None),
        (-1, 0, None),
        (4, 2, SubProcess.GRINDING),
        (4, 4, None),
        (4, 5, SubProcess.COFFEE),
        (4, 8, SubProcess.STEAM),
        (4, 9, SubProcess.WATER),
        (4, 10, None),
    ],
)
def test_incoming_states_do_not_use_8107_maps(raw, sub, expected):
    c = _client("9101")
    status = c.brand.parse_status(
        c.capabilities.family_key, struct.pack(">hhhh", raw, sub, 0, 25)
    )
    assert status.process == (
        MachineProcess.READY if raw in (-1, 3) else MachineProcess.PRODUCT
    )
    assert status.sub_process == expected
    assert status.raw_sub_process == sub


@pytest.mark.parametrize(
    "message,key",
    [
        (8, "wait"),
        (9, "other"),
        (255, "recipe_not_valid"),
        (254, "process_aborted"),
        (123, "unknown"),
    ],
)
def test_nonzero_messages_block_and_cannot_confirm(message, key):
    c = _client("9101")
    s = c.brand.parse_status(
        c.capabilities.family_key, struct.pack(">hhhh", 3, 0, message, 0)
    )
    assert s.message_key == key
    assert not s.is_ready_for_brew()
    assert not c.brand.can_confirm_prompt(s, c.capabilities)


@pytest.mark.parametrize(
    "selector,field,value,register",
    [
        (0, "coffee_amount", 40, 10112),
        (1, "coffee_amount", 40, 10012),
        (9, "water_amount", 500, 10913),
        (5, "milk_foam_amount", 15, 10515),
    ],
)
async def test_standard_storage_and_home_ack(selector, field, value, register):
    c = ready_client()
    assert await c.write_standard_recipe_param(selector, field, value)
    assert packets(c)[0] == ("HE", b"\x00\x62" + bytes(16))
    assert _writes(c) == [(register, value)]


@pytest.mark.parametrize(
    "context,selector,field,value",
    [
        ("standard", 9, "temperature", 1),
        ("standard", 10, "coffee_temperature", 99),
        ("standard", 0, "water_amount", 50),
        ("standard", 5, "milk_foam_amount", 14),
        ("mycoffee", 0, "milk_type", 2),
        ("temporary", 0, "milk_type", 2),
        ("temporary", 10, "profile", 2),
    ],
)
def test_context_domains_reject_wrong_fields_and_choices(
    context, selector, field, value
):
    c = _client("9101")
    with pytest.raises(ValueError):
        c.brand.validate_recipe_param(c.capabilities, context, selector, field, value)


async def test_mycoffee_slot_15_and_no_17th_slot():
    c = ready_client()
    assert await c.write_mycoffee_param(15, "water_amount", 500)
    assert _writes(c) == [(21514, 500)]
    assert not await c.write_mycoffee_param(16, "water_amount", 50)
    c._protocol.send_and_wait_ack.reset_mock()
    assert await c.brew_mycoffee_slot(15)
    assert packets(c) == [
        ("HE", b"\x00\x62" + bytes(16)),
        ("HE", b"\x00\x04\x00\x1c" + bytes(14)),
    ]
    assert not await c.brew_mycoffee_slot(16)


async def test_remote_type_strength_and_selector_are_distinct_and_fully_seeded():
    c = ready_client()
    assert await c.brew_nivona(0, {"coffee_amount": 40, "strength": 4})
    assert packets(c)[0] == ("HE", b"\x00\x62" + bytes(16))
    writes = dict(_writes(c))
    assert writes == {
        9000: 0,
        9001: 4,
        9002: 2,
        9003: 2,
        9004: 0,
        9005: 0,
        9006: 0,
        9007: 1,
        9008: 2,
        9009: 0,
        9010: 40,
        9011: 0,
        9012: 0,
        9013: 0,
    }
    assert packets(c)[-1] == ("HE", b"\x00\x04\x00\x00\x00\x01" + bytes(12))


@pytest.mark.parametrize("operation", ["brew", "mycoffee", "standard", "setting"])
async def test_rejected_home_prerequisite_aborts_dependent_operation(operation):
    c = ready_client()
    c._protocol.send_and_wait_ack.return_value = False
    result = await {
        "brew": lambda: c.brew_nivona(0, {"strength": 4}),
        "mycoffee": lambda: c.brew_mycoffee_slot(0),
        "standard": lambda: c.write_standard_recipe_param(0, "strength", 4),
        "setting": lambda: c.write_setting(119, 3),
    }[operation]()
    assert not result
    assert packets(c) == [("HE", b"\x00\x62" + bytes(16))]
    assert c._protocol.send_and_wait_ack.call_args.kwargs["retries"] == 1


def test_settings_stats_and_effective_cares_are_independent():
    caps = _client("9101").capabilities
    settings = {s.setting_id: s for s in caps.settings}
    assert 105 not in settings  # no inherited coffee-temperature control
    assert dict(settings[102].options)[60] == "1 h"
    assert dict(settings[126].options)[128] == "Spectrum"
    stats = {s.stat_id: s for s in caps.stats}
    assert stats[213].key == "total_beverages"
    assert stats[238].key == "remote_starts"
    assert stats[244].key == "milling_operations"
    assert {i for i in stats if i >= 600} == {600, 610, 620, 640, 650}


async def test_chilled_remote_uses_profile_five_without_8107_temperature_99():
    c = ready_client()
    saved = {11003: 3, 11004: 5, 11011: 0, 11012: 120}
    c._protocol.read_numerical = AsyncMock(side_effect=lambda _, r: saved.get(r))
    assert await c.brew_nivona(10, {"coffee_amount": 60})
    assert dict(_writes(c))[9000] == 10
    assert dict(_writes(c))[9002] == 5
    assert dict(_writes(c))[9003] == 1
    assert packets(c)[-1] == ("HE", b"\x00\x04\x00\x00\x00\x01" + bytes(12))


@pytest.mark.parametrize("failure", ["type", "field", "missing_saved"])
async def test_remote_failure_never_starts_a_partial_recipe(failure):
    c = ready_client()
    if failure == "missing_saved":
        c._protocol.read_numerical.return_value = None
        c._protocol.read_numerical.side_effect = None
    else:

        async def ack(command, payload, *args, **kwargs):
            return command != "HW" or struct.unpack(">hi", payload)[0] != (
                9000 if failure == "type" else 9002
            )

        c._protocol.send_and_wait_ack.side_effect = ack
    assert not await c.brew_nivona(0, {"strength": 4})
    assert sum(command == "HE" for command, _ in packets(c)) == 1


async def test_unknown_selector_and_numeric_name_are_rejected_before_io():
    c = ready_client()
    with pytest.raises(ValueError):
        await c.brew_nivona(13)
    with pytest.raises(ValueError):
        await c.write_mycoffee_param(0, "name", 10)
    assert packets(c) == []


async def test_unknown_current_state_prevents_write_preparation():
    c = ready_client()
    c._protocol.read_status.return_value = None
    assert not await c.write_setting(119, 2)
    assert packets(c) == []


@pytest.mark.parametrize("selector",[2,12])
async def test_saved_americano_water_domain_is_not_the_remote_500_ml_domain(selector):
    c=ready_client()
    assert await c.write_standard_recipe_param(selector,"water_amount",240)
    c._protocol.send_and_wait_ack.reset_mock()
    for value in (241,500):
        with pytest.raises(ValueError):
            await c.write_standard_recipe_param(selector,"water_amount",value)
    assert packets(c)==[]


async def test_obsolete_generic_controls_preserve_registry_customization(hass):
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from custom_components.melitta_barista.const import DOMAIN
    from custom_components.melitta_barista.number import async_setup_entry as setup_numbers
    from custom_components.melitta_barista.switch import async_setup_entry as setup_switches
    c=ready_client();entry=MockConfigEntry(domain=DOMAIN,data={"address":c.address})
    entry.add_to_hass(hass);entry.runtime_data=c
    reg=er.async_get(hass)
    old_numbers=reg.async_get_or_create("number",DOMAIN,f"{c.address}_setting_22",config_entry=entry)
    old_switch=reg.async_get_or_create("switch",DOMAIN,f"{c.address}_switch_16",config_entry=entry)
    for old in (old_numbers,old_switch):
        reg.async_update_entity(old.entity_id,name="My customization")
    await setup_numbers(hass,entry,lambda entities:None)
    switches=[]
    await setup_switches(hass,entry,switches.extend)
    for old in (old_numbers,old_switch):
        kept=reg.async_get(old.entity_id)
        assert kept is not None
        assert kept.name=="My customization"
        assert kept.disabled_by==er.RegistryEntryDisabler.INTEGRATION
    assert switches==[]


async def test_unsupported_generic_switch_is_unavailable_and_rejects_service():
    from homeassistant.exceptions import ServiceValidationError
    from custom_components.melitta_barista.switch import MelittaSettingSwitch, SWITCH_DEFINITIONS
    from unittest.mock import MagicMock
    c=ready_client()
    entity=MelittaSettingSwitch(c,MagicMock(),"Nivona",SWITCH_DEFINITIONS[0])
    assert not entity.available
    with pytest.raises(ServiceValidationError):
        await entity.async_turn_on()
    assert packets(c)==[]


def test_9101_keeps_unsupported_brand_select_migration_keys():
    assert "coffee_temperature" in _client("9101").capabilities.unsupported_setting_keys
