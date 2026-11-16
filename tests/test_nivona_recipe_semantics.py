"""Regression coverage for model-specific Nivona recipe command semantics."""

import struct
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.melitta_barista.ble_client import MelittaBleClient
from custom_components.melitta_barista.brands import get_profile
from tests import MOCK_ADDRESS


def _client(model, firmware=None):
    client = MelittaBleClient(MOCK_ADDRESS, brand=get_profile("nivona"))
    client._capabilities = client.brand.capabilities_for_model(f"{model}000000000000")
    client._firmware = firmware
    client._connected = True
    client._client = MagicMock(is_connected=True)
    client.start_polling = MagicMock()
    client._protocol.send_and_wait_ack = AsyncMock(return_value=True)
    return client


def _packets(client):
    return [(call.args[0], call.args[1]) for call in client._protocol.send_and_wait_ack.await_args_list]


@pytest.mark.parametrize("model,selector,register", [
    ("920", 7, 9006), ("930", 7, 9006),
    ("960", 7, 9007), ("965", 7, 9007), ("970", 7, 9007),
    ("960", 6, 9008), ("965", 6, 9008), ("970", 6, 9008),
])
async def test_9xx_temperature_register_follows_physical_model(model, selector, register):
    """Vendor family names and integration family aliases must not be transposed."""
    client = _client(model)
    with patch("asyncio.sleep", AsyncMock()):
        assert await client.brew_nivona(selector, {"temperature": 2})
    assert _packets(client)[:-1] == [
        ("HW", struct.pack(">hi", 9001, selector)),
        ("HW", struct.pack(">hi", register, 2)),
    ]


@pytest.mark.parametrize("model", ["920", "930"])
async def test_920_930_hot_milk_has_no_temperature_override(model):
    """These models lack a pure-milk temperature control despite sharing offsets."""
    client = _client(model)
    with pytest.raises(ValueError, match="unsupported"):
        await client.brew_nivona(6, {"temperature": 2})
    assert _packets(client) == []


@pytest.mark.parametrize("model,selector,firmware,mode", [
    ("030", 8, "1040A015G15", 1),
    ("030", 9, None, 1),
    ("040", 8, "1040A015G15", 1),
    ("8107", 8, "1040A018G18", 1),
    ("8107", 9, None, 1),
    ("8103", 8, "1040A015G15", 1),
])
async def test_temporary_recipe_mode_uses_model_catalog_and_exact_firmware(model, selector, firmware, mode):
    """Milk IDs and unlisted/unknown firmware must not trigger chilled fallback."""
    client = _client(model, firmware)
    with patch("asyncio.sleep", AsyncMock()):
        assert await client.brew_nivona(selector, {"two_cups": 1})
    packets = _packets(client)
    expected_writes = [
        ("HW", struct.pack(">hi", 9001, selector)),
        ("HW", struct.pack(">hi", 9005, 1)),
    ]
    if model == "8107" and mode == 1:
        expected_writes.append(("HW", struct.pack(">hi", 9004, 99)))
    assert packets[:-1] == expected_writes
    assert packets[-1][0] == "HE"
    assert packets[-1][1][3] == selector
    assert packets[-1][1][5] == mode


@pytest.mark.parametrize("model,slot,selector", [
    ("970", 0, 8), ("970", 8, 16),
    ("960", 0, 8), ("965", 0, 8),
    ("030", 0, 10), ("030", 17, 27),
    ("040", 0, 10), ("040", 17, 27),
    ("930", 0, 20), ("8103", 8, 28), ("795", 4, 24),
])
async def test_mycoffee_slot_uses_model_selector_base(model, slot, selector):
    """A slot addresses the model's MyCoffee selector range, in saved mode."""
    client = _client(model)
    assert await client.brew_mycoffee_slot(slot)
    expected = bytearray.fromhex("000b00000000000000000000000000000000")
    expected[1] = 4 if model == "8103" else 11
    expected[3] = selector
    assert _packets(client) == [("HE", bytes(expected))]


@pytest.mark.parametrize("model,selector,value,register", [
    ("795", 0, 2, 9004),
    ("930", 0, 2, 9006), ("970", 0, 2, 9006),
    ("030", 0, 2, 9006), ("040", 0, 2, 9006),
    ("030", 3, 1, 9006), ("040", 5, 2, 9006),
    ("930", 7, 2, 9006), ("970", 7, 2, 9007),
    ("030", 6, 2, 9007), ("040", 6, 2, 9007),
    ("970", 6, 2, 9008), ("040", 7, 2, 9008),
    ("040", 8, 1, 9009),
])
async def test_legacy_temperature_preset_reaches_recipe_process_register(model, selector, value, register):
    """The preserved 0..2 preset is a raw level of the recipe's primary process."""
    client = _client(model)
    with patch("asyncio.sleep", AsyncMock()):
        assert await client.brew_nivona(selector, {"temperature": value})
    expected = bytearray.fromhex("000b00000001000000000000000000000000")
    expected[3] = selector
    assert _packets(client) == [
        ("HW", struct.pack(">hi", 9001, selector)),
        ("HW", struct.pack(">hi", register, value)),
        ("HE", bytes(expected)),
    ]


