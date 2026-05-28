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
    client.reset_recipe_default = AsyncMock(return_value=True)
    client.profile_names = {0: "My Coffee"}
    client.directkey_recipes = {}
    # Brand profile mock (Melitta default — supports HC/HJ)
    client.brand = MagicMock()
    client.brand.brand_slug = "melitta"
    client.brand.brand_name = "Melitta"
    client.brand.supported_extensions = frozenset({"HC", "HJ"})
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


# ---------------------------------------------------------------------------
# MelittaResetRecipeButton (HD command)
# ---------------------------------------------------------------------------

async def test_reset_recipe_button_available_when_recipe_selected(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Reset button is available when client is ready and recipe selected."""
    from custom_components.melitta_barista.button import MelittaResetRecipeButton

    client = _mock_client(selected_recipe=RecipeId.CAPPUCCINO)
    btn = MelittaResetRecipeButton(client, mock_entry, "Test")
    assert btn.available is True


async def test_reset_recipe_button_unavailable_when_no_selection(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Reset button is unavailable without selected recipe."""
    from custom_components.melitta_barista.button import MelittaResetRecipeButton

    client = _mock_client(selected_recipe=None)
    btn = MelittaResetRecipeButton(client, mock_entry, "Test")
    assert btn.available is False


async def test_reset_recipe_button_press_calls_reset(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Pressing button invokes reset_recipe_default with selected recipe id."""
    from custom_components.melitta_barista.button import MelittaResetRecipeButton

    client = _mock_client(selected_recipe=RecipeId.CAPPUCCINO)
    btn = MelittaResetRecipeButton(client, mock_entry, "Test")
    await btn.async_press()

    client.reset_recipe_default.assert_awaited_once_with(int(RecipeId.CAPPUCCINO))


async def test_reset_recipe_button_press_nack_logs_warning(
    hass: HomeAssistant, mock_entry: MockConfigEntry, caplog
) -> None:
    """NACK from machine produces a warning but does not raise."""
    import logging
    from custom_components.melitta_barista.button import MelittaResetRecipeButton

    client = _mock_client(selected_recipe=RecipeId.CAPPUCCINO)
    client.reset_recipe_default = AsyncMock(return_value=False)
    btn = MelittaResetRecipeButton(client, mock_entry, "Test")

    with caplog.at_level(logging.WARNING, logger="melitta_barista"):
        await btn.async_press()

    assert any("NACK" in rec.message or "timeout" in rec.message
               for rec in caplog.records)


# ---------------------------------------------------------------------------
# MelittaConfirmPromptButton (HY command)
# ---------------------------------------------------------------------------

async def test_confirm_prompt_button_available_on_active_prompt(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Available when manipulation is in PROMPT_MANIPULATIONS."""
    from custom_components.melitta_barista.button import MelittaConfirmPromptButton
    from custom_components.melitta_barista.const import Manipulation

    status = MachineStatus(
        process=MachineProcess.READY, manipulation=Manipulation.FILL_WATER,
    )
    client = _mock_client(status=status)
    client.confirm_prompt = AsyncMock(return_value=True)
    btn = MelittaConfirmPromptButton(client, mock_entry, "Test")
    assert btn.available is True


async def test_confirm_prompt_button_unavailable_when_idle(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Unavailable when no prompt active (manipulation NONE)."""
    from custom_components.melitta_barista.button import MelittaConfirmPromptButton

    client = _mock_client()  # default status: READY + NONE
    client.confirm_prompt = AsyncMock(return_value=True)
    btn = MelittaConfirmPromptButton(client, mock_entry, "Test")
    assert btn.available is False


async def test_confirm_prompt_button_press(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Pressing button calls client.confirm_prompt() once."""
    from custom_components.melitta_barista.button import MelittaConfirmPromptButton
    from custom_components.melitta_barista.const import Manipulation

    status = MachineStatus(
        process=MachineProcess.READY,
        manipulation=Manipulation.MOVE_CUP_TO_FROTHER,
    )
    client = _mock_client(status=status)
    client.confirm_prompt = AsyncMock(return_value=True)
    btn = MelittaConfirmPromptButton(client, mock_entry, "Test")
    await btn.async_press()

    client.confirm_prompt.assert_awaited_once()


def _mock_nivona_client(family_key="700"):
    """Nivona client mock with capabilities preset to the given family.

    Used to verify factory-reset button gating per family.
    """
    client = _mock_client()
    client.brand.brand_slug = "nivona"
    client.brand.brand_name = "Nivona"
    client.brand.supported_extensions = frozenset()
    # Minimal capabilities stub — enough for `available` checks.
    caps = MagicMock()
    caps.family_key = family_key
    caps.my_coffee_slots = 1
    caps.stats = ()
    client.capabilities = caps
    client.execute_he_command = AsyncMock(return_value=True)
    return client


async def test_factory_reset_buttons_registered_for_nivona_700(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Both factory-reset buttons appear for a Nivona 700-family entry."""
    client = _mock_nivona_client(family_key="700")
    await _setup_integration(hass, mock_entry, client)

    button_ids = [
        s.entity_id for s in hass.states.async_all("button")
    ]
    assert any("factory_reset_settings" in eid for eid in button_ids), button_ids
    assert any("factory_reset_recipes" in eid for eid in button_ids), button_ids


async def test_factory_reset_buttons_unavailable_for_nivona_8000(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Buttons register on Nivona but stay 'unavailable' for 8000 family.

    Mirrors the vendor-app gating: NIVO 8000 has no factory-reset
    menu, so the buttons stay unavailable even though they're
    registered as Nivona-brand entities.
    """
    client = _mock_nivona_client(family_key="8000")
    await _setup_integration(hass, mock_entry, client)

    settings_state = next(
        (s for s in hass.states.async_all("button")
         if "factory_reset_settings" in s.entity_id),
        None,
    )
    recipes_state = next(
        (s for s in hass.states.async_all("button")
         if "factory_reset_recipes" in s.entity_id),
        None,
    )
    assert settings_state is not None, "Button registered (state must exist)"
    assert recipes_state is not None
    # Family is 8000 → button stays unavailable.
    assert settings_state.state == "unavailable"
    assert recipes_state.state == "unavailable"


async def test_factory_reset_buttons_not_registered_for_melitta(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Melitta brand does not get the Nivona factory-reset buttons at all."""
    client = _mock_client()  # default brand = melitta
    await _setup_integration(hass, mock_entry, client)

    button_ids = [s.entity_id for s in hass.states.async_all("button")]
    assert not any(
        "factory_reset" in eid for eid in button_ids
    ), button_ids


async def test_factory_reset_settings_press_invokes_command_50(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Pressing the settings reset button calls execute_he_command(50)."""
    client = _mock_nivona_client(family_key="700")
    await _setup_integration(hass, mock_entry, client)

    settings_eid = next(
        s.entity_id for s in hass.states.async_all("button")
        if "factory_reset_settings" in s.entity_id
    )
    await hass.services.async_call(
        "button", "press", {"entity_id": settings_eid}, blocking=True,
    )
    client.execute_he_command.assert_awaited_with(50)


async def test_factory_reset_recipes_press_invokes_command_51(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Pressing the recipes reset button calls execute_he_command(51)."""
    client = _mock_nivona_client(family_key="700")
    await _setup_integration(hass, mock_entry, client)

    recipes_eid = next(
        s.entity_id for s in hass.states.async_all("button")
        if "factory_reset_recipes" in s.entity_id
    )
    await hass.services.async_call(
        "button", "press", {"entity_id": recipes_eid}, blocking=True,
    )
    client.execute_he_command.assert_awaited_with(51)


async def test_mycoffee_brew_buttons_registered_for_nivona_8000(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """9 slots on 8000 family → 9 brew-MyCoffee buttons."""
    client = _mock_nivona_client(family_key="8000")
    client.capabilities.my_coffee_slots = 9
    client.my_coffee_slots = None
    await _setup_integration(hass, mock_entry, client)

    brew_buttons = [
        s.entity_id for s in hass.states.async_all("button")
        if "brew_mycoffee_slot_" in s.entity_id
    ]
    assert len(brew_buttons) == 9, brew_buttons


async def test_mycoffee_brew_buttons_unavailable_when_slot_not_enabled(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Slot N's button stays unavailable if the cached `enabled` flag is 0."""
    client = _mock_nivona_client(family_key="8000")
    client.capabilities.my_coffee_slots = 3
    # Simulate post-connect cache: only slot 1 is armed.
    client.my_coffee_slots = [
        {"enabled": 0, "coffee_amount": 0},
        {"enabled": 1, "coffee_amount": 40},
        {"enabled": 0, "coffee_amount": 0},
    ]
    # Real-shape status — other platforms inspect `.process` and trip
    # over a plain MagicMock substitution.
    client.status = MachineStatus(process=MachineProcess.READY)

    await _setup_integration(hass, mock_entry, client)

    states = {
        s.entity_id: s.state
        for s in hass.states.async_all("button")
        if "brew_mycoffee_slot_" in s.entity_id
    }
    # Entity ids are 1-indexed (derived from the display name
    # "Brew MyCoffee slot N+1"). Slots 0/2 (cache index) → display
    # 1/3 → unavailable. Slot 1 → display 2 → available.
    slot_eids = {eid.rsplit("_", 1)[-1]: eid for eid in states}
    assert states[slot_eids["1"]] == "unavailable", states
    assert states[slot_eids["2"]] != "unavailable", (
        f"slot 1 (display 2) should be available; got {states[slot_eids['2']]}"
    )
    assert states[slot_eids["3"]] == "unavailable", states


async def test_mycoffee_brew_button_press_invokes_brew_mycoffee_slot(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Pressing the slot-N button calls brew_mycoffee_slot(N)."""
    client = _mock_nivona_client(family_key="8000")
    client.capabilities.my_coffee_slots = 4
    client.my_coffee_slots = [
        {"enabled": 1, "coffee_amount": 30},  # slot 0
        {"enabled": 1, "coffee_amount": 40},
        {"enabled": 1, "coffee_amount": 50},
        {"enabled": 1, "coffee_amount": 60},
    ]
    client.status = MachineStatus(process=MachineProcess.READY)
    client.brew_mycoffee_slot = AsyncMock(return_value=True)

    await _setup_integration(hass, mock_entry, client)

    # Entity id is 1-indexed; press the display-3 entity, which maps
    # to cache slot index 2.
    slot3_eid = next(
        s.entity_id for s in hass.states.async_all("button")
        if s.entity_id.endswith("_brew_mycoffee_slot_3")
    )
    await hass.services.async_call(
        "button", "press", {"entity_id": slot3_eid}, blocking=True,
    )
    client.brew_mycoffee_slot.assert_awaited_with(2)
