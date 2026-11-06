"""Capability controls must survive an offline Home Assistant startup."""

from types import SimpleNamespace
from dataclasses import replace
import logging
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_component import EntityComponent
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.melitta_barista import number, select, switch
from custom_components.melitta_barista.brands import MelittaProfile, NivonaProfile
from custom_components.melitta_barista.const import CONF_FAMILY_OVERRIDE, DOMAIN

from . import MOCK_ADDRESS, MOCK_CONFIG_DATA


def _client(caps=None, *, brand=None):
    """Keep the local connection subscription lifecycle real without BLE I/O."""
    listeners = []
    return SimpleNamespace(
        address=MOCK_ADDRESS, brand=brand or NivonaProfile(), capabilities=caps,
        connected=caps is not None, machine_type=None,
        model_name="Coffee Machine", firmware_version="test",
        add_connection_callback=listeners.append,
        remove_connection_callback=listeners.remove, listeners=listeners,
        read_setting=AsyncMock(return_value=7),
    )


async def _setup(hass, platform, client, *, options=None, scanner_caps=None):
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG_DATA, options=options or {})
    entry.add_to_hass(hass)
    entry.runtime_data = client
    entities = []
    with patch.object(platform, "resolve_caps_from_scanner", return_value=scanner_caps, create=True):
        await platform.async_setup_entry(hass, entry, entities.extend)
    return entry, entities


def _connect(client, caps):
    client.capabilities = caps
    client.connected = True
    for listener in tuple(client.listeners):
        listener(True)


@pytest.mark.parametrize("platform,expected", [
    (number, {"brew_strength", "brew_temperature", "brand_setting_111", "brand_setting_112"}),
    (select, {"setting_water_hardness", "setting_auto_off", "setting_tank_light"}),
])
async def test_controls_appear_once_after_offline_start(hass, platform, expected):
    """Missing model controls appear after discovery without an entry reload."""
    client = _client()
    entry, entities = await _setup(hass, platform, client)
    assert all(f"{MOCK_ADDRESS}_{suffix}" not in {e.unique_id for e in entities} for suffix in expected)
    for listener in tuple(client.listeners):
        listener(False)
        listener(True)  # A connect with no resolved family is not enough.
    original_count = len(entities)
    caps = client.brand.capabilities_for("900-light")
    _connect(client, caps)
    assert {f"{MOCK_ADDRESS}_{suffix}" for suffix in expected} <= {e.unique_id for e in entities}
    assert len(entities) > original_count
    count = len(entities)
    _connect(client, caps)
    assert len(entities) == count
    assert len({e.unique_id for e in entities}) == count
    await entry._async_process_on_unload(hass)
    assert client.listeners == []
    _connect(client, client.brand.capabilities_for("1030"))
    assert len(entities) == count


@pytest.mark.parametrize("platform,domain,suffix,raw,expected", [
    (number, "number", "brand_setting_111", 7, "7.0"),
    (select, "select", "setting_tank_light_brightness", 2, "high"),
])
async def test_late_controls_publish_state_without_another_reconnect(
    hass, platform, domain, suffix, raw, expected,
):
    """Exercise HA registration as well as callbacks after an offline startup."""
    client = _client()
    client.read_setting.return_value = raw
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG_DATA)
    entry.add_to_hass(hass)
    entry.runtime_data = client
    component = EntityComponent(logging.getLogger(__name__), domain, hass)
    added = []

    def add_entities(entities):
        added.extend(entities)
        hass.async_create_task(component.async_add_entities(entities))

    with patch.object(platform, "resolve_caps_from_scanner", return_value=None):
        await platform.async_setup_entry(hass, entry, add_entities)
    await hass.async_block_till_done()
    caps = client.brand.capabilities_for("900-light")
    _connect(client, caps)
    await hass.async_block_till_done()
    uid = f"{MOCK_ADDRESS}_{suffix}"
    entity = next(entity for entity in added if entity.unique_id == uid)
    state = hass.states.get(entity.entity_id)
    assert state is not None
    assert state.state == expected
    count = len(added)
    _connect(client, caps)
    await hass.async_block_till_done()
    assert len(added) == count
    for entity in added:
        await component.async_remove_entity(entity.entity_id)
    await entry._async_process_on_unload(hass)
    assert client.listeners == []


