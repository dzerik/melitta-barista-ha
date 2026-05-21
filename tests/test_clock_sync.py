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


@pytest.mark.asyncio
async def test_repair_issue_created_for_legacy_clock_entities(hass: HomeAssistant):
    """If the entity registry has <addr>_setting_20 or _setting_21, raise a repair issue."""
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers import issue_registry as ir

    from custom_components.melitta_barista.const import DOMAIN
    from custom_components.melitta_barista import _async_check_clock_migration

    registry = er.async_get(hass)
    registry.async_get_or_create(
        domain="number",
        platform=DOMAIN,
        unique_id="F1:11:22:33:44:55_setting_20",
    )

    entry = MagicMock()
    entry.entry_id = "abc"

    _async_check_clock_migration(hass, entry, "F1:11:22:33:44:55")

    issue = ir.async_get(hass).async_get_issue(DOMAIN, "clock_entity_migration")
    assert issue is not None
    assert issue.severity == ir.IssueSeverity.WARNING


@pytest.mark.asyncio
async def test_repair_issue_not_created_on_fresh_install(hass: HomeAssistant):
    """No legacy unique_id → no repair issue."""
    from homeassistant.helpers import issue_registry as ir

    from custom_components.melitta_barista.const import DOMAIN
    from custom_components.melitta_barista import _async_check_clock_migration

    entry = MagicMock()
    entry.entry_id = "abc"

    _async_check_clock_migration(hass, entry, "F1:11:22:33:44:55")

    issue = ir.async_get(hass).async_get_issue(DOMAIN, "clock_entity_migration")
    assert issue is None


def test_circular_drift_handles_midnight_wrap():
    """drift across midnight must be the shorter circular distance."""
    from custom_components.melitta_barista import _clock_circular_drift

    # 00:01 vs 23:59 → 2 minutes, not 1438
    assert _clock_circular_drift(1, 1439) == 2
    # 14:30 vs 14:32 → 2
    assert _clock_circular_drift(870, 872) == 2
    # 12:00 vs 00:00 → 720 (max)
    assert _clock_circular_drift(720, 0) == 720
    # identical
    assert _clock_circular_drift(500, 500) == 0


@pytest.mark.asyncio
async def test_trigger_sync_writes_when_drift_above_threshold(hass: HomeAssistant):
    """drift=10min, threshold=2min → write with HA current minutes."""
    from custom_components.melitta_barista import ClockSyncCoordinator
    from custom_components.melitta_barista.const import (
        CONF_AUTO_SYNC_CLOCK,
        CONF_AUTO_SYNC_DRIFT_MINUTES,
        CONF_AUTO_SYNC_DAILY_TIME,
    )

    client = _client_mock()
    # Machine says 14:00, HA will say 14:10.
    client.read_setting = AsyncMock(return_value=840)
    client.write_setting = AsyncMock(return_value=True)

    coord = ClockSyncCoordinator(
        hass,
        client,
        {
            CONF_AUTO_SYNC_CLOCK: True,
            CONF_AUTO_SYNC_DRIFT_MINUTES: 2,
            CONF_AUTO_SYNC_DAILY_TIME: "03:17",
        },
    )

    from unittest.mock import patch
    from datetime import datetime
    fake_now = datetime(2026, 5, 21, 14, 10, 0)
    with patch("custom_components.melitta_barista.dt_util.now", return_value=fake_now):
        await coord._trigger_sync("test", force=False)

    client.read_setting.assert_awaited_with(20)
    client.write_setting.assert_awaited_with(21, 850)


@pytest.mark.asyncio
async def test_trigger_sync_skips_when_drift_below_threshold(hass: HomeAssistant):
    """drift=1min, threshold=2min → no write, _last_sync set."""
    from custom_components.melitta_barista import ClockSyncCoordinator
    from custom_components.melitta_barista.const import (
        CONF_AUTO_SYNC_CLOCK,
        CONF_AUTO_SYNC_DRIFT_MINUTES,
        CONF_AUTO_SYNC_DAILY_TIME,
    )

    client = _client_mock()
    client.read_setting = AsyncMock(return_value=849)
    client.write_setting = AsyncMock(return_value=True)

    coord = ClockSyncCoordinator(
        hass,
        client,
        {
            CONF_AUTO_SYNC_CLOCK: True,
            CONF_AUTO_SYNC_DRIFT_MINUTES: 2,
            CONF_AUTO_SYNC_DAILY_TIME: "03:17",
        },
    )

    from unittest.mock import patch
    from datetime import datetime
    fake_now = datetime(2026, 5, 21, 14, 10, 0)
    with patch("custom_components.melitta_barista.dt_util.now", return_value=fake_now):
        await coord._trigger_sync("test", force=False)

    client.write_setting.assert_not_awaited()
    assert coord._last_sync == fake_now