@pytest.mark.parametrize("model,selector,value", [
    ("930", 6, 1), ("030", 7, 1), ("030", 8, 1), ("030", 9, 1),
    ("040", 8, 2), ("040", 0, 3), ("930", 0, -1), ("8107", 8, 1),
])
async def test_unsupported_temperature_fails_before_any_machine_command(model, selector, value):
    """Reject absent temperature controls and invalid levels instead of dropping them."""
    client = _client(model)
    with pytest.raises(ValueError, match="temperature"):
        await client.brew_nivona(selector, {"temperature": value})
    assert _packets(client) == []


@pytest.mark.parametrize("model,recipe_name", [("930", "Hot Milk"), ("030", "Frothy Milk")])
async def test_native_other_override_omits_untouched_unsupported_temperature(hass, model, recipe_name):
    """The native default temperature must not prevent a supported milk override."""
    from tests.test_nivona_two_cups import _native_controls

    client = _client(model)
    numbers, button = await _native_controls(hass, client)
    # Find the actual registered recipe selector without assuming its entity ID.
    from homeassistant.helpers import entity_registry as er
    for entity_id, entity in er.async_get(hass).entities.items():
        if entity.unique_id == f"{client.address}_nivona_recipe_select":
            hass.states.async_set(entity_id, recipe_name)
    # Exercise a dimensionless override independently of fluid-unit mappings.
    await numbers["two_cups"].async_set_native_value(1)
    with patch("asyncio.sleep", AsyncMock()):
        await button.async_press()
    packets = _packets(client)
    assert ("HW", struct.pack(">hi", 9005, 1)) in packets
    assert packets[-1][0] == "HE"
    assert packets[-1][1][5] == 1
    assert all(not (cmd == "HW" and struct.unpack(">h", payload[:2])[0] in (9006, 9007, 9008, 9009)) for cmd, payload in packets)


async def test_native_explicit_unsupported_temperature_reports_validation_error(hass):
    """A temperature-only edit must fail visibly before starting a different brew."""
    from homeassistant.exceptions import ServiceValidationError
    from homeassistant.helpers import entity_registry as er
    from tests.test_nivona_two_cups import _native_controls

    client = _client("030")
    numbers, button = await _native_controls(hass, client)
    for entity_id, entity in er.async_get(hass).entities.items():
        if entity.unique_id == f"{client.address}_nivona_recipe_select":
            hass.states.async_set(entity_id, "Frothy Milk")
    await numbers["temperature"].async_set_native_value(2)
    with pytest.raises(ServiceValidationError, match="temperature"):
        await button.async_press()
    assert _packets(client) == []


@pytest.mark.parametrize("selector", [8, 9, 10])
@pytest.mark.parametrize("firmware", ["1040A015G15", "1040A016G16", "1040A017G17"])
async def test_saved_chilled_recipe_remains_available_on_affected_firmware(selector, firmware):
    """No override request uses the machine's saved chilled recipe."""
    client = _client("8107", firmware)
    assert await client.brew_nivona(selector)
    expected = bytearray.fromhex("000400000000000000000000000000000000")
    expected[3] = selector
    assert _packets(client) == [("HE", bytes(expected))]


@pytest.mark.parametrize("selector", [8, 9, 10])
@pytest.mark.parametrize("firmware", ["1040A015G15", "1040A016G16", "1040A017G17"])
async def test_affected_chilled_firmware_rejects_overrides_before_commands(selector, firmware):
    """A saved-only firmware must not silently discard requested overrides."""
    client = _client("8107", firmware)
    with pytest.raises(ValueError, match="overrides.*unsupported"):
        await client.brew_nivona(selector, {"two_cups": 1})
    assert _packets(client) == []


async def test_chilled_temperature_marker_is_enforced_last_without_mutating_request():
    """A caller cannot replace or reorder the required chilled temperature marker."""
    client = _client("8107", "1040A018G18")
    overrides = {"chilled_temperature": 1, "two_cups": 1}
    with patch("asyncio.sleep", AsyncMock()):
        assert await client.brew_nivona(8, overrides)
    assert overrides == {"chilled_temperature": 1, "two_cups": 1}
    assert _packets(client)[-2:] == [
        ("HW", struct.pack(">hi", 9004, 99)),
        ("HE", bytes.fromhex("000400080001000000000000000000000000")),
    ]
