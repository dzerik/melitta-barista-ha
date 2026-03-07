"""Tests for integration setup, unload, and legacy cleanup."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.melitta_barista.const import DOMAIN

from . import MOCK_ADDRESS, MOCK_CONFIG_DATA


def _mock_client():
    """Create a MelittaBleClient mock."""
    client = MagicMock()
    client.address = MOCK_ADDRESS
    client.connected = False
    client.status = None
    client.firmware_version = None
    client.machine_type = None
    client.model_name = "Melitta Barista"
    client.selected_recipe = None
    client.set_ble_device = MagicMock()
    client.add_status_callback = MagicMock()
    client.add_connection_callback = MagicMock()
    client.connect = AsyncMock(return_value=True)
    client.disconnect = AsyncMock()
    client.start_polling = MagicMock()
    return client


@pytest.fixture
def mock_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data=MOCK_CONFIG_DATA,
        unique_id="aabbccddeeff",
    )


async def test_setup_entry(hass: HomeAssistant, mock_entry: MockConfigEntry) -> None:
    """Test successful setup of a config entry."""
    mock_entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.melitta_barista.MelittaBleClient",
            return_value=_mock_client(),
        ),
        patch(
            "custom_components.melitta_barista.bluetooth.async_ble_device_from_address",
            return_value=None,
        ),
        patch(
            "custom_components.melitta_barista.bluetooth.async_register_callback",
            return_value=lambda: None,
        ),
    ):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_entry.state is ConfigEntryState.LOADED


async def test_unload_entry(hass: HomeAssistant, mock_entry: MockConfigEntry) -> None:
    """Test unloading a config entry."""
    mock_entry.add_to_hass(hass)
    client = _mock_client()

    with (
        patch(
            "custom_components.melitta_barista.MelittaBleClient",
            return_value=client,
        ),
        patch(
            "custom_components.melitta_barista.bluetooth.async_ble_device_from_address",
            return_value=None,
        ),
        patch(
            "custom_components.melitta_barista.bluetooth.async_register_callback",
            return_value=lambda: None,
        ),
    ):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
        assert await hass.config_entries.async_unload(mock_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_entry.state is ConfigEntryState.NOT_LOADED
    client.disconnect.assert_awaited_once()


async def test_legacy_cleanup_removes_old_entities(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test that legacy per-recipe button entities are cleaned up on setup."""
    mock_entry.add_to_hass(hass)

    # Pre-create legacy entities in the registry
    registry = er.async_get(hass)
    for recipe_val in range(200, 205):
        registry.async_get_or_create(
            "button",
            DOMAIN,
            f"{MOCK_ADDRESS}_brew_{recipe_val}",
            config_entry=mock_entry,
        )

    assert len(er.async_entries_for_config_entry(registry, mock_entry.entry_id)) == 5

    with (
        patch(
            "custom_components.melitta_barista.MelittaBleClient",
            return_value=_mock_client(),
        ),
        patch(
            "custom_components.melitta_barista.bluetooth.async_ble_device_from_address",
            return_value=None,
        ),
        patch(
            "custom_components.melitta_barista.bluetooth.async_register_callback",
            return_value=lambda: None,
        ),
    ):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

    # Legacy entities should be removed
    remaining = [
        e for e in er.async_entries_for_config_entry(registry, mock_entry.entry_id)
        if "_brew_2" in e.unique_id  # legacy pattern: _brew_200, _brew_201, ...
    ]
    assert len(remaining) == 0


async def test_legacy_cleanup_removes_named_entities(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test that legacy named recipe button entities are cleaned up."""
    mock_entry.add_to_hass(hass)

    registry = er.async_get(hass)
    # Create legacy entities with named unique_ids
    for name in ("espresso", "americano", "cappuccino"):
        registry.async_get_or_create(
            "button",
            DOMAIN,
            f"{MOCK_ADDRESS}_brew_{name}",
            config_entry=mock_entry,
        )

    assert len(er.async_entries_for_config_entry(registry, mock_entry.entry_id)) == 3

    with (
        patch(
            "custom_components.melitta_barista.MelittaBleClient",
            return_value=_mock_client(),
        ),
        patch(
            "custom_components.melitta_barista.bluetooth.async_ble_device_from_address",
            return_value=None,
        ),
        patch(
            "custom_components.melitta_barista.bluetooth.async_register_callback",
            return_value=lambda: None,
        ),
    ):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

    remaining = [
        e for e in er.async_entries_for_config_entry(registry, mock_entry.entry_id)
        if "_brew_" in e.unique_id
    ]
    assert len(remaining) == 0
