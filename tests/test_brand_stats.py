"""Lifecycle, backpressure and cache behavior of shared statistics reads."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from custom_components.melitta_barista._brand_stats import BrandStatsCoordinator
from custom_components.melitta_barista.coffee_platform.domain import StatDescriptor
from custom_components.melitta_barista.const import MachineProcess
from custom_components.melitta_barista.protocol import MachineStatus
from tests.test_sensor import _mock_client


@pytest.fixture
async def stats(hass):
    client = _mock_client(MachineStatus(process=MachineProcess.READY))
    client.read_statistic = AsyncMock(return_value=100)
    statuses, connections = [], []
    client.add_status_callback.side_effect = statuses.append
    client.add_connection_callback.side_effect = connections.append
    coordinator = BrandStatsCoordinator(hass, client)
    coordinator.set_descriptors((StatDescriptor(213, 'total', 'Total', unit='count'),))
    yield coordinator, client, statuses, connections
    coordinator.close()
    await hass.async_block_till_done()


async def test_concurrent_requests_join_one_read_and_queue_one_follow_up(hass, stats):
    coordinator, client, statuses, _ = stats
    started, release = asyncio.Event(), asyncio.Event()
    async def read(_):
        if client.read_statistic.await_count == 1:
            started.set()
            await release.wait()
            return 100
        return 101
    client.read_statistic.side_effect = read
    first = coordinator.request_refresh()
    await started.wait()
    for _ in range(10):
        assert coordinator.request_refresh() is first
    for process in (MachineProcess.PRODUCT, MachineProcess.READY):
        client.status = MachineStatus(process=process)
        for callback in statuses:
            callback(client.status)
    release.set()
    await hass.async_block_till_done()
    assert client.read_statistic.await_count == 2
    assert coordinator.values == {213: 101}


async def test_disconnect_then_immediate_reconnect_restarts_read(hass, stats):
    coordinator, client, _, connections = stats
    started = asyncio.Event()
    async def read(_):
        if client.read_statistic.await_count == 1:
            started.set()
            await asyncio.Event().wait()
        return 101
    client.read_statistic.side_effect = read
    old = coordinator.request_refresh()
    await started.wait()
    for connected in (False, True):
        client.connected = connected
        for callback in connections:
            callback(connected)
    await hass.async_block_till_done()
    assert old.cancelled()
    assert coordinator.values == {213: 101}
    assert client.read_statistic.await_count == 2


async def test_unload_cancels_read_and_does_not_publish_partial_values(hass, stats):
    coordinator, client, _, _ = stats
    started = asyncio.Event()
    async def read(_):
        started.set()
        await asyncio.Event().wait()
    client.read_statistic.side_effect = read
    notifications = []
    coordinator.subscribe(lambda: notifications.append(True))
    task = coordinator.request_refresh()
    await started.wait()
    coordinator.close()
    await hass.async_block_till_done()
    assert task.cancelled()
    assert notifications == []
    assert coordinator.request_refresh() is None
    client.remove_status_callback.assert_called_once()
    client.remove_connection_callback.assert_called_once()


async def test_busy_machine_defers_reads_until_ready(hass, stats):
    coordinator, client, statuses, connections = stats
    client.status = MachineStatus(process=MachineProcess.PRODUCT)
    for callback in connections:
        callback(True)
    coordinator.request_initial_refresh()
    client.read_statistic.assert_not_awaited()
    client.status = MachineStatus(process=MachineProcess.READY)
    for callback in statuses:
        callback(client.status)
    await hass.async_block_till_done()
    client.read_statistic.assert_awaited_once_with(213)


async def test_exception_retains_cache_and_other_registers_still_refresh(hass, stats):
    coordinator, client, _, _ = stats
    coordinator.set_descriptors((
        StatDescriptor(213, 'total', 'Total', unit='count'),
        StatDescriptor(214, 'cleaning', 'Cleaning', unit='count'),
    ))
    coordinator.values = {213: 100, 214: 6}
    client.read_statistic.side_effect = [OSError('link error'), 7]
    coordinator.request_refresh()
    await hass.async_block_till_done()
    assert coordinator.values == {213: 100, 214: 7}


async def test_explicit_client_disconnect_cancels_in_flight_statistics(hass, mock_bleak_client):
    """An intentional disconnect must notify subscribers, just like link loss."""
    from custom_components.melitta_barista.ble_client import MelittaBleClient
    from custom_components.melitta_barista.brands.nivona import NivonaProfile
    from tests import MOCK_ADDRESS

    client = MelittaBleClient(MOCK_ADDRESS, brand=NivonaProfile())
    client._capabilities = client.brand.capabilities_for("79x")
    client._client = mock_bleak_client
    client._connected = True
    started = asyncio.Event()
    async def blocked_read(*_):
        started.set()
        await asyncio.Event().wait()
    client._protocol.read_numerical = AsyncMock(side_effect=blocked_read)
    coordinator = BrandStatsCoordinator(hass, client)
    coordinator.set_descriptors((StatDescriptor(213, 'total', 'Total', unit='count'),))
    disconnected = []
    client.add_connection_callback(disconnected.append)
    task = coordinator.request_refresh()
    try:
        await started.wait()
        await client.disconnect()
        await asyncio.sleep(0)
        assert task.cancelled()
        assert disconnected == [False]
        await client.disconnect()
        assert disconnected == [False]
    finally:
        coordinator.close()
        await asyncio.gather(task, return_exceptions=True)
