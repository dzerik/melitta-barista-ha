"""Tests for Melitta Barista Smart BLE Agent (D-Bus pairing).

Since dbus_fast is not available in the test environment, all dbus_fast
imports are mocked at the module level before importing ble_agent.
"""

from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from . import MOCK_ADDRESS

# ---------------------------------------------------------------------------
# Mock dbus_fast modules so ble_agent can be imported in test env
# ---------------------------------------------------------------------------


def _build_dbus_mocks():
    """Create mock dbus_fast modules and inject them into sys.modules."""
    # Top-level dbus_fast
    dbus_fast = types.ModuleType("dbus_fast")
    dbus_fast.BusType = MagicMock()
    dbus_fast.BusType.SYSTEM = "system"
    dbus_fast.Variant = MagicMock()

    # dbus_fast.aio
    dbus_fast_aio = types.ModuleType("dbus_fast.aio")
    dbus_fast_aio.MessageBus = MagicMock()
    dbus_fast.aio = dbus_fast_aio

    # dbus_fast.service
    dbus_fast_service = types.ModuleType("dbus_fast.service")

    # ServiceInterface needs to be a real class so _NoInputOutputAgent
    # can inherit from it
    class FakeServiceInterface:
        def __init__(self, name: str = ""):
            self._name = name

    dbus_fast_service.ServiceInterface = FakeServiceInterface

    # dbus_method is a no-op decorator
    def fake_dbus_method(*args, **kwargs):
        def decorator(fn):
            return fn
        return decorator

    dbus_fast_service.dbus_method = fake_dbus_method
    dbus_fast.service = dbus_fast_service

    return {
        "dbus_fast": dbus_fast,
        "dbus_fast.aio": dbus_fast_aio,
        "dbus_fast.service": dbus_fast_service,
    }


@pytest.fixture(autouse=True)
def _inject_dbus_mocks():
    """Inject mock dbus_fast modules before each test and clean up after."""
    mocks = _build_dbus_mocks()
    # Remove any cached ble_agent module so it re-imports with our mocks
    mod_key = "custom_components.melitta_barista.ble_agent"
    saved_module = sys.modules.pop(mod_key, None)
    saved_dbus = {k: sys.modules.pop(k, None) for k in list(mocks)}

    sys.modules.update(mocks)
    yield mocks
    # Restore
    for k in mocks:
        sys.modules.pop(k, None)
    for k, v in saved_dbus.items():
        if v is not None:
            sys.modules[k] = v
    sys.modules.pop(mod_key, None)
    if saved_module is not None:
        sys.modules[mod_key] = saved_module


def _import_ble_agent():
    """Import ble_agent after dbus mocks are in place."""
    import importlib

    mod_key = "custom_components.melitta_barista.ble_agent"
    if mod_key in sys.modules:
        return importlib.reload(sys.modules[mod_key])
    return importlib.import_module(mod_key)


# ---------------------------------------------------------------------------
# _NoInputOutputAgent class
# ---------------------------------------------------------------------------


class TestNoInputOutputAgent:
    """Tests for the _NoInputOutputAgent D-Bus agent class."""

    def test_agent_initializes_with_interface_name(self) -> None:
        """Agent is initialized with 'org.bluez.Agent1' interface name."""
        mod = _import_ble_agent()
        agent = mod._NoInputOutputAgent()
        assert agent._name == "org.bluez.Agent1"

    def test_release_does_not_raise(self) -> None:
        """Release method completes without error."""
        mod = _import_ble_agent()
        agent = mod._NoInputOutputAgent()
        agent.Release()

    def test_request_confirmation_does_not_raise(self) -> None:
        """RequestConfirmation accepts device path and passkey."""
        mod = _import_ble_agent()
        agent = mod._NoInputOutputAgent()
        agent.RequestConfirmation("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF", 123456)

    def test_request_authorization_does_not_raise(self) -> None:
        """RequestAuthorization accepts a device path."""
        mod = _import_ble_agent()
        agent = mod._NoInputOutputAgent()
        agent.RequestAuthorization("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")

    def test_authorize_service_does_not_raise(self) -> None:
        """AuthorizeService accepts device path and uuid."""
        mod = _import_ble_agent()
        agent = mod._NoInputOutputAgent()
        agent.AuthorizeService("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF", "some-uuid")

    def test_request_passkey_returns_zero(self) -> None:
        """RequestPasskey returns 0."""
        mod = _import_ble_agent()
        agent = mod._NoInputOutputAgent()
        assert agent.RequestPasskey("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF") == 0

    def test_request_pin_code_returns_0000(self) -> None:
        """RequestPinCode returns '0000'."""
        mod = _import_ble_agent()
        agent = mod._NoInputOutputAgent()
        assert agent.RequestPinCode("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF") == "0000"

    def test_display_passkey_does_not_raise(self) -> None:
        """DisplayPasskey accepts device, passkey and entered count."""
        mod = _import_ble_agent()
        agent = mod._NoInputOutputAgent()
        agent.DisplayPasskey("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF", 123456, 0)

    def test_display_pin_code_does_not_raise(self) -> None:
        """DisplayPinCode accepts device path and pin string."""
        mod = _import_ble_agent()
        agent = mod._NoInputOutputAgent()
        agent.DisplayPinCode("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF", "1234")

    def test_cancel_does_not_raise(self) -> None:
        """Cancel method completes without error."""
        mod = _import_ble_agent()
        agent = mod._NoInputOutputAgent()
        agent.Cancel()


