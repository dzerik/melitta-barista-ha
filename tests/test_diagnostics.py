"""Tests for Melitta Barista Smart diagnostics."""

from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock

import pytest
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.melitta_barista.const import DOMAIN
from custom_components.melitta_barista.diagnostics import (
    async_get_config_entry_diagnostics,
)

from . import MOCK_ADDRESS, MOCK_NAME


def _make_mock_client(
    *,
    connected: bool = True,
    firmware: str | None = "2.3.1",
    machine_type=None,
    model_name: str = "Melitta Barista Smart",
    status=None,
    total_cups: int | None = 42,
    cup_counters: dict[str, int] | None = None,
    profile_names: dict[int, str] | None = None,
    active_profile: int = 0,
) -> MagicMock:
    """Create a mock MelittaBleClient with all properties used by diagnostics."""
    client = MagicMock()
    type(client).connected = PropertyMock(return_value=connected)
    type(client).firmware_version = PropertyMock(return_value=firmware)
    type(client).machine_type = PropertyMock(return_value=machine_type)
    type(client).model_name = PropertyMock(return_value=model_name)
    type(client).status = PropertyMock(return_value=status)
    type(client).total_cups = PropertyMock(return_value=total_cups)
    type(client).cup_counters = PropertyMock(
        return_value=cup_counters if cup_counters is not None else {"Espresso": 10, "Coffee": 32}
    )
    type(client).profile_names = PropertyMock(
        return_value=profile_names if profile_names is not None else {0: "My Coffee", 1: "Guest"}
    )
    type(client).active_profile = PropertyMock(return_value=active_profile)
    type(client).ble_source_affinity = PropertyMock(return_value=None)
    type(client).ble_device_source = PropertyMock(return_value=None)
    type(client).last_connected_source = PropertyMock(return_value=None)
    type(client).source_migration_pending = PropertyMock(return_value=False)
    type(client).seen_ble_sources = PropertyMock(return_value={})
    return client


