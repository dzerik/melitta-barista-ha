"""Tests for the v0.51.0 pairing-recovery routine.

The bug being fixed (#10): after long BLE silence the ESPHome proxy caches
a stale BLEDevice in habluetooth's `_previous_service_info`, all reconnect
attempts then fail with HU handshake timeout / `BluetoothDevicePairingResponse`
timeout, and the only recovery used to be delete+re-add of the integration.

These tests verify the three reconnect-loop invariants that drive the
recovery:

1. The settle delay between pair=False fail and pair=True is honored.
2. Connect failures are counted and the repair callback fires at the
   configured threshold (and not before).
3. `disconnect()` resets the bond-tracking flag.
4. `connect()` success resets the failure counter.

The actual ESPHome-entry reload + BLEDevice eviction happens via HA APIs
that need a full hass fixture; that path is hand-tested.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bleak.exc import BleakError

from custom_components.melitta_barista.ble_client import MelittaBleClient


# ── Settle-delay between pair attempts ─────────────────────────────────


class TestPairSettleDelay:
    """Layer 1: pause between pair=False fail and pair=True retry."""

    async def test_sleep_happens_only_when_pair_false_fails(self):
        """No settle delay when pair=False succeeds on first try."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF", pair_settle_delay=0.05)

        with patch.object(client, "_try_connect_and_handshake", new=AsyncMock(return_value=True)):
            with patch("asyncio.sleep", new=AsyncMock()) as sleep_mock:
                with patch.object(client, "_read_dis_service", new=AsyncMock()):
                    with patch.object(client, "_protocol") as proto:
                        proto.read_version = AsyncMock(return_value="1.0")
                        proto.read_features = AsyncMock(return_value=None)
                        proto.read_numerical = AsyncMock(return_value=None)
                        await client._connect_impl()

        # No sleep with settle-delay argument
        for call in sleep_mock.call_args_list:
            assert call.args[0] != client._pair_settle_delay

    async def test_sleep_happens_between_pair_false_fail_and_pair_true(self):
        """When pair=False fails, settle delay precedes the pair=True attempt."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF", pair_settle_delay=0.05)
        order: list[str] = []

        async def fake_handshake(pair: bool = False) -> bool:
            order.append(f"connect_pair={pair}")
            return False  # both attempts fail so loop goes through unpair path

        async def fake_sleep(t):
            order.append(f"sleep_{t}")

        with patch.object(client, "_try_connect_and_handshake", new=fake_handshake):
            with patch.object(client, "_try_unpair", new=AsyncMock()):
                with patch("asyncio.sleep", new=fake_sleep):
                    result = await client._connect_impl()

        assert result is False
        # Pattern: pair=False → sleep → pair=True → unpair → sleep → pair=True
        assert order == [
            "connect_pair=False",
            "sleep_0.05",
            "connect_pair=True",
            "sleep_0.05",
            "connect_pair=True",
        ]


# ── Disconnect resets bond flag ────────────────────────────────────────


class TestDisconnectResetsPaired:
    """Layer 2: disconnect() drops the _paired flag (hygiene)."""

    async def test_disconnect_clears_paired(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        client._paired = True
        client._client = None
        client._reconnect_task = None
        client._post_connect_task = None

        await client.disconnect()

        assert client._paired is False


# ── Reconnect loop: failure counter + repair trigger ───────────────────


class TestRepairTrigger:
    """Layer 3: the repair callback fires after N consecutive failures."""

    async def test_counter_increments_on_failure(self):
        client = MelittaBleClient(
            "AA:BB:CC:DD:EE:FF",
            reconnect_delay=0.01,
            reconnect_max_delay=0.01,
            repair_after_failures=3,
        )
        client._auto_reconnect = True
        callback_calls: list[None] = []
        client.set_repair_callback(lambda: callback_calls.append(None))

        # Connect always fails for 2 calls then we shut down the loop.
        call_count = 0

        async def fake_connect():
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                client._auto_reconnect = False
            return False

        with patch.object(client, "connect", side_effect=fake_connect):
            await asyncio.wait_for(client._reconnect_loop(), timeout=1.0)

        # 2 fails, threshold is 3 — callback NOT called yet
        assert client._consecutive_connect_failures == 2
        assert callback_calls == []

    async def test_callback_fires_at_threshold(self):
        client = MelittaBleClient(
            "AA:BB:CC:DD:EE:FF",
            reconnect_delay=0.01,
            reconnect_max_delay=0.01,
            repair_after_failures=2,
        )
        client._auto_reconnect = True
        callback_calls: list[None] = []
        client.set_repair_callback(lambda: callback_calls.append(None))

        call_count = 0

        async def fake_connect():
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                client._auto_reconnect = False
            return False

        with patch.object(client, "connect", side_effect=fake_connect):
            await asyncio.wait_for(client._reconnect_loop(), timeout=1.0)

        # Threshold=2, so first 2 failures → callback fires, counter resets.
        assert len(callback_calls) >= 1

    async def test_counter_resets_on_success(self):
        """A successful connect zeroes the counter."""
        client = MelittaBleClient(
            "AA:BB:CC:DD:EE:FF", repair_after_failures=2,
        )
        client._consecutive_connect_failures = 5

        with patch.object(client, "_try_connect_and_handshake", new=AsyncMock(return_value=True)):
            with patch.object(client, "_read_dis_service", new=AsyncMock()):
                with patch.object(client, "_protocol") as proto:
                    proto.read_version = AsyncMock(return_value="1.0")
                    proto.read_features = AsyncMock(return_value=None)
                    proto.read_numerical = AsyncMock(return_value=None)
                    proto.set_family = MagicMock()
                    result = await client._connect_impl()

        assert result is True
        assert client._consecutive_connect_failures == 0

    async def test_threshold_zero_disables_repair(self):
        """repair_after_failures=0 means recovery is OFF (debug mode)."""
        client = MelittaBleClient(
            "AA:BB:CC:DD:EE:FF",
            reconnect_delay=0.01,
            reconnect_max_delay=0.01,
            repair_after_failures=0,
        )
        client._auto_reconnect = True
        callback_calls: list[None] = []
        client.set_repair_callback(lambda: callback_calls.append(None))

        call_count = 0

        async def fake_connect():
            nonlocal call_count
            call_count += 1
            if call_count >= 5:
                client._auto_reconnect = False
            return False

        with patch.object(client, "connect", side_effect=fake_connect):
            await asyncio.wait_for(client._reconnect_loop(), timeout=1.0)

        # Despite many failures, no repair attempt — repair_after_failures
        # is the off switch.
        assert callback_calls == []

    async def test_no_callback_means_no_crash(self):
        """When the integration didn't wire a callback, the loop survives."""
        client = MelittaBleClient(
            "AA:BB:CC:DD:EE:FF",
            reconnect_delay=0.01,
            reconnect_max_delay=0.01,
            repair_after_failures=2,
        )
        client._auto_reconnect = True
        # No callback installed.

        call_count = 0

        async def fake_connect():
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                client._auto_reconnect = False
            return False

        with patch.object(client, "connect", side_effect=fake_connect):
            await asyncio.wait_for(client._reconnect_loop(), timeout=1.0)
        # The point: no exception was raised by the loop.