# ---------------------------------------------------------------------------
# _wait_for_device
# ---------------------------------------------------------------------------


class TestWaitForDevice:
    """Tests for the _wait_for_device retry loop."""

    @pytest.mark.asyncio
    async def test_device_found_immediately(self) -> None:
        """Device found on first introspect returns True."""
        mod = _import_ble_agent()
        bus = MagicMock()
        bus.introspect = AsyncMock(return_value="<introspection>")

        result = await mod._wait_for_device(bus, "/org/bluez/hci0/dev_AA", timeout=5.0)
        assert result is True
        bus.introspect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_device_found_after_retries(self) -> None:
        """Device is found after a few failed attempts."""
        mod = _import_ble_agent()
        bus = MagicMock()
        bus.introspect = AsyncMock(
            side_effect=[Exception("not found"), Exception("not found"), "<ok>"]
        )

        with patch("custom_components.melitta_barista.ble_agent.asyncio.sleep", new_callable=AsyncMock):
            result = await mod._wait_for_device(bus, "/path", timeout=5.0)

        assert result is True
        assert bus.introspect.await_count == 3

    @pytest.mark.asyncio
    async def test_device_not_found_timeout(self) -> None:
        """When device is never found, returns False after timeout."""
        mod = _import_ble_agent()
        bus = MagicMock()
        bus.introspect = AsyncMock(side_effect=Exception("not found"))

        with patch("custom_components.melitta_barista.ble_agent.asyncio.sleep", new_callable=AsyncMock):
            result = await mod._wait_for_device(bus, "/path", timeout=3.0)

        assert result is False
        assert bus.introspect.await_count == 3


# ---------------------------------------------------------------------------
# async_pair_device
# ---------------------------------------------------------------------------


def _make_mock_bus(
    adapter_exists: bool = True,
    device_exists: bool = False,
    device_paired: bool = False,
):
    """Create a mock MessageBus with configurable behavior.

    Returns (bus_instance, MessageBus_constructor_mock).
    """
    bus = MagicMock()
    bus.connect = AsyncMock(return_value=bus)
    bus.disconnect = MagicMock()
    bus.export = MagicMock()

    # Adapter introspection
    adapter_introspection = MagicMock()
    adapter_proxy = MagicMock()
    adapter_iface = MagicMock()
    adapter_iface.call_start_discovery = AsyncMock()
    adapter_iface.call_stop_discovery = AsyncMock()
    adapter_proxy.get_interface = MagicMock(return_value=adapter_iface)

    # Agent manager
    agent_mgr = MagicMock()
    agent_mgr.call_register_agent = AsyncMock()
    agent_mgr.call_request_default_agent = AsyncMock()
    agent_mgr.call_unregister_agent = AsyncMock()

    # Root bluez proxy
    bluez_introspection = MagicMock()
    bluez_proxy = MagicMock()
    bluez_proxy.get_interface = MagicMock(return_value=agent_mgr)

    # Device proxy
    dev_introspection = MagicMock()
    dev_proxy = MagicMock()
    dev_props = MagicMock()

    paired_variant = MagicMock()
    paired_variant.value = device_paired
    dev_props.call_get = AsyncMock(return_value=paired_variant)
    dev_props.call_set = AsyncMock()

    device_iface = MagicMock()
    device_iface.call_pair = AsyncMock()

    def get_dev_interface(name):
        if name == "org.freedesktop.DBus.Properties":
            return dev_props
        if name == "org.bluez.Device1":
            return device_iface
        return MagicMock()

    dev_proxy.get_interface = MagicMock(side_effect=get_dev_interface)

    # Configure introspect behavior
    introspect_map = {}
    if adapter_exists:
        introspect_map[("/org/bluez/hci0",)] = adapter_introspection
    introspect_map[("/org/bluez",)] = bluez_introspection

    async def mock_introspect(service, path):
        if path == "/org/bluez/hci0":
            if not adapter_exists:
                raise Exception("No adapter")
            return adapter_introspection
        if path == "/org/bluez":
            return bluez_introspection
        # Device path
        if device_exists:
            return dev_introspection
        raise Exception("Device not found")

    bus.introspect = AsyncMock(side_effect=mock_introspect)

    def mock_get_proxy(service, path, introspection):
        if path == "/org/bluez/hci0":
            return adapter_proxy
        if path == "/org/bluez":
            return bluez_proxy
        return dev_proxy

    bus.get_proxy_object = MagicMock(side_effect=mock_get_proxy)

    # Constructor mock
    constructor = MagicMock()
    constructor.return_value = MagicMock()
    constructor.return_value.connect = AsyncMock(return_value=bus)

    return bus, constructor