def _make_entry(
    *,
    address: str = MOCK_ADDRESS,
    name: str = MOCK_NAME,
    options: dict | None = None,
    runtime_data=None,
    unique_id: str | None = None,
) -> MockConfigEntry:
    """Create a MockConfigEntry with runtime_data."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: address, CONF_NAME: name},
        options=options or {},
        version=1,
        unique_id=unique_id,
    )
    entry.runtime_data = runtime_data
    return entry


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_diagnostics_full_result_structure(hass: HomeAssistant) -> None:
    """Diagnostics returns all expected top-level keys with correct values."""
    client = _make_mock_client()
    entry = _make_entry(runtime_data=client, options={"poll_interval": 10.0})
    entry.add_to_hass(hass)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert set(result.keys()) == {
        "entry", "device", "status", "counters", "profiles", "options",
        "ble_trace", "domain_entries", "recovery", "bluetooth_affinity",
    }

    # Entry section
    assert result["entry"]["title"] == entry.title
    assert result["entry"]["source"] == entry.source
    assert result["entry"]["version"] == 1

    # Device section
    assert result["device"]["connected"] is True
    assert result["device"]["firmware"] == "2.3.1"
    assert result["device"]["model_name"] == "Melitta Barista Smart"

    # Counters section
    assert result["counters"]["total_cups"] == 42
    assert result["counters"]["per_recipe"] == {"Espresso": 10, "Coffee": 32}

    # Profiles section
    assert result["profiles"]["count"] == 2
    assert result["profiles"]["active_profile"] == 0
    assert result["profiles"]["names"] == {0: "My Coffee", 1: "Guest"}

    # Options section
    assert result["options"] == {"poll_interval": 10.0}

    # BLE trace section
    assert "recent_frames_raw" in result["ble_trace"]
    assert "frame_log_decoded" in result["ble_trace"]
    assert isinstance(result["ble_trace"]["recent_frames_raw"], list)
    assert isinstance(result["ble_trace"]["frame_log_decoded"], list)


async def test_diagnostics_bluetooth_affinity_redacts_mac_sources(
    hass: HomeAssistant,
) -> None:
    """Affinity diagnostics expose routing state without full adapter MACs."""
    client = _make_mock_client()
    type(client).ble_source_affinity = PropertyMock(return_value="11:22:33:44:55:66")
    type(client).ble_device_source = PropertyMock(return_value="11:22:33:44:55:66")
    type(client).last_connected_source = PropertyMock(return_value="11:22:33:44:55:66")
    type(client).source_migration_pending = PropertyMock(return_value=True)
    type(client).seen_ble_sources = PropertyMock(return_value={
        "11:22:33:44:55:66": 123.0,
        "proxy-kitchen": 456.0,
    })
    entry = _make_entry(runtime_data=client)
    entry.add_to_hass(hass)

    result = await async_get_config_entry_diagnostics(hass, entry)
    affinity = result["bluetooth_affinity"]

    assert affinity["affinity_source"] == "11:22:**:**:**:**:66"
    assert affinity["current_device_source"] == "11:22:**:**:**:**:66"
    assert affinity["last_connected_source"] == "11:22:**:**:**:**:66"
    assert affinity["migration_pending"] is True
    assert affinity["seen_sources"] == {
        "11:22:**:**:**:**:66": 123.0,
        "proxy-kitchen": 456.0,
    }


async def test_diagnostics_address_redacted(hass: HomeAssistant) -> None:
    """BLE address is redacted in diagnostics output (privacy)."""
    client = _make_mock_client()
    entry = _make_entry(address="F1:23:45:67:89:AB", runtime_data=client)
    entry.add_to_hass(hass)

    result = await async_get_config_entry_diagnostics(hass, entry)

    redacted = result["entry"]["address"]
    # Full address = "F1:23:45:67:89:AB" (17 chars)
    # Expected: "F1:23:**:**:**:**:AB"
    assert "F1:23" in redacted
    assert "AB" in redacted
    assert "45" not in redacted
    assert "67" not in redacted
    assert "89" not in redacted
    assert "**" in redacted


async def test_diagnostics_short_address_fully_redacted(hass: HomeAssistant) -> None:
    """Address shorter than 17 chars is fully redacted."""
    client = _make_mock_client()
    entry = _make_entry(address="SHORT", runtime_data=client)
    entry.add_to_hass(hass)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["entry"]["address"] == "redacted"


async def test_diagnostics_empty_address_redacted(hass: HomeAssistant) -> None:
    """Empty address is fully redacted."""
    client = _make_mock_client()
    entry = _make_entry(address="", runtime_data=client)
    entry.add_to_hass(hass)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["entry"]["address"] == "redacted"


async def test_diagnostics_no_address_key_redacted(hass: HomeAssistant) -> None:
    """Missing address key in data results in redacted."""
    client = _make_mock_client()
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_NAME: MOCK_NAME},  # no CONF_ADDRESS
        options={},
    )
    entry.runtime_data = client
    entry.add_to_hass(hass)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["entry"]["address"] == "redacted"


# ---------------------------------------------------------------------------
# Status: present vs None
# ---------------------------------------------------------------------------


async def test_diagnostics_with_status(hass: HomeAssistant) -> None:
    """When status is available, diagnostics includes process/sub_process/progress."""
    mock_status = MagicMock()
    mock_status.process = MagicMock()
    mock_status.process.__str__ = lambda self: "MachineProcess.READY"
    mock_status.sub_process = MagicMock()
    mock_status.sub_process.__str__ = lambda self: "SubProcess.NONE"
    mock_status.progress = 100
    mock_status.is_ready = True

    client = _make_mock_client(status=mock_status)
    entry = _make_entry(runtime_data=client)
    entry.add_to_hass(hass)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["status"]["process"] == "MachineProcess.READY"
    assert result["status"]["sub_process"] == "SubProcess.NONE"
    assert result["status"]["progress"] == 100
    assert result["status"]["is_ready"] is True


async def test_diagnostics_status_none(hass: HomeAssistant) -> None:
    """When status is None, all status fields are None."""
    client = _make_mock_client(status=None)
    entry = _make_entry(runtime_data=client)
    entry.add_to_hass(hass)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["status"]["process"] is None
    assert result["status"]["sub_process"] is None
    assert result["status"]["progress"] is None
    assert result["status"]["is_ready"] is None


# ---------------------------------------------------------------------------
# Machine type
# ---------------------------------------------------------------------------


async def test_diagnostics_machine_type_present(hass: HomeAssistant) -> None:
    """When machine_type is set, it appears as a string."""
    mock_type = MagicMock()
    mock_type.__str__ = lambda self: "MachineType.BARISTA_TS"

    client = _make_mock_client(machine_type=mock_type)
    entry = _make_entry(runtime_data=client)
    entry.add_to_hass(hass)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["device"]["machine_type"] == "MachineType.BARISTA_TS"


async def test_diagnostics_machine_type_none(hass: HomeAssistant) -> None:
    """When machine_type is None, diagnostics shows None."""
    client = _make_mock_client(machine_type=None)
    entry = _make_entry(runtime_data=client)
    entry.add_to_hass(hass)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["device"]["machine_type"] is None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


async def test_diagnostics_empty_counters_and_profiles(hass: HomeAssistant) -> None:
    """Diagnostics works with empty counters and profiles."""
    client = _make_mock_client(
        cup_counters={},
        profile_names={},
        total_cups=0,
    )
    entry = _make_entry(runtime_data=client)
    entry.add_to_hass(hass)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["counters"]["total_cups"] == 0
    assert result["counters"]["per_recipe"] == {}
    assert result["profiles"]["count"] == 0
    assert result["profiles"]["names"] == {}


async def test_diagnostics_disconnected_client(hass: HomeAssistant) -> None:
    """Diagnostics works when client is disconnected."""
    client = _make_mock_client(
        connected=False,
        firmware=None,
        total_cups=None,
    )
    entry = _make_entry(runtime_data=client)
    entry.add_to_hass(hass)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["device"]["connected"] is False
    assert result["device"]["firmware"] is None
    assert result["counters"]["total_cups"] is None


async def test_diagnostics_empty_options(hass: HomeAssistant) -> None:
    """Diagnostics with no options returns empty dict for options."""
    client = _make_mock_client()
    entry = _make_entry(runtime_data=client, options={})
    entry.add_to_hass(hass)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["options"] == {}


# ---------------------------------------------------------------------------
# Issue #10: entry identity + duplicate visibility
# ---------------------------------------------------------------------------


async def test_diagnostics_entry_id_and_unique_id(hass: HomeAssistant) -> None:
    """entry_id is exported unredacted; unique_id is masked like the address."""
    client = _make_mock_client()
    entry = _make_entry(runtime_data=client, unique_id="aabbccddeeff")
    entry.add_to_hass(hass)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["entry"]["entry_id"] == entry.entry_id
    uid = result["entry"]["unique_id"]
    assert uid.startswith("aabb") and uid.endswith("ff")
    assert "ccddee" not in uid


async def test_diagnostics_unique_id_none_passthrough(hass: HomeAssistant) -> None:
    """unique_id=None is a diagnostic signal of its own — not 'redacted'."""
    client = _make_mock_client()
    entry = _make_entry(runtime_data=client, unique_id=None)
    entry.add_to_hass(hass)

    result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["entry"]["unique_id"] is None


async def test_diagnostics_unique_id_nonstandard_redacted(hass: HomeAssistant) -> None:
    client = _make_mock_client()
    entry = _make_entry(runtime_data=client, unique_id="short")
    entry.add_to_hass(hass)

    result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["entry"]["unique_id"] == "redacted"


async def test_diagnostics_domain_entries_lists_duplicates(hass: HomeAssistant) -> None:
    """Two entries for the domain are both visible with masked unique_ids —
    the field case (issue #10) was two entries fighting over one machine."""
    client = _make_mock_client()
    entry = _make_entry(runtime_data=client, unique_id="aabbccddeeff")
    entry.add_to_hass(hass)
    twin = _make_entry(unique_id=None)
    twin.add_to_hass(hass)

    result = await async_get_config_entry_diagnostics(hass, entry)

    section = result["domain_entries"]
    assert section["count"] == 2
    listed = {e["entry_id"] for e in section["entries"]}
    assert listed == {entry.entry_id, twin.entry_id}
    current_flags = [e["is_current"] for e in section["entries"]]
    assert current_flags.count(True) == 1
    for e in section["entries"]:
        assert "ccddee" not in (e["unique_id"] or "")


async def test_diagnostics_recovery_block(hass: HomeAssistant) -> None:
    """The recovery block exports the bond state machine audit trail."""
    from custom_components.melitta_barista.bond_state import BondStateMachine
    from custom_components.melitta_barista.const import FAILURE_AUTH

    client = _make_mock_client()
    client._consecutive_connect_failures = 3
    client._last_failure_class = FAILURE_AUTH
    client._ble_link_seen = True
    client._auth_fail_seen = True
    client._unpaired_this_episode = False
    machine = BondStateMachine()
    machine.on_cycle_failure(FAILURE_AUTH)
    client.bond = machine
    entry = _make_entry(runtime_data=client)
    entry.add_to_hass(hass)

    result = await async_get_config_entry_diagnostics(hass, entry)

    rec = result["recovery"]
    assert rec["consecutive_connect_failures"] == 3
    assert rec["last_failure_class"] == FAILURE_AUTH
    assert rec["bond"]["state"] == "suspect"
    assert rec["bond"]["auth_fail_cycles"] == 1
    assert rec["bond"]["history"], "bond_ops audit trail must not be empty"
