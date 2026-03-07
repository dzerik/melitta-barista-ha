"""Tests for MelittaBleClient — connect, disconnect, reconnect, brew."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError

from custom_components.melitta_barista.ble_client import MelittaBleClient
from custom_components.melitta_barista.const import (
    MachineProcess,
    MachineType,
    RecipeId,
)
from custom_components.melitta_barista.protocol import MachineRecipe, MachineStatus, RecipeComponent


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


class TestClientBLEDevice:
    """Test BLEDevice management."""

    def test_set_ble_device(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        device = BLEDevice("AA:BB:CC:DD:EE:FF", "test", {})
        client.set_ble_device(device)
        assert client._ble_device is device


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


class TestConnect:
    """Test connection flow."""

    @pytest.mark.asyncio
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
        ):
            result = await client._connect_impl()

        assert result is True
        assert client.connected is True
        assert client.firmware_version == "1.2.3"
        assert client.machine_type == MachineType.BARISTA_TS

    @pytest.mark.asyncio
    async def test_connect_handshake_fails(self, mock_bleak_client):
        """Test connection failure when handshake fails."""
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

    @pytest.mark.asyncio
    async def test_connect_exception(self, mock_bleak_client):
        """Test connection failure on exception."""
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")

        with patch.object(
            client, "_establish_connection",
            new=AsyncMock(side_effect=BleakError("Connection failed")),
        ):
            result = await client._connect_impl()

        assert result is False
        assert client.connected is False


class TestDisconnect:
    """Test disconnection."""

    @pytest.mark.asyncio
    async def test_disconnect_stops_polling(self, mock_bleak_client):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        client._client = mock_bleak_client
        client._connected = True
        client._auto_reconnect = True

        await client.disconnect()

        assert client._auto_reconnect is False
        assert client.connected is False
        mock_bleak_client.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disconnect_when_not_connected(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        await client.disconnect()
        assert client.connected is False


class TestHighLevelAPI:
    """Test high-level operations (brew, cancel, etc.)."""

    @pytest.mark.asyncio
    async def test_brew_recipe_not_connected(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        result = await client.brew_recipe(RecipeId.ESPRESSO)
        assert result is False

    @pytest.mark.asyncio
    async def test_brew_recipe_not_ready(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        client._connected = True
        client._client = MagicMock(is_connected=True)
        client._status = MachineStatus(process=MachineProcess.PRODUCT)

        result = await client.brew_recipe(RecipeId.ESPRESSO)
        assert result is False

    @pytest.mark.asyncio
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

        result = await client.brew_recipe(RecipeId.ESPRESSO)
        assert result is True
        client._protocol.read_recipe.assert_awaited_once()
        client._protocol.start_process.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancel_not_connected(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        result = await client.cancel_process()
        assert result is False

    @pytest.mark.asyncio
    async def test_read_setting_not_connected(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        result = await client.read_setting(11)
        assert result is None

    @pytest.mark.asyncio
    async def test_write_setting_not_connected(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        result = await client.write_setting(11, 3)
        assert result is False

    @pytest.mark.asyncio
    async def test_read_alpha_not_connected(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        result = await client.read_alpha(310)
        assert result is None

    @pytest.mark.asyncio
    async def test_poll_status_not_connected(self):
        client = MelittaBleClient("AA:BB:CC:DD:EE:FF")
        result = await client.poll_status()
        assert result is None


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
        client.start_polling(interval=0.01)
        await asyncio.sleep(0.05)
        client._stop_polling()
        assert client._poll_task is None
