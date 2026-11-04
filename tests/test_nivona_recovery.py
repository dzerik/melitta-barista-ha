"""Regression coverage for NICR 795 identification and statistics (#59)."""

import struct

import pytest
from unittest.mock import AsyncMock, patch

from bleak.backends.device import BLEDevice
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.melitta_barista.ble_client import (
    MelittaBleClient, resolve_caps_from_scanner,
)
from custom_components.melitta_barista.brands import get_profile
from custom_components.melitta_barista.const import DOMAIN, MachineProcess
from custom_components.melitta_barista.protocol import MachineStatus, MelittaProtocol
from tests import MOCK_ADDRESS, MOCK_CONFIG_DATA
from tests.test_sensor import _mock_client, _setup_integration


async def test_hl_identity_resolves_795_when_advertisement_has_no_model(mock_bleak_client):
    c = MelittaBleClient(MOCK_ADDRESS, device_name="Nivona NICR 79x", brand=get_profile("nivona"))
    c._client = mock_bleak_client
    c._try_connect_and_handshake = AsyncMock(return_value=True)
    c._dis_info = {"model": "EF-BTLE"}
    c._read_dis_service = AsyncMock()
    c._load_post_connect_data = AsyncMock()
    c._protocol.read_version = AsyncMock(return_value="0253C021B15")
    c._protocol.read_serial = AsyncMock(return_value="795000000000000-----")
    c._protocol.read_features = AsyncMock(return_value=None)
    c._protocol.read_numerical = AsyncMock(return_value=None)
    try:
        assert await c.connect()
        assert c.capabilities is not None, "HL model prefix 795 was ignored"
        assert c.capabilities.family_key == "79x"
        assert c.capabilities.my_coffee_slots == 5
    finally:
        await c.disconnect()


async def test_serial_display_removes_vendor_padding():
    p = MelittaProtocol(brand=get_profile("nivona"))
    p.send_and_wait_response = AsyncMock(return_value=b"795000000000000-----")
    serial = await p.read_serial(AsyncMock())
    assert serial[-1:].isdigit(), "HL padding remains visible"


def test_early_scanner_resolution_retains_model_specific_fifth_slot(hass):
    brand = get_profile("nivona")
    device = BLEDevice(MOCK_ADDRESS, "NIVONA-795000000000000", {})
    assert brand.capabilities_for_model(device.name).my_coffee_slots == 5
    with patch(
        "custom_components.melitta_barista.bluetooth.async_ble_device_from_address",
        return_value=device,
    ):
        caps = resolve_caps_from_scanner(hass, MOCK_ADDRESS, brand)
    assert caps.my_coffee_slots == 5, "Early family lookup discarded model overrides"


async def _setup_795(hass, read):
    client = _mock_client()
    client.brand = get_profile("nivona")
    client.capabilities = client.brand.capabilities_for_model("795000000000000")
    client.write_setting = AsyncMock(return_value=True)
    client.read_setting = AsyncMock(side_effect=read)
    callbacks = []
    client.add_status_callback.side_effect = callbacks.append
    entry = MockConfigEntry(domain=DOMAIN, data={**MOCK_CONFIG_DATA, "brand": "nivona"}, version=3)
    await _setup_integration(hass, entry, client)
    return client, callbacks


def _stat_entity(hass, key):
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{MOCK_ADDRESS}_stat_{key}")
    assert entity_id is not None
    return hass.states.get(entity_id)


async def test_packed_cleaning_stat_exposes_low_word(hass):
    # HR 214 = 0x015b0006 in attachment 1; high word mirrors HR 620 (347).
    async def read(register):
        return 0x015B0006 if register == 214 else 0
    await _setup_795(hass, read)
    state = _stat_entity(hass, "clean_coffee_system")
    assert state.state == "6", f"Packed register exposed as {state.state} instead of cleaning count 6"


async def test_statistics_update_when_machine_finishes_brewing(hass):
    count = [100]
    async def read(register):
        return count[0] if register == 213 else 0
    client, callbacks = await _setup_795(hass, read)
    assert _stat_entity(hass, "total_beverages").state == "100"
    client.status = MachineStatus(process=MachineProcess.PRODUCT)
    for callback in callbacks:
        callback(client.status)
    count[0] = 101
    client.status = MachineStatus(process=MachineProcess.READY)
    for callback in callbacks:
        callback(client.status)
    await hass.async_block_till_done()
    assert _stat_entity(hass, "total_beverages").state == "101", "Statistics stayed stale after PRODUCT to READY"


