"""Tests for MelittaBleClient — connect, disconnect, reconnect, brew."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bleak.backends.device import BLEDevice
from bleak.exc import BleakDBusError, BleakError

from custom_components.melitta_barista.ble_client import (
    MelittaBleClient,
    discover_melitta_devices,
    MELITTA_SERVICE_UUID,
)
from custom_components.melitta_barista.const import (
    CHAR_NOTIFY,
    CHAR_WRITE,
    DirectKeyCategory,
    MachineProcess,
    MachineType,
    PROFILE_NAMES,
    RecipeId,
    USER_ACTIVITY_IDS,
    USER_NAME_IDS,
    get_directkey_id,
)
from custom_components.melitta_barista.protocol import MachineRecipe, MachineStatus, RecipeComponent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_connected_client(mock_bleak_client) -> MelittaBleClient:
    """Return a MelittaBleClient that looks connected."""
    client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
    client._connected = True
    client._client = mock_bleak_client
    client._protocol = MagicMock()
    # Ensure read_status is awaitable (needed for poll_loop after brew)
    client._protocol.read_status = AsyncMock(return_value=None)
    return client


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

class TestClientInit:
    """Test client initialization."""

    def test_basic_init(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        assert client.address == "AA:BB:CC:DD:EE:FF"
        assert client.connected is False
        assert client.status is None
        assert client.firmware_version is None
        assert client.machine_type is None

    def test_init_with_device_name_detects_type(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF", device_name="8601ABCD")
        assert client.machine_type == MachineType.BARISTA_TS

    def test_init_with_t_device_name(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF", device_name="8301ABCD")
        assert client.machine_type == MachineType.BARISTA_T

    def test_init_with_unknown_name(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF", device_name="UnknownDevice")
        assert client.machine_type is None

    def test_model_name_ts(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF", device_name="8601ABCD")
        assert "TS" in client.model_name

    def test_model_name_unknown(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        assert client.model_name == "Melitta Barista"

    def test_total_cups_initially_none(self):
        """total_cups property returns None when not read yet."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        assert client.total_cups is None

    def test_cup_counters_initially_empty(self):
        """cup_counters property returns empty dict initially (line 129)."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        assert client.cup_counters == {}


# ---------------------------------------------------------------------------
# BLEDevice management
# ---------------------------------------------------------------------------

class TestClientBLEDevice:
    """Test BLEDevice management."""

    def test_set_ble_device(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        device = BLEDevice("AA:BB:CC:DD:EE:FF", "test", {})
        client.set_ble_device(device)
        assert client._ble_device is device


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

class TestCallbacks:
    """Test callback registration."""

    def test_add_status_callback(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        cb = MagicMock()
        client.add_status_callback(cb)
        assert cb in client._status_callbacks

    def test_add_connection_callback(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        cb = MagicMock()
        client.add_connection_callback(cb)
        assert cb in client._connection_callbacks

    def test_add_cups_callback(self):
        """add_cups_callback registers the callback (line 132)."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        cb = MagicMock()
        client.add_cups_callback(cb)
        assert cb in client._cups_callbacks

    def test_status_callback_called(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        statuses = []
        client.add_status_callback(lambda s: statuses.append(s))
        status = MachineStatus(process=MachineProcess.READY)
        client._on_status(status)
        assert len(statuses) == 1
        assert statuses[0].process == MachineProcess.READY

    def test_status_callback_exception_isolated(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        client.add_status_callback(MagicMock(side_effect=ValueError("test")))
        good_cb = MagicMock()
        client.add_status_callback(good_cb)

        status = MachineStatus(process=MachineProcess.READY)
        client._on_status(status)
        good_cb.assert_called_once_with(status)


# ---------------------------------------------------------------------------
# _on_status: PRODUCT -> READY triggers cup counter refresh (lines 157-158)
# ---------------------------------------------------------------------------

class TestOnStatusCupRefresh:
    """Test that PRODUCT->READY transition triggers cup counter refresh."""

    async def test_product_to_ready_triggers_cup_counter_read(self):
        """When status transitions PRODUCT->READY, read_cup_counters is scheduled (lines 157-158)."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        client._connected = True
        client._client = MagicMock(is_connected=True)

        # Set previous status to PRODUCT
        client._status = MachineStatus(process=MachineProcess.PRODUCT)

        # Mock read_cup_counters
        read_mock = AsyncMock(return_value=True)
        client.read_cup_counters = read_mock

        # Trigger transition to READY
        new_status = MachineStatus(process=MachineProcess.READY)
        client._on_status(new_status)

        # Give the ensure_future task a chance to run
        await asyncio.sleep(0.05)

        read_mock.assert_awaited_once()

    async def test_ready_to_ready_does_not_trigger_refresh(self):
        """No refresh if previous status is not PRODUCT."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        client._status = MachineStatus(process=MachineProcess.READY)

        read_mock = AsyncMock(return_value=True)
        client.read_cup_counters = read_mock

        client._on_status(MachineStatus(process=MachineProcess.READY))
        await asyncio.sleep(0.05)

        read_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# _on_cup_refresh_done (lines 167-171)
# ---------------------------------------------------------------------------

class TestOnCupRefreshDone:
    """Test the done callback for cup counter refresh tasks."""

    def test_cancelled_task_returns_silently(self):
        """Cancelled task does not log error (line 167-168)."""
        task = MagicMock()
        task.cancelled.return_value = True
        MelittaBleClient._on_cup_refresh_done(task)
        task.exception.assert_not_called()

    def test_task_with_exception_logs_debug(self):
        """Task with exception logs debug message (lines 169-171)."""
        task = MagicMock()
        task.cancelled.return_value = False
        task.exception.return_value = BleakError("read failed")
        MelittaBleClient._on_cup_refresh_done(task)
        task.exception.assert_called_once()

    def test_successful_task_no_error(self):
        """Successful task (no exception) does nothing special."""
        task = MagicMock()
        task.cancelled.return_value = False
        task.exception.return_value = None
        MelittaBleClient._on_cup_refresh_done(task)


# ---------------------------------------------------------------------------
# _on_disconnect (lines 174-183)
# ---------------------------------------------------------------------------

class TestOnDisconnect:
    """Test disconnect callback behavior."""

    def test_on_disconnect_clears_state_and_calls_callbacks(self):
        """_on_disconnect sets connected=False, calls callbacks (lines 174-181)."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        client._connected = True
        client._client = MagicMock()
        client._auto_reconnect = False  # Prevent reconnect scheduling

        cb = MagicMock()
        client.add_connection_callback(cb)

        client._on_disconnect(MagicMock())

        assert client._connected is False
        assert client._client is None
        cb.assert_called_once_with(False)

    def test_on_disconnect_schedules_reconnect(self):
        """_on_disconnect schedules reconnect when auto_reconnect=True (lines 182-183)."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        client._connected = True
        client._client = MagicMock()
        client._auto_reconnect = True

        with patch.object(client, "_schedule_reconnect") as mock_sched:
            client._on_disconnect(MagicMock())
            mock_sched.assert_called_once()

    def test_on_disconnect_callback_exception_isolated(self):
        """Exception in connection callback does not break other callbacks (line 180)."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        client._connected = True
        client._auto_reconnect = False

        bad_cb = MagicMock(side_effect=RuntimeError("boom"))
        good_cb = MagicMock()
        client.add_connection_callback(bad_cb)
        client.add_connection_callback(good_cb)

        client._on_disconnect(MagicMock())
        good_cb.assert_called_once_with(False)


# ---------------------------------------------------------------------------
# _on_notification (line 186)
# ---------------------------------------------------------------------------

class TestOnNotification:
    """Test BLE notification forwarding."""

    def test_on_notification_forwards_to_protocol(self):
        """_on_notification calls protocol.on_ble_data (line 186)."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        client._protocol = MagicMock()

        data = bytearray(b"\x01\x02\x03")
        client._on_notification(0, data)

        client._protocol.on_ble_data.assert_called_once_with(b"\x01\x02\x03")


# ---------------------------------------------------------------------------
# _write_ble (lines 189-198)
# ---------------------------------------------------------------------------

class TestWriteBle:
    """Test low-level BLE write method."""

    async def test_write_ble_success(self):
        """Successful write under lock."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        mock_bleak = MagicMock()
        mock_bleak.is_connected = True
        mock_bleak.write_gatt_char = AsyncMock()
        client._client = mock_bleak

        await client._write_ble(b"\xAA\xBB")
        mock_bleak.write_gatt_char.assert_awaited_once_with(CHAR_WRITE, b"\xAA\xBB", response=False)

    async def test_write_ble_not_connected_raises(self):
        """Raises BleakError when not connected (lines 191-192)."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        client._client = None

        with pytest.raises(BleakError, match="Not connected"):
            await client._write_ble(b"\x00")

    async def test_write_ble_client_not_is_connected_raises(self):
        """Raises BleakError when client.is_connected is False."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        mock_bleak = MagicMock()
        mock_bleak.is_connected = False
        client._client = mock_bleak

        with pytest.raises(BleakError, match="Not connected"):
            await client._write_ble(b"\x00")

    async def test_write_ble_assertion_error_raises_bleak(self):
        """AssertionError is caught and re-raised as BleakError (lines 195-198)."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        mock_bleak = MagicMock()
        mock_bleak.is_connected = True
        mock_bleak.write_gatt_char = AsyncMock(side_effect=AssertionError("assert self._bus"))
        client._client = mock_bleak

        with pytest.raises(BleakError, match="D-Bus connection lost"):
            await client._write_ble(b"\x00")


# ---------------------------------------------------------------------------
# _establish_connection (lines 208-250)
# ---------------------------------------------------------------------------

class TestEstablishConnection:
    """Test BLE connection establishment strategies."""

    async def test_establish_via_retry_connector(self):
        """Uses establish_connection when ble_device is set and bleak_retry_connector available (lines 209-230)."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        device = BLEDevice("AA:BB:CC:DD:EE:FF", "test", {})
        client.set_ble_device(device)

        mock_conn = AsyncMock()
        mock_conn.return_value = MagicMock()  # returned client

        with patch.dict("sys.modules", {
            "bleak_retry_connector": MagicMock(
                establish_connection=mock_conn,
                BleakClientWithServiceCache=MagicMock(),
            ),
        }):
            result = await client._establish_connection()

        mock_conn.assert_awaited_once()
        assert result is mock_conn.return_value

    async def test_establish_import_error_falls_back_to_raw(self):
        """Falls back to raw BleakClient when bleak_retry_connector not installed (lines 231-234)."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        device = BLEDevice("AA:BB:CC:DD:EE:FF", "test", {})
        client.set_ble_device(device)

        mock_bleak_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.connect = AsyncMock()
        mock_bleak_cls.return_value = mock_instance

        with (
            patch(
                "custom_components.melitta_barista.ble_client.BleakClient",
                mock_bleak_cls,
            ),
            patch.dict("sys.modules", {"bleak_retry_connector": None}),
        ):
            # ImportError when bleak_retry_connector is None in sys.modules
            # Actually we need a real ImportError, let's mock differently
            pass

        # Better approach: patch the import inside the method
        with patch(
            "custom_components.melitta_barista.ble_client.BleakClient",
            mock_bleak_cls,
        ):
            # Simulate ImportError by making the import fail
            import builtins
            real_import = builtins.__import__

            def fake_import(name, *args, **kwargs):
                if name == "bleak_retry_connector":
                    raise ImportError("No module named bleak_retry_connector")
                return real_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=fake_import):
                result = await client._establish_connection()

        mock_instance.connect.assert_awaited_once()
        assert result is mock_instance

    async def test_establish_bleak_error_falls_back_to_raw(self):
        """Falls back to raw BleakClient when establish_connection raises BleakError (lines 235-239)."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        device = BLEDevice("AA:BB:CC:DD:EE:FF", "test", {})
        client.set_ble_device(device)

        mock_bleak_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.connect = AsyncMock()
        mock_bleak_cls.return_value = mock_instance

        import builtins
        real_import = builtins.__import__

        mock_establish = AsyncMock(side_effect=BleakError("connection failed"))

        def fake_import(name, *args, **kwargs):
            if name == "bleak_retry_connector":
                mod = MagicMock()
                mod.establish_connection = mock_establish
                mod.BleakClientWithServiceCache = MagicMock()
                return mod
            return real_import(name, *args, **kwargs)

        with (
            patch(
                "custom_components.melitta_barista.ble_client.BleakClient",
                mock_bleak_cls,
            ),
            patch("builtins.__import__", side_effect=fake_import),
        ):
            result = await client._establish_connection()

        assert result is mock_instance

    async def test_establish_no_ble_device_uses_raw_client(self):
        """Uses raw BleakClient when no ble_device is set (lines 242-250)."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        assert client._ble_device is None

        mock_bleak_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.connect = AsyncMock()
        mock_bleak_cls.return_value = mock_instance

        with patch(
            "custom_components.melitta_barista.ble_client.BleakClient",
            mock_bleak_cls,
        ):
            result = await client._establish_connection()

        mock_bleak_cls.assert_called_once()
        mock_instance.connect.assert_awaited_once()
        assert result is mock_instance


# ---------------------------------------------------------------------------
# _start_notify (lines 259-286)
# ---------------------------------------------------------------------------

class TestStartNotify:
    """Test notification subscription strategies."""

    async def test_start_notify_with_bluez_args(self):
        """Uses BlueZStartNotifyArgs when available (lines 261-268)."""
        mock_client = MagicMock()
        mock_client.start_notify = AsyncMock()
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")

        mock_bluez_args = MagicMock()
        with patch.dict("sys.modules", {
            "bleak.args.bluez": MagicMock(BlueZStartNotifyArgs=mock_bluez_args),
        }):
            import builtins
            real_import = builtins.__import__

            def fake_import(name, *args, **kwargs):
                if name == "bleak.args.bluez":
                    mod = MagicMock()
                    mod.BlueZStartNotifyArgs = mock_bluez_args
                    return mod
                return real_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=fake_import):
                await client._start_notify(mock_client)

        mock_client.start_notify.assert_awaited_once()

    async def test_start_notify_fallback_no_bluez_args(self):
        """Falls back to plain start_notify when BlueZStartNotifyArgs not available (lines 269-273)."""
        mock_client = MagicMock()
        mock_client.start_notify = AsyncMock()
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")

        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "bleak.args.bluez":
                raise ImportError("no bluez args")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            await client._start_notify(mock_client)

        # Should have been called once (the fallback call)
        mock_client.start_notify.assert_awaited_once_with(
            CHAR_NOTIFY, client._on_notification,
        )

    async def test_start_notify_notify_acquired_treated_as_success(self):
        """BleakDBusError with 'Notify acquired' is treated as success (lines 275-284)."""
        mock_client = MagicMock()
        mock_client.start_notify = AsyncMock(
            side_effect=BleakDBusError("org.bluez.Error", ["Notify acquired"]),
        )
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")

        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "bleak.args.bluez":
                raise ImportError("no bluez")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            # Should NOT raise
            await client._start_notify(mock_client)

    async def test_start_notify_other_dbus_error_reraises(self):
        """BleakDBusError without 'Notify acquired' is re-raised (lines 285-286)."""
        mock_client = MagicMock()
        mock_client.start_notify = AsyncMock(
            side_effect=BleakDBusError("org.bluez.Error", ["Something else"]),
        )
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")

        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "bleak.args.bluez":
                raise ImportError("no bluez")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            with pytest.raises(BleakDBusError):
                await client._start_notify(mock_client)


