"""Unverified native maintenance actions must never emit a command (#68)."""

import struct
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.melitta_barista.ble_client import MelittaBleClient
from custom_components.melitta_barista.brands import get_profile
from custom_components.melitta_barista.button import MelittaMaintenanceButton
from custom_components.melitta_barista.const import DOMAIN, MachineProcess
from custom_components.melitta_barista.protocol import MachineStatus
from tests import MOCK_ADDRESS, MOCK_CONFIG_DATA


MAINTENANCE_ACTIONS = [
    ("switch_off", MachineProcess.SWITCH_OFF),
    ("start_easy_clean", MachineProcess.EASY_CLEAN),
    ("start_intensive_clean", MachineProcess.INTENSIVE_CLEAN),
    ("start_descaling", MachineProcess.DESCALING),
    ("start_filter_insert", MachineProcess.FILTER_INSERT),
    ("start_filter_replace", MachineProcess.FILTER_REPLACE),
    ("start_filter_remove", MachineProcess.FILTER_REMOVE),
    ("start_evaporating", MachineProcess.EVAPORATING),
]


def _client(brand, family):
    client = MelittaBleClient(MOCK_ADDRESS, brand=get_profile(brand))
    client._capabilities = client.brand.capabilities_for(family)
    client._connected = True
    client._client = MagicMock(is_connected=True)
    client._status = MachineStatus(process=MachineProcess.READY)
    client._protocol.send_and_wait_ack = AsyncMock(return_value=True)
    return client


@pytest.mark.parametrize("family", ["600", "700", "79x", "900", "900-light", "1030", "1040", "8000"])
@pytest.mark.parametrize(("method", "process"), MAINTENANCE_ACTIONS)
async def test_unverified_nivona_maintenance_never_reaches_transport(family, method, process):
    """No mapped Nivona family has verified generic maintenance starts."""
    client = _client("nivona", family)
    assert await getattr(client, method)() is False
    assert client._protocol.send_and_wait_ack.await_args_list == []


@pytest.mark.parametrize("family", ["barista_t", "barista_ts"])
@pytest.mark.parametrize(("method", "process"), MAINTENANCE_ACTIONS)
async def test_verified_melitta_maintenance_keeps_existing_payload(family, method, process):
    """The gate must retain the verified Melitta HE layout exactly."""
    client = _client("melitta", family)
    assert await getattr(client, method)() is True
    call, = client._protocol.send_and_wait_ack.await_args_list
    assert call.args[:2] == ("HE", struct.pack(">hh", int(process), 2) + bytes(14))


@pytest.mark.parametrize(("method", "process"), MAINTENANCE_ACTIONS)
async def test_native_nivona_buttons_are_unavailable_and_forced_press_is_blocked(method, process):
    """Even a direct button callback cannot bypass the transport gate."""
    client = _client("nivona", "700")
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG_DATA)
    button = MelittaMaintenanceButton(
        client, entry, "NICR 756", key=method, label=method,
        icon="mdi:power", process=process,
    )
    assert button.available is False
    await button.async_press()
    assert client._protocol.send_and_wait_ack.await_args_list == []


@pytest.mark.parametrize(("method", "process"), MAINTENANCE_ACTIONS)
async def test_unknown_nivona_family_fails_closed(method, process):
    """Connection alone must not authorize commands before family resolution."""
    client = MelittaBleClient(MOCK_ADDRESS, brand=get_profile("nivona"))
    client._connected = True
    client._client = MagicMock(is_connected=True)
    client._protocol.send_and_wait_ack = AsyncMock(return_value=True)
    assert await getattr(client, method)() is False
    assert client._protocol.send_and_wait_ack.await_args_list == []


@pytest.mark.parametrize(("method", "process"), MAINTENANCE_ACTIONS)
async def test_disconnected_verified_family_cannot_send(method, process):
    """Verification never overrides the live connection requirement."""
    client = _client("melitta", "barista_ts")
    client._connected = False
    assert await getattr(client, method)() is False
    assert client._protocol.send_and_wait_ack.await_args_list == []


def test_native_button_without_capabilities_is_unavailable():
    """A native control must also fail closed while model detection is pending."""
    client = _client("nivona", "700")
    client._capabilities = None
    button = MelittaMaintenanceButton(
        client, MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG_DATA), "NICR 756",
        key="switch_off", label="Switch Off", icon="mdi:power",
        process=MachineProcess.SWITCH_OFF,
    )
    assert button.available is False


@pytest.mark.parametrize(("method", "allowed"), [("switch_off", True), ("start_descaling", False)])
async def test_partial_verification_allows_only_listed_process(method, allowed):
    """A nonempty audit tuple is a whitelist, not permission for every action."""
    client = _client("melitta", "barista_ts")
    client._capabilities = replace(
        client.capabilities, verified_maintenance_processes=(16,),
    )
    assert await getattr(client, method)() is allowed
    calls = client._protocol.send_and_wait_ack.await_args_list
    if allowed:
        call, = calls
        assert call.args[:2] == ("HE", bytes.fromhex("001000020000000000000000000000000000"))
    else:
        assert calls == []
