"""Fixtures for Melitta Barista Smart integration tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bleak.backends.device import BLEDevice



@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for all tests."""
    yield




@pytest.fixture(autouse=True)
async def auto_enable_bluetooth(enable_bluetooth):
    """Enable bluetooth for all tests (required by melitta_barista dependency)."""
    yield


@pytest.fixture
def mock_ble_device() -> BLEDevice:
    """Return a mock BLEDevice."""
    return BLEDevice(
        address="AA:BB:CC:DD:EE:FF",
        name="8601ABCD1234",
        details={},
    )


@pytest.fixture
def mock_bleak_client():
    """Return a mock BleakClient that simulates a connected device."""
    client = MagicMock()
    client.is_connected = True
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.start_notify = AsyncMock()
    client.stop_notify = AsyncMock()
    client.write_gatt_char = AsyncMock()
    client.services = MagicMock()
    return client