# ---------------------------------------------------------------------------
# connect / _connect_impl (lines 295-366)
# ---------------------------------------------------------------------------

class TestConnect:
    """Test connection flow."""

    async def test_connect_with_raw_bleak(self, mock_bleak_client):
        """Test connection using raw BleakClient fallback."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")

        mock_protocol = MagicMock()
        mock_protocol.perform_handshake = AsyncMock(return_value=True)
        mock_protocol.read_version = AsyncMock(return_value="1.2.3")
        mock_protocol.read_numerical = AsyncMock(return_value=259)
        mock_protocol.set_status_callback = MagicMock()

        with (
            patch(
                "custom_components.melitta_barista.ble_client.MelittaProtocol",
                return_value=mock_protocol,
            ),
            patch.object(
                client, "_establish_connection",
                new=AsyncMock(return_value=mock_bleak_client),
            ),
            patch.object(
                client, "_start_notify",
                new=AsyncMock(),
            ),
            patch.object(
                client, "read_cup_counters",
                new=AsyncMock(return_value=True),
            ),
            patch.object(
                client, "read_profile_data",
                new=AsyncMock(),
            ),
        ):
            result = await client._connect_impl()

        assert result is True
        assert client.connected is True
        assert client.firmware_version == "1.2.3"
        assert client.machine_type == MachineType.BARISTA_TS

    async def test_connect_handshake_fails(self, mock_bleak_client):
        """Test connection failure when handshake fails (lines 318-324)."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")

        mock_protocol = MagicMock()
        mock_protocol.perform_handshake = AsyncMock(return_value=False)
        mock_protocol.set_status_callback = MagicMock()

        with (
            patch(
                "custom_components.melitta_barista.ble_client.MelittaProtocol",
                return_value=mock_protocol,
            ),
            patch.object(
                client, "_establish_connection",
                new=AsyncMock(return_value=mock_bleak_client),
            ),
            patch.object(
                client, "_start_notify",
                new=AsyncMock(),
            ),
        ):
            result = await client._connect_impl()

        assert result is False
        mock_bleak_client.disconnect.assert_awaited_once()

    async def test_connect_handshake_fails_disconnect_raises(self, mock_bleak_client):
        """Disconnect error during handshake failure is swallowed (lines 322-323)."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        mock_bleak_client.disconnect = AsyncMock(side_effect=BleakError("disc err"))

        mock_protocol = MagicMock()
        mock_protocol.perform_handshake = AsyncMock(return_value=False)
        mock_protocol.set_status_callback = MagicMock()

        with (
            patch(
                "custom_components.melitta_barista.ble_client.MelittaProtocol",
                return_value=mock_protocol,
            ),
            patch.object(
                client, "_establish_connection",
                new=AsyncMock(return_value=mock_bleak_client),
            ),
            patch.object(client, "_start_notify", new=AsyncMock()),
        ):
            result = await client._connect_impl()

        assert result is False

    async def test_connect_client_not_connected_after_establish(self, mock_bleak_client):
        """Returns False when client.is_connected is False after establish (lines 308-310)."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        mock_bleak_client.is_connected = False

        mock_protocol = MagicMock()
        mock_protocol.set_status_callback = MagicMock()

        with (
            patch(
                "custom_components.melitta_barista.ble_client.MelittaProtocol",
                return_value=mock_protocol,
            ),
            patch.object(
                client, "_establish_connection",
                new=AsyncMock(return_value=mock_bleak_client),
            ),
        ):
            result = await client._connect_impl()

        assert result is False

    async def test_connect_unknown_machine_type(self, mock_bleak_client):
        """Unknown machine type ID logs warning but doesn't fail (lines 340-341)."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")

        mock_protocol = MagicMock()
        mock_protocol.perform_handshake = AsyncMock(return_value=True)
        mock_protocol.read_version = AsyncMock(return_value="1.0.0")
        mock_protocol.read_numerical = AsyncMock(return_value=9999)  # unknown type
        mock_protocol.set_status_callback = MagicMock()

        with (
            patch(
                "custom_components.melitta_barista.ble_client.MelittaProtocol",
                return_value=mock_protocol,
            ),
            patch.object(
                client, "_establish_connection",
                new=AsyncMock(return_value=mock_bleak_client),
            ),
            patch.object(client, "_start_notify", new=AsyncMock()),
            patch.object(client, "read_cup_counters", new=AsyncMock(return_value=True)),
            patch.object(client, "read_profile_data", new=AsyncMock()),
        ):
            result = await client._connect_impl()

        assert result is True
        # machine_type should remain None for unknown ID
        assert client._machine_type is None

    async def test_connect_calls_connection_callbacks(self, mock_bleak_client):
        """Connection callbacks are called on success (lines 348-352)."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        cb = MagicMock()
        bad_cb = MagicMock(side_effect=RuntimeError("boom"))
        client.add_connection_callback(bad_cb)
        client.add_connection_callback(cb)

        mock_protocol = MagicMock()
        mock_protocol.perform_handshake = AsyncMock(return_value=True)
        mock_protocol.read_version = AsyncMock(return_value="1.0.0")
        mock_protocol.read_numerical = AsyncMock(return_value=259)
        mock_protocol.set_status_callback = MagicMock()

        with (
            patch(
                "custom_components.melitta_barista.ble_client.MelittaProtocol",
                return_value=mock_protocol,
            ),
            patch.object(
                client, "_establish_connection",
                new=AsyncMock(return_value=mock_bleak_client),
            ),
            patch.object(client, "_start_notify", new=AsyncMock()),
            patch.object(client, "read_cup_counters", new=AsyncMock(return_value=True)),
            patch.object(client, "read_profile_data", new=AsyncMock()),
        ):
            result = await client._connect_impl()

        assert result is True
        cb.assert_called_once_with(True)

    async def test_connect_exception(self, mock_bleak_client):
        """Test connection failure on exception (lines 356-366)."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")

        with patch.object(
            client, "_establish_connection",
            new=AsyncMock(side_effect=BleakError("Connection failed")),
        ):
            result = await client._connect_impl()

        assert result is False
        assert client.connected is False

    async def test_connect_exception_with_client_disconnects(self, mock_bleak_client):
        """On exception, existing client is disconnected (lines 361-365)."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")

        mock_protocol = MagicMock()
        mock_protocol.set_status_callback = MagicMock()
        mock_protocol.perform_handshake = AsyncMock(side_effect=BleakError("fail"))

        with (
            patch(
                "custom_components.melitta_barista.ble_client.MelittaProtocol",
                return_value=mock_protocol,
            ),
            patch.object(
                client, "_establish_connection",
                new=AsyncMock(return_value=mock_bleak_client),
            ),
            patch.object(client, "_start_notify", new=AsyncMock()),
        ):
            result = await client._connect_impl()

        assert result is False
        mock_bleak_client.disconnect.assert_awaited_once()

    async def test_connect_exception_disconnect_also_raises(self, mock_bleak_client):
        """Disconnect error during exception cleanup is swallowed (lines 364-365)."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        mock_bleak_client.disconnect = AsyncMock(side_effect=OSError("disc failed"))

        mock_protocol = MagicMock()
        mock_protocol.set_status_callback = MagicMock()
        mock_protocol.perform_handshake = AsyncMock(side_effect=BleakError("fail"))

        with (
            patch(
                "custom_components.melitta_barista.ble_client.MelittaProtocol",
                return_value=mock_protocol,
            ),
            patch.object(
                client, "_establish_connection",
                new=AsyncMock(return_value=mock_bleak_client),
            ),
            patch.object(client, "_start_notify", new=AsyncMock()),
        ):
            result = await client._connect_impl()

        assert result is False
        mock_bleak_client.disconnect.assert_awaited_once()

    async def test_connect_via_public_method_uses_lock(self, mock_bleak_client):
        """Public connect() acquires _connect_lock (lines 295-296)."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")

        with patch.object(
            client, "_connect_impl",
            new=AsyncMock(return_value=True),
        ):
            result = await client.connect()

        assert result is True


