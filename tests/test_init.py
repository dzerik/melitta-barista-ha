"""Tests for integration setup, unload, and legacy cleanup."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest
from bleak.exc import BleakError
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
    client.active_profile = 0
    client.freestyle_name = "Custom"
    client.freestyle_process1 = "coffee"
    client.freestyle_intensity1 = "medium"
    client.freestyle_portion1_ml = 40
    client.freestyle_temperature1 = "normal"
    client.freestyle_shots1 = "one"
    client.freestyle_process2 = "none"
    client.freestyle_intensity2 = "medium"
    client.freestyle_portion2_ml = 0
    client.freestyle_temperature2 = "normal"
    client.freestyle_shots2 = "none"
    client.set_ble_device = MagicMock()
    client.add_status_callback = MagicMock()
    client.add_connection_callback = MagicMock()
    client.connect = AsyncMock(return_value=True)
    client.disconnect = AsyncMock()
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
        if "_brew_" in e.unique_id and "_brew_freestyle" not in e.unique_id
    ]
    assert len(remaining) == 0


# ---------------------------------------------------------------------------
# Helper: common patches for async_setup
# ---------------------------------------------------------------------------

def _setup_patches(client=None):
    """Return a tuple of context managers that patch BLE + client for setup."""
    if client is None:
        client = _mock_client()
    return (
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
    )


async def _setup_entry_with_client(hass, mock_entry, client=None):
    """Set up the integration with a mock client and return the client."""
    if client is None:
        client = _mock_client()
    mock_entry.add_to_hass(hass)
    p1, p2, p3 = _setup_patches(client)
    with p1, p2, p3:
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
    return client


# ---------------------------------------------------------------------------
# Legacy cleanup — Format 3 (wildcard _brew_ pattern)
# ---------------------------------------------------------------------------


async def test_legacy_cleanup_format3_wildcard_pattern(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test that Format 3 cleanup removes _brew_ entities not matching current IDs."""
    mock_entry.add_to_hass(hass)
    registry = er.async_get(hass)

    # Create entities that match Format 3 pattern (not numeric, not named recipe)
    registry.async_get_or_create(
        "button", DOMAIN,
        f"{MOCK_ADDRESS}_brew_custom_thing",
        config_entry=mock_entry,
    )
    # These should NOT be removed (current valid entities)
    registry.async_get_or_create(
        "button", DOMAIN,
        f"{MOCK_ADDRESS}_brew",
        config_entry=mock_entry,
    )
    registry.async_get_or_create(
        "button", DOMAIN,
        f"{MOCK_ADDRESS}_brew_freestyle",
        config_entry=mock_entry,
    )

    p1, p2, p3 = _setup_patches()
    with p1, p2, p3:
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

    remaining = er.async_entries_for_config_entry(registry, mock_entry.entry_id)
    remaining_ids = [e.unique_id for e in remaining]

    # _brew_custom_thing should be removed
    assert f"{MOCK_ADDRESS}_brew_custom_thing" not in remaining_ids
    # _brew and _brew_freestyle should remain
    assert f"{MOCK_ADDRESS}_brew" in remaining_ids
    assert f"{MOCK_ADDRESS}_brew_freestyle" in remaining_ids


# ---------------------------------------------------------------------------
# BLE device cache errors
# ---------------------------------------------------------------------------


async def test_setup_ble_device_cache_error(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test setup continues when BLEDevice cache lookup raises an error."""
    mock_entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.melitta_barista.MelittaBleClient",
            return_value=_mock_client(),
        ),
        patch(
            "custom_components.melitta_barista.bluetooth.async_ble_device_from_address",
            side_effect=AttributeError("no bluetooth"),
        ),
        patch(
            "custom_components.melitta_barista.bluetooth.async_register_callback",
            return_value=lambda: None,
        ),
    ):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_entry.state is ConfigEntryState.LOADED


# ---------------------------------------------------------------------------
# Bluetooth callback registration error
# ---------------------------------------------------------------------------


async def test_setup_bluetooth_callback_registration_error(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test setup continues when bluetooth callback registration fails."""
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
            side_effect=KeyError("no adapter"),
        ),
    ):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_entry.state is ConfigEntryState.LOADED


# ---------------------------------------------------------------------------
# _async_connect_and_poll — failure paths
# ---------------------------------------------------------------------------


