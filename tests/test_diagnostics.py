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
    return client


def _make_entry(
    *,
    address: str = MOCK_ADDRESS,
    name: str = MOCK_NAME,
    options: dict | None = None,
    runtime_data=None,
) -> MockConfigEntry:
    """Create a MockConfigEntry with runtime_data."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: address, CONF_NAME: name},
        options=options or {},
        version=1,
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

    assert set(result.keys()) == {"entry", "device", "status", "counters", "profiles", "options"}

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
