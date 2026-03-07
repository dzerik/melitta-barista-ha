"""BLE client for Melitta Barista Smart coffee machine.

Architecture follows the switchbot/led_ble pattern:
- Store and update BLEDevice reference from HA bluetooth advertisements
- Use BleakClientWithServiceCache + establish_connection() for reliable connections
- ble_device_callback provides fresh device reference on each retry
- Force StartNotify to avoid bleak 2.0 AcquireNotify issues
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Callable

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.exc import BleakDBusError, BleakError

from .const import (
    BLE_PREFIXES_ALL,
    CHAR_NOTIFY,
    CHAR_WRITE,
    MACHINE_MODEL_NAMES,
    MACHINE_TYPE_SETTING_ID,
    MachineProcess,
    MachineType,
    RecipeId,
    detect_machine_type_from_name,
)
from .protocol import MachineRecipe, MachineStatus, MelittaProtocol

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger("melitta_barista")

# Melitta service UUID for BLE discovery
MELITTA_SERVICE_UUID = "0000ad00-b35c-11e4-9813-0002a5d5c51b"


class MelittaBleClient:
    """BLE client managing connection and communication with the machine.

    Follows the HA BLE integration pattern (switchbot/led_ble):
    - BLEDevice reference is updated from advertisement callbacks
    - Connection uses establish_connection() with ble_device_callback
    - Persistent connection with notification subscription for HX status
    """

    def __init__(
        self,
        address: str,
        device_name: str | None = None,
        ble_device: BLEDevice | None = None,
    ) -> None:
        self._address = address
        self._device_name = device_name
        self._ble_device: BLEDevice | None = ble_device
        self._client: BleakClient | None = None
        self._protocol = MelittaProtocol()
        self._connected = False
        self._connect_lock = asyncio.Lock()
        self._status: MachineStatus | None = None
        self._firmware: str | None = None
        self._machine_type: MachineType | None = None
        self._status_callbacks: list[Callable[[MachineStatus], None]] = []
        self._connection_callbacks: list[Callable[[bool], None]] = []
        self._poll_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._auto_reconnect = True
        self.selected_recipe: RecipeId | None = None

        # Pre-detect machine type from BLE device name if available
        if device_name:
            self._machine_type = detect_machine_type_from_name(device_name)

    @property
    def address(self) -> str:
        return self._address

    @property
    def connected(self) -> bool:
        return self._connected and self._client is not None and self._client.is_connected

    @property
    def status(self) -> MachineStatus | None:
        return self._status

    @property
    def firmware_version(self) -> str | None:
        return self._firmware

    @property
    def machine_type(self) -> MachineType | None:
        return self._machine_type

    @property
    def model_name(self) -> str:
        if self._machine_type:
            return MACHINE_MODEL_NAMES.get(self._machine_type, "Melitta Barista")
        return "Melitta Barista"

    def set_ble_device(self, ble_device: BLEDevice) -> None:
        """Update BLEDevice reference from advertisement callback.

        Called by __init__.py when HA sees a new advertisement from the device.
        This keeps the BLEDevice fresh for establish_connection() retries.
        """
        self._ble_device = ble_device

    def add_status_callback(self, callback: Callable[[MachineStatus], None]) -> None:
        self._status_callbacks.append(callback)

    def add_connection_callback(self, callback: Callable[[bool], None]) -> None:
        self._connection_callbacks.append(callback)

    def _on_status(self, status: MachineStatus) -> None:
        self._status = status
        for cb in self._status_callbacks:
            try:
                cb(status)
            except Exception:
                _LOGGER.exception("Error in status callback")

    def _on_disconnect(self, client: BleakClient) -> None:
        _LOGGER.info("Disconnected from %s", self._address)
        self._connected = False
        self._client = None
        for cb in self._connection_callbacks:
            try:
                cb(False)
            except Exception:
                _LOGGER.exception("Error in connection callback")
        if self._auto_reconnect:
            self._schedule_reconnect()

    def _on_notification(self, _sender: int, data: bytearray) -> None:
        self._protocol.on_ble_data(bytes(data))

    async def _write_ble(self, data: bytes) -> None:
        if not self._client or not self._client.is_connected:
            raise BleakError("Not connected")
        try:
            await self._client.write_gatt_char(CHAR_WRITE, data, response=False)
        except AssertionError:
            # bleak internal: assert self._bus — D-Bus connection lost
            _LOGGER.error("D-Bus connection lost during write (assert self._bus)")
            raise BleakError("D-Bus connection lost")

    async def _establish_connection(self) -> BleakClient:
        """Establish BLE connection following the switchbot/led_ble pattern.

        Uses BleakClientWithServiceCache + establish_connection() with
        ble_device_callback for fresh device reference on each retry.
        """
        if self._ble_device is not None:
            try:
                from bleak_retry_connector import (
                    BleakClientWithServiceCache,
                    establish_connection,
                )

                _LOGGER.debug(
                    "Using establish_connection for %s (ble_device=%s)",
                    self._address,
                    self._ble_device,
                )
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    self._ble_device,
                    self._device_name or self._address,
                    disconnected_callback=self._on_disconnect,
                    use_services_cache=True,
                    ble_device_callback=lambda: self._ble_device,
                    max_attempts=3,
                )
                return client
            except ImportError:
                _LOGGER.warning(
                    "bleak_retry_connector not available, using raw BleakClient"
                )
            except Exception:
                _LOGGER.debug(
                    "establish_connection failed, falling back to raw BleakClient",
                    exc_info=True,
                )

        # Fallback: raw BleakClient (e.g. outside HA or missing retry-connector)
        _LOGGER.debug("Using raw BleakClient for %s", self._address)
        client = BleakClient(
            self._ble_device or self._address,
            disconnected_callback=self._on_disconnect,
            timeout=15.0,
        )
        await client.connect()
        return client

    async def _start_notify(self, client: BleakClient) -> None:
        """Subscribe to notifications.

        Handles two issues:
        1. "Notify acquired" (stale BlueZ D-Bus state) — treat as success
        2. bleak 2.0 AcquireNotify regression — force StartNotify via bluez param
        """
        try:
            # Try with bluez StartNotify parameter (bleak >= 2.1.0)
            try:
                from bleak.args.bluez import BlueZStartNotifyArgs
                await client.start_notify(
                    CHAR_NOTIFY,
                    self._on_notification,
                    bluez=BlueZStartNotifyArgs(use_start_notify=True),
                )
                return
            except (ImportError, TypeError):
                # Older bleak — no bluez parameter support
                pass

            await client.start_notify(CHAR_NOTIFY, self._on_notification)

        except BleakDBusError as err:
            if "Notify acquired" in str(err):
                # BlueZ already has notifications active — our callback is
                # registered in bleak's Python layer before the D-Bus call,
                # so data will flow. Treat as success.
                _LOGGER.info(
                    "BlueZ reports notifications already acquired for %s — "
                    "treating as success",
                    CHAR_NOTIFY,
                )
            else:
                raise

    async def connect(self) -> bool:
        """Connect to the coffee machine.

        Flow: BLE connect -> subscribe notify -> HU handshake -> read version.
        Bonding must be done beforehand via config_flow or bluetoothctl.
        """
        async with self._connect_lock:
            return await self._connect_impl()

    async def _connect_impl(self) -> bool:
        """Internal connect implementation (must be called under _connect_lock)."""
        try:
            _LOGGER.info("Connecting to Melitta at %s", self._address)

            self._protocol = MelittaProtocol()
            self._protocol.set_status_callback(self._on_status)

            self._client = await self._establish_connection()

            if not self._client.is_connected:
                _LOGGER.error("Failed to connect to %s", self._address)
                return False

            _LOGGER.debug("BLE connected to %s", self._address)

            # Subscribe to notifications on ad02
            await self._start_notify(self._client)

            # Perform HU handshake to get key_prefix
            if not await self._protocol.perform_handshake(self._write_ble):
                _LOGGER.error("HU handshake failed")
                await self._client.disconnect()
                return False

            self._connected = True
            _LOGGER.info("Connected and handshake complete for %s", self._address)

            # Read firmware version
            self._firmware = await self._protocol.read_version(self._write_ble)
            _LOGGER.debug("Firmware: %s", self._firmware)

            # Read machine type via HR id=6 (confirms BLE name detection)
            type_id = await self._protocol.read_numerical(
                self._write_ble, MACHINE_TYPE_SETTING_ID,
            )
            if type_id is not None:
                try:
                    self._machine_type = MachineType(type_id)
                except ValueError:
                    _LOGGER.warning("Unknown machine type ID: %d", type_id)
            _LOGGER.debug("Machine type: %s", self._machine_type)

            # Notify connection callbacks
            for cb in self._connection_callbacks:
                try:
                    cb(True)
                except Exception:
                    _LOGGER.exception("Error in connection callback")

            return True

        except Exception:
            _LOGGER.exception("Connection failed for %s", self._address)
            self._connected = False
            if self._client:
                try:
                    await self._client.disconnect()
                except Exception:
                    pass
                self._client = None
            return False

    def _schedule_reconnect(self) -> None:
        """Schedule a reconnect attempt."""
        if self._reconnect_task and not self._reconnect_task.done():
            return
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        """Try to reconnect with exponential backoff."""
        delay = 5.0
        max_delay = 300.0
        while self._auto_reconnect and not self.connected:
            _LOGGER.info("Reconnecting to %s in %.0fs...", self._address, delay)
            await asyncio.sleep(delay)
            if not self._auto_reconnect:
                break
            try:
                if await self.connect():
                    _LOGGER.info("Reconnected to %s", self._address)
                    self.start_polling(interval=5.0)
                    return
            except Exception:
                _LOGGER.debug("Reconnect attempt failed", exc_info=True)
            delay = min(delay * 2, max_delay)

    async def disconnect(self) -> None:
        self._auto_reconnect = False
        self._stop_polling()
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            self._reconnect_task = None
        if self._client:
            try:
                if self._client.is_connected:
                    try:
                        await self._client.stop_notify(CHAR_NOTIFY)
                    except Exception:
                        _LOGGER.debug("stop_notify during disconnect failed", exc_info=True)
                await self._client.disconnect()
            except Exception:
                _LOGGER.debug("Error during disconnect", exc_info=True)
        self._connected = False
        self._client = None

    async def poll_status(self) -> MachineStatus | None:
        if not self.connected:
            return None
        return await self._protocol.read_status(self._write_ble)

    def start_polling(self, interval: float = 2.0) -> None:
        self._stop_polling()
        self._poll_task = asyncio.create_task(self._poll_loop(interval))

    def _stop_polling(self) -> None:
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            self._poll_task = None

    async def _poll_loop(self, interval: float) -> None:
        while self.connected:
            try:
                await self.poll_status()
            except Exception:
                _LOGGER.debug("Poll error", exc_info=True)
            await asyncio.sleep(interval)

    # High-level API

    async def brew_recipe(self, recipe_id: RecipeId) -> bool:
        """Brew a recipe using the 3-step protocol: HJ -> HB -> HE."""
        if not self.connected:
            return False
        if self._status and not self._status.is_ready:
            _LOGGER.warning("Machine not ready: %s", self._status)
            return False

        from .const import RECIPE_NAMES

        recipe = await self._protocol.read_recipe(self._write_ble, recipe_id)
        if not recipe:
            _LOGGER.error("Failed to read recipe %d", recipe_id)
            return False

        if not await self._protocol.write_recipe(
            self._write_ble, 400, recipe.recipe_type, 0,
            recipe.component1, recipe.component2,
        ):
            _LOGGER.error("Failed to write recipe to temp slot")
            return False

        await asyncio.sleep(0.2)

        name = RECIPE_NAMES.get(recipe_id, str(recipe_id))
        if not await self._protocol.write_alphanumeric(self._write_ble, 401, name):
            _LOGGER.error("Failed to write recipe name")
            return False

        await asyncio.sleep(0.2)

        return await self._protocol.start_process(
            self._write_ble, MachineProcess.PRODUCT,
        )

    async def cancel_process(self, process: MachineProcess = MachineProcess.PRODUCT) -> bool:
        if not self.connected:
            return False
        return await self._protocol.cancel_process(self._write_ble, process)

    async def cancel_brewing(self) -> bool:
        return await self.cancel_process(MachineProcess.PRODUCT)

    # Maintenance operations

    async def start_easy_clean(self) -> bool:
        if not self.connected:
            return False
        return await self._protocol.start_process(
            self._write_ble, MachineProcess.EASY_CLEAN)

    async def start_intensive_clean(self) -> bool:
        if not self.connected:
            return False
        return await self._protocol.start_process(
            self._write_ble, MachineProcess.INTENSIVE_CLEAN)

    async def start_descaling(self) -> bool:
        if not self.connected:
            return False
        return await self._protocol.start_process(
            self._write_ble, MachineProcess.DESCALING)

    async def switch_off(self) -> bool:
        if not self.connected:
            return False
        return await self._protocol.start_process(
            self._write_ble, MachineProcess.SWITCH_OFF)

    # Recipe operations

    async def read_recipe(self, recipe_id: int) -> MachineRecipe | None:
        if not self.connected:
            return None
        return await self._protocol.read_recipe(self._write_ble, recipe_id)

    # Settings (numerical) operations

    async def read_setting(self, setting_id: int) -> int | None:
        if not self.connected:
            return None
        return await self._protocol.read_numerical(self._write_ble, setting_id)

    async def write_setting(self, setting_id: int, value: int) -> bool:
        if not self.connected:
            return False
        return await self._protocol.write_numerical(self._write_ble, setting_id, value)

    # Alphanumeric operations

    async def read_alpha(self, value_id: int) -> str | None:
        if not self.connected:
            return None
        return await self._protocol.read_alphanumeric(self._write_ble, value_id)

    async def write_alpha(self, value_id: int, value: str) -> bool:
        if not self.connected:
            return False
        return await self._protocol.write_alphanumeric(self._write_ble, value_id, value)


async def discover_melitta_devices(timeout: float = 10.0) -> list[BLEDevice]:
    """Discover Melitta Barista devices via BLE scan."""
    devices = []

    def detection_callback(device: BLEDevice, adv_data) -> None:
        if adv_data.service_uuids and MELITTA_SERVICE_UUID in adv_data.service_uuids:
            devices.append(device)
        elif device.name and any(
            device.name.startswith(p) for p in BLE_PREFIXES_ALL
        ):
            devices.append(device)

    scanner = BleakScanner(detection_callback=detection_callback)
    await scanner.start()
    await asyncio.sleep(timeout)
    await scanner.stop()

    return devices
