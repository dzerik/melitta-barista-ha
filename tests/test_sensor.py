"""Tests for sensor platform entities."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.melitta_barista.const import DOMAIN, MachineProcess, Manipulation
from custom_components.melitta_barista.protocol import MachineStatus

from . import MOCK_ADDRESS, MOCK_CONFIG_DATA


def _mock_client(status=None):
    """Create a MelittaBleClient mock with optional status."""
    client = MagicMock()
    client.address = MOCK_ADDRESS
    client.connected = True
    client.status = status
    client.firmware_version = "1.2.3"
    client.serial_number = "0259002901420260521"
    client.machine_type = None
    client.model_name = "Melitta Barista"
    client.selected_recipe = None
    client.set_ble_device = MagicMock()
    client.add_status_callback = MagicMock()
    client.add_connection_callback = MagicMock()
    client.connect = AsyncMock(return_value=True)
    client.disconnect = AsyncMock()
    client.start_polling = MagicMock()
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


async def test_sensors_created(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test that all sensor entities are created."""
    client = _mock_client(status=MachineStatus(process=MachineProcess.READY))
    await _setup_integration(hass, mock_entry, client)

    sensors = hass.states.async_all("sensor")
    sensor_ids = [s.entity_id for s in sensors]

    # Should have: state, activity, progress, action_required, connection, firmware
    assert len(sensors) >= 5
    assert any("state" in eid for eid in sensor_ids)
    assert any("activity" in eid for eid in sensor_ids)
    assert any("progress" in eid for eid in sensor_ids)
    assert any("connection" in eid for eid in sensor_ids)
    assert any("firmware" in eid for eid in sensor_ids)


async def test_state_sensor_ready(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test state sensor shows Ready."""
    client = _mock_client(status=MachineStatus(process=MachineProcess.READY))
    await _setup_integration(hass, mock_entry, client)

    state = [s for s in hass.states.async_all("sensor") if "state" in s.entity_id]
    assert len(state) >= 1
    assert state[0].state == "Ready"


async def test_state_sensor_brewing(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test state sensor shows Brewing."""
    client = _mock_client(status=MachineStatus(process=MachineProcess.PRODUCT))
    await _setup_integration(hass, mock_entry, client)

    state = [s for s in hass.states.async_all("sensor") if "state" in s.entity_id]
    assert len(state) >= 1
    assert state[0].state == "Brewing"


async def test_connection_sensor(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test connection sensor shows Connected."""
    client = _mock_client()
    await _setup_integration(hass, mock_entry, client)

    conn = [s for s in hass.states.async_all("sensor") if "connection" in s.entity_id]
    assert len(conn) >= 1
    assert conn[0].state == "Connected"


async def test_firmware_sensor(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test firmware sensor shows version."""
    client = _mock_client()
    await _setup_integration(hass, mock_entry, client)

    fw = [s for s in hass.states.async_all("sensor") if "firmware" in s.entity_id]
    assert len(fw) >= 1
    assert fw[0].state == "1.2.3"


async def test_serial_sensor(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test serial sensor surfaces serial_number from the client."""
    client = _mock_client()
    await _setup_integration(hass, mock_entry, client)

    serial = [s for s in hass.states.async_all("sensor") if "serial" in s.entity_id]
    assert len(serial) >= 1
    assert serial[0].state == "0259002901420260521"


async def test_action_required_sensor(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Test action required sensor with manipulation."""
    status = MachineStatus(
        process=MachineProcess.READY,
        manipulation=Manipulation.FILL_WATER,
    )
    client = _mock_client(status=status)
    await _setup_integration(hass, mock_entry, client)

    action = [s for s in hass.states.async_all("sensor") if "action" in s.entity_id or "manipulation" in s.entity_id]
    assert len(action) >= 1
    assert action[0].state == "Fill Water"


def _mock_nivona_client(status=None):
    """Same as `_mock_client` but with brand_slug = 'nivona' and no HC/HJ.

    Used to verify brand-gated sensor registration logic.
    """
    client = _mock_client(status=status)
    client.brand.brand_slug = "nivona"
    client.brand.brand_name = "Nivona"
    client.brand.supported_extensions = frozenset()
    # capabilities resolved before setup so BrandStatSensor gets created
    client.capabilities = None
    return client


async def test_total_cups_sensor_only_for_melitta(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Legacy `MelittaTotalCupsSensor` reads HR id 150 (`TOTAL_CUPS_ID`),
    which is a Melitta-only register. On Nivona the register doesn't
    exist and the sensor was stuck at `unknown` (reported in #15).
    Gap #12 fix: gate registration to brand_slug == 'melitta'.

    For Melitta the entity still appears as before.
    """
    client = _mock_nivona_client()
    await _setup_integration(hass, mock_entry, client)

    sensor_ids = [s.entity_id for s in hass.states.async_all("sensor")]
    # On Nivona, NO sensor.*_total_cups should be present.
    assert not any(eid.endswith("_total_cups") or "total_cups" in eid for eid in sensor_ids), (
        f"Total Cups sensor must not be registered for Nivona; saw: {sensor_ids}"
    )


async def test_total_cups_sensor_still_created_for_melitta(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Sanity check that Melitta brand still gets the Total Cups sensor."""
    client = _mock_client()  # default brand = melitta
    await _setup_integration(hass, mock_entry, client)

    sensor_ids = [s.entity_id for s in hass.states.async_all("sensor")]
    assert any("total_cups" in eid for eid in sensor_ids), (
        f"Total Cups sensor should still register for Melitta; saw: {sensor_ids}"
    )


async def test_mycoffee_amount_sensors_registered_for_nivona_8000(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Nivona 8000 family with 9 MyCoffee slots → 9 amount sensors."""
    client = _mock_client()  # default mock fits — we override below
    client.brand.brand_slug = "nivona"
    client.brand.brand_name = "Nivona"
    client.brand.supported_extensions = frozenset()
    caps = MagicMock()
    caps.family_key = "8000"
    caps.my_coffee_slots = 9
    caps.stats = ()
    client.capabilities = caps
    client.my_coffee_slots = None
    await _setup_integration(hass, mock_entry, client)

    mycoffee_sensors = [
        s for s in hass.states.async_all("sensor")
        if "mycoffee_slot_" in s.entity_id and "_coffee_amount" in s.entity_id
    ]
    assert len(mycoffee_sensors) == 9, (
        f"Expected 9 mycoffee amount sensors for 8000 family; "
        f"got {[s.entity_id for s in mycoffee_sensors]}"
    )


async def test_mycoffee_sensors_not_registered_for_melitta(
    hass: HomeAssistant, mock_entry: MockConfigEntry
) -> None:
    """Melitta has its own MyCoffee scheme; no per-slot amount sensors register."""
    client = _mock_client()  # default brand = melitta
    await _setup_integration(hass, mock_entry, client)

    mycoffee_sensors = [
        s for s in hass.states.async_all("sensor")
        if "mycoffee_slot_" in s.entity_id
    ]
    assert mycoffee_sensors == [], (
        f"Melitta must not get Nivona mycoffee sensors; got {mycoffee_sensors}"
    )