async def test_connect_and_poll_connect_returns_false_then_succeeds() -> None:
    """Test backoff retry when connect() returns False, then succeeds."""
    from custom_components.melitta_barista import _async_connect_and_poll

    client = _mock_client()
    client.connect = AsyncMock(side_effect=[False, True])
    client._reconnect_event = asyncio.Event()

    await _async_connect_and_poll(
        client,
        poll_interval=30,
        initial_delay=0,
        reconnect_delay=0.01,
        reconnect_max_delay=0.02,
    )

    assert client.connect.await_count == 2
    client.start_polling.assert_called_once_with(interval=30)


async def test_connect_and_poll_bleak_error_then_succeeds() -> None:
    """Test backoff retry when connect() raises BleakError."""
    from custom_components.melitta_barista import _async_connect_and_poll

    client = _mock_client()
    client.connect = AsyncMock(side_effect=[BleakError("timeout"), True])
    client._reconnect_event = asyncio.Event()

    await _async_connect_and_poll(
        client,
        poll_interval=30,
        initial_delay=0,
        reconnect_delay=0.01,
        reconnect_max_delay=0.02,
    )

    assert client.connect.await_count == 2
    client.start_polling.assert_called_once()


async def test_connect_and_poll_unexpected_error_then_succeeds() -> None:
    """Test backoff retry when connect() raises an unexpected exception."""
    from custom_components.melitta_barista import _async_connect_and_poll

    client = _mock_client()
    client.connect = AsyncMock(side_effect=[RuntimeError("unexpected"), True])
    client._reconnect_event = asyncio.Event()

    await _async_connect_and_poll(
        client,
        poll_interval=30,
        initial_delay=0,
        reconnect_delay=0.01,
        reconnect_max_delay=0.02,
    )

    assert client.connect.await_count == 2
    client.start_polling.assert_called_once()


async def test_connect_and_poll_woken_by_ble_advertisement() -> None:
    """Test that _reconnect_event wakes up the backoff wait early."""
    from custom_components.melitta_barista import _async_connect_and_poll

    client = _mock_client()
    call_count = 0

    async def _connect_side_effect():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First call fails; set event to wake up early
            asyncio.get_event_loop().call_soon(client._reconnect_event.set)
            return False
        return True

    client.connect = AsyncMock(side_effect=_connect_side_effect)
    client._reconnect_event = asyncio.Event()

    await _async_connect_and_poll(
        client,
        poll_interval=30,
        initial_delay=0,
        reconnect_delay=60,  # long delay — event should wake us up before this
        reconnect_max_delay=120,
    )

    assert client.connect.await_count == 2
    client.start_polling.assert_called_once()


# ---------------------------------------------------------------------------
# Service registration — idempotent (already registered)
# ---------------------------------------------------------------------------


async def test_services_registered_only_once(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test that services are not re-registered on second setup."""
    client = _mock_client()
    await _setup_entry_with_client(hass, mock_entry, client)

    # Services should be registered
    assert hass.services.has_service(DOMAIN, "brew_freestyle")
    assert hass.services.has_service(DOMAIN, "brew_directkey")
    assert hass.services.has_service(DOMAIN, "save_directkey")

    # Set up a second entry — should not re-register
    mock_entry2 = MockConfigEntry(
        domain=DOMAIN,
        data=MOCK_CONFIG_DATA,
        unique_id="112233445566",
    )
    mock_entry2.add_to_hass(hass)
    client2 = _mock_client()
    client2.address = "11:22:33:44:55:66"
    p1, p2, p3 = _setup_patches(client2)
    with p1, p2, p3:
        assert await hass.config_entries.async_setup(mock_entry2.entry_id)
        await hass.async_block_till_done()

    # Still registered (no error)
    assert hass.services.has_service(DOMAIN, "brew_freestyle")


# ---------------------------------------------------------------------------
# Service handlers — brew_freestyle
# ---------------------------------------------------------------------------


async def test_handle_brew_freestyle_success(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test brew_freestyle service handler calls client.brew_freestyle."""
    client = _mock_client()
    client.brew_freestyle = AsyncMock(return_value=True)
    await _setup_entry_with_client(hass, mock_entry, client)

    # Create an entity in the registry so _find_client can resolve it
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "button", DOMAIN,
        f"{MOCK_ADDRESS}_brew",
        config_entry=mock_entry,
    )
    entity_id = registry.async_get_entity_id("button", DOMAIN, f"{MOCK_ADDRESS}_brew")

    await hass.services.async_call(
        DOMAIN, "brew_freestyle",
        {
            "entity_id": entity_id,
            "name": "TestBrew",
            "process1": "coffee",
            "intensity1": "medium",
            "aroma1": "standard",
            "portion1_ml": 50,
            "temperature1": "normal",
            "shots1": "one",
            "process2": "none",
            "two_cups": False,
        },
        blocking=True,
    )

    client.brew_freestyle.assert_awaited_once()
    kw = client.brew_freestyle.call_args
    assert kw.kwargs["name"] == "TestBrew"
    assert kw.kwargs["two_cups"] is False


