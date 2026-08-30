"""Tests for issue #35 recovery fixes.

The C6 proxy controller wedge (status=133 loop) starves advertisements, so
the integration ends up with no cached BLEDevice — and before these fixes
every recovery path (unpair, repair escalation, proxy lookup) was gated on
exactly the state the wedge removes. These tests cover:

1. Connection-less unpair via the proxy API (no BLEDevice needed).
2. Failure counting + repair escalation in the initial-connect loop.
3. Proxy entry lookup fallback without a live scanner sighting.
4. Presence gate keeps (not resets) the wedge counter; a starved scanner
   overrides the gate so the wedge state can still escalate.
5. The connect ladder short-circuits when there is no BLEDevice instead of
   burning four identical local failures.
6. force_repair invokes the proxy's restart_ble action when available.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.melitta_barista import (
    _async_connect_and_poll,
    _async_force_repair,
    _async_proxy_unpair,
    _find_proxy_entry_for_address,
)
from custom_components.melitta_barista.ble_client import (
    MelittaBleClient,
    NoBleDeviceError,
)

ADDRESS = "AA:BB:CC:DD:EE:FF"


# ── Fix 5: connect ladder short-circuits without a BLEDevice ───────────


class TestNoBleDeviceShortCircuit:
    async def test_establish_connection_raises_typed_error(self):
        """Without a BLEDevice the typed error is raised (brc installed)."""
        client = MelittaBleClient(ADDRESS)
        assert client._ble_device is None
        with pytest.raises(NoBleDeviceError):
            await client._establish_connection(pair=False)

    async def test_connect_impl_short_circuits_ladder(self):
        """One NoBleDeviceError aborts the whole ladder: no pair=True retry,
        no unpair, no settle sleeps — a single fast local failure."""
        client = MelittaBleClient(ADDRESS, pair_settle_delay=0)
        establish = AsyncMock(side_effect=NoBleDeviceError("no device"))
        unpair = AsyncMock()
        with patch.object(client, "_establish_connection", establish), \
                patch.object(client, "_try_unpair", unpair):
            result = await asyncio.wait_for(client._connect_impl(), timeout=1.0)
        assert result is False
        assert establish.await_count == 1
        unpair.assert_not_awaited()


# ── Fix 1: connection-less unpair through the proxy API ────────────────


class TestConnectionlessUnpair:
    def test_set_and_clear_unpair_callback(self):
        client = MelittaBleClient(ADDRESS)
        cb = AsyncMock()
        client.set_unpair_callback(cb)
        assert client._unpair_callback is cb
        client.set_unpair_callback(None)
        assert client._unpair_callback is None

    async def test_unpair_uses_proxy_callback_first(self):
        """When the connection-less path succeeds we never try to connect."""
        client = MelittaBleClient(ADDRESS)
        client.set_unpair_callback(AsyncMock(return_value=True))
        establish = AsyncMock()
        with patch.object(client, "_establish_connection", establish):
            await client._try_unpair()
        establish.assert_not_awaited()

    async def test_unpair_falls_back_to_connect_path(self):
        """Callback returning False falls back to connect-then-unpair."""
        client = MelittaBleClient(ADDRESS)
        client.set_unpair_callback(AsyncMock(return_value=False))
        bleak_client = MagicMock()
        bleak_client.unpair = AsyncMock()
        bleak_client.disconnect = AsyncMock()
        establish = AsyncMock(return_value=bleak_client)
        with patch.object(client, "_establish_connection", establish):
            await client._try_unpair()
        establish.assert_awaited_once()
        bleak_client.unpair.assert_awaited_once()

    async def test_unpair_callback_exception_falls_back(self):
        client = MelittaBleClient(ADDRESS)
        client.set_unpair_callback(AsyncMock(side_effect=RuntimeError("boom")))
        establish = AsyncMock(side_effect=NoBleDeviceError("no device"))
        with patch.object(client, "_establish_connection", establish):
            await client._try_unpair()  # must not raise
        establish.assert_awaited_once()


# ── Fix 4: presence gate keeps the counter; starved scanner overrides ──


class TestPresenceGateNoReset:
    async def test_absent_device_keeps_existing_wedge_counter(self):
        """Going absent must NOT wipe accrued failures (issue #35): in the
        proxy-wedge state the machine looks absent precisely because the
        scanner is starved. The counter only resets on a successful connect."""
        client = MelittaBleClient(
            ADDRESS,
            reconnect_delay=0.01,
            reconnect_max_delay=0.01,
            repair_after_failures=5,
        )
        client._auto_reconnect = True
        client._consecutive_connect_failures = 3

        checks = 0

        def fake_presence() -> bool:
            nonlocal checks
            checks += 1
            if checks >= 2:
                client._auto_reconnect = False
            return False

        client.set_presence_callback(fake_presence)
        await asyncio.wait_for(client._reconnect_loop(), timeout=1.0)
        assert client._consecutive_connect_failures == 3

    def test_set_and_clear_scanner_starved_callback(self):
        client = MelittaBleClient(ADDRESS)
        cb = MagicMock(return_value=True)
        client.set_scanner_starved_callback(cb)
        assert client._scanner_starved_callback is cb
        client.set_scanner_starved_callback(None)
        assert client._scanner_starved_callback is None

    async def test_absent_but_starved_scanner_still_wedges(self):
        """Absent device + starved scanner = suspected proxy wedge: keep
        attempting (fails fast locally) so the repair can escalate."""
        client = MelittaBleClient(
            ADDRESS,
            reconnect_delay=0.01,
            reconnect_max_delay=0.01,
            repair_after_failures=2,
        )
        client._auto_reconnect = True
        callback_calls: list[None] = []
        client.set_repair_callback(lambda: callback_calls.append(None))
        client.set_presence_callback(lambda: False)
        client.set_scanner_starved_callback(lambda: True)

        call_count = 0

        async def fake_connect():
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                client._auto_reconnect = False
            return False

        with patch.object(client, "connect", side_effect=fake_connect):
            await asyncio.wait_for(client._reconnect_loop(), timeout=1.0)

        assert call_count >= 2
        assert len(callback_calls) >= 1

    async def test_absent_healthy_scanner_still_skips(self):
        """Absent device + healthy scanner = machine really off (issue #12):
        no connect attempts, no repair."""
        client = MelittaBleClient(
            ADDRESS,
            reconnect_delay=0.01,
            reconnect_max_delay=0.01,
            repair_after_failures=2,
        )
        client._auto_reconnect = True
        callback_calls: list[None] = []
        client.set_repair_callback(lambda: callback_calls.append(None))
        client.set_scanner_starved_callback(lambda: False)

        checks = 0

        def fake_presence() -> bool:
            nonlocal checks
            checks += 1
            if checks >= 3:
                client._auto_reconnect = False
            return False

        client.set_presence_callback(fake_presence)

        connect_calls: list[None] = []

        async def fake_connect():
            connect_calls.append(None)
            return False

        with patch.object(client, "connect", side_effect=fake_connect):
            await asyncio.wait_for(client._reconnect_loop(), timeout=1.0)

        assert connect_calls == []
        assert callback_calls == []


# ── Fix 2: initial-connect loop counts failures and escalates ──────────


class TestConnectAndPollEscalation:
    async def test_repair_fires_from_initial_connect_loop(self):
        client = MelittaBleClient(
            ADDRESS,
            reconnect_delay=0.01,
            reconnect_max_delay=0.01,
            repair_after_failures=2,
        )
        callback_calls: list[None] = []
        client.set_repair_callback(lambda: callback_calls.append(None))

        connect = AsyncMock(return_value=False)
        with patch.object(client, "connect", connect):
            task = asyncio.create_task(_async_connect_and_poll(
                client,
                initial_delay=0,
                reconnect_delay=0.01,
                reconnect_max_delay=0.01,
            ))
            for _ in range(200):
                await asyncio.sleep(0.01)
                if callback_calls:
                    break
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert connect.await_count >= 2
        assert len(callback_calls) >= 1

    async def test_initial_loop_skips_connect_when_absent(self):
        """Presence gate applies to the initial loop too (issue #12 parity)."""
        client = MelittaBleClient(
            ADDRESS,
            reconnect_delay=0.01,
            reconnect_max_delay=0.01,
            repair_after_failures=2,
        )
        client.set_presence_callback(lambda: False)
        client.set_scanner_starved_callback(lambda: False)

        connect = AsyncMock(return_value=False)
        with patch.object(client, "connect", connect):
            task = asyncio.create_task(_async_connect_and_poll(
                client,
                initial_delay=0,
                reconnect_delay=0.01,
                reconnect_max_delay=0.01,
            ))
            await asyncio.sleep(0.2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        connect.assert_not_awaited()


# ── 0.86.1 regression fix: unpair only on bond-class evidence ──────────


class TestUnpairGate:
    """Bond-clearing must never fire on unreachability failures.

    0.86.0 regression: the connection-less unpair made ladder rung 3
    destructive while the machine was powered off — the proxy-side bond got
    wiped while the machine kept its LTK, producing a permanent SMP
    `auth fail reason=82` mismatch by morning. Unpair now requires evidence
    of a bond-class failure: a BLE link was actually established during
    this connect cycle, or the device is currently advertising.
    """

    async def test_unpair_skipped_when_no_link_and_absent(self):
        client = MelittaBleClient(ADDRESS, pair_settle_delay=0)
        client.set_presence_callback(lambda: False)
        handshake = AsyncMock(return_value=False)
        unpair = AsyncMock()
        with patch.object(client, "_try_connect_and_handshake", handshake), \
                patch.object(client, "_try_unpair", unpair):
            result = await client._connect_impl()
        assert result is False
        unpair.assert_not_awaited()
        # Ladder stops after pair=False + pair=True — no post-unpair rung.
        assert handshake.await_count == 2

    async def test_unpair_skipped_without_presence_info_and_no_link(self):
        """No presence callback (standalone) + no link seen → still skip."""
        client = MelittaBleClient(ADDRESS, pair_settle_delay=0)
        handshake = AsyncMock(return_value=False)
        unpair = AsyncMock()
        with patch.object(client, "_try_connect_and_handshake", handshake), \
                patch.object(client, "_try_unpair", unpair):
            result = await client._connect_impl()
        assert result is False
        unpair.assert_not_awaited()

    async def test_presence_alone_does_not_authorize_unpair(self):
        """0.86.2 regression: habluetooth keeps async_address_present True
        for ~195s after the machine powers off, and the first reconnect
        cycle (2-2.5 min of pair timeouts) always lands inside that window.
        Stale presence authorized the unpair rung and wiped the proxy bond
        again. Presence must NOT authorize unpair — only a BLE link that
        actually opened during this cycle may."""
        client = MelittaBleClient(ADDRESS, pair_settle_delay=0)
        client.set_presence_callback(lambda: True)  # stale-True window
        handshake = AsyncMock(return_value=False)   # no link ever opened
        unpair = AsyncMock()
        with patch.object(client, "_try_connect_and_handshake", handshake), \
                patch.object(client, "_try_unpair", unpair):
            result = await client._connect_impl()
        assert result is False
        unpair.assert_not_awaited()
        assert handshake.await_count == 2

    async def test_link_alone_no_longer_authorizes_unpair(self):
        """0.87.2: an opened link is NOT bond evidence — only a classified
        SMP/auth rejection is (the link-seen gate wiped a valid bond on
        transient failures, field case Jay)."""
        client = MelittaBleClient(ADDRESS, pair_settle_delay=0)

        async def fake_handshake(*, pair: bool = False) -> bool:
            client._ble_link_seen = True  # link opened, no auth evidence
            return False

        unpair = AsyncMock()
        with patch.object(client, "_try_connect_and_handshake", new=fake_handshake), \
                patch.object(client, "_try_unpair", unpair):
            result = await client._connect_impl()
        assert result is False
        unpair.assert_not_awaited()

    async def test_auth_evidence_authorizes_unpair(self):
        client = MelittaBleClient(
            ADDRESS, pair_settle_delay=0,
            ble_source_affinity="11:22:33:44:55:66",
        )
        # 0.88: destruction requires a prior auth cycle + the one in flight.
        from custom_components.melitta_barista.const import FAILURE_AUTH
        client.bond.on_cycle_failure(FAILURE_AUTH)

        async def fake_handshake(*, pair: bool = False) -> bool:
            client._auth_fail_seen = True  # classified SMP rejection
            return False

        unpair = AsyncMock()
        with patch.object(client, "_try_connect_and_handshake", new=fake_handshake), \
                patch.object(client, "_try_unpair", unpair):
            result = await client._connect_impl()
        assert result is False
        unpair.assert_awaited_once()

    async def test_link_seen_flag_resets_each_cycle(self):
        """A stale flag from a previous cycle must not authorize unpair."""
        client = MelittaBleClient(ADDRESS, pair_settle_delay=0)
        client._ble_link_seen = True  # leftover from an earlier cycle
        handshake = AsyncMock(return_value=False)
        unpair = AsyncMock()
        with patch.object(client, "_try_connect_and_handshake", handshake), \
                patch.object(client, "_try_unpair", unpair):
            await client._connect_impl()
        unpair.assert_not_awaited()


# ── Fix 3: proxy entry lookup fallback without a live sighting ─────────


def _mock_proxy_entry(feature_flags: int = 1) -> MagicMock:
    entry = MagicMock()
    entry.unique_id = "aa:bb:cc:dd:ee:00"
    entry.data = {}
    entry.runtime_data.device_info.bluetooth_proxy_feature_flags = feature_flags
    entry.runtime_data.device_info.legacy_bluetooth_proxy_version = 0
    return entry


class TestProxyEntryFallback:
    def _hass_with_entries(self, entries: list[MagicMock]) -> MagicMock:
        hass = MagicMock()
        hass.config_entries.async_entries.return_value = entries
        return hass

    def test_fallback_single_proxy_entry(self):
        proxy = _mock_proxy_entry()
        hass = self._hass_with_entries([proxy])
        with patch(
            "custom_components.melitta_barista.bluetooth.async_scanner_devices_by_address",
            return_value=[],
        ):
            assert _find_proxy_entry_for_address(hass, ADDRESS) is proxy

    def test_explicit_source_selects_matching_proxy_without_live_sighting(self):
        """Affinity targets its owning proxy even while that scanner is starved."""
        owner = _mock_proxy_entry()
        owner.unique_id = "11:22:33:44:55:66"
        other = _mock_proxy_entry()
        other.unique_id = "AA:BB:CC:DD:EE:00"
        hass = self._hass_with_entries([other, owner])

        with patch(
            "custom_components.melitta_barista.bluetooth.async_scanner_devices_by_address",
            return_value=[],
        ):
            assert (
                _find_proxy_entry_for_address(
                    hass, ADDRESS, "11:22:33:44:55:66",
                )
                is owner
            )

    def test_fallback_ambiguous_multiple_proxies(self):
        hass = self._hass_with_entries([_mock_proxy_entry(), _mock_proxy_entry()])
        with patch(
            "custom_components.melitta_barista.bluetooth.async_scanner_devices_by_address",
            return_value=[],
        ):
            assert _find_proxy_entry_for_address(hass, ADDRESS) is None

    def test_fallback_no_proxy_capability(self):
        """ESPHome entries without bluetooth proxy capability don't match."""
        entry = _mock_proxy_entry(feature_flags=0)
        hass = self._hass_with_entries([entry])
        with patch(
            "custom_components.melitta_barista.bluetooth.async_scanner_devices_by_address",
            return_value=[],
        ):
            assert _find_proxy_entry_for_address(hass, ADDRESS) is None


# ── Fix 1 wiring: _async_proxy_unpair helper ───────────────────────────


class TestProxyUnpairHelper:
    async def test_unpair_calls_api_with_int_address(self):
        proxy = _mock_proxy_entry()
        api = proxy.runtime_data.client
        api.bluetooth_device_unpair = AsyncMock(
            return_value=MagicMock(success=True),
        )
        hass = MagicMock()
        with patch(
            "custom_components.melitta_barista._find_proxy_entry_for_address",
            return_value=proxy,
        ):
            ok = await _async_proxy_unpair(hass, "D6:36:48:EB:40:08")
        assert ok is True
        api.bluetooth_device_unpair.assert_awaited_once_with(
            0xD63648EB4008, timeout=10.0,
        )

    async def test_unpair_returns_false_without_proxy(self):
        hass = MagicMock()
        with patch(
            "custom_components.melitta_barista._find_proxy_entry_for_address",
            return_value=None,
        ):
            assert await _async_proxy_unpair(hass, ADDRESS) is False


    async def test_unpair_timeout_is_not_treated_as_success(self):
        proxy = _mock_proxy_entry()
        proxy.runtime_data.client.bluetooth_device_unpair = AsyncMock(
            side_effect=TimeoutError("no response"),
        )
        hass = MagicMock()
        with patch(
            "custom_components.melitta_barista._find_proxy_entry_for_address",
            return_value=proxy,
        ):
            assert await _async_proxy_unpair(hass, ADDRESS) is False


    async def test_unpair_timeout_is_not_treated_as_success(self):
        proxy = _mock_proxy_entry()
        proxy.runtime_data.client.bluetooth_device_unpair = AsyncMock(
            side_effect=TimeoutError("no response"),
        )
        hass = MagicMock()
        with patch(
            "custom_components.melitta_barista._find_proxy_entry_for_address",
            return_value=proxy,
        ):
            assert await _async_proxy_unpair(hass, ADDRESS) is False

    async def test_unpair_returns_false_on_api_error(self):
        proxy = _mock_proxy_entry()
        proxy.runtime_data.client.bluetooth_device_unpair = AsyncMock(
            side_effect=RuntimeError("API down"),
        )
        hass = MagicMock()
        with patch(
            "custom_components.melitta_barista._find_proxy_entry_for_address",
            return_value=proxy,
        ):
            assert await _async_proxy_unpair(hass, ADDRESS) is False


# ── Fix 6-lite: force_repair triggers restart_ble when available ───────


class TestForceRepairRestartBle:
    async def test_restart_ble_called_when_registered(self):
        client = MagicMock()
        client.address = ADDRESS
        client.disconnect = AsyncMock()
        client._reconnect_task = None
        entry = MagicMock()
        entry.runtime_data = client

        proxy = _mock_proxy_entry()
        proxy.runtime_data.device_info.name = "ble-proxy-melitta"

        hass = MagicMock()
        hass.services.has_service.return_value = True
        hass.services.async_call = AsyncMock()

        with patch(
            "custom_components.melitta_barista._find_proxy_entry_for_address",
            return_value=proxy,
        ):
            result = await _async_force_repair(hass, entry)

        called_services = [
            call.args[1] for call in hass.services.async_call.await_args_list
        ]
        assert "ble_proxy_melitta_restart_ble" in called_services
        assert result["ble_restarted"] is True
