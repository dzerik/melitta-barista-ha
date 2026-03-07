"""Tests for profile select and freestyle recipe features."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.melitta_barista.const import (
    DOMAIN,
    DirectKeyCategory,
    MachineProcess,
    RecipeId,
    get_directkey_id,
)
from custom_components.melitta_barista.protocol import MachineStatus, RecipeComponent

from . import MOCK_ADDRESS, MOCK_CONFIG_DATA


def _mock_client(status=None, selected_recipe=None):
    client = MagicMock()
    client.address = MOCK_ADDRESS
    client.connected = True
    client.status = status or MachineStatus(process=MachineProcess.READY)
    client.firmware_version = "1.0.0"
    client.machine_type = None
    client.model_name = "Melitta Barista"
    client.selected_recipe = selected_recipe
    client.active_profile = 0
    client.set_ble_device = MagicMock()
    client.add_status_callback = MagicMock()
    client.add_connection_callback = MagicMock()
    client.connect = AsyncMock(return_value=True)
    client.disconnect = AsyncMock()
    client.start_polling = MagicMock()
    client.brew_recipe = AsyncMock(return_value=True)
    client.brew_freestyle = AsyncMock(return_value=True)
    client.read_alpha = AsyncMock(return_value=None)
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


# --- Profile Select Tests ---


async def test_profile_select_created(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test profile select entity is created."""
    client = _mock_client()
    await _setup_integration(hass, mock_entry, client)

    selects = hass.states.async_all("select")
    profile_select = [s for s in selects if "profile" in s.entity_id]
    assert len(profile_select) == 1


async def test_profile_select_has_options(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test profile select has correct options."""
    client = _mock_client()
    await _setup_integration(hass, mock_entry, client)

    profile_select = [
        s for s in hass.states.async_all("select") if "profile" in s.entity_id
    ][0]
    options = profile_select.attributes.get("options", [])

    assert "My Coffee" in options
    assert len(options) >= 2  # At least default + 1 profile


async def test_profile_select_default(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test profile select defaults to My Coffee."""
    client = _mock_client()
    await _setup_integration(hass, mock_entry, client)

    profile_select = [
        s for s in hass.states.async_all("select") if "profile" in s.entity_id
    ][0]
    assert profile_select.state == "My Coffee"


async def test_profile_select_changes_active_profile(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test selecting a profile updates active_profile on client."""
    client = _mock_client()
    await _setup_integration(hass, mock_entry, client)

    profile_select = [
        s for s in hass.states.async_all("select") if "profile" in s.entity_id
    ][0]

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": profile_select.entity_id, "option": "Profile 1"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert client.active_profile == 1


# --- DirectKey Calculation Tests ---


class TestDirectKey:
    """Test DirectKey ID calculations."""

    def test_directkey_profile0_espresso(self):
        assert get_directkey_id(0, DirectKeyCategory.ESPRESSO) == 302

    def test_directkey_profile0_water(self):
        assert get_directkey_id(0, DirectKeyCategory.WATER) == 308

    def test_directkey_profile1_espresso(self):
        assert get_directkey_id(1, DirectKeyCategory.ESPRESSO) == 312

    def test_directkey_profile1_cappuccino(self):
        assert get_directkey_id(1, DirectKeyCategory.CAPPUCCINO) == 314

    def test_directkey_profile8_latte(self):
        assert get_directkey_id(8, DirectKeyCategory.LATTE_MACCHIATO) == 385


# --- Freestyle Service Tests ---


async def test_freestyle_service_registered(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test that brew_freestyle service is registered."""
    client = _mock_client()
    await _setup_integration(hass, mock_entry, client)

    assert hass.services.has_service(DOMAIN, "brew_freestyle")


# --- BLE Client Profile Brew Tests ---


class TestBleClientProfileBrew:
    """Test brew_recipe with profile selection."""

    @pytest.mark.asyncio
    async def test_brew_with_default_profile(self):
        """Default profile (0) reads the standard recipe ID."""
        from custom_components.melitta_barista.ble_client import MelittaBleClient

        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        client._connected = True
        client._client = MagicMock(is_connected=True)
        client._status = MachineStatus(process=MachineProcess.READY)
        client.active_profile = 0

        mock_recipe = MagicMock()
        mock_recipe.recipe_type = 0
        mock_recipe.component1 = RecipeComponent()
        mock_recipe.component2 = RecipeComponent()
        client._protocol.read_recipe = AsyncMock(return_value=mock_recipe)
        client._protocol.write_recipe = AsyncMock(return_value=True)
        client._protocol.write_alphanumeric = AsyncMock(return_value=True)
        client._protocol.start_process = AsyncMock(return_value=True)

        result = await client.brew_recipe(RecipeId.ESPRESSO)
        assert result is True
        # Should read from standard recipe ID 200
        client._protocol.read_recipe.assert_awaited_once_with(
            client._write_ble, RecipeId.ESPRESSO
        )

    @pytest.mark.asyncio
    async def test_brew_with_profile1(self):
        """Profile 1 reads from DirectKey ID."""
        from custom_components.melitta_barista.ble_client import MelittaBleClient

        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        client._connected = True
        client._client = MagicMock(is_connected=True)
        client._status = MachineStatus(process=MachineProcess.READY)
        client.active_profile = 1

        mock_recipe = MagicMock()
        mock_recipe.recipe_type = 0
        mock_recipe.component1 = RecipeComponent()
        mock_recipe.component2 = RecipeComponent()
        client._protocol.read_recipe = AsyncMock(return_value=mock_recipe)
        client._protocol.write_recipe = AsyncMock(return_value=True)
        client._protocol.write_alphanumeric = AsyncMock(return_value=True)
        client._protocol.start_process = AsyncMock(return_value=True)

        result = await client.brew_recipe(RecipeId.ESPRESSO)
        assert result is True
        # DirectKey for profile 1, ESPRESSO category = 302 + 1*10 + 0 = 312
        client._protocol.read_recipe.assert_awaited_once_with(
            client._write_ble, 312
        )


class TestBleClientFreestyle:
    """Test brew_freestyle method."""

    @pytest.mark.asyncio
    async def test_freestyle_not_connected(self):
        from custom_components.melitta_barista.ble_client import MelittaBleClient

        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        result = await client.brew_freestyle(
            "Test", 24, RecipeComponent(), RecipeComponent()
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_freestyle_success(self):
        from custom_components.melitta_barista.ble_client import MelittaBleClient

        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        client._connected = True
        client._client = MagicMock(is_connected=True)
        client._status = MachineStatus(process=MachineProcess.READY)
        client._protocol.write_recipe = AsyncMock(return_value=True)
        client._protocol.write_alphanumeric = AsyncMock(return_value=True)
        client._protocol.start_process = AsyncMock(return_value=True)

        comp1 = RecipeComponent(process=1, shots=1, intensity=3, portion=8)
        comp2 = RecipeComponent(process=2, shots=0, portion=20)

        result = await client.brew_freestyle("My Drink", 24, comp1, comp2)
        assert result is True
        client._protocol.write_recipe.assert_awaited_once()
        client._protocol.write_alphanumeric.assert_awaited_once_with(
            client._write_ble, 401, "My Drink"
        )
        client._protocol.start_process.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_freestyle_not_ready(self):
        from custom_components.melitta_barista.ble_client import MelittaBleClient

        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        client._connected = True
        client._client = MagicMock(is_connected=True)
        client._status = MachineStatus(process=MachineProcess.PRODUCT)

        result = await client.brew_freestyle(
            "Test", 24, RecipeComponent(), RecipeComponent()
        )
        assert result is False