# ---------------------------------------------------------------------------
# _schedule_reconnect / _reconnect_loop (lines 368-390)
# ---------------------------------------------------------------------------

class TestReconnect:
    """Test reconnection logic."""

    async def test_schedule_reconnect_creates_task(self):
        """_schedule_reconnect creates a task (lines 370-372)."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        client._auto_reconnect = True

        with patch.object(client, "_reconnect_loop", new=AsyncMock()):
            client._schedule_reconnect()
            assert client._reconnect_task is not None
            client._reconnect_task.cancel()
            try:
                await client._reconnect_task
            except asyncio.CancelledError:
                pass

    async def test_schedule_reconnect_does_not_duplicate(self):
        """Does not create a new task if one is already running (lines 370-371)."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        client._auto_reconnect = True

        with patch.object(client, "_reconnect_loop", new=AsyncMock()):
            client._schedule_reconnect()
            first_task = client._reconnect_task

            client._schedule_reconnect()
            assert client._reconnect_task is first_task
            first_task.cancel()
            try:
                await first_task
            except asyncio.CancelledError:
                pass

    async def test_reconnect_loop_successful(self):
        """Successful reconnect stops the loop (lines 376-390)."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        client._auto_reconnect = True

        mock_start_polling = MagicMock()

        with (
            patch.object(client, "connect", new=AsyncMock(return_value=True)),
            patch.object(client, "start_polling", mock_start_polling),
            patch("asyncio.sleep", new=AsyncMock()),
        ):
            await client._reconnect_loop()

        mock_start_polling.assert_called_once_with(interval=5.0)

    async def test_reconnect_loop_auto_reconnect_false_breaks(self):
        """Loop breaks when auto_reconnect becomes False (lines 381-382)."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        client._auto_reconnect = True

        call_count = 0

        async def fake_sleep(delay):
            nonlocal call_count
            call_count += 1
            # Disable after first sleep
            client._auto_reconnect = False

        with (
            patch("asyncio.sleep", side_effect=fake_sleep),
        ):
            await client._reconnect_loop()

        assert call_count == 1

    async def test_reconnect_loop_retries_on_failure(self):
        """Retry on BleakError with increasing delay (lines 388-390)."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        client._auto_reconnect = True
        attempt = 0

        async def fake_connect():
            nonlocal attempt
            attempt += 1
            if attempt < 3:
                raise BleakError("fail")
            return True

        with (
            patch.object(client, "connect", side_effect=fake_connect),
            patch.object(client, "start_polling"),
            patch("asyncio.sleep", new=AsyncMock()),
        ):
            await client._reconnect_loop()

        assert attempt == 3


# ---------------------------------------------------------------------------
# disconnect (lines 392-410)
# ---------------------------------------------------------------------------

class TestDisconnect:
    """Test disconnection."""

    async def test_disconnect_stops_polling(self, mock_bleak_client):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        client._client = mock_bleak_client
        client._connected = True
        client._auto_reconnect = True

        await client.disconnect()

        assert client._auto_reconnect is False
        assert client.connected is False
        mock_bleak_client.disconnect.assert_awaited_once()

    async def test_disconnect_when_not_connected(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        await client.disconnect()
        assert client.connected is False

    async def test_disconnect_cancels_reconnect_task(self):
        """Reconnect task is cancelled on disconnect (lines 395-397)."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        client._connected = True
        client._auto_reconnect = True

        mock_task = MagicMock()
        mock_task.done.return_value = False
        client._reconnect_task = mock_task

        await client.disconnect()

        mock_task.cancel.assert_called_once()
        assert client._reconnect_task is None

    async def test_disconnect_stop_notify_error_swallowed(self, mock_bleak_client):
        """Error during stop_notify is swallowed (lines 405-407)."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        client._client = mock_bleak_client
        client._connected = True
        mock_bleak_client.stop_notify = AsyncMock(side_effect=BleakError("stop err"))

        await client.disconnect()
        # Should not raise
        mock_bleak_client.disconnect.assert_awaited_once()

    async def test_disconnect_error_swallowed(self, mock_bleak_client):
        """Error during disconnect is swallowed (lines 409-410)."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        client._client = mock_bleak_client
        client._connected = True
        mock_bleak_client.disconnect = AsyncMock(side_effect=BleakError("disc err"))

        # Should not raise
        await client.disconnect()


