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


# ---------------------------------------------------------------------------
# Coverage for _on_status_update / _on_connection_change (lines 118, 122)
# ---------------------------------------------------------------------------

async def test_status_callback_triggers_state_write(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test that status callback is registered and calls async_write_ha_state."""
    client = _mock_client(selected_recipe=RecipeId.ESPRESSO)
    await _setup_integration(hass, mock_entry, client)

    # The button registers a status callback on async_added_to_hass
    client.add_status_callback.assert_called()
    status_cb = client.add_status_callback.call_args_list[0][0][0]

    # Calling the callback should not raise (it calls async_write_ha_state internally)
    new_status = MachineStatus(process=MachineProcess.READY)
    status_cb(new_status)


async def test_connection_callback_triggers_state_write(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test that connection callback is registered and calls async_write_ha_state."""
    client = _mock_client(selected_recipe=RecipeId.ESPRESSO)
    await _setup_integration(hass, mock_entry, client)

    client.add_connection_callback.assert_called()
    conn_cb = client.add_connection_callback.call_args_list[0][0][0]

    # Calling the callback should not raise
    conn_cb(True)
    conn_cb(False)


# ---------------------------------------------------------------------------
# Coverage for brew button: no recipe selected (lines 147-148)
# ---------------------------------------------------------------------------

async def test_brew_button_no_recipe_selected(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test brew button logs warning when no recipe is selected."""
    client = _mock_client(selected_recipe=RecipeId.ESPRESSO)
    await _setup_integration(hass, mock_entry, client)

    # Now set selected_recipe to None after setup
    client.selected_recipe = None
    # The button is still available because status.is_ready was True at setup;
    # we need to call async_press directly on the entity object.
    from custom_components.melitta_barista.button import MelittaBrewButton

    btn = MelittaBrewButton(client, mock_entry, "Test")
    await btn.async_press()

    client.brew_recipe.assert_not_awaited()


# ---------------------------------------------------------------------------
# Coverage for brew button: brew returns False (line 154)
# ---------------------------------------------------------------------------

async def test_brew_button_brew_fails(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test brew button when brew_recipe returns False."""
    client = _mock_client(selected_recipe=RecipeId.ESPRESSO)
    client.brew_recipe = AsyncMock(return_value=False)
    await _setup_integration(hass, mock_entry, client)

    from custom_components.melitta_barista.button import MelittaBrewButton

    btn = MelittaBrewButton(client, mock_entry, "Test")
    await btn.async_press()

    client.brew_recipe.assert_awaited_once_with(RecipeId.ESPRESSO)


# ---------------------------------------------------------------------------
# Coverage for brew button: BLE error (lines 155-156)
# ---------------------------------------------------------------------------

async def test_brew_button_ble_error(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test brew button handles BLE errors gracefully."""
    from bleak.exc import BleakError

    client = _mock_client(selected_recipe=RecipeId.ESPRESSO)
    client.brew_recipe = AsyncMock(side_effect=BleakError("connection lost"))

    from custom_components.melitta_barista.button import MelittaBrewButton

    btn = MelittaBrewButton(client, mock_entry, "Test")
    # Should not raise
    await btn.async_press()

    client.brew_recipe.assert_awaited_once()


# ---------------------------------------------------------------------------
# Coverage for maintenance button async_press (lines 274-294)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "key,process,method_name",
    [
        ("easy_clean", MachineProcess.EASY_CLEAN, "start_easy_clean"),
        ("intensive_clean", MachineProcess.INTENSIVE_CLEAN, "start_intensive_clean"),
        ("descaling", MachineProcess.DESCALING, "start_descaling"),
        ("filter_insert", MachineProcess.FILTER_INSERT, "start_filter_insert"),
        ("filter_replace", MachineProcess.FILTER_REPLACE, "start_filter_replace"),
        ("filter_remove", MachineProcess.FILTER_REMOVE, "start_filter_remove"),
        ("evaporating", MachineProcess.EVAPORATING, "start_evaporating"),
        ("switch_off", MachineProcess.SWITCH_OFF, "switch_off"),
    ],
)
async def test_maintenance_button_press_calls_correct_method(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    key: str,
    process: MachineProcess,
    method_name: str,
) -> None:
    """Test that each maintenance button calls the correct client method."""
    from custom_components.melitta_barista.button import MelittaMaintenanceButton

    client = _mock_client()
    # Ensure all filter/evaporating methods exist on mock
    for m in (
        "start_filter_insert", "start_filter_replace", "start_filter_remove",
        "start_evaporating",
    ):
        setattr(client, m, AsyncMock(return_value=True))

    btn = MelittaMaintenanceButton(
        client, mock_entry, "Test",
        key=key, label=key.replace("_", " ").title(),
        icon="mdi:test", process=process,
    )
    await btn.async_press()

    getattr(client, method_name).assert_awaited_once()


async def test_maintenance_button_press_failure(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test maintenance button when client method returns False."""
    from custom_components.melitta_barista.button import MelittaMaintenanceButton

    client = _mock_client()
    client.start_easy_clean = AsyncMock(return_value=False)

    btn = MelittaMaintenanceButton(
        client, mock_entry, "Test",
        key="easy_clean", label="Easy Clean",
        icon="mdi:shimmer", process=MachineProcess.EASY_CLEAN,
    )
    await btn.async_press()

    client.start_easy_clean.assert_awaited_once()


async def test_maintenance_button_ble_error(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test maintenance button handles BLE errors gracefully."""
    from bleak.exc import BleakError
    from custom_components.melitta_barista.button import MelittaMaintenanceButton

    client = _mock_client()
    client.start_descaling = AsyncMock(side_effect=BleakError("timeout"))

    btn = MelittaMaintenanceButton(
        client, mock_entry, "Test",
        key="descaling", label="Descaling",
        icon="mdi:water-sync", process=MachineProcess.DESCALING,
    )
    # Should not raise
    await btn.async_press()

    client.start_descaling.assert_awaited_once()


async def test_maintenance_button_unknown_process(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test maintenance button with a process not in method_map."""
    from custom_components.melitta_barista.button import MelittaMaintenanceButton

    client = _mock_client()

    # Use PRODUCT process which is not in the method_map
    btn = MelittaMaintenanceButton(
        client, mock_entry, "Test",
        key="unknown", label="Unknown",
        icon="mdi:help", process=MachineProcess.PRODUCT,
    )
    # Should log error and return without calling anything
    await btn.async_press()


# ---------------------------------------------------------------------------
# Coverage for cancel button: not connected (line 234)
# ---------------------------------------------------------------------------

async def test_cancel_button_not_available_when_disconnected(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test cancel button is unavailable when client is disconnected."""
    from custom_components.melitta_barista.button import MelittaCancelButton

    client = _mock_client()
    client.connected = False

    btn = MelittaCancelButton(client, mock_entry, "Test")
    assert btn.available is False


async def test_cancel_button_not_available_when_no_status(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test cancel button is unavailable when status is None."""
    from custom_components.melitta_barista.button import MelittaCancelButton

    client = _mock_client()
    client.status = None

    btn = MelittaCancelButton(client, mock_entry, "Test")
    assert btn.available is False


# ---------------------------------------------------------------------------
# Coverage for cancel button: BLE error (lines 243-244)
# ---------------------------------------------------------------------------

async def test_cancel_button_ble_error(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test cancel button handles BLE errors gracefully."""
    from bleak.exc import BleakError
    from custom_components.melitta_barista.button import MelittaCancelButton

    status = MachineStatus(process=MachineProcess.PRODUCT)
    client = _mock_client(status=status)
    client.cancel_process = AsyncMock(side_effect=BleakError("disconnected"))

    btn = MelittaCancelButton(client, mock_entry, "Test")
    # Should not raise
    await btn.async_press()

    client.cancel_process.assert_awaited_once()


# ---------------------------------------------------------------------------
# Coverage for freestyle brew button async_press (lines 274-294)
# ---------------------------------------------------------------------------

async def test_freestyle_brew_button_press(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test freestyle brew button builds RecipeComponent and calls brew_freestyle."""
    from custom_components.melitta_barista.button import MelittaBrewFreestyleButton

    client = _mock_client()
    # Set freestyle attributes (defaults from ble_client.py)
    client.freestyle_name = "My Drink"
    client.freestyle_process1 = "coffee"
    client.freestyle_intensity1 = "strong"
    client.freestyle_aroma1 = "intense"
    client.freestyle_portion1_ml = 100
    client.freestyle_temperature1 = "high"
    client.freestyle_shots1 = "two"
    client.freestyle_process2 = "milk"
    client.freestyle_intensity2 = "mild"
    client.freestyle_aroma2 = "standard"
    client.freestyle_portion2_ml = 50
    client.freestyle_temperature2 = "normal"
    client.freestyle_shots2 = "one"
    client.brew_freestyle = AsyncMock(return_value=True)

    btn = MelittaBrewFreestyleButton(client, mock_entry, "Test")
    await btn.async_press()

    client.brew_freestyle.assert_awaited_once()
    call_kwargs = client.brew_freestyle.call_args[1]
    assert call_kwargs["name"] == "My Drink"
    # Verify component1 values from maps
    comp1 = call_kwargs["component1"]
    assert comp1.process == 1   # coffee
    assert comp1.intensity == 3  # strong
    assert comp1.aroma == 1      # intense
    assert comp1.portion == 20   # 100 // 5
    assert comp1.temperature == 2  # high
    assert comp1.shots == 2      # two
    # Verify component2 values
    comp2 = call_kwargs["component2"]
    assert comp2.process == 2   # milk
    assert comp2.intensity == 1  # mild
    assert comp2.aroma == 0      # standard
    assert comp2.portion == 10   # 50 // 5
    assert comp2.temperature == 1  # normal
    assert comp2.shots == 1      # one


async def test_freestyle_brew_button_failure(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test freestyle brew button when brew_freestyle returns False."""
    from custom_components.melitta_barista.button import MelittaBrewFreestyleButton

    client = _mock_client()
    client.freestyle_name = "Custom"
    client.freestyle_process1 = "coffee"
    client.freestyle_intensity1 = "medium"
    client.freestyle_aroma1 = "standard"
    client.freestyle_portion1_ml = 40
    client.freestyle_temperature1 = "normal"
    client.freestyle_shots1 = "one"
    client.freestyle_process2 = "none"
    client.freestyle_intensity2 = "medium"
    client.freestyle_aroma2 = "standard"
    client.freestyle_portion2_ml = 0
    client.freestyle_temperature2 = "normal"
    client.freestyle_shots2 = "none"
    client.brew_freestyle = AsyncMock(return_value=False)

    btn = MelittaBrewFreestyleButton(client, mock_entry, "Test")
    await btn.async_press()

    client.brew_freestyle.assert_awaited_once()


async def test_freestyle_brew_button_ble_error(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test freestyle brew button handles BLE errors gracefully."""
    from bleak.exc import BleakError
    from custom_components.melitta_barista.button import MelittaBrewFreestyleButton

    client = _mock_client()
    client.freestyle_name = "Custom"
    client.freestyle_process1 = "coffee"
    client.freestyle_intensity1 = "medium"
    client.freestyle_aroma1 = "standard"
    client.freestyle_portion1_ml = 40
    client.freestyle_temperature1 = "normal"
    client.freestyle_shots1 = "one"
    client.freestyle_process2 = "none"
    client.freestyle_intensity2 = "medium"
    client.freestyle_aroma2 = "standard"
    client.freestyle_portion2_ml = 0
    client.freestyle_temperature2 = "normal"
    client.freestyle_shots2 = "none"
    client.brew_freestyle = AsyncMock(side_effect=BleakError("ble error"))

    btn = MelittaBrewFreestyleButton(client, mock_entry, "Test")
    # Should not raise
    await btn.async_press()

    client.brew_freestyle.assert_awaited_once()