async def test_handle_brew_freestyle_client_not_found(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test brew_freestyle raises ServiceValidationError when no client found."""
    from homeassistant.exceptions import ServiceValidationError

    client = _mock_client()
    await _setup_entry_with_client(hass, mock_entry, client)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "brew_freestyle",
            {
                "entity_id": "button.nonexistent",
                "name": "Test",
                "process1": "coffee",
            },
            blocking=True,
        )


async def test_handle_brew_freestyle_returns_false(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test brew_freestyle raises HomeAssistantError when client returns False."""
    from homeassistant.exceptions import HomeAssistantError

    client = _mock_client()
    client.brew_freestyle = AsyncMock(return_value=False)
    await _setup_entry_with_client(hass, mock_entry, client)

    registry = er.async_get(hass)
    registry.async_get_or_create(
        "button", DOMAIN,
        f"{MOCK_ADDRESS}_brew",
        config_entry=mock_entry,
    )
    entity_id = registry.async_get_entity_id("button", DOMAIN, f"{MOCK_ADDRESS}_brew")

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN, "brew_freestyle",
            {
                "entity_id": entity_id,
                "name": "Test",
                "process1": "coffee",
            },
            blocking=True,
        )

    client.brew_freestyle.assert_awaited_once()


# ---------------------------------------------------------------------------
# Service handlers — brew_directkey
# ---------------------------------------------------------------------------


async def test_handle_brew_directkey_success(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test brew_directkey service handler calls client.brew_directkey."""
    client = _mock_client()
    client.brew_directkey = AsyncMock(return_value=True)
    await _setup_entry_with_client(hass, mock_entry, client)

    registry = er.async_get(hass)
    registry.async_get_or_create(
        "button", DOMAIN,
        f"{MOCK_ADDRESS}_brew",
        config_entry=mock_entry,
    )
    entity_id = registry.async_get_entity_id("button", DOMAIN, f"{MOCK_ADDRESS}_brew")

    await hass.services.async_call(
        DOMAIN, "brew_directkey",
        {
            "entity_id": entity_id,
            "category": "espresso",
            "two_cups": False,
        },
        blocking=True,
    )

    client.brew_directkey.assert_awaited_once()
    args = client.brew_directkey.call_args
    from custom_components.melitta_barista.const import DirectKeyCategory
    assert args[0][0] == DirectKeyCategory.ESPRESSO
    assert args[1]["two_cups"] is False


async def test_handle_brew_directkey_client_not_found(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test brew_directkey raises ServiceValidationError when no client found."""
    from homeassistant.exceptions import ServiceValidationError

    client = _mock_client()
    await _setup_entry_with_client(hass, mock_entry, client)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "brew_directkey",
            {
                "entity_id": "button.nonexistent",
                "category": "espresso",
            },
            blocking=True,
        )