# ---------------------------------------------------------------------------
# poll_loop error handling (lines 430-431)
# ---------------------------------------------------------------------------

class TestPollLoop:
    """Test polling loop error handling."""

    async def test_poll_loop_bleak_error_continues(self):
        """BleakError during poll does not break the loop (lines 430-431)."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        client._connected = True
        client._client = MagicMock(is_connected=True)
        client._protocol = MagicMock()

        call_count = 0

        async def fake_read_status(write_fn):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise BleakError("poll error")
            # Stop by disconnecting
            client._connected = False
            return None

        client._protocol.read_status = fake_read_status

        with patch("asyncio.sleep", new=AsyncMock()):
            await client._poll_loop(1.0)

        assert call_count == 2


# ---------------------------------------------------------------------------
# High-level API
# ---------------------------------------------------------------------------

class TestHighLevelAPI:
    """Test high-level operations (brew, cancel, etc.)."""

    async def test_brew_recipe_not_connected(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        result = await client.brew_recipe(RecipeId.ESPRESSO)
        assert result is False

    async def test_brew_recipe_not_ready(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        client._connected = True
        client._client = MagicMock(is_connected=True)
        client._status = MachineStatus(process=MachineProcess.PRODUCT)
        client._protocol.read_status = AsyncMock(return_value=None)

        result = await client.brew_recipe(RecipeId.ESPRESSO)
        assert result is False

    async def test_brew_recipe_success(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        client._connected = True
        client._client = MagicMock(is_connected=True)
        client._status = MachineStatus(process=MachineProcess.READY)

        mock_recipe = MachineRecipe(
            recipe_id=200, recipe_type=0,
            component1=RecipeComponent(), component2=RecipeComponent(),
        )
        client._protocol.read_recipe = AsyncMock(return_value=mock_recipe)
        client._protocol.write_recipe = AsyncMock(return_value=True)
        client._protocol.write_alphanumeric = AsyncMock(return_value=True)
        client._protocol.start_process = AsyncMock(return_value=True)
        client._protocol.read_status = AsyncMock(return_value=None)

        result = await client.brew_recipe(RecipeId.ESPRESSO)
        assert result is True
        client._protocol.read_recipe.assert_awaited_once()
        client._protocol.start_process.assert_awaited_once()

    async def test_brew_recipe_read_fails(self):
        """brew_recipe returns False when read_recipe returns None."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        client._connected = True
        client._client = MagicMock(is_connected=True)
        client._status = MachineStatus(process=MachineProcess.READY)

        client._protocol.read_recipe = AsyncMock(return_value=None)
        client._protocol.read_status = AsyncMock(return_value=None)

        result = await client.brew_recipe(RecipeId.ESPRESSO)
        assert result is False

    async def test_brew_recipe_write_recipe_fails(self):
        """brew_recipe returns False when write_recipe fails."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        client._connected = True
        client._client = MagicMock(is_connected=True)
        client._status = MachineStatus(process=MachineProcess.READY)

        mock_recipe = MachineRecipe(
            recipe_id=200, recipe_type=0,
            component1=RecipeComponent(), component2=RecipeComponent(),
        )
        client._protocol.read_recipe = AsyncMock(return_value=mock_recipe)
        client._protocol.write_recipe = AsyncMock(return_value=False)
        client._protocol.read_status = AsyncMock(return_value=None)

        result = await client.brew_recipe(RecipeId.ESPRESSO)
        assert result is False

    async def test_brew_recipe_write_name_fails(self):
        """brew_recipe returns False when write_alphanumeric fails."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        client._connected = True
        client._client = MagicMock(is_connected=True)
        client._status = MachineStatus(process=MachineProcess.READY)

        mock_recipe = MachineRecipe(
            recipe_id=200, recipe_type=0,
            component1=RecipeComponent(), component2=RecipeComponent(),
        )
        client._protocol.read_recipe = AsyncMock(return_value=mock_recipe)
        client._protocol.write_recipe = AsyncMock(return_value=True)
        client._protocol.write_alphanumeric = AsyncMock(return_value=False)
        client._protocol.read_status = AsyncMock(return_value=None)

        result = await client.brew_recipe(RecipeId.ESPRESSO)
        assert result is False

    async def test_brew_recipe_with_active_profile(self):
        """brew_recipe uses DirectKey for active_profile > 0."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        client._connected = True
        client._client = MagicMock(is_connected=True)
        client._status = MachineStatus(process=MachineProcess.READY)
        client.active_profile = 1

        mock_recipe = MachineRecipe(
            recipe_id=200, recipe_type=0,
            component1=RecipeComponent(), component2=RecipeComponent(),
        )
        client._protocol.read_recipe = AsyncMock(return_value=mock_recipe)
        client._protocol.write_recipe = AsyncMock(return_value=True)
        client._protocol.write_alphanumeric = AsyncMock(return_value=True)
        client._protocol.start_process = AsyncMock(return_value=True)
        client._protocol.read_status = AsyncMock(return_value=None)

        result = await client.brew_recipe(RecipeId.ESPRESSO)
        assert result is True

    async def test_brew_recipe_double_brew_rejected(self):
        """Second brew is rejected while first is in progress."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        client._connected = True
        client._client = MagicMock(is_connected=True)
        client._status = MachineStatus(process=MachineProcess.READY)
        client._protocol.read_status = AsyncMock(return_value=None)

        # Simulate locked brew
        await client._brew_lock.acquire()
        result = await client.brew_recipe(RecipeId.ESPRESSO)
        assert result is False
        client._brew_lock.release()

    async def test_cancel_not_connected(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        result = await client.cancel_process()
        assert result is False

    async def test_cancel_process_when_connected(self, mock_bleak_client):
        """cancel_process delegates to protocol when connected (line 533)."""
        client = _make_connected_client(mock_bleak_client)
        client._protocol.cancel_process = AsyncMock(return_value=True)

        result = await client.cancel_process()
        assert result is True

    async def test_cancel_brewing(self, mock_bleak_client):
        """cancel_brewing calls cancel_process with PRODUCT (line 536)."""
        client = _make_connected_client(mock_bleak_client)
        client._protocol.cancel_process = AsyncMock(return_value=True)

        result = await client.cancel_brewing()
        assert result is True

    async def test_read_setting_not_connected(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        result = await client.read_setting(11)
        assert result is None

    async def test_write_setting_not_connected(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        result = await client.write_setting(11, 3)
        assert result is False

    async def test_read_alpha_not_connected(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        result = await client.read_alpha(310)
        assert result is None

    async def test_poll_status_not_connected(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        result = await client.poll_status()
        assert result is None


# ---------------------------------------------------------------------------
# Maintenance operations (lines 540-562)
# ---------------------------------------------------------------------------

class TestMaintenanceOperations:
    """Test maintenance operations when connected."""

    async def test_start_easy_clean_connected(self, mock_bleak_client):
        """start_easy_clean delegates to protocol (lines 541-543)."""
        client = _make_connected_client(mock_bleak_client)
        client._protocol.start_process = AsyncMock(return_value=True)
        result = await client.start_easy_clean()
        assert result is True

    async def test_start_easy_clean_not_connected(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        assert await client.start_easy_clean() is False

    async def test_start_intensive_clean_connected(self, mock_bleak_client):
        """start_intensive_clean delegates to protocol (lines 547-549)."""
        client = _make_connected_client(mock_bleak_client)
        client._protocol.start_process = AsyncMock(return_value=True)
        result = await client.start_intensive_clean()
        assert result is True

    async def test_start_intensive_clean_not_connected(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        assert await client.start_intensive_clean() is False

    async def test_start_descaling_connected(self, mock_bleak_client):
        """start_descaling delegates to protocol (lines 553-555)."""
        client = _make_connected_client(mock_bleak_client)
        client._protocol.start_process = AsyncMock(return_value=True)
        result = await client.start_descaling()
        assert result is True

    async def test_start_descaling_not_connected(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        assert await client.start_descaling() is False

    async def test_switch_off_connected(self, mock_bleak_client):
        """switch_off delegates to protocol (lines 559-561)."""
        client = _make_connected_client(mock_bleak_client)
        client._protocol.start_process = AsyncMock(return_value=True)
        result = await client.switch_off()
        assert result is True

    async def test_switch_off_not_connected(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        assert await client.switch_off() is False


# ---------------------------------------------------------------------------
# Recipe / Settings / Alpha connected paths (lines 567-624)
# ---------------------------------------------------------------------------

class TestConnectedOperations:
    """Test read/write operations when connected."""

    async def test_read_recipe_connected(self, mock_bleak_client):
        """read_recipe delegates to protocol when connected (lines 567-569)."""
        client = _make_connected_client(mock_bleak_client)
        mock_recipe = MagicMock()
        client._protocol.read_recipe = AsyncMock(return_value=mock_recipe)
        result = await client.read_recipe(200)
        assert result is mock_recipe

    async def test_read_recipe_not_connected(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        assert await client.read_recipe(200) is None

    async def test_read_setting_connected(self, mock_bleak_client):
        """read_setting delegates to protocol when connected (lines 574-576)."""
        client = _make_connected_client(mock_bleak_client)
        client._protocol.read_numerical = AsyncMock(return_value=42)
        result = await client.read_setting(11)
        assert result == 42

    async def test_write_setting_connected(self, mock_bleak_client):
        """write_setting delegates to protocol when connected (line 581)."""
        client = _make_connected_client(mock_bleak_client)
        client._protocol.write_numerical = AsyncMock(return_value=True)
        result = await client.write_setting(11, 3)
        assert result is True

    async def test_read_alpha_connected(self, mock_bleak_client):
        """read_alpha delegates to protocol when connected (line 619)."""
        client = _make_connected_client(mock_bleak_client)
        client._protocol.read_alphanumeric = AsyncMock(return_value="test")
        result = await client.read_alpha(310)
        assert result == "test"

    async def test_write_alpha_connected(self, mock_bleak_client):
        """write_alpha delegates to protocol when connected (lines 622-624)."""
        client = _make_connected_client(mock_bleak_client)
        client._protocol.write_alphanumeric = AsyncMock(return_value=True)
        result = await client.write_alpha(310, "hello")
        assert result is True

    async def test_write_alpha_not_connected(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        assert await client.write_alpha(310, "hello") is False


# ---------------------------------------------------------------------------
# brew_freestyle (lines 493-528)
# ---------------------------------------------------------------------------

class TestBrewFreestyle:
    """Test freestyle brew operations."""

    async def test_brew_freestyle_not_connected(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        result = await client.brew_freestyle(
            "Custom", 0, RecipeComponent(), RecipeComponent(),
        )
        assert result is False

    async def test_brew_freestyle_not_ready(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        client._connected = True
        client._client = MagicMock(is_connected=True)
        client._status = MachineStatus(process=MachineProcess.PRODUCT)
        client._protocol.read_status = AsyncMock(return_value=None)

        result = await client.brew_freestyle(
            "Custom", 0, RecipeComponent(), RecipeComponent(),
        )
        assert result is False

    async def test_brew_freestyle_success(self, mock_bleak_client):
        """Full freestyle brew flow."""
        client = _make_connected_client(mock_bleak_client)
        client._status = MachineStatus(process=MachineProcess.READY)
        client._protocol.write_recipe = AsyncMock(return_value=True)
        client._protocol.write_alphanumeric = AsyncMock(return_value=True)
        client._protocol.start_process = AsyncMock(return_value=True)

        result = await client.brew_freestyle(
            "Custom", 0, RecipeComponent(), RecipeComponent(),
        )
        assert result is True

    async def test_brew_freestyle_write_recipe_fails(self, mock_bleak_client):
        """Returns False when write_recipe fails (lines 513-514)."""
        client = _make_connected_client(mock_bleak_client)
        client._status = MachineStatus(process=MachineProcess.READY)
        client._protocol.write_recipe = AsyncMock(return_value=False)

        result = await client.brew_freestyle(
            "Custom", 0, RecipeComponent(), RecipeComponent(),
        )
        assert result is False

    async def test_brew_freestyle_write_name_fails(self, mock_bleak_client):
        """Returns False when write_alphanumeric fails (lines 521-522)."""
        client = _make_connected_client(mock_bleak_client)
        client._status = MachineStatus(process=MachineProcess.READY)
        client._protocol.write_recipe = AsyncMock(return_value=True)
        client._protocol.write_alphanumeric = AsyncMock(return_value=False)

        result = await client.brew_freestyle(
            "Custom", 0, RecipeComponent(), RecipeComponent(),
        )
        assert result is False


# ---------------------------------------------------------------------------
# read_cup_counters (lines 585-614)
# ---------------------------------------------------------------------------

class TestReadCupCounters:
    """Test cup counter reading."""

    async def test_read_cup_counters_not_connected(self):
        """Returns False when not connected (line 588)."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        result = await client.read_cup_counters()
        assert result is False

    async def test_read_cup_counters_success(self, mock_bleak_client):
        """Reads all counters and total (lines 589-614)."""
        client = _make_connected_client(mock_bleak_client)
        client._protocol.read_numerical = AsyncMock(return_value=5)

        cb = MagicMock()
        client.add_cups_callback(cb)

        result = await client.read_cup_counters()

        assert result is True
        assert client._total_cups == 5
        assert len(client._cup_counters) > 0
        cb.assert_called_once()

    async def test_read_cup_counters_partial_error(self, mock_bleak_client):
        """BleakError on individual counter is swallowed (lines 597-599)."""
        client = _make_connected_client(mock_bleak_client)

        call_count = 0

        async def read_with_errors(write_fn, setting_id):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise BleakError("read failed")
            return 10

        client._protocol.read_numerical = read_with_errors

        result = await client.read_cup_counters()
        assert result is True
        # Some counters should be present, some skipped
        assert client._total_cups == 10

    async def test_read_cup_counters_total_error(self, mock_bleak_client):
        """BleakError on total cups is swallowed, total set to None (lines 604-605)."""
        client = _make_connected_client(mock_bleak_client)

        from custom_components.melitta_barista.const import TOTAL_CUPS_ID

        async def read_numerical(write_fn, setting_id):
            if setting_id == TOTAL_CUPS_ID:
                raise BleakError("total read failed")
            return 3

        client._protocol.read_numerical = read_numerical

        result = await client.read_cup_counters()
        assert result is True
        assert client._total_cups is None
        assert len(client._cup_counters) > 0

    async def test_read_cup_counters_callback_error_isolated(self, mock_bleak_client):
        """Exception in cups callback is isolated (lines 610-613)."""
        client = _make_connected_client(mock_bleak_client)
        client._protocol.read_numerical = AsyncMock(return_value=1)

        bad_cb = MagicMock(side_effect=RuntimeError("boom"))
        good_cb = MagicMock()
        client.add_cups_callback(bad_cb)
        client.add_cups_callback(good_cb)

        result = await client.read_cup_counters()
        assert result is True
        good_cb.assert_called_once()


# ---------------------------------------------------------------------------
# discover_melitta_devices (lines 629-646)
# ---------------------------------------------------------------------------

class TestDiscoverMelittaDevices:
    """Test BLE device discovery."""

    async def test_discover_by_service_uuid(self):
        """Discovers devices by Melitta service UUID (lines 634-635)."""
        device = BLEDevice("AA:BB:CC:DD:EE:FF", "8601ABCD", {})
        adv_data = MagicMock()
        adv_data.service_uuids = [MELITTA_SERVICE_UUID]

        captured_callback = None

        def scanner_init(**kwargs):
            nonlocal captured_callback
            captured_callback = kwargs.get("detection_callback")
            scanner = MagicMock()
            scanner.start = AsyncMock()
            scanner.stop = AsyncMock()
            return scanner

        async def fake_sleep(timeout):
            # Simulate device discovery during scan
            if captured_callback:
                captured_callback(device, adv_data)

        with (
            patch(
                "custom_components.melitta_barista.ble_client.BleakScanner",
                side_effect=scanner_init,
            ),
            patch("asyncio.sleep", side_effect=fake_sleep),
        ):
            result = await discover_melitta_devices(timeout=1.0)

        assert len(result) == 1
        assert result[0].address == "AA:BB:CC:DD:EE:FF"

    async def test_discover_by_name_prefix(self):
        """Discovers devices by BLE name prefix (lines 636-639)."""
        device = BLEDevice("11:22:33:44:55:66", "8601ABCD1234", {})
        adv_data = MagicMock()
        adv_data.service_uuids = []

        captured_callback = None

        def scanner_init(**kwargs):
            nonlocal captured_callback
            captured_callback = kwargs.get("detection_callback")
            scanner = MagicMock()
            scanner.start = AsyncMock()
            scanner.stop = AsyncMock()
            return scanner

        async def fake_sleep(timeout):
            if captured_callback:
                captured_callback(device, adv_data)

        with (
            patch(
                "custom_components.melitta_barista.ble_client.BleakScanner",
                side_effect=scanner_init,
            ),
            patch("asyncio.sleep", side_effect=fake_sleep),
        ):
            result = await discover_melitta_devices(timeout=1.0)

        assert len(result) == 1

    async def test_discover_ignores_unknown_devices(self):
        """Ignores devices without matching UUID or name (lines 632-639)."""
        device = BLEDevice("11:22:33:44:55:66", "UnknownDevice", {})
        adv_data = MagicMock()
        adv_data.service_uuids = ["00001234-0000-1000-8000-00805f9b34fb"]

        captured_callback = None

        def scanner_init(**kwargs):
            nonlocal captured_callback
            captured_callback = kwargs.get("detection_callback")
            scanner = MagicMock()
            scanner.start = AsyncMock()
            scanner.stop = AsyncMock()
            return scanner

        async def fake_sleep(timeout):
            if captured_callback:
                captured_callback(device, adv_data)

        with (
            patch(
                "custom_components.melitta_barista.ble_client.BleakScanner",
                side_effect=scanner_init,
            ),
            patch("asyncio.sleep", side_effect=fake_sleep),
        ):
            result = await discover_melitta_devices(timeout=1.0)

        assert len(result) == 0

    async def test_discover_deduplicates_by_address(self):
        """Same address is not added twice (lines 632-633)."""
        device = BLEDevice("AA:BB:CC:DD:EE:FF", "8601ABCD", {})
        adv_data = MagicMock()
        adv_data.service_uuids = [MELITTA_SERVICE_UUID]

        captured_callback = None

        def scanner_init(**kwargs):
            nonlocal captured_callback
            captured_callback = kwargs.get("detection_callback")
            scanner = MagicMock()
            scanner.start = AsyncMock()
            scanner.stop = AsyncMock()
            return scanner

        async def fake_sleep(timeout):
            if captured_callback:
                captured_callback(device, adv_data)
                captured_callback(device, adv_data)  # duplicate

        with (
            patch(
                "custom_components.melitta_barista.ble_client.BleakScanner",
                side_effect=scanner_init,
            ),
            patch("asyncio.sleep", side_effect=fake_sleep),
        ):
            result = await discover_melitta_devices(timeout=1.0)

        assert len(result) == 1


# ---------------------------------------------------------------------------
# Polling
# ---------------------------------------------------------------------------

class TestPolling:
    """Test polling lifecycle."""

    async def test_start_polling_creates_task(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        client._connected = True
        client._client = MagicMock(is_connected=True)
        client.start_polling(interval=1.0)
        assert client._poll_task is not None
        client._stop_polling()
        assert client._poll_task is None

    async def test_stop_polling_cancels_task(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        client._connected = True
        client._client = MagicMock(is_connected=True)
        client._protocol = MagicMock()
        client._protocol.read_status = AsyncMock(return_value=None)
        client.start_polling(interval=0.01)
        await asyncio.sleep(0.05)
        client._stop_polling()
        assert client._poll_task is None


# ---------------------------------------------------------------------------
# Profile management
# ---------------------------------------------------------------------------

class TestProfileManagement:
    """Tests for profile name CRUD operations."""

    @pytest.fixture()
    def client(self):
        mock_bleak = MagicMock(is_connected=True)
        return _make_connected_client(mock_bleak)

    @pytest.mark.asyncio
    async def test_read_profile_name_zero_returns_my_coffee(self, client):
        result = await client.read_profile_name(0)
        assert result == PROFILE_NAMES[0]
        # Should NOT call protocol
        client._protocol.read_alphanumeric.assert_not_called()

    @pytest.mark.asyncio
    async def test_read_profile_name_valid(self, client):
        client._protocol.read_alphanumeric = AsyncMock(return_value="Alice")
        result = await client.read_profile_name(1)
        assert result == "Alice"
        client._protocol.read_alphanumeric.assert_awaited_once_with(
            client._write_ble, USER_NAME_IDS[1],
        )

    @pytest.mark.asyncio
    async def test_read_profile_name_invalid_id(self, client):
        result = await client.read_profile_name(99)
        assert result is None

    @pytest.mark.asyncio
    async def test_read_profile_name_disconnected(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        result = await client.read_profile_name(1)
        assert result is None

    @pytest.mark.asyncio
    async def test_read_all_profile_names(self, client):
        client._machine_type = MachineType.BARISTA_T  # 4 user profiles
        names = {
            USER_NAME_IDS[1]: "Alice",
            USER_NAME_IDS[2]: "Bob",
            USER_NAME_IDS[3]: None,  # empty name
            USER_NAME_IDS[4]: "Diana",
        }

        async def mock_read_alpha(write_func, value_id):
            return names.get(value_id)

        client._protocol.read_alphanumeric = AsyncMock(side_effect=mock_read_alpha)
        result = await client.read_all_profile_names()
        assert result[0] == PROFILE_NAMES[0]
        assert result[1] == "Alice"
        assert result[2] == "Bob"
        assert result[3] == "Profile 3"  # fallback for None
        assert result[4] == "Diana"

    @pytest.mark.asyncio
    async def test_read_all_profile_names_disconnected(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        result = await client.read_all_profile_names()
        assert result == {}

    @pytest.mark.asyncio
    async def test_read_all_profile_names_ble_error(self, client):
        client._machine_type = MachineType.BARISTA_T
        client._protocol.read_alphanumeric = AsyncMock(
            side_effect=BleakError("timeout"),
        )
        result = await client.read_all_profile_names()
        # Should fallback to "Profile N" for all
        assert result[0] == PROFILE_NAMES[0]
        for i in range(1, 5):
            assert result[i] == f"Profile {i}"

    @pytest.mark.asyncio
    async def test_write_profile_name_valid(self, client):
        client._protocol.write_alphanumeric = AsyncMock(return_value=True)
        result = await client.write_profile_name(2, "Bob")
        assert result is True
        client._protocol.write_alphanumeric.assert_awaited_once_with(
            client._write_ble, USER_NAME_IDS[2], "Bob",
        )

    @pytest.mark.asyncio
    async def test_write_profile_name_zero_rejected(self, client):
        result = await client.write_profile_name(0, "Nope")
        assert result is False

    @pytest.mark.asyncio
    async def test_write_profile_name_invalid_id(self, client):
        result = await client.write_profile_name(99, "Nope")
        assert result is False

    @pytest.mark.asyncio
    async def test_write_profile_name_disconnected(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        result = await client.write_profile_name(1, "Alice")
        assert result is False


# ---------------------------------------------------------------------------
# Profile recipe management
# ---------------------------------------------------------------------------

class TestProfileRecipeManagement:
    """Tests for profile recipe CRUD operations."""

    @pytest.fixture()
    def client(self):
        mock_bleak = MagicMock(is_connected=True)
        return _make_connected_client(mock_bleak)

    def _make_recipe(self, recipe_id=302, recipe_type=0):
        return MachineRecipe(
            recipe_id=recipe_id,
            recipe_type=recipe_type,
            component1=RecipeComponent(1, 1, 1, 2, 0, 1, 8, 0),
            component2=RecipeComponent(0, 0, 0, 0, 0, 0, 0, 0),
        )

    @pytest.mark.asyncio
    async def test_read_profile_recipe(self, client):
        expected = self._make_recipe()
        client._protocol.read_recipe = AsyncMock(return_value=expected)
        result = await client.read_profile_recipe(1, DirectKeyCategory.ESPRESSO)
        assert result is expected
        expected_id = get_directkey_id(1, DirectKeyCategory.ESPRESSO)
        client._protocol.read_recipe.assert_awaited_once_with(
            client._write_ble, expected_id,
        )

    @pytest.mark.asyncio
    async def test_read_profile_recipe_disconnected(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        result = await client.read_profile_recipe(1, DirectKeyCategory.ESPRESSO)
        assert result is None

    @pytest.mark.asyncio
    async def test_read_all_profile_recipes(self, client):
        recipe = self._make_recipe()
        client._protocol.read_recipe = AsyncMock(return_value=recipe)
        result = await client.read_all_profile_recipes(0)
        assert len(result) == len(DirectKeyCategory)
        for cat in DirectKeyCategory:
            assert cat in result

    @pytest.mark.asyncio
    async def test_read_all_profile_recipes_partial_failure(self, client):
        recipe = self._make_recipe()
        call_count = 0

        async def mock_read(write_func, rid):
            nonlocal call_count
            call_count += 1
            if call_count == 3:
                raise BleakError("timeout")
            return recipe

        client._protocol.read_recipe = AsyncMock(side_effect=mock_read)
        result = await client.read_all_profile_recipes(0)
        # 7 categories, 1 failed -> 6 recipes
        assert len(result) == 6

    @pytest.mark.asyncio
    async def test_read_all_profile_recipes_disconnected(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        result = await client.read_all_profile_recipes(0)
        assert result == {}

    @pytest.mark.asyncio
    async def test_write_profile_recipe(self, client):
        current = self._make_recipe(recipe_type=5)
        client._protocol.read_recipe = AsyncMock(return_value=current)
        client._protocol.write_recipe = AsyncMock(return_value=True)

        comp1 = RecipeComponent(1, 2, 1, 3, 0, 2, 10, 0)
        comp2 = RecipeComponent(0, 0, 0, 0, 0, 0, 0, 0)
        result = await client.write_profile_recipe(
            2, DirectKeyCategory.CAPPUCCINO, comp1, comp2,
        )
        assert result is True
        expected_id = get_directkey_id(2, DirectKeyCategory.CAPPUCCINO)
        # Should read current recipe first, then re-read for cache update
        assert client._protocol.read_recipe.await_count == 2
        client._protocol.read_recipe.assert_any_await(
            client._write_ble, expected_id,
        )
        # Should write with preserved recipe_type=5 and correct recipe_key=1 (COFFEE)
        client._protocol.write_recipe.assert_awaited_once_with(
            client._write_ble, expected_id, 5, comp1, comp2,
            recipe_key=1,
        )

    @pytest.mark.asyncio
    async def test_write_profile_recipe_read_fails_uses_default_type(self, client):
        """When read_recipe fails, fallback to default recipe_type for the category."""
        client._protocol.read_recipe = AsyncMock(return_value=None)
        client._protocol.write_recipe = AsyncMock(return_value=True)
        comp1 = RecipeComponent(1, 1, 1, 2, 0, 1, 8, 0)
        comp2 = RecipeComponent(0, 0, 0, 0, 0, 0, 0, 0)
        result = await client.write_profile_recipe(
            1, DirectKeyCategory.ESPRESSO, comp1, comp2,
        )
        assert result is True
        # Default recipe_type for ESPRESSO = 0, recipe_key = 0
        client._protocol.write_recipe.assert_called_once()
        call_args = client._protocol.write_recipe.call_args
        assert call_args.kwargs.get("recipe_key") == 0

    @pytest.mark.asyncio
    async def test_write_profile_recipe_disconnected(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        comp1 = RecipeComponent(1, 1, 1, 2, 0, 1, 8, 0)
        comp2 = RecipeComponent(0, 0, 0, 0, 0, 0, 0, 0)
        result = await client.write_profile_recipe(
            1, DirectKeyCategory.ESPRESSO, comp1, comp2,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_reset_profile_recipe(self, client):
        default_recipe = self._make_recipe(recipe_type=0)
        client._protocol.read_recipe = AsyncMock(return_value=default_recipe)
        client._protocol.write_recipe = AsyncMock(return_value=True)

        result = await client.reset_profile_recipe(
            3, DirectKeyCategory.LATTE_MACCHIATO,
        )
        assert result is True
        default_id = get_directkey_id(0, DirectKeyCategory.LATTE_MACCHIATO)
        target_id = get_directkey_id(3, DirectKeyCategory.LATTE_MACCHIATO)
        # Should read from profile 0
        client._protocol.read_recipe.assert_awaited_once_with(
            client._write_ble, default_id,
        )
        # Should write to target profile with recipe_key=0 (ESPRESSO for type=0)
        client._protocol.write_recipe.assert_awaited_once_with(
            client._write_ble, target_id, default_recipe.recipe_type,
            default_recipe.component1, default_recipe.component2,
            recipe_key=0,
        )

    @pytest.mark.asyncio
    async def test_reset_profile_recipe_zero_rejected(self, client):
        result = await client.reset_profile_recipe(0, DirectKeyCategory.ESPRESSO)
        assert result is False

    @pytest.mark.asyncio
    async def test_reset_profile_recipe_default_read_fails(self, client):
        client._protocol.read_recipe = AsyncMock(return_value=None)
        result = await client.reset_profile_recipe(
            1, DirectKeyCategory.ESPRESSO,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_reset_profile_recipe_disconnected(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        result = await client.reset_profile_recipe(
            1, DirectKeyCategory.ESPRESSO,
        )
        assert result is False


# ---------------------------------------------------------------------------
# Profile activity
# ---------------------------------------------------------------------------

class TestProfileActivity:
    """Tests for profile activity read/write."""

    @pytest.fixture()
    def client(self):
        mock_bleak = MagicMock(is_connected=True)
        return _make_connected_client(mock_bleak)

    @pytest.mark.asyncio
    async def test_read_activity_valid(self, client):
        client._protocol.read_numerical = AsyncMock(return_value=1)
        result = await client.read_profile_activity(1)
        assert result == 1
        client._protocol.read_numerical.assert_awaited_once_with(
            client._write_ble, USER_ACTIVITY_IDS[1],
        )

    @pytest.mark.asyncio
    async def test_read_activity_profile_zero(self, client):
        result = await client.read_profile_activity(0)
        assert result is None

    @pytest.mark.asyncio
    async def test_read_activity_invalid_id(self, client):
        result = await client.read_profile_activity(99)
        assert result is None

    @pytest.mark.asyncio
    async def test_read_activity_disconnected(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        result = await client.read_profile_activity(1)
        assert result is None

    @pytest.mark.asyncio
    async def test_write_activity_valid(self, client):
        client._protocol.write_numerical = AsyncMock(return_value=True)
        result = await client.write_profile_activity(2, 1)
        assert result is True
        client._protocol.write_numerical.assert_awaited_once_with(
            client._write_ble, USER_ACTIVITY_IDS[2], 1,
        )

    @pytest.mark.asyncio
    async def test_write_activity_profile_zero(self, client):
        result = await client.write_profile_activity(0, 1)
        assert result is False

    @pytest.mark.asyncio
    async def test_write_activity_invalid_id(self, client):
        result = await client.write_profile_activity(99, 1)
        assert result is False

    @pytest.mark.asyncio
    async def test_write_activity_disconnected(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        result = await client.write_profile_activity(1, 1)
        assert result is False


# ---------------------------------------------------------------------------
# Update profile recipe (partial)
# ---------------------------------------------------------------------------

class TestUpdateProfileRecipe:
    """Tests for partial update of profile recipe parameters."""

    @pytest.fixture()
    def client(self):
        mock_bleak = MagicMock(is_connected=True)
        return _make_connected_client(mock_bleak)

    def _make_recipe(self, recipe_type=0):
        return MachineRecipe(
            recipe_id=302,
            recipe_type=recipe_type,
            component1=RecipeComponent(1, 1, 1, 2, 0, 1, 8, 0),
            component2=RecipeComponent(0, 0, 0, 0, 0, 0, 0, 0),
        )

    @pytest.mark.asyncio
    async def test_update_intensity_only(self, client):
        current = self._make_recipe(recipe_type=5)
        client._protocol.read_recipe = AsyncMock(return_value=current)
        client._protocol.write_recipe = AsyncMock(return_value=True)

        result = await client.update_profile_recipe(
            1, DirectKeyCategory.ESPRESSO, intensity=4,
        )
        assert result is True
        # Verify the written component has intensity=4, rest unchanged
        call_args = client._protocol.write_recipe.call_args
        written_comp1 = call_args[0][3]  # comp1 (4th positional arg)
        assert written_comp1.intensity == 4
        assert written_comp1.process == 1  # unchanged
        assert written_comp1.shots == 1    # unchanged
        assert written_comp1.portion == 8  # unchanged

    @pytest.mark.asyncio
    async def test_update_portion_ml(self, client):
        current = self._make_recipe()
        client._protocol.read_recipe = AsyncMock(return_value=current)
        client._protocol.write_recipe = AsyncMock(return_value=True)

        result = await client.update_profile_recipe(
            1, DirectKeyCategory.ESPRESSO, portion_ml=100,
        )
        assert result is True
        call_args = client._protocol.write_recipe.call_args
        written_comp1 = call_args[0][3]  # comp1 (4th positional arg)
        assert written_comp1.portion == 20  # 100 // 5

    @pytest.mark.asyncio
    async def test_update_multiple_params(self, client):
        current = self._make_recipe()
        client._protocol.read_recipe = AsyncMock(return_value=current)
        client._protocol.write_recipe = AsyncMock(return_value=True)

        result = await client.update_profile_recipe(
            2, DirectKeyCategory.CAPPUCCINO,
            intensity=3, temperature=2, shots=2,
        )
        assert result is True
        call_args = client._protocol.write_recipe.call_args
        written_comp1 = call_args[0][3]  # comp1 (4th positional arg)
        assert written_comp1.intensity == 3
        assert written_comp1.temperature == 2
        assert written_comp1.shots == 2
        assert written_comp1.process == 1  # unchanged

    @pytest.mark.asyncio
    async def test_update_preserves_component2(self, client):
        comp2 = RecipeComponent(2, 0, 0, 1, 0, 1, 6, 0)
        current = MachineRecipe(302, 5,
            RecipeComponent(1, 1, 1, 2, 0, 1, 8, 0), comp2)
        client._protocol.read_recipe = AsyncMock(return_value=current)
        client._protocol.write_recipe = AsyncMock(return_value=True)

        await client.update_profile_recipe(
            1, DirectKeyCategory.CAPPUCCINO, intensity=4,
        )
        call_args = client._protocol.write_recipe.call_args
        written_comp2 = call_args[0][4]  # comp2 (5th positional arg)
        assert written_comp2 is comp2

    @pytest.mark.asyncio
    async def test_update_read_fails(self, client):
        client._protocol.read_recipe = AsyncMock(return_value=None)
        result = await client.update_profile_recipe(
            1, DirectKeyCategory.ESPRESSO, intensity=4,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_update_disconnected(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        result = await client.update_profile_recipe(
            1, DirectKeyCategory.ESPRESSO, intensity=4,
        )
        assert result is False


# ---------------------------------------------------------------------------
# Copy and reset all profile recipes
# ---------------------------------------------------------------------------

class TestCopyAndResetAllRecipes:
    """Tests for copy_profile_recipe and reset_all_profile_recipes."""

    @pytest.fixture()
    def client(self):
        mock_bleak = MagicMock(is_connected=True)
        return _make_connected_client(mock_bleak)

    def _make_recipe(self, recipe_id=302, recipe_type=0):
        return MachineRecipe(
            recipe_id=recipe_id,
            recipe_type=recipe_type,
            component1=RecipeComponent(1, 1, 1, 2, 0, 1, 8, 0),
            component2=RecipeComponent(0, 0, 0, 0, 0, 0, 0, 0),
        )

    @pytest.mark.asyncio
    async def test_copy_recipe_between_profiles(self, client):
        source = self._make_recipe(recipe_type=5)
        client._protocol.read_recipe = AsyncMock(return_value=source)
        client._protocol.write_recipe = AsyncMock(return_value=True)

        result = await client.copy_profile_recipe(
            1, 3, DirectKeyCategory.ESPRESSO,
        )
        assert result is True
        source_id = get_directkey_id(1, DirectKeyCategory.ESPRESSO)
        target_id = get_directkey_id(3, DirectKeyCategory.ESPRESSO)
        client._protocol.read_recipe.assert_awaited_once_with(
            client._write_ble, source_id,
        )
        client._protocol.write_recipe.assert_awaited_once_with(
            client._write_ble, target_id, 5,
            source.component1, source.component2,
            recipe_key=1,
        )

    @pytest.mark.asyncio
    async def test_copy_recipe_source_fails(self, client):
        client._protocol.read_recipe = AsyncMock(return_value=None)
        result = await client.copy_profile_recipe(
            1, 3, DirectKeyCategory.ESPRESSO,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_copy_recipe_disconnected(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        result = await client.copy_profile_recipe(
            1, 3, DirectKeyCategory.ESPRESSO,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_reset_all_recipes(self, client):
        default = self._make_recipe()
        client._protocol.read_recipe = AsyncMock(return_value=default)
        client._protocol.write_recipe = AsyncMock(return_value=True)

        result = await client.reset_all_profile_recipes(2)
        assert result is True
        # 7 categories = 7 reads + 7 writes
        assert client._protocol.read_recipe.await_count == 7
        assert client._protocol.write_recipe.await_count == 7

    @pytest.mark.asyncio
    async def test_reset_all_recipes_profile_zero(self, client):
        result = await client.reset_all_profile_recipes(0)
        assert result is False

    @pytest.mark.asyncio
    async def test_reset_all_recipes_partial_failure(self, client):
        default = self._make_recipe()
        call_count = 0

        async def mock_read(write_func, rid):
            nonlocal call_count
            call_count += 1
            if call_count == 5:
                raise BleakError("timeout")
            return default

        client._protocol.read_recipe = AsyncMock(side_effect=mock_read)
        client._protocol.write_recipe = AsyncMock(return_value=True)

        result = await client.reset_all_profile_recipes(3)
        assert result is False  # partial failure => False

    @pytest.mark.asyncio
    async def test_reset_all_recipes_disconnected(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        result = await client.reset_all_profile_recipes(1)
        assert result is False
