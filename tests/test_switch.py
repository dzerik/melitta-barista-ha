"""Tests for switch platform entities (MelittaSettingSwitch, MelittaProfileActivitySwitch)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bleak.exc import BleakError
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.melitta_barista.const import (
    DOMAIN,
    MachineProcess,
    MachineSettingId,
    MachineType,
)
from custom_components.melitta_barista.protocol import MachineStatus
from custom_components.melitta_barista.switch import (
    MelittaProfileActivitySwitch,
    MelittaSettingSwitch,
)

from . import MOCK_ADDRESS, MOCK_CONFIG_DATA


def _mock_client(machine_type=None):
    """Create a MelittaBleClient mock for switch tests."""
    client = MagicMock()
    client.address = MOCK_ADDRESS
    client.connected = True
    client.status = MachineStatus(process=MachineProcess.READY)
    client.firmware_version = "1.0.0"
    client.machine_type = machine_type
    client.model_name = "Melitta Barista"
    client.selected_recipe = None
    client.set_ble_device = MagicMock()
    client.add_status_callback = MagicMock()
    client.add_connection_callback = MagicMock()
    client.remove_connection_callback = MagicMock()
    client.connect = AsyncMock(return_value=True)
    client.disconnect = AsyncMock()
    client.start_polling = MagicMock()
    client.read_setting = AsyncMock(return_value=None)
    client.write_setting = AsyncMock(return_value=True)
    client.read_profile_activity = AsyncMock(return_value=None)
    client.write_profile_activity = AsyncMock(return_value=True)
    client.read_recipe = AsyncMock(return_value=None)
    client.read_alpha = AsyncMock(return_value=None)
    client.write_alpha = AsyncMock(return_value=True)
    client.read_cup_counters = AsyncMock(return_value=True)
    client.read_profile_data = AsyncMock()
    client.read_all_profile_names = AsyncMock(return_value={0: "My Coffee"})
    client.read_profile_name = AsyncMock(return_value=None)
    client.write_profile_name = AsyncMock(return_value=True)
    client.add_profile_callback = MagicMock()
    client.remove_profile_callback = MagicMock()
    client.add_cups_callback = MagicMock()
    client.remove_cups_callback = MagicMock()
    client.remove_status_callback = MagicMock()
    client.profile_names = {0: "My Coffee"}
    # Brand profile mock (Melitta default — supports HC/HJ)
    client.brand = MagicMock()
    client.brand.brand_slug = "melitta"
    client.brand.brand_name = "Melitta"
    client.brand.supported_extensions = frozenset({"HC", "HJ"})
    client.directkey_recipes = {}
    client.total_cups = 0
    client.cup_counters = {}
    client.active_profile = 0
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
    return client


@pytest.fixture
def mock_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data=MOCK_CONFIG_DATA,
        unique_id="aabbccddeeff",
    )


async def _setup_integration(hass, mock_entry, client):
    """Setup the integration with a mock client."""
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


# ---------------------------------------------------------------------------
# MelittaSettingSwitch tests
# ---------------------------------------------------------------------------


class TestMelittaSettingSwitchCreation:
    """Tests for switch entity creation and setup."""

    async def test_switches_created(
        self, hass: HomeAssistant, mock_entry: MockConfigEntry
    ) -> None:
        """Test that setting switch entities are created."""
        client = _mock_client()
        await _setup_integration(hass, mock_entry, client)

        switches = hass.states.async_all("switch")
        switch_ids = [s.entity_id for s in switches]

        assert any("energy_saving" in eid for eid in switch_ids)
        assert any("rinsing" in eid for eid in switch_ids)

    async def test_profile_activity_switches_created(
        self, hass: HomeAssistant, mock_entry: MockConfigEntry
    ) -> None:
        """Test that profile activity switch entities are created."""
        client = _mock_client()
        await _setup_integration(hass, mock_entry, client)

        switches = hass.states.async_all("switch")
        switch_ids = [s.entity_id for s in switches]

        assert any("profile" in eid and "active" in eid for eid in switch_ids)

    async def test_connection_callback_registered(
        self, hass: HomeAssistant, mock_entry: MockConfigEntry
    ) -> None:
        """Test that switches register their connection callback on setup."""
        client = _mock_client()
        await _setup_integration(hass, mock_entry, client)

        # Each setting switch + each profile switch registers a callback
        assert client.add_connection_callback.call_count >= 3


# ---------------------------------------------------------------------------
# MelittaSettingSwitch — _async_read_value (lines 113-121)
# ---------------------------------------------------------------------------


class TestSettingSwitchReadValue:
    """Tests for MelittaSettingSwitch._async_read_value."""

    async def test_read_value_on_connect_sets_is_on_true(
        self, hass: HomeAssistant, mock_entry: MockConfigEntry
    ) -> None:
        """Test that _on_connection_change(True) reads the setting and sets is_on=True."""
        client = _mock_client()
        client.read_setting = AsyncMock(return_value=1)
        await _setup_integration(hass, mock_entry, client)

        # Find a registered connection callback for a setting switch
        callbacks = [
            call.args[0]
            for call in client.add_connection_callback.call_args_list
        ]
        assert len(callbacks) >= 1

        # Simulate connection event
        for cb in callbacks:
            cb(True)
        await hass.async_block_till_done()

        # read_setting should have been called
        assert client.read_setting.await_count >= 1

    async def test_read_value_on_connect_sets_is_on_false(
        self, hass: HomeAssistant, mock_entry: MockConfigEntry
    ) -> None:
        """Test that _async_read_value sets is_on=False when setting value is 0."""
        client = _mock_client()
        client.read_setting = AsyncMock(return_value=0)
        await _setup_integration(hass, mock_entry, client)

        callbacks = [
            call.args[0]
            for call in client.add_connection_callback.call_args_list
        ]
        for cb in callbacks:
            cb(True)
        await hass.async_block_till_done()

        assert client.read_setting.await_count >= 1

    async def test_read_value_none_does_not_update_state(
        self, hass: HomeAssistant, mock_entry: MockConfigEntry
    ) -> None:
        """Test that _async_read_value does nothing when read returns None."""
        client = _mock_client()
        client.read_setting = AsyncMock(return_value=None)
        await _setup_integration(hass, mock_entry, client)

        callbacks = [
            call.args[0]
            for call in client.add_connection_callback.call_args_list
        ]
        for cb in callbacks:
            cb(True)
        await hass.async_block_till_done()

        # Should not raise; state remains unknown

    async def test_read_value_bleak_error_handled(
        self, hass: HomeAssistant, mock_entry: MockConfigEntry
    ) -> None:
        """Test that BleakError during read is caught gracefully."""
        client = _mock_client()
        client.read_setting = AsyncMock(side_effect=BleakError("disconnected"))
        await _setup_integration(hass, mock_entry, client)

        callbacks = [
            call.args[0]
            for call in client.add_connection_callback.call_args_list
        ]
        for cb in callbacks:
            cb(True)
        await hass.async_block_till_done()
        # Should not raise

    async def test_read_value_timeout_error_handled(
        self, hass: HomeAssistant, mock_entry: MockConfigEntry
    ) -> None:
        """Test that TimeoutError during read is caught gracefully."""
        client = _mock_client()
        client.read_setting = AsyncMock(side_effect=asyncio.TimeoutError())
        await _setup_integration(hass, mock_entry, client)

        callbacks = [
            call.args[0]
            for call in client.add_connection_callback.call_args_list
        ]
        for cb in callbacks:
            cb(True)
        await hass.async_block_till_done()

    async def test_on_connection_change_disconnect_does_not_read(
        self, hass: HomeAssistant, mock_entry: MockConfigEntry
    ) -> None:
        """Test that _on_connection_change(False) does NOT trigger a read."""
        client = _mock_client()
        client.read_setting = AsyncMock(return_value=1)
        await _setup_integration(hass, mock_entry, client)

        callbacks = [
            call.args[0]
            for call in client.add_connection_callback.call_args_list
        ]
        for cb in callbacks:
            cb(False)
        await hass.async_block_till_done()

        # read_setting should NOT have been called
        client.read_setting.assert_not_awaited()


# ---------------------------------------------------------------------------
# MelittaSettingSwitch — async_turn_on / async_turn_off (lines 123-131)
# ---------------------------------------------------------------------------


class TestSettingSwitchTurnOnOff:
    """Tests for MelittaSettingSwitch turn_on/turn_off."""

    async def test_turn_on_success(
        self, hass: HomeAssistant, mock_entry: MockConfigEntry
    ) -> None:
        """Test turn_on writes setting=1 and updates state to on."""
        client = _mock_client()
        client.write_setting = AsyncMock(return_value=True)
        await _setup_integration(hass, mock_entry, client)

        # Find an energy_saving switch entity
        switches = hass.states.async_all("switch")
        energy_switch = [s for s in switches if "energy_saving" in s.entity_id]
        assert len(energy_switch) >= 1

        await hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": energy_switch[0].entity_id},
            blocking=True,
        )

        # write_setting should have been called with (setting_id, 1)
        client.write_setting.assert_awaited()
        call_args = client.write_setting.call_args
        assert call_args[0][1] == 1

    async def test_turn_off_success(
        self, hass: HomeAssistant, mock_entry: MockConfigEntry
    ) -> None:
        """Test turn_off writes setting=0 and updates state to off."""
        client = _mock_client()
        client.write_setting = AsyncMock(return_value=True)
        await _setup_integration(hass, mock_entry, client)

        switches = hass.states.async_all("switch")
        energy_switch = [s for s in switches if "energy_saving" in s.entity_id]
        assert len(energy_switch) >= 1

        await hass.services.async_call(
            "switch",
            "turn_off",
            {"entity_id": energy_switch[0].entity_id},
            blocking=True,
        )

        client.write_setting.assert_awaited()
        call_args = client.write_setting.call_args
        assert call_args[0][1] == 0

    async def test_turn_on_failure_does_not_update_state(
        self, hass: HomeAssistant, mock_entry: MockConfigEntry
    ) -> None:
        """Test that turn_on with write failure does not set is_on=True."""
        client = _mock_client()
        client.write_setting = AsyncMock(return_value=False)
        await _setup_integration(hass, mock_entry, client)

        switches = hass.states.async_all("switch")
        energy_switch = [s for s in switches if "energy_saving" in s.entity_id]
        assert len(energy_switch) >= 1

        await hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": energy_switch[0].entity_id},
            blocking=True,
        )

        # State should remain unknown (not "on")
        state = hass.states.get(energy_switch[0].entity_id)
        assert state.state != "on"

    async def test_turn_off_failure_does_not_update_state(
        self, hass: HomeAssistant, mock_entry: MockConfigEntry
    ) -> None:
        """Test that turn_off with write failure does not set is_on=False."""
        client = _mock_client()
        client.write_setting = AsyncMock(return_value=False)
        await _setup_integration(hass, mock_entry, client)

        switches = hass.states.async_all("switch")
        energy_switch = [s for s in switches if "energy_saving" in s.entity_id]
        assert len(energy_switch) >= 1

        # First turn on successfully
        client.write_setting = AsyncMock(return_value=True)
        await hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": energy_switch[0].entity_id},
            blocking=True,
        )

        # Now fail the turn_off
        client.write_setting = AsyncMock(return_value=False)
        await hass.services.async_call(
            "switch",
            "turn_off",
            {"entity_id": energy_switch[0].entity_id},
            blocking=True,
        )

        # State should remain "on" since write failed
        state = hass.states.get(energy_switch[0].entity_id)
        assert state.state == "on"


# ---------------------------------------------------------------------------
# MelittaProfileActivitySwitch — _async_read_value (lines 176-184)
# ---------------------------------------------------------------------------


class TestProfileActivitySwitchReadValue:
    """Tests for MelittaProfileActivitySwitch._async_read_value."""

    async def test_read_profile_activity_on_connect(
        self, hass: HomeAssistant, mock_entry: MockConfigEntry
    ) -> None:
        """Test that profile activity is read on connection."""
        client = _mock_client()
        client.read_profile_activity = AsyncMock(return_value=1)
        await _setup_integration(hass, mock_entry, client)

        callbacks = [
            call.args[0]
            for call in client.add_connection_callback.call_args_list
        ]
        for cb in callbacks:
            cb(True)
        await hass.async_block_till_done()

        assert client.read_profile_activity.await_count >= 1

    async def test_read_profile_activity_value_zero_sets_off(
        self, hass: HomeAssistant, mock_entry: MockConfigEntry
    ) -> None:
        """Test that read_profile_activity returning 0 sets is_on=False."""
        client = _mock_client()
        client.read_profile_activity = AsyncMock(return_value=0)
        await _setup_integration(hass, mock_entry, client)

        callbacks = [
            call.args[0]
            for call in client.add_connection_callback.call_args_list
        ]
        for cb in callbacks:
            cb(True)
        await hass.async_block_till_done()

        assert client.read_profile_activity.await_count >= 1

    async def test_read_profile_activity_none_no_update(
        self, hass: HomeAssistant, mock_entry: MockConfigEntry
    ) -> None:
        """Test that read_profile_activity returning None does not update state."""
        client = _mock_client()
        client.read_profile_activity = AsyncMock(return_value=None)
        await _setup_integration(hass, mock_entry, client)

        callbacks = [
            call.args[0]
            for call in client.add_connection_callback.call_args_list
        ]
        for cb in callbacks:
            cb(True)
        await hass.async_block_till_done()

    async def test_read_profile_activity_bleak_error_handled(
        self, hass: HomeAssistant, mock_entry: MockConfigEntry
    ) -> None:
        """Test that BleakError during profile activity read is caught."""
        client = _mock_client()
        client.read_profile_activity = AsyncMock(
            side_effect=BleakError("disconnected")
        )
        await _setup_integration(hass, mock_entry, client)

        callbacks = [
            call.args[0]
            for call in client.add_connection_callback.call_args_list
        ]
        for cb in callbacks:
            cb(True)
        await hass.async_block_till_done()

    async def test_read_profile_activity_os_error_handled(
        self, hass: HomeAssistant, mock_entry: MockConfigEntry
    ) -> None:
        """Test that OSError during profile activity read is caught."""
        client = _mock_client()
        client.read_profile_activity = AsyncMock(side_effect=OSError("I/O error"))
        await _setup_integration(hass, mock_entry, client)

        callbacks = [
            call.args[0]
            for call in client.add_connection_callback.call_args_list
        ]
        for cb in callbacks:
            cb(True)
        await hass.async_block_till_done()

    async def test_on_connection_disconnect_does_not_read_profile(
        self, hass: HomeAssistant, mock_entry: MockConfigEntry
    ) -> None:
        """Test that disconnect event does NOT trigger profile activity read."""
        client = _mock_client()
        client.read_profile_activity = AsyncMock(return_value=1)
        await _setup_integration(hass, mock_entry, client)

        callbacks = [
            call.args[0]
            for call in client.add_connection_callback.call_args_list
        ]
        for cb in callbacks:
            cb(False)
        await hass.async_block_till_done()

        client.read_profile_activity.assert_not_awaited()


# ---------------------------------------------------------------------------
# MelittaProfileActivitySwitch — turn_on / turn_off (lines 186-200)
# ---------------------------------------------------------------------------


class TestProfileActivitySwitchTurnOnOff:
    """Tests for MelittaProfileActivitySwitch turn_on/turn_off."""

    async def test_turn_on_profile_success(
        self, hass: HomeAssistant, mock_entry: MockConfigEntry
    ) -> None:
        """Test enabling a profile via turn_on."""
        client = _mock_client()
        client.write_profile_activity = AsyncMock(return_value=True)
        await _setup_integration(hass, mock_entry, client)

        switches = hass.states.async_all("switch")
        profile_switch = [
            s for s in switches if "profile" in s.entity_id and "active" in s.entity_id
        ]
        assert len(profile_switch) >= 1

        await hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": profile_switch[0].entity_id},
            blocking=True,
        )

        client.write_profile_activity.assert_awaited()
        call_args = client.write_profile_activity.call_args
        assert call_args[0][1] == 1  # value=1 for enable

    async def test_turn_off_profile_success(
        self, hass: HomeAssistant, mock_entry: MockConfigEntry
    ) -> None:
        """Test disabling a profile via turn_off."""
        client = _mock_client()
        client.write_profile_activity = AsyncMock(return_value=True)
        await _setup_integration(hass, mock_entry, client)

        switches = hass.states.async_all("switch")
        profile_switch = [
            s for s in switches if "profile" in s.entity_id and "active" in s.entity_id
        ]
        assert len(profile_switch) >= 1

        await hass.services.async_call(
            "switch",
            "turn_off",
            {"entity_id": profile_switch[0].entity_id},
            blocking=True,
        )

        client.write_profile_activity.assert_awaited()
        call_args = client.write_profile_activity.call_args
        assert call_args[0][1] == 0  # value=0 for disable

    async def test_turn_on_profile_failure_does_not_update(
        self, hass: HomeAssistant, mock_entry: MockConfigEntry
    ) -> None:
        """Test that turn_on with write failure does not change state."""
        client = _mock_client()
        client.write_profile_activity = AsyncMock(return_value=False)
        await _setup_integration(hass, mock_entry, client)

        switches = hass.states.async_all("switch")
        profile_switch = [
            s for s in switches if "profile" in s.entity_id and "active" in s.entity_id
        ]
        assert len(profile_switch) >= 1

        await hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": profile_switch[0].entity_id},
            blocking=True,
        )

        state = hass.states.get(profile_switch[0].entity_id)
        assert state.state != "on"

    async def test_turn_off_profile_failure_does_not_update(
        self, hass: HomeAssistant, mock_entry: MockConfigEntry
    ) -> None:
        """Test that turn_off with write failure does not change state."""
        client = _mock_client()
        client.write_profile_activity = AsyncMock(return_value=True)
        await _setup_integration(hass, mock_entry, client)

        switches = hass.states.async_all("switch")
        profile_switch = [
            s for s in switches if "profile" in s.entity_id and "active" in s.entity_id
        ]
        assert len(profile_switch) >= 1

        # First enable
        await hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": profile_switch[0].entity_id},
            blocking=True,
        )

        # Now fail the turn_off
        client.write_profile_activity = AsyncMock(return_value=False)
        await hass.services.async_call(
            "switch",
            "turn_off",
            {"entity_id": profile_switch[0].entity_id},
            blocking=True,
        )

        state = hass.states.get(profile_switch[0].entity_id)
        assert state.state == "on"

    async def test_turn_on_profile_bleak_error_handled(
        self, hass: HomeAssistant, mock_entry: MockConfigEntry
    ) -> None:
        """Test that BleakError during turn_on is caught gracefully."""
        client = _mock_client()
        client.write_profile_activity = AsyncMock(
            side_effect=BleakError("disconnected")
        )
        await _setup_integration(hass, mock_entry, client)

        switches = hass.states.async_all("switch")
        profile_switch = [
            s for s in switches if "profile" in s.entity_id and "active" in s.entity_id
        ]
        assert len(profile_switch) >= 1

        # Should not raise
        await hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": profile_switch[0].entity_id},
            blocking=True,
        )

    async def test_turn_off_profile_bleak_error_handled(
        self, hass: HomeAssistant, mock_entry: MockConfigEntry
    ) -> None:
        """Test that BleakError during turn_off is caught gracefully."""
        client = _mock_client()
        client.write_profile_activity = AsyncMock(
            side_effect=BleakError("disconnected")
        )
        await _setup_integration(hass, mock_entry, client)

        switches = hass.states.async_all("switch")
        profile_switch = [
            s for s in switches if "profile" in s.entity_id and "active" in s.entity_id
        ]
        assert len(profile_switch) >= 1

        await hass.services.async_call(
            "switch",
            "turn_off",
            {"entity_id": profile_switch[0].entity_id},
            blocking=True,
        )

    async def test_turn_on_profile_timeout_error_handled(
        self, hass: HomeAssistant, mock_entry: MockConfigEntry
    ) -> None:
        """Test that TimeoutError during turn_on is caught gracefully."""
        client = _mock_client()
        client.write_profile_activity = AsyncMock(
            side_effect=asyncio.TimeoutError()
        )
        await _setup_integration(hass, mock_entry, client)

        switches = hass.states.async_all("switch")
        profile_switch = [
            s for s in switches if "profile" in s.entity_id and "active" in s.entity_id
        ]
        assert len(profile_switch) >= 1

        await hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": profile_switch[0].entity_id},
            blocking=True,
        )

    async def test_turn_off_profile_os_error_handled(
        self, hass: HomeAssistant, mock_entry: MockConfigEntry
    ) -> None:
        """Test that OSError during turn_off is caught gracefully."""
        client = _mock_client()
        client.write_profile_activity = AsyncMock(side_effect=OSError("I/O error"))
        await _setup_integration(hass, mock_entry, client)

        switches = hass.states.async_all("switch")
        profile_switch = [
            s for s in switches if "profile" in s.entity_id and "active" in s.entity_id
        ]
        assert len(profile_switch) >= 1

        await hass.services.async_call(
            "switch",
            "turn_off",
            {"entity_id": profile_switch[0].entity_id},
            blocking=True,
        )