async def test_handle_brew_directkey_returns_false(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test brew_directkey raises HomeAssistantError when client returns False."""
    from homeassistant.exceptions import HomeAssistantError

    client = _mock_client()
    client.brew_directkey = AsyncMock(return_value=False)
    await _setup_entry_with_client(hass, mock_entry, client)

    registry = er.async_get(hass)
    registry.async_get_or_create(
        "button", DOMAIN,
        f"{MOCK_ADDRESS}_brew",
        config_entry=mock_entry,
    )
    entity_id = registry.async_get_entity_id("button", DOMAIN, f"{MOCK_ADDRESS}_brew")

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN, "brew_directkey",
            {
                "entity_id": entity_id,
                "category": "cappuccino",
                "two_cups": True,
            },
            blocking=True,
        )

    client.brew_directkey.assert_awaited_once()


# ---------------------------------------------------------------------------
# Service handlers — save_directkey
# ---------------------------------------------------------------------------


async def test_handle_save_directkey_success(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test save_directkey service handler calls client.write_profile_recipe."""
    client = _mock_client()
    client.write_profile_recipe = AsyncMock(return_value=True)
    client.active_profile = 2
    await _setup_entry_with_client(hass, mock_entry, client)

    registry = er.async_get(hass)
    registry.async_get_or_create(
        "button", DOMAIN,
        f"{MOCK_ADDRESS}_brew",
        config_entry=mock_entry,
    )
    entity_id = registry.async_get_entity_id("button", DOMAIN, f"{MOCK_ADDRESS}_brew")

    await hass.services.async_call(
        DOMAIN, "save_directkey",
        {
            "entity_id": entity_id,
            "category": "latte_macchiato",
            "process1": "coffee",
            "intensity1": "medium",
            "aroma1": "standard",
            "portion1_ml": 40,
            "temperature1": "normal",
            "shots1": "one",
            "process2": "milk",
            "intensity2": "medium",
            "aroma2": "standard",
            "portion2_ml": 100,
            "temperature2": "normal",
            "shots2": "none",
        },
        blocking=True,
    )

    client.write_profile_recipe.assert_awaited_once()
    args = client.write_profile_recipe.call_args[0]
    # profile_id defaults to client.active_profile (2)
    assert args[0] == 2
    from custom_components.melitta_barista.const import DirectKeyCategory
    assert args[1] == DirectKeyCategory.LATTE_MACCHIATO


async def test_handle_save_directkey_with_explicit_profile(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test save_directkey with an explicit profile_id."""
    client = _mock_client()
    client.write_profile_recipe = AsyncMock(return_value=True)
    client.active_profile = 0
    await _setup_entry_with_client(hass, mock_entry, client)

    registry = er.async_get(hass)
    registry.async_get_or_create(
        "button", DOMAIN,
        f"{MOCK_ADDRESS}_brew",
        config_entry=mock_entry,
    )
    entity_id = registry.async_get_entity_id("button", DOMAIN, f"{MOCK_ADDRESS}_brew")

    await hass.services.async_call(
        DOMAIN, "save_directkey",
        {
            "entity_id": entity_id,
            "category": "water",
            "profile_id": 5,
            "process1": "water",
        },
        blocking=True,
    )

    args = client.write_profile_recipe.call_args[0]
    assert args[0] == 5


async def test_handle_save_directkey_client_not_found(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test save_directkey raises ServiceValidationError when no client found."""
    from homeassistant.exceptions import ServiceValidationError

    client = _mock_client()
    await _setup_entry_with_client(hass, mock_entry, client)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "save_directkey",
            {
                "entity_id": "button.nonexistent",
                "category": "espresso",
                "process1": "coffee",
            },
            blocking=True,
        )


async def test_handle_save_directkey_returns_false(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test save_directkey raises HomeAssistantError when write returns False."""
    from homeassistant.exceptions import HomeAssistantError

    client = _mock_client()
    client.write_profile_recipe = AsyncMock(return_value=False)
    await _setup_entry_with_client(hass, mock_entry, client)

    registry = er.async_get(hass)
    registry.async_get_or_create(
        "button", DOMAIN,
        f"{MOCK_ADDRESS}_brew",
        config_entry=mock_entry,
    )
    entity_id = registry.async_get_entity_id("button", DOMAIN, f"{MOCK_ADDRESS}_brew")

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN, "save_directkey",
            {
                "entity_id": entity_id,
                "category": "milk",
                "process1": "milk",
            },
            blocking=True,
        )

    client.write_profile_recipe.assert_awaited_once()


# ---------------------------------------------------------------------------
# Options update listener
# ---------------------------------------------------------------------------


async def test_update_listener_reloads_entry(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test that _async_update_listener triggers a config entry reload."""
    client = _mock_client()
    await _setup_entry_with_client(hass, mock_entry, client)

    assert mock_entry.state is ConfigEntryState.LOADED

    # Trigger options update — this calls _async_update_listener which reloads
    p1, p2, p3 = _setup_patches(_mock_client())
    with p1, p2, p3:
        hass.config_entries.async_update_entry(
            mock_entry, options={"poll_interval": 60}
        )
        await hass.async_block_till_done()

    assert mock_entry.state is ConfigEntryState.LOADED