@pytest.mark.parametrize("platform,suffix", [
    (number, "brand_setting_111"), (select, "setting_tank_light"),
])
@pytest.mark.parametrize("source", ["live", "scanner"])
async def test_known_or_scanner_capabilities_register_initial_controls(hass, platform, suffix, source):
    """The scanner fallback also supplies model-specific numeric settings."""
    caps = NivonaProfile().capabilities_for("900-light")
    client = _client(caps if source == "live" else None)
    entry, entities = await _setup(
        hass, platform, client, options={CONF_FAMILY_OVERRIDE: "900-light"}, scanner_caps=caps,
    )
    assert f"{MOCK_ADDRESS}_{suffix}" in {e.unique_id for e in entities}
    count = len(entities)
    _connect(client, caps)
    assert len(entities) == count
    await entry._async_process_on_unload(hass)


@pytest.mark.parametrize("kind", ["generic_number", "brand_number", "generic_switch"])
async def test_connected_setting_reads_when_entity_is_added(hass, kind):
    """Entities added after the connect notification must not wait for reconnect."""
    client = _client(NivonaProfile().capabilities_for("900-light"))
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG_DATA)
    if kind == "generic_number":
        entity = number.MelittaSettingNumber(client, entry, "Coffee", number.SETTING_DEFINITIONS[0])
    elif kind == "brand_number":
        descriptor = next(d for d in client.capabilities.settings if d.setting_id == 111)
        entity = number.BrandSettingNumber(client, entry, "Coffee", descriptor)
    else:
        entity = switch.MelittaSettingSwitch(client, entry, "Coffee", switch.SWITCH_DEFINITIONS[0])
    entity.hass = hass
    entity.async_write_ha_state = lambda: None
    await entity.async_added_to_hass()
    assert entity.is_on is True if kind == "generic_switch" else entity.native_value == 7
    await entity.async_will_remove_from_hass()
    assert client.listeners == []


@pytest.mark.parametrize("platform,domain,suffix", [
    (number, "number", "setting_14"), (switch, "switch", "switch_11"),
])
async def test_late_exclusions_preserve_user_disabled_registry_entries(hass, platform, domain, suffix):
    """Late family discovery disables only stale entries enabled by the integration."""
    client = _client()
    entry, _ = await _setup(hass, platform, client)
    registry = er.async_get(hass)
    stale = registry.async_get_or_create(
        domain, DOMAIN, f"{MOCK_ADDRESS}_{suffix}", config_entry=entry,
    )
    caps = replace(client.brand.capabilities_for("900-light"), unsupported_generic_setting_ids=frozenset({11, 14}))
    _connect(client, caps)
    assert registry.async_get(stale.entity_id).disabled_by == er.RegistryEntryDisabler.INTEGRATION
    registry.async_update_entity(stale.entity_id, disabled_by=er.RegistryEntryDisabler.USER)
    _connect(client, caps)
    assert registry.async_get(stale.entity_id).disabled_by == er.RegistryEntryDisabler.USER
    await entry._async_process_on_unload(hass)


@pytest.mark.parametrize("platform,suffix", [
    (number, "brand_setting_111"), (select, "setting_tank_light"),
])
async def test_explicit_family_override_works_without_advertisement(hass, platform, suffix):
    """An offline machine still gets controls for its explicitly selected family."""
    client = _client()
    entry = MockConfigEntry(
        domain=DOMAIN, data=MOCK_CONFIG_DATA,
        options={CONF_FAMILY_OVERRIDE: "900-light"},
    )
    entry.add_to_hass(hass)
    entry.runtime_data = client
    entities = []
    with patch(
        "homeassistant.components.bluetooth.async_ble_device_from_address",
        return_value=None,
    ):
        await platform.async_setup_entry(hass, entry, entities.extend)
    assert f"{MOCK_ADDRESS}_{suffix}" in {entity.unique_id for entity in entities}
    await entry._async_process_on_unload(hass)


@pytest.mark.parametrize("platform", [number, select, switch])
async def test_melitta_reconnect_preserves_static_entity_ids(hass, platform):
    """The late-registration path leaves Melitta's established controls intact."""
    brand = MelittaProfile()
    client = _client(brand.capabilities_for("barista_ts"), brand=brand)
    entry, entities = await _setup(hass, platform, client)
    before = [entity.unique_id for entity in entities]
    _connect(client, client.capabilities)
    assert [entity.unique_id for entity in entities] == before
    await entry._async_process_on_unload(hass)
