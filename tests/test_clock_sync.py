"""Tests for clock sync — time entity, service, coordinator, options."""

from __future__ import annotations

from datetime import time as dt_time
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.melitta_barista.time import MelittaClockEntity


def _client_mock(connected: bool = True):
    client = MagicMock()
    client.address = "F1:11:22:33:44:55"
    client.connected = connected
    client.model_name = "Melitta Barista"
    client.firmware_version = "EF_1.00R4__386"
    brand = MagicMock()
    brand.brand_name = "Melitta"
    brand.brand_slug = "melitta"
    client.brand = brand
    client.add_connection_callback = MagicMock()
    client.remove_connection_callback = MagicMock()
    client.read_setting = AsyncMock(return_value=870)  # 14:30
    client.write_setting = AsyncMock(return_value=True)
    return client


@pytest.mark.asyncio
async def test_time_entity_read_value_on_connect():
    """When connection_callback fires with True, entity reads setting 20."""
    client = _client_mock()
    entry = MagicMock()
    entry.entry_id = "abc"
    entity = MelittaClockEntity(client, entry, "Melitta Barista")
    entity.hass = MagicMock(spec=HomeAssistant)
    entity.hass.async_create_task = MagicMock()
    entity.async_write_ha_state = MagicMock()

    # Simulate the connection callback firing (connected=True)
    entity._on_connection_change(True)

    # async_create_task should have scheduled the read
    assert entity.hass.async_create_task.called

    # Drive the coroutine the entity scheduled
    coro = entity.hass.async_create_task.call_args[0][0]
    await coro

    client.read_setting.assert_awaited_once_with(20)
    assert entity.native_value == dt_time(hour=14, minute=30)


@pytest.mark.asyncio
async def test_time_entity_write_calls_setting_21():
    """async_set_value(time(14, 30)) writes 870 to setting 21."""
    client = _client_mock()
    entry = MagicMock()
    entity = MelittaClockEntity(client, entry, "Melitta Barista")
    entity.hass = MagicMock(spec=HomeAssistant)
    entity.async_write_ha_state = MagicMock()

    await entity.async_set_value(dt_time(hour=14, minute=30))

    client.write_setting.assert_awaited_once_with(21, 870)
    assert entity.native_value == dt_time(hour=14, minute=30)


def test_setting_definitions_no_longer_include_clock_ids():
    """0.52.0 removes the legacy clock numbers."""
    from custom_components.melitta_barista.number import SETTING_DEFINITIONS

    ids = {d["id"] for d in SETTING_DEFINITIONS}
    assert 20 not in ids
    assert 21 not in ids