async def test_late_model_resolution_creates_mycoffee_buttons(hass):
    client = _mock_client()
    client.brand = get_profile("nivona")
    client.capabilities = None
    client.write_setting = AsyncMock(return_value=True)
    client.read_setting = AsyncMock(return_value=None)
    connected_callbacks = []
    client.add_connection_callback.side_effect = connected_callbacks.append
    entry = MockConfigEntry(domain=DOMAIN, data={**MOCK_CONFIG_DATA, "brand": "nivona"}, version=3)
    with patch(
        "custom_components.melitta_barista.bluetooth.async_ble_device_from_address",
        return_value=None,
    ):
        await _setup_integration(hass, entry, client)
    client.capabilities = client.brand.capabilities_for_model("795000000000000")
    for callback in list(connected_callbacks):
        callback(True)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    mycoffee = [
        e for e in registry.entities.values()
        if e.domain == "button" and "_brew_mycoffee_slot_" in e.unique_id
    ]
    assert len(mycoffee) == 5, "MyCoffee buttons were never added after capabilities resolved"


def test_control_serial_prefix_already_has_five_slot_model_support():
    caps = get_profile("nivona").capabilities_for_model("795000000000000-----")
    assert caps.family_key == "79x"
    assert caps.my_coffee_slots == 5


def test_control_ready_and_brew_status_are_already_recognized():
    brand = get_profile("nivona")
    assert brand.parse_status("79x", struct.pack(">hhhh", 8, 0, 0, 0)).is_ready
    assert brand.parse_status("79x", struct.pack(">hhhh", 11, 0, 0, 0)).is_brewing


async def test_late_hr_reply_cannot_supply_another_stat():
    p = MelittaProtocol(brand=get_profile("nivona"), frame_timeout=0.01)
    writes = [0]
    async def respond(_):
        writes[0] += 1
        if writes[0] == 2:
            p.on_ble_data(p.build_frame("HR", struct.pack(">hi", 217, 0), include_key_prefix=False))
    assert await p.read_numerical(respond, 217) is None
    result = await p.read_numerical(respond, 214)
    assert result is None, "A late HR 217 zero was accepted as the HR 214 cleaning counter"


@pytest.mark.parametrize(('register', 'key', 'raw', 'expected'), [
    (209, 'steam_drinks', 0x0B5C09AE, 2478),
    (210, 'powder_coffee', 0x08DB0000, 0),
    (214, 'clean_coffee_system', 0x015F0006, 6),
    (215, 'clean_frother', 0x00FF000B, 11),
    (219, 'filter_changes', 0x08DB0007, 7),
    (220, 'descaling', 0x00AA0006, 6),
])
async def test_all_observed_packed_stats(hass, register, key, raw, expected):
    await _setup_795(hass, lambda reg: raw if reg == register else 0)
    assert _stat_entity(hass, key).state == str(expected)


async def test_usage_gauges_have_no_percentage_or_total_state_class(hass):
    await _setup_795(hass, lambda _: 347)
    for key in ('descale_percent', 'brew_unit_clean_percent', 'frother_clean_percent', 'filter_percent'):
        state = _stat_entity(hass, key)
        assert state.state == '347'
        assert state.attributes.get('unit_of_measurement') is None
        assert state.attributes.get('state_class') is None


async def test_corrected_labels_preserve_registered_entity_ids(hass):
    registry = er.async_get(hass)
    old_ids = {}
    for key in ('lungo', 'americano', 'single_cup', 'total_beverages'):
        old_ids[key] = registry.async_get_or_create(
            'sensor', DOMAIN, f'{MOCK_ADDRESS}_stat_{key}',
            suggested_object_id=f'existing_{key}',
        ).entity_id
    await _setup_795(hass, lambda reg: reg)
    for key, register in [('lungo', 202), ('americano', 203), ('single_cup', 211), ('total_beverages', 213)]:
        state = _stat_entity(hass, key)
        assert state.entity_id == old_ids[key]
        assert state.state == str(register)
    assert 'Americano' in _stat_entity(hass, 'lungo').name
    assert 'Cappuccino' in _stat_entity(hass, 'americano').name
    assert 'Total coffee brews' in _stat_entity(hass, 'total_beverages').name


@pytest.mark.parametrize('initial_family_known', [False, True])
async def test_refined_capabilities_add_entities_once(hass, initial_family_known):
    client = _mock_client()
    client.brand = get_profile('nivona')
    client.capabilities = client.brand.capabilities_for('79x') if initial_family_known else None
    client.write_setting = AsyncMock(return_value=True)
    client.read_setting = AsyncMock(return_value=0)
    callbacks = []
    client.add_connection_callback.side_effect = callbacks.append
    entry = MockConfigEntry(domain=DOMAIN, data={**MOCK_CONFIG_DATA, 'brand': 'nivona'}, version=3)
    await _setup_integration(hass, entry, client)
    client.capabilities = client.brand.capabilities_for_model('795000000000000')
    for _ in range(2):
        for callback in list(callbacks):
            callback(True)
        await hass.async_block_till_done()
    entities = list(er.async_get(hass).entities.values())
    buttons = [e for e in entities if e.domain == 'button' and '_brew_mycoffee_slot_' in e.unique_id]
    amounts = [e for e in entities if e.domain == 'sensor' and '_mycoffee_slot_' in e.unique_id]
    assert len(buttons) == 5
    assert len(amounts) == 5 * 7
    assert len({e.unique_id for e in amounts + buttons}) == 40