@pytest.mark.asyncio
async def test_trigger_sync_force_writes_even_if_drift_small(hass: HomeAssistant):
    """force=True bypasses both drift and throttle."""
    from custom_components.melitta_barista import ClockSyncCoordinator
    from custom_components.melitta_barista.const import (
        CONF_AUTO_SYNC_CLOCK,
        CONF_AUTO_SYNC_DRIFT_MINUTES,
        CONF_AUTO_SYNC_DAILY_TIME,
    )

    client = _client_mock()
    client.read_setting = AsyncMock(return_value=850)  # exact match
    client.write_setting = AsyncMock(return_value=True)

    coord = ClockSyncCoordinator(
        hass,
        client,
        {
            CONF_AUTO_SYNC_CLOCK: True,
            CONF_AUTO_SYNC_DRIFT_MINUTES: 5,
            CONF_AUTO_SYNC_DAILY_TIME: "03:17",
        },
    )

    from unittest.mock import patch
    from datetime import datetime
    fake_now = datetime(2026, 5, 21, 14, 10, 0)
    with patch("custom_components.melitta_barista.dt_util.now", return_value=fake_now):
        await coord._trigger_sync("daily", force=True)

    client.write_setting.assert_awaited_with(21, 850)


@pytest.mark.asyncio
async def test_trigger_sync_skips_when_disabled(hass: HomeAssistant):
    from custom_components.melitta_barista import ClockSyncCoordinator
    from custom_components.melitta_barista.const import (
        CONF_AUTO_SYNC_CLOCK,
        CONF_AUTO_SYNC_DRIFT_MINUTES,
        CONF_AUTO_SYNC_DAILY_TIME,
    )

    client = _client_mock()
    coord = ClockSyncCoordinator(
        hass,
        client,
        {
            CONF_AUTO_SYNC_CLOCK: False,
            CONF_AUTO_SYNC_DRIFT_MINUTES: 2,
            CONF_AUTO_SYNC_DAILY_TIME: "03:17",
        },
    )
    await coord._trigger_sync("test", force=False)
    client.read_setting.assert_not_awaited()
    client.write_setting.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_connect_schedules_trigger_sync(hass: HomeAssistant):
    """connected=True calls _trigger_sync('reconnect') via async_create_task."""
    from custom_components.melitta_barista import ClockSyncCoordinator
    from custom_components.melitta_barista.const import (
        CONF_AUTO_SYNC_CLOCK,
        CONF_AUTO_SYNC_DRIFT_MINUTES,
        CONF_AUTO_SYNC_DAILY_TIME,
    )

    client = _client_mock()
    client.read_setting = AsyncMock(return_value=840)
    client.write_setting = AsyncMock(return_value=True)

    coord = ClockSyncCoordinator(
        hass,
        client,
        {
            CONF_AUTO_SYNC_CLOCK: True,
            CONF_AUTO_SYNC_DRIFT_MINUTES: 2,
            CONF_AUTO_SYNC_DAILY_TIME: "03:17",
        },
    )

    from unittest.mock import patch
    from datetime import datetime
    fake_now = datetime(2026, 5, 21, 14, 10, 0)
    with patch("custom_components.melitta_barista.dt_util.now", return_value=fake_now):
        coord._on_connect(True)
        # Drain pending tasks scheduled by hass.async_create_task
        await hass.async_block_till_done()

    client.write_setting.assert_awaited_with(21, 850)


def test_on_connect_ignores_disconnect_event(hass: HomeAssistant):
    """connected=False does nothing."""
    from custom_components.melitta_barista import ClockSyncCoordinator
    from custom_components.melitta_barista.const import (
        CONF_AUTO_SYNC_CLOCK,
        CONF_AUTO_SYNC_DRIFT_MINUTES,
        CONF_AUTO_SYNC_DAILY_TIME,
    )

    client = _client_mock()
    coord = ClockSyncCoordinator(
        hass,
        client,
        {
            CONF_AUTO_SYNC_CLOCK: True,
            CONF_AUTO_SYNC_DRIFT_MINUTES: 2,
            CONF_AUTO_SYNC_DAILY_TIME: "03:17",
        },
    )
    coord._on_connect(False)
    client.read_setting.assert_not_called()


