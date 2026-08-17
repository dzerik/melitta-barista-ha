"""Tests for the 0.87.2 recovery-layer hardening (issue #10 field case Jay).

Audit-confirmed defects addressed here:
1. Unpair rung must require AUTH-class evidence (SMP rejection is string-
   distinguishable HA-side), not merely "a link opened this cycle".
2. Unpair fires at most once per disconnected episode.
3. A proxy UNPAIR timeout means the bond IS most likely gone (firmware
   answers with the wrong message type) — treat as done, skip the fallback.
4. Advertisement wake-ups must not defeat exponential backoff during a
   failure episode.
5. set_ble_device must not spawn _reconnect_loop while the initial-connect
   loop is running (dual-ladder hammering).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bleak.exc import BleakError

from custom_components.melitta_barista.ble_client import (
    FAILURE_AUTH,
    FAILURE_LINK,
    FAILURE_TIMEOUT,
    MelittaBleClient,
)

ADDRESS = "AA:BB:CC:DD:EE:FF"


# ── 1. Failure classification ──────────────────────────────────────────


class TestFailureClassification:
    def test_pairing_error_is_auth_class(self):
        err = BleakError("Pairing failed due to error: 82")
        assert MelittaBleClient._classify_connect_error(err) == FAILURE_AUTH

    def test_nested_cause_pairing_error_is_auth_class(self):
        inner = BleakError("Pairing failed due to error: 82")
        outer = BleakError("failed after 3 attempts")
        outer.__cause__ = inner
        assert MelittaBleClient._classify_connect_error(outer) == FAILURE_AUTH

    def test_timeout_is_timeout_class(self):
        assert (
            MelittaBleClient._classify_connect_error(asyncio.TimeoutError())
            == FAILURE_TIMEOUT
        )

    def test_establish_failure_is_link_class(self):
        err = BleakError(
            "Error ESP_GATT_CONN_FAIL_ESTABLISH while connecting: "
            "Connection failed to establish"
        )
        assert MelittaBleClient._classify_connect_error(err) == FAILURE_LINK


# ── 2. Auth-evidence gate for the unpair rung ──────────────────────────


class TestAuthEvidenceGate:
    async def test_transient_failures_with_link_do_not_unpair(self):
        """The Jay regression: link opens, notify/handshake fails for a
        non-auth reason twice — a valid bond must survive."""
        client = MelittaBleClient(ADDRESS, pair_settle_delay=0)
        bleak_client = MagicMock()
        bleak_client.is_connected = True
        establish = AsyncMock(return_value=bleak_client)
        notify = AsyncMock(side_effect=BleakError("connection dropped"))
        unpair = AsyncMock()
        with patch.object(client, "_establish_connection", establish), \
                patch.object(client, "_start_notify", notify), \
                patch.object(client, "_safe_disconnect", new=AsyncMock()), \
                patch.object(client, "_try_unpair", unpair):
            result = await client._connect_impl()
        assert result is False
        assert client._ble_link_seen is True  # link DID open...
        unpair.assert_not_awaited()           # ...but no auth evidence

    async def test_auth_failure_authorizes_unpair(self):
        client = MelittaBleClient(ADDRESS, pair_settle_delay=0)
        establish = AsyncMock(
            side_effect=BleakError("Pairing failed due to error: 82"),
        )
        unpair = AsyncMock()
        with patch.object(client, "_establish_connection", establish), \
                patch.object(client, "_try_unpair", unpair):
            result = await client._connect_impl()
        assert result is False
        unpair.assert_awaited_once()

    async def test_auth_failure_without_link_still_authorizes_unpair(self):
        """The old gate's false-negative: SMP rejection can happen without
        our link_seen flag; auth evidence alone must suffice."""
        client = MelittaBleClient(ADDRESS, pair_settle_delay=0)
        assert client._ble_link_seen is False
        establish = AsyncMock(
            side_effect=BleakError("Pairing failed due to error: 82"),
        )
        unpair = AsyncMock()
        with patch.object(client, "_establish_connection", establish), \
                patch.object(client, "_try_unpair", unpair):
            await client._connect_impl()
        unpair.assert_awaited_once()

    async def test_auth_flag_resets_each_cycle(self):
        client = MelittaBleClient(ADDRESS, pair_settle_delay=0)
        client._auth_fail_seen = True  # stale from a previous cycle
        establish = AsyncMock(side_effect=asyncio.TimeoutError())
        unpair = AsyncMock()
        with patch.object(client, "_establish_connection", establish), \
                patch.object(client, "_try_unpair", unpair):
            await client._connect_impl()
        unpair.assert_not_awaited()


# ── 3. Once-per-episode unpair latch ───────────────────────────────────


class TestUnpairLatch:
    async def _fail_cycle_with_auth(self, client, unpair):
        establish = AsyncMock(
            side_effect=BleakError("Pairing failed due to error: 82"),
        )
        with patch.object(client, "_establish_connection", establish), \
                patch.object(client, "_try_unpair", unpair):
            await client._connect_impl()

    async def test_second_cycle_does_not_unpair_again(self):
        client = MelittaBleClient(ADDRESS, pair_settle_delay=0)
        unpair = AsyncMock(side_effect=lambda: setattr(
            client, "_unpaired_this_episode", True))
        await self._fail_cycle_with_auth(client, unpair)
        await self._fail_cycle_with_auth(client, unpair)
        assert unpair.await_count == 1

    async def test_latch_resets_on_successful_connect(self):
        client = MelittaBleClient(ADDRESS)
        client._unpaired_this_episode = True
        with patch.object(client, "_try_connect_and_handshake",
                          new=AsyncMock(return_value=True)), \
                patch.object(client, "_read_dis_service", new=AsyncMock()), \
                patch.object(client, "_protocol") as proto:
            proto.read_version = AsyncMock(return_value="1.0")
            proto.read_serial = AsyncMock(return_value=None)
            proto.read_features = AsyncMock(return_value=None)
            proto.read_numerical = AsyncMock(return_value=None)
            proto.set_family = MagicMock()
            assert await client._connect_impl() is True
        assert client._unpaired_this_episode is False


# ── 4. Backoff survives advertisement wake-ups during a failure episode ─


class TestBackoffEpisode:
    async def test_wake_ignored_during_failure_episode(self):
        client = MelittaBleClient(ADDRESS)
        client._consecutive_connect_failures = 2

        async def keep_waking():
            for _ in range(20):
                client._reconnect_event.set()
                await asyncio.sleep(0.01)

        waker = asyncio.create_task(keep_waking())
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        woke_early = await client._wait_backoff(0.3)
        elapsed = loop.time() - t0
        waker.cancel()
        assert woke_early is False
        assert elapsed >= 0.25  # waited (almost) the full delay

    async def test_wake_honored_when_no_failures(self):
        client = MelittaBleClient(ADDRESS)
        client._consecutive_connect_failures = 0
        loop = asyncio.get_running_loop()
        loop.call_later(0.02, client._reconnect_event.set)
        t0 = loop.time()
        woke_early = await client._wait_backoff(5.0)
        elapsed = loop.time() - t0
        assert woke_early is True
        assert elapsed < 1.0


# ── 5. No second loop while the initial-connect loop runs ──────────────


class TestLoopDedup:
    def test_set_ble_device_does_not_spawn_second_loop(self):
        client = MelittaBleClient(ADDRESS)
        client._auto_reconnect = True
        client._external_loop_active = True
        device = MagicMock()
        client.set_ble_device(device)
        assert client._reconnect_task is None
        assert client._reconnect_event.is_set()  # existing loop still woken

    async def test_connect_and_poll_marks_and_clears_flag(self):
        from custom_components.melitta_barista import _async_connect_and_poll

        client = MelittaBleClient(ADDRESS)
        seen: list[bool] = []

        async def fake_connect():
            seen.append(client._external_loop_active)
            return True

        with patch.object(client, "connect", side_effect=fake_connect), \
                patch.object(client, "start_polling"):
            await _async_connect_and_poll(client, initial_delay=0)
        assert seen == [True]
        assert client._external_loop_active is False
