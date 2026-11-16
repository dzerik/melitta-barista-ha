"""Statistics retain their own read namespace and live model allowlist."""

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from custom_components.melitta_barista._brand_stats import BrandStatsCoordinator
from custom_components.melitta_barista.ble_client import MelittaBleClient
from custom_components.melitta_barista.brands.nivona import NivonaProfile
from tests import MOCK_ADDRESS


@pytest.fixture
def statistic_client(mock_bleak_client):
    """Keep real settings guards and substitute only the external HR operation."""
    profile = NivonaProfile()
    client = MelittaBleClient(MOCK_ADDRESS, brand=profile)
    client._client = mock_bleak_client
    client._connected = True
    client._capabilities = profile.capabilities_for("79x")
    client._protocol.read_numerical = AsyncMock(return_value=0)
    client._protocol.write_numerical = AsyncMock(return_value=True)
    return client


@pytest.mark.parametrize("family", ["600", "79x", "9101"])
async def test_nivona_coordinator_reads_counters_and_care_outside_settings(
    hass, statistic_client, family,
):
    """The settings allowlist must not silently suppress declared HR statistics."""
    client = statistic_client
    client._capabilities = client.brand.capabilities_for(family)
    observed = []

    async def read_numerical(_write, register):
        observed.append(register)
        return {200: 7, 600: 31}.get(register)

    client._protocol.read_numerical.side_effect = read_numerical
    coordinator = BrandStatsCoordinator(hass, client)
    coordinator.set_descriptors(client.capabilities.stats)
    try:
        await coordinator.request_refresh()
        assert coordinator.values == {200: 7, 600: 31}
        assert 200 in observed
        assert 600 in observed
    finally:
        coordinator.close()


async def test_statistic_read_returns_raw_packed_value(statistic_client):
    """Masking belongs to presentation, not the HR read contract."""
    client = statistic_client
    client._protocol.read_numerical.return_value = 0x12340007
    assert await client.read_statistic(209) == 0x12340007
    client._protocol.read_numerical.assert_awaited_once_with(client._write_ble, 209)


@pytest.mark.parametrize("capabilities", [None, "600", "empty"])
async def test_statistic_read_rejects_address_outside_live_model(
    statistic_client, capabilities,
):
    """A stale 79x descriptor must not authorize HR on a different model."""
    client = statistic_client
    if capabilities == "empty":
        client._capabilities = replace(client.capabilities, stats=())
    elif capabilities is None:
        client._capabilities = None
    else:
        client._capabilities = client.brand.capabilities_for(capabilities)
    assert await client.read_statistic(213) is None
    client._protocol.read_numerical.assert_not_awaited()


async def test_statistic_read_rejects_unknown_addresses_and_disconnected_client(
    statistic_client,
):
    """Generic settings and arbitrary addresses are not statistics permissions."""
    client = statistic_client
    for register in (11, 101, 99999):
        assert await client.read_statistic(register) is None
    client._connected = False
    assert await client.read_statistic(200) is None
    client._protocol.read_numerical.assert_not_awaited()


async def test_statistic_namespace_does_not_enable_setting_reads_or_writes(
    statistic_client,
):
    """A supported statistic must not become a writable/readable setting."""
    client = statistic_client
    assert await client.read_setting(200) is None
    assert await client.read_setting(600) is None
    with pytest.raises(ValueError):
        await client.write_setting(200, 1)
    client._protocol.read_numerical.assert_not_awaited()
    client._protocol.write_numerical.assert_not_awaited()