# ── Presence-gated wedge detection (issue #12) ─────────────────────────


class TestPresenceGating:
    """A powered-off device (not advertising) must NOT trigger pairing_wedged.

    Regression for issue #12: turning the machine off between uses caused the
    reconnect loop to rack up consecutive failures and falsely fire the
    pairing_wedged repair. A genuinely wedged device keeps advertising, so
    presence cleanly separates the two cases.
    """

    async def test_absent_device_skips_connect_and_does_not_wedge(self):
        client = MelittaBleClient(
            "AA:BB:CC:DD:EE:FF",
            reconnect_delay=0.01,
            reconnect_max_delay=0.01,
            repair_after_failures=2,
        )
        client._auto_reconnect = True
        callback_calls: list[None] = []
        client.set_repair_callback(lambda: callback_calls.append(None))

        # Device never advertises (powered off). Terminate after a few checks.
        presence_checks = 0

        def fake_presence() -> bool:
            nonlocal presence_checks
            presence_checks += 1
            if presence_checks >= 4:
                client._auto_reconnect = False
            return False

        client.set_presence_callback(fake_presence)

        connect_calls: list[None] = []

        async def fake_connect():
            connect_calls.append(None)
            return False

        with patch.object(client, "connect", side_effect=fake_connect):
            await asyncio.wait_for(client._reconnect_loop(), timeout=1.0)

        # Never attempted a connect while the device was absent ...
        assert connect_calls == []
        # ... wedge counter stayed at zero ...
        assert client._consecutive_connect_failures == 0
        # ... and the repair never fired.
        assert callback_calls == []

    async def test_absent_device_resets_existing_wedge_counter(self):
        client = MelittaBleClient(
            "AA:BB:CC:DD:EE:FF",
            reconnect_delay=0.01,
            reconnect_max_delay=0.01,
            repair_after_failures=5,
        )
        client._auto_reconnect = True
        # Simulate prior failures while the device was present.
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

        # Going absent clears the wedge counter — a wedge must be re-established
        # from a present, reachable device.
        assert client._consecutive_connect_failures == 0

    async def test_present_device_still_wedges(self):
        """A still-advertising device that won't connect remains a wedge."""
        client = MelittaBleClient(
            "AA:BB:CC:DD:EE:FF",
            reconnect_delay=0.01,
            reconnect_max_delay=0.01,
            repair_after_failures=2,
        )
        client._auto_reconnect = True
        callback_calls: list[None] = []
        client.set_repair_callback(lambda: callback_calls.append(None))
        client.set_presence_callback(lambda: True)  # always advertising

        call_count = 0

        async def fake_connect():
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                client._auto_reconnect = False
            return False

        with patch.object(client, "connect", side_effect=fake_connect):
            await asyncio.wait_for(client._reconnect_loop(), timeout=1.0)

        # Threshold=2 with a present-but-unconnectable device → wedge fires.
        assert len(callback_calls) >= 1


