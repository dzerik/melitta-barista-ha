"""Tests for select platform entities."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.melitta_barista.const import DOMAIN, RecipeId

from . import MOCK_ADDRESS, MOCK_CONFIG_DATA


def _mock_client():
    client = MagicMock()
    client.address = MOCK_ADDRESS
    client.connected = True
    client.status = None
    client.firmware_version = "1.0.0"
    client.machine_type = None
    client.model_name = "Melitta Barista"
    client.selected_recipe = None
    client.active_profile = 0
    client.set_ble_device = MagicMock()
    client.add_status_callback = MagicMock()
    client.add_connection_callback = MagicMock()
    client.connect = AsyncMock(return_value=True)
    client.disconnect = AsyncMock()
    client.read_recipe = AsyncMock(return_value=None)
    client.start_polling = MagicMock()
    client.profile_names = {0: "My Coffee"}
    client.directkey_recipes = {}
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


async def test_recipe_select_created(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test recipe select entity is created."""
    client = _mock_client()
    await _setup_integration(hass, mock_entry, client)

    selects = hass.states.async_all("select")
    assert len(selects) >= 1
    recipe_select = [s for s in selects if "recipe" in s.entity_id]
    assert len(recipe_select) == 1


async def test_recipe_select_has_options(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test recipe select has all recipe options."""
    client = _mock_client()
    await _setup_integration(hass, mock_entry, client)

    recipe_select = [s for s in hass.states.async_all("select") if "recipe" in s.entity_id][0]
    options = recipe_select.attributes.get("options", [])

    # Should have all 24 recipes (machine_type=None returns all)
    assert len(options) == 24
    assert "Espresso" in options
    assert "Cappuccino" in options
    assert "Hot Water" in options


async def test_recipe_select_option(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test selecting a recipe option."""
    client = _mock_client()
    await _setup_integration(hass, mock_entry, client)

    recipe_select = [s for s in hass.states.async_all("select") if "recipe" in s.entity_id][0]

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": recipe_select.entity_id, "option": "Espresso"},
        blocking=True,
    )
    await hass.async_block_till_done()

    # Client should have selected_recipe set
    assert client.selected_recipe == RecipeId.ESPRESSO

    state = hass.states.get(recipe_select.entity_id)
    assert state.state == "Espresso"