@pytest.mark.asyncio
async def test_trigger_sync_skips_when_disconnected(hass: HomeAssistant):
    from custom_components.melitta_barista import ClockSyncCoordinator
    from custom_components.melitta_barista.const import (
        CONF_AUTO_SYNC_CLOCK,
        CONF_AUTO_SYNC_DRIFT_MINUTES,
        CONF_AUTO_SYNC_DAILY_TIME,
    )

    client = _client_mock(connected=False)
    coord = ClockSyncCoordinator(
        hass,
        client,
        {
            CONF_AUTO_SYNC_CLOCK: True,
            CONF_AUTO_SYNC_DRIFT_MINUTES: 2,
            CONF_AUTO_SYNC_DAILY_TIME: "03:17",
        },
    )
    await coord._trigger_sync("test", force=False)
    client.read_setting.assert_not_awaited()


@pytest.mark.asyncio
async def test_daily_tick_forces_sync_bypassing_throttle(hass: HomeAssistant):
    """Daily tick triggers sync with force=True."""
    from custom_components.melitta_barista import ClockSyncCoordinator
    from custom_components.melitta_barista.const import (
        CONF_AUTO_SYNC_CLOCK,
        CONF_AUTO_SYNC_DRIFT_MINUTES,
        CONF_AUTO_SYNC_DAILY_TIME,
    )

    client = _client_mock()
    client.read_setting = AsyncMock(return_value=850)  # zero drift
    client.write_setting = AsyncMock(return_value=True)

    coord = ClockSyncCoordinator(
        hass,
        client,
        {
            CONF_AUTO_SYNC_CLOCK: True,
            CONF_AUTO_SYNC_DRIFT_MINUTES: 30,  # large threshold
            CONF_AUTO_SYNC_DAILY_TIME: "03:17",
        },
    )
    # Throttle window full
    from datetime import datetime
    coord._last_sync = datetime(2026, 5, 21, 14, 9, 0)

    from unittest.mock import patch
    fake_now = datetime(2026, 5, 21, 14, 10, 0)
    with patch("custom_components.melitta_barista.dt_util.now", return_value=fake_now):
        coord._on_daily_tick(fake_now)
        await hass.async_block_till_done()

    client.write_setting.assert_awaited_with(21, 850)


@pytest.mark.asyncio
async def test_daily_tick_skips_when_disconnected(hass: HomeAssistant):
    from custom_components.melitta_barista import ClockSyncCoordinator
    from custom_components.melitta_barista.const import (
        CONF_AUTO_SYNC_CLOCK,
        CONF_AUTO_SYNC_DRIFT_MINUTES,
        CONF_AUTO_SYNC_DAILY_TIME,
    )

    client = _client_mock(connected=False)
    coord = ClockSyncCoordinator(
        hass,
        client,
        {
            CONF_AUTO_SYNC_CLOCK: True,
            CONF_AUTO_SYNC_DRIFT_MINUTES: 2,
            CONF_AUTO_SYNC_DAILY_TIME: "03:17",
        },
    )
    from datetime import datetime
    coord._on_daily_tick(datetime(2026, 5, 21, 3, 17, 0))
    await hass.async_block_till_done()
    client.read_setting.assert_not_awaited()


@pytest.mark.asyncio
async def test_coordinator_start_stop_idempotent_pairing(hass: HomeAssistant):
    """start() subscribes once; stop() unsubscribes the same number of times."""
    from custom_components.melitta_barista import (
        ClockSyncCoordinator,
        _async_clock_coordinator_key,
    )
    from custom_components.melitta_barista.const import (
        CONF_AUTO_SYNC_CLOCK,
        CONF_AUTO_SYNC_DRIFT_MINUTES,
        CONF_AUTO_SYNC_DAILY_TIME,
    )

    client = _client_mock()
    coord = ClockSyncCoordinator(
        hass,
        client,
        {
            CONF_AUTO_SYNC_CLOCK: True,
            CONF_AUTO_SYNC_DRIFT_MINUTES: 2,
            CONF_AUTO_SYNC_DAILY_TIME: "03:17",
        },
    )
    coord.start()
    coord.stop()

    assert client.add_connection_callback.call_count == 1
    assert client.remove_connection_callback.call_count == 1
    # helper exists and returns deterministic per-entry key
    assert _async_clock_coordinator_key("abc") == "clock_coordinator_abc"