# ── Set/clear repair callback ──────────────────────────────────────────


class TestRepairCallbackAPI:
    """Setter accepts both plain callables and coroutine functions."""

    def test_set_and_clear(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        cb = lambda: None
        client.set_repair_callback(cb)
        assert client._repair_callback is cb
        client.set_repair_callback(None)
        assert client._repair_callback is None

    def test_set_and_clear_presence(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        cb = lambda: True
        client.set_presence_callback(cb)
        assert client._presence_callback is cb
        client.set_presence_callback(None)
        assert client._presence_callback is None

    def test_consecutive_failures_property(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        client._consecutive_connect_failures = 7
        assert client.consecutive_connect_failures == 7


# ── Manual recovery via Options Flow ───────────────────────────────────


class TestOptionsRepairStep:
    """The "Repair connection" menu item routes to _async_repair_pairing."""

    async def test_repair_step_calls_repair_routine_and_aborts_proxy(self):
        from custom_components.melitta_barista.config_flow import MelittaOptionsFlow

        flow = MelittaOptionsFlow(MagicMock(entry_id="abc"))
        flow.hass = MagicMock()

        with patch(
            "custom_components.melitta_barista._async_repair_pairing",
            new=AsyncMock(return_value=True),
        ) as patched:
            result = await flow.async_step_repair(user_input={})

        patched.assert_awaited_once()
        assert result["type"] == "abort"
        assert result["reason"] == "repair_proxy_reloaded"

    async def test_repair_step_aborts_local_when_no_proxy(self):
        from custom_components.melitta_barista.config_flow import MelittaOptionsFlow

        flow = MelittaOptionsFlow(MagicMock(entry_id="abc"))
        flow.hass = MagicMock()

        with patch(
            "custom_components.melitta_barista._async_repair_pairing",
            new=AsyncMock(return_value=False),
        ):
            result = await flow.async_step_repair(user_input={})

        assert result["type"] == "abort"
        assert result["reason"] == "repair_local_reconnect"

    async def test_repair_step_aborts_failed_on_exception(self):
        from custom_components.melitta_barista.config_flow import MelittaOptionsFlow

        flow = MelittaOptionsFlow(MagicMock(entry_id="abc"))
        flow.hass = MagicMock()

        with patch(
            "custom_components.melitta_barista._async_repair_pairing",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            result = await flow.async_step_repair(user_input={})

        assert result["type"] == "abort"
        assert result["reason"] == "repair_failed"

    async def test_repair_step_shows_form_on_first_entry(self):
        from custom_components.melitta_barista.config_flow import MelittaOptionsFlow

        flow = MelittaOptionsFlow(MagicMock(entry_id="abc"))
        result = await flow.async_step_repair(user_input=None)

        assert result["type"] == "form"
        assert result["step_id"] == "repair"


class TestOptionsFullPairStep:
    """The hard recovery option in Options Flow — wipes ESP bond + reloads."""

    async def test_full_pair_done_when_bond_cleared_and_reloaded(self):
        from custom_components.melitta_barista.config_flow import MelittaOptionsFlow

        flow = MelittaOptionsFlow(MagicMock(entry_id="abc"))
        flow.hass = MagicMock()

        with patch(
            "custom_components.melitta_barista._async_force_repair",
            new=AsyncMock(return_value={
                "bond_cleared": True,
                "proxy_reloaded": True,
                "service_name": "ble_proxy_x_clear_ble_bonds",
                "service_missing": False,
            }),
        ):
            result = await flow.async_step_full_pair(user_input={})

        assert result["type"] == "abort"
        assert result["reason"] == "full_pair_done"

    async def test_full_pair_no_action_when_service_missing(self):
        from custom_components.melitta_barista.config_flow import MelittaOptionsFlow

        flow = MelittaOptionsFlow(MagicMock(entry_id="abc"))
        flow.hass = MagicMock()

        with patch(
            "custom_components.melitta_barista._async_force_repair",
            new=AsyncMock(return_value={
                "bond_cleared": False,
                "proxy_reloaded": True,
                "service_name": "ble_proxy_x_clear_ble_bonds",
                "service_missing": True,
            }),
        ):
            result = await flow.async_step_full_pair(user_input={})

        assert result["type"] == "abort"
        assert result["reason"] == "full_pair_no_action"
        assert "service" in (result.get("description_placeholders") or {})

    async def test_full_pair_local_only_when_no_proxy(self):
        from custom_components.melitta_barista.config_flow import MelittaOptionsFlow

        flow = MelittaOptionsFlow(MagicMock(entry_id="abc"))
        flow.hass = MagicMock()

        with patch(
            "custom_components.melitta_barista._async_force_repair",
            new=AsyncMock(return_value={
                "bond_cleared": False,
                "proxy_reloaded": False,
                "service_name": None,
                "service_missing": False,
            }),
        ):
            result = await flow.async_step_full_pair(user_input={})

        assert result["type"] == "abort"
        assert result["reason"] == "full_pair_local_only"

    async def test_full_pair_partial_when_reload_only(self):
        """Proxy reloaded, but clear-bond call failed at runtime (not missing)."""
        from custom_components.melitta_barista.config_flow import MelittaOptionsFlow

        flow = MelittaOptionsFlow(MagicMock(entry_id="abc"))
        flow.hass = MagicMock()

        with patch(
            "custom_components.melitta_barista._async_force_repair",
            new=AsyncMock(return_value={
                "bond_cleared": False,
                "proxy_reloaded": True,
                "service_name": "ble_proxy_x_clear_ble_bonds",
                "service_missing": False,
            }),
        ):
            result = await flow.async_step_full_pair(user_input={})

        assert result["type"] == "abort"
        assert result["reason"] == "full_pair_partial"

    async def test_full_pair_aborts_failed_on_exception(self):
        from custom_components.melitta_barista.config_flow import MelittaOptionsFlow

        flow = MelittaOptionsFlow(MagicMock(entry_id="abc"))
        flow.hass = MagicMock()

        with patch(
            "custom_components.melitta_barista._async_force_repair",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            result = await flow.async_step_full_pair(user_input={})

        assert result["type"] == "abort"
        assert result["reason"] == "full_pair_failed"
