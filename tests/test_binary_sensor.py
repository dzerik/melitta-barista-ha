"""Tests for binary_sensor platform entities."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.melitta_barista.const import (
    DOMAIN, MachineProcess, Manipulation,
)
from custom_components.melitta_barista.protocol import MachineStatus

from . import MOCK_ADDRESS, MOCK_CONFIG_DATA


def _mock_client(status=None):
    client = MagicMock()
    client.address = MOCK_ADDRESS
    client.connected = True
    client.status = status or MachineStatus(process=MachineProcess.READY)
    client.add_status_callback = MagicMock()
    client.add_connection_callback = MagicMock()
    client.remove_status_callback = MagicMock()
    client.remove_connection_callback = MagicMock()
    return client


@pytest.fixture
def mock_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data=MOCK_CONFIG_DATA,
        unique_id="aabbccddeeff",
    )


async def test_awaiting_confirmation_off_when_idle(
    hass: HomeAssistant, mock_entry: MockConfigEntry,
) -> None:
    """Off when manipulation == NONE."""
    from custom_components.melitta_barista.binary_sensor import (
        MelittaAwaitingConfirmationBinarySensor,
    )
    client = _mock_client()
    sensor = MelittaAwaitingConfirmationBinarySensor(client, mock_entry, "Test")
    assert sensor.is_on is False


async def test_awaiting_confirmation_on_with_hardware_prompt(
    hass: HomeAssistant, mock_entry: MockConfigEntry,
) -> None:
    """On when manipulation is a hardware prompt (e.g. FILL_WATER)."""
    from custom_components.melitta_barista.binary_sensor import (
        MelittaAwaitingConfirmationBinarySensor,
    )
    status = MachineStatus(
        process=MachineProcess.READY, manipulation=Manipulation.FILL_WATER,
    )
    client = _mock_client(status=status)
    sensor = MelittaAwaitingConfirmationBinarySensor(client, mock_entry, "Test")
    assert sensor.is_on is True


async def test_awaiting_confirmation_on_with_soft_prompt(
    hass: HomeAssistant, mock_entry: MockConfigEntry,
) -> None:
    """On when manipulation is a soft prompt (e.g. FLUSH_REQUIRED)."""
    from custom_components.melitta_barista.binary_sensor import (
        MelittaAwaitingConfirmationBinarySensor,
    )
    status = MachineStatus(
        process=MachineProcess.READY, manipulation=Manipulation.FLUSH_REQUIRED,
    )
    client = _mock_client(status=status)
    sensor = MelittaAwaitingConfirmationBinarySensor(client, mock_entry, "Test")
    assert sensor.is_on is True


async def test_awaiting_confirmation_off_when_status_none(
    hass: HomeAssistant, mock_entry: MockConfigEntry,
) -> None:
    """Off when client.status is None (not yet polled)."""
    from custom_components.melitta_barista.binary_sensor import (
        MelittaAwaitingConfirmationBinarySensor,
    )
    client = _mock_client()
    client.status = None
    sensor = MelittaAwaitingConfirmationBinarySensor(client, mock_entry, "Test")
    assert sensor.is_on is False