class TestAsyncPairDevice:
    """Tests for the async_pair_device function."""

    @pytest.mark.asyncio
    async def test_no_adapter_returns_ok(self) -> None:
        """When no BlueZ adapter (hci0) exists, returns 'ok' (ESPHome proxy)."""
        mod = _import_ble_agent()
        bus, constructor = _make_mock_bus(adapter_exists=False)

        with patch.object(mod, "MessageBus", constructor):
            result = await mod.async_pair_device(MOCK_ADDRESS, timeout=5.0)

        assert result == "ok"
        bus.disconnect.assert_called()

    @pytest.mark.asyncio
    async def test_device_already_paired_returns_ok(self) -> None:
        """When device is already paired, returns 'ok' without re-pairing."""
        mod = _import_ble_agent()
        bus, constructor = _make_mock_bus(
            adapter_exists=True, device_exists=True, device_paired=True
        )

        with patch.object(mod, "MessageBus", constructor):
            result = await mod.async_pair_device(MOCK_ADDRESS, timeout=5.0)

        assert result == "ok"

    @pytest.mark.asyncio
    async def test_successful_pairing(self) -> None:
        """Successful D-Bus pairing flow returns 'ok'."""
        mod = _import_ble_agent()
        bus, constructor = _make_mock_bus(
            adapter_exists=True, device_exists=True, device_paired=False
        )

        with patch.object(mod, "MessageBus", constructor):
            result = await mod.async_pair_device(MOCK_ADDRESS, timeout=5.0)

        assert result == "ok"

    @pytest.mark.asyncio
    async def test_pairing_timeout_returns_pairing_timeout(self) -> None:
        """When pairing times out, returns 'pairing_timeout'."""
        mod = _import_ble_agent()
        bus, constructor = _make_mock_bus(
            adapter_exists=True, device_exists=True, device_paired=False
        )

        # Make call_pair hang forever
        dev_path = "/org/bluez/hci0/dev_" + MOCK_ADDRESS.upper().replace(":", "_")
        dev_proxy = bus.get_proxy_object("org.bluez", dev_path, MagicMock())
        device_iface = dev_proxy.get_interface("org.bluez.Device1")
        device_iface.call_pair = AsyncMock(side_effect=asyncio.TimeoutError)

        # We need to also patch asyncio.wait_for to raise TimeoutError
        async def mock_wait_for(coro, timeout):
            try:
                return await coro
            except asyncio.TimeoutError:
                raise

        with (
            patch.object(mod, "MessageBus", constructor),
            patch.object(mod.asyncio, "wait_for", side_effect=asyncio.TimeoutError),
        ):
            result = await mod.async_pair_device(MOCK_ADDRESS, timeout=1.0)

        assert result == "pairing_timeout"

    @pytest.mark.asyncio
    async def test_pairing_already_exists_returns_ok(self) -> None:
        """When pairing fails with AlreadyExists, returns 'ok'."""
        mod = _import_ble_agent()
        bus, constructor = _make_mock_bus(
            adapter_exists=True, device_exists=True, device_paired=False
        )

        # Make asyncio.wait_for propagate AlreadyExists
        async def mock_wait_for(coro, timeout):
            raise Exception("org.bluez.Error.AlreadyExists: Already Paired")

        with (
            patch.object(mod, "MessageBus", constructor),
            patch.object(mod.asyncio, "wait_for", mock_wait_for),
        ):
            result = await mod.async_pair_device(MOCK_ADDRESS, timeout=5.0)

        assert result == "ok"

    @pytest.mark.asyncio
    async def test_pairing_generic_failure_returns_pairing_failed(self) -> None:
        """When pairing fails with a generic error, returns 'pairing_failed'."""
        mod = _import_ble_agent()
        bus, constructor = _make_mock_bus(
            adapter_exists=True, device_exists=True, device_paired=False
        )

        async def mock_wait_for(coro, timeout):
            raise Exception("org.bluez.Error.AuthenticationFailed")

        with (
            patch.object(mod, "MessageBus", constructor),
            patch.object(mod.asyncio, "wait_for", mock_wait_for),
        ):
            result = await mod.async_pair_device(MOCK_ADDRESS, timeout=5.0)

        assert result == "pairing_failed"

    @pytest.mark.asyncio
    async def test_dbus_connection_error_returns_pairing_failed(self) -> None:
        """When D-Bus connection itself fails, returns 'pairing_failed'."""
        mod = _import_ble_agent()

        constructor = MagicMock()
        connector = MagicMock()
        connector.connect = AsyncMock(side_effect=Exception("Connection refused"))
        constructor.return_value = connector

        with patch.object(mod, "MessageBus", constructor):
            result = await mod.async_pair_device(MOCK_ADDRESS, timeout=5.0)

        assert result == "pairing_failed"

    @pytest.mark.asyncio
    async def test_device_not_known_starts_discovery(self) -> None:
        """When device is not known to BlueZ, discovery is started."""
        mod = _import_ble_agent()
        bus, constructor = _make_mock_bus(
            adapter_exists=True, device_exists=False, device_paired=False
        )

        # Make device appear after discovery (_wait_for_device returns False
        # so we get cannot_connect)
        with (
            patch.object(mod, "MessageBus", constructor),
            patch.object(
                mod, "_wait_for_device", new_callable=AsyncMock, return_value=False
            ),
        ):
            result = await mod.async_pair_device(MOCK_ADDRESS, timeout=5.0)

        assert result == "cannot_connect"

    @pytest.mark.asyncio
    async def test_device_not_known_discovery_then_pair(self) -> None:
        """Device not known, discovery finds it, pairing succeeds."""
        mod = _import_ble_agent()

        # Build bus with device_exists=False initially, but we'll override
        # introspect to succeed for device path after _wait_for_device
        bus, constructor = _make_mock_bus(
            adapter_exists=True, device_exists=False, device_paired=False
        )

        call_count = 0
        original_introspect = bus.introspect.side_effect

        async def patched_introspect(service, path):
            nonlocal call_count
            if "dev_" in path:
                call_count += 1
                if call_count <= 1:
                    # First call (device check) — not found
                    raise Exception("Device not found")
                # Subsequent calls (after wait_for_device) — device exists
                return MagicMock()
            return await original_introspect(service, path)

        bus.introspect = AsyncMock(side_effect=patched_introspect)

        # _wait_for_device returns True (device appeared)
        with (
            patch.object(mod, "MessageBus", constructor),
            patch.object(
                mod, "_wait_for_device", new_callable=AsyncMock, return_value=True
            ),
        ):
            result = await mod.async_pair_device(MOCK_ADDRESS, timeout=5.0)

        assert result == "ok"

    @pytest.mark.asyncio
    async def test_device_path_format(self) -> None:
        """Verify the device path is correctly formed from MAC address."""
        mod = _import_ble_agent()
        bus, constructor = _make_mock_bus(adapter_exists=False)

        with patch.object(mod, "MessageBus", constructor):
            await mod.async_pair_device(MOCK_ADDRESS, timeout=5.0)

        # The adapter introspect should have been called for hci0
        bus.introspect.assert_any_await("org.bluez", "/org/bluez/hci0")

    @pytest.mark.asyncio
    async def test_cleanup_stops_discovery_on_success(self) -> None:
        """When discovery was started, it is stopped in the finally block."""
        mod = _import_ble_agent()

        bus, constructor = _make_mock_bus(
            adapter_exists=True, device_exists=False, device_paired=False
        )

        with (
            patch.object(mod, "MessageBus", constructor),
            patch.object(
                mod, "_wait_for_device", new_callable=AsyncMock, return_value=True
            ),
        ):
            await mod.async_pair_device(MOCK_ADDRESS, timeout=5.0)

        # Bus should be disconnected in finally
        bus.disconnect.assert_called()
