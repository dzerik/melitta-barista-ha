"""Tests for button platform entities."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.melitta_barista.const import DOMAIN, MachineProcess, RecipeId
from custom_components.melitta_barista.protocol import MachineStatus

from . import MOCK_ADDRESS, MOCK_CONFIG_DATA


def _mock_client(status=None, selected_recipe=None):
    """Create a MelittaBleClient mock."""
    client = MagicMock()
    client.address = MOCK_ADDRESS
    client.connected = True
    client.status = status or MachineStatus(process=MachineProcess.READY)
    client.firmware_version = "1.0.0"
    client.machine_type = None
    client.model_name = "Melitta Barista"
    client.selected_recipe = selected_recipe
    client.set_ble_device = MagicMock()
    client.add_status_callback = MagicMock()
    client.add_connection_callback = MagicMock()
    client.connect = AsyncMock(return_value=True)
    client.disconnect = AsyncMock()
    client.start_polling = MagicMock()
    client.brew_recipe = AsyncMock(return_value=True)
    client.cancel_process = AsyncMock(return_value=True)
    client.start_easy_clean = AsyncMock(return_value=True)
    client.start_intensive_clean = AsyncMock(return_value=True)
    client.start_descaling = AsyncMock(return_value=True)
    client.switch_off = AsyncMock(return_value=True)
    return client


@pytest.fixture
def mock_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data=MOCK_CONFIG_DATA,
        unique_id="aabbccddeeff",
    )


async def _setup_integration(hass, mock_entry, client):
    mock_entry.add_to_hass(hass)
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


async def test_buttons_created(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test that all button entities are created."""
    client = _mock_client()
    await _setup_integration(hass, mock_entry, client)

    buttons = hass.states.async_all("button")
    button_ids = [b.entity_id for b in buttons]

    # Should have: brew, cancel, easy_clean, intensive_clean, descaling, switch_off
    assert len(buttons) >= 6
    assert any("brew" in eid for eid in button_ids)
    assert any("cancel" in eid for eid in button_ids)
    assert any("easy_clean" in eid for eid in button_ids)
    assert any("descaling" in eid for eid in button_ids)


async def test_brew_button_press(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test pressing the brew button."""
    client = _mock_client(selected_recipe=RecipeId.ESPRESSO)
    await _setup_integration(hass, mock_entry, client)

    brew_entity = [b for b in hass.states.async_all("button") if "brew" in b.entity_id and "cancel" not in b.entity_id]
    assert len(brew_entity) >= 1

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": brew_entity[0].entity_id},
        blocking=True,
    )

    client.brew_recipe.assert_awaited_once_with(RecipeId.ESPRESSO)


async def test_cancel_button_press(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test pressing the cancel button during brewing."""
    status = MachineStatus(process=MachineProcess.PRODUCT)
    client = _mock_client(status=status)
    await _setup_integration(hass, mock_entry, client)

    cancel_entity = [b for b in hass.states.async_all("button") if "cancel" in b.entity_id]
    assert len(cancel_entity) >= 1

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": cancel_entity[0].entity_id},
        blocking=True,
    )

    client.cancel_process.assert_awaited_once()