@pytest.mark.parametrize(('override', 'expected_family', 'slots'), [
    ('79x', '79x', 5),
    ('700', '700', 4),
])
def test_identity_refinement_respects_family_override(override, expected_family, slots):
    client = MelittaBleClient(MOCK_ADDRESS, brand=get_profile('nivona'), family_override=override)
    client._serial = '795000000000000'
    caps = client._resolve_capabilities()
    assert caps.family_key == expected_family
    assert caps.my_coffee_slots == slots


def test_generic_identity_does_not_mask_advertisement_model():
    client = MelittaBleClient(
        MOCK_ADDRESS, device_name='Kitchen coffee', brand=get_profile('nivona'),
        ble_device=BLEDevice(MOCK_ADDRESS, 'NIVONA-795000000000000', {}),
    )
    client._serial = 'generic'
    client._dis_info = {'model': 'NICR 79x', 'serial': '795000000000000'}
    assert client._resolve_capabilities().my_coffee_slots == 5


async def _finish_operation(hass, client, callbacks):
    for process in (MachineProcess.PRODUCT, MachineProcess.READY):
        client.status = MachineStatus(process=process)
        for callback in list(callbacks):
            callback(client.status)
    await hass.async_block_till_done()


async def test_failed_reads_keep_last_value_but_real_resets_are_accepted(hass):
    count = [100]
    client, callbacks = await _setup_795(hass, lambda reg: count[0] if reg == 213 else 0)
    for raw, expected in [(None, '100'), (0, '0'), (7, '7'), (3, '3')]:
        count[0] = raw
        await _finish_operation(hass, client, callbacks)
        assert _stat_entity(hass, 'total_beverages').state == expected


async def test_status_polls_do_not_reread_all_stats(hass):
    client, callbacks = await _setup_795(hass, lambda _: 0)
    stat_ids = {d.stat_id for d in client.capabilities.stats}
    assert sum(call.args[0] in stat_ids for call in client.read_setting.await_args_list) == len(stat_ids)
    client.read_setting.reset_mock()
    for _ in range(10):
        client.status = MachineStatus(process=MachineProcess.READY)
        for callback in callbacks:
            callback(client.status)
    await hass.async_block_till_done()
    assert not any(call.args[0] in stat_ids for call in client.read_setting.await_args_list)


async def test_statistics_refresh_periodically(hass):
    from datetime import timedelta
    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    count = [100]
    client, _ = await _setup_795(hass, lambda reg: count[0] if reg == 213 else 0)
    count[0] = 101
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=5, seconds=1))
    await hass.async_block_till_done()
    assert _stat_entity(hass, 'total_beverages').state == '101'


@pytest.mark.parametrize('command', ['HR', 'HA'])
async def test_wrong_register_reply_is_ignored_until_correct_reply_arrives(command):
    p = MelittaProtocol(brand=get_profile('nivona'), frame_timeout=0.05)
    def payload(register, value):
        if command == 'HR':
            return struct.pack('>hi', register, value)
        return struct.pack('>h', register) + str(value).encode().ljust(64, b'\0')
    async def respond(_):
        for register, value in [(217, 0), (214, 6)]:
            p.on_ble_data(p.build_frame(command, payload(register, value), include_key_prefix=False))
    read = p.read_numerical if command == 'HR' else p.read_alphanumeric
    assert await read(respond, 214) == (6 if command == 'HR' else '6')


@pytest.mark.parametrize(('override', 'slots'), [('700', 4), ('79x', 5)])
async def test_early_entities_honor_family_override(hass, override, slots):
    """Scanner model refinement must not bypass the user's family choice."""
    from custom_components.melitta_barista.button import async_setup_entry as setup_buttons
    from custom_components.melitta_barista.sensor import async_setup_entry as setup_sensors

    client = _mock_client()
    client.brand = get_profile('nivona')
    client.capabilities = None
    entry = MockConfigEntry(
        domain=DOMAIN, data={**MOCK_CONFIG_DATA, 'brand': 'nivona'},
        options={'family_override': override}, version=3,
    )
    entry.runtime_data = client
    buttons, sensors = [], []
    with patch(
        'custom_components.melitta_barista.bluetooth.async_ble_device_from_address',
        return_value=BLEDevice(MOCK_ADDRESS, 'NIVONA-795000000000000', {}),
    ):
        await setup_buttons(hass, entry, buttons.extend)
        await setup_sensors(hass, entry, sensors.extend)
    try:
        assert len([e for e in buttons if '_brew_mycoffee_slot_' in e.unique_id]) == slots
        assert len([e for e in sensors if '_mycoffee_slot_' in e.unique_id]) == slots * 7
    finally:
        # These platform-only entities were never added to HA; run their
        # entry cleanup so the coordinator does not retain its timer.
        await entry._async_process_on_unload(hass)
