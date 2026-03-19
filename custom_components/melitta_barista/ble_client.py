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
from typing import Callable

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.exc import BleakDBusError, BleakError

from ._ble_commands import BleCommandsMixin
from ._ble_recipes import BleRecipesMixin
from ._ble_settings import BleSettingsMixin
from .const import (
    BLE_PREFIXES_ALL,
    CHAR_NOTIFY,
    CHAR_WRITE,
    DEFAULT_BLE_CONNECT_TIMEOUT,
    DEFAULT_FRAME_TIMEOUT,
    DEFAULT_MAX_CONSECUTIVE_ERRORS,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_RECIPE_RETRIES,
    DEFAULT_RECONNECT_DELAY,
    DEFAULT_RECONNECT_MAX_DELAY,
    MACHINE_MODEL_NAMES,
    MACHINE_TYPE_SETTING_ID,
    MachineProcess,
    MachineType,
    PROFILE_NAMES,
    RecipeId,
    detect_machine_type_from_name,
)
from .protocol import MachineRecipe, MachineStatus, MelittaProtocol

_LOGGER = logging.getLogger("melitta_barista")

# Melitta service UUID for BLE discovery
MELITTA_SERVICE_UUID = "0000ad00-b35c-11e4-9813-0002a5d5c51b"


class MelittaBleClient(BleCommandsMixin, BleRecipesMixin, BleSettingsMixin):
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
        *,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        ble_connect_timeout: float = DEFAULT_BLE_CONNECT_TIMEOUT,
        frame_timeout: int = DEFAULT_FRAME_TIMEOUT,
        max_consecutive_errors: int = DEFAULT_MAX_CONSECUTIVE_ERRORS,
        reconnect_delay: float = DEFAULT_RECONNECT_DELAY,
        reconnect_max_delay: float = DEFAULT_RECONNECT_MAX_DELAY,
        recipe_retries: int = DEFAULT_RECIPE_RETRIES,
    ) -> None:
        self._address = address
        self._device_name = device_name
        self._ble_device: BLEDevice | None = ble_device
        self._client: BleakClient | None = None
        self._protocol = MelittaProtocol(frame_timeout=frame_timeout)

        # Configurable parameters
        self._poll_interval = poll_interval
        self._ble_connect_timeout = ble_connect_timeout
        self._frame_timeout = frame_timeout
        self._max_consecutive_errors = max_consecutive_errors
        self._reconnect_delay = reconnect_delay
        self._reconnect_max_delay = reconnect_max_delay
        self._recipe_retries = recipe_retries
        self._connected = False
        self._connect_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._brew_lock = asyncio.Lock()
        self._status: MachineStatus | None = None
        self._firmware: str | None = None
        self._machine_type: MachineType | None = None
        self._status_callbacks: list[Callable[[MachineStatus], None]] = []
        self._connection_callbacks: list[Callable[[bool], None]] = []
        self._poll_task: asyncio.Task | None = None
        self._post_connect_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._reconnect_event = asyncio.Event()
        self._auto_reconnect = True
        self._disconnecting = False
        self.selected_recipe: RecipeId | None = None
        self.active_profile: int = 0  # 0 = default "My Coffee"
        self._cup_counters: dict[str, int] = {}  # recipe_name -> count
        self._total_cups: int | None = None
        self._cups_callbacks: list[Callable[[], None]] = []

        # Profile data: names and DirectKey recipes per profile
        self._profile_names: dict[int, str] = {0: PROFILE_NAMES[0]}
        self._directkey_recipes: dict[int, dict[int, MachineRecipe]] = {}
        self._profile_callbacks: list[Callable[[], None]] = []

        # Freestyle recipe state (used by freestyle entities)
        self.freestyle_name: str = "Custom"
        self.freestyle_process1: str = "coffee"
        self.freestyle_intensity1: str = "medium"
        self.freestyle_aroma1: str = "standard"
        self.freestyle_portion1_ml: int = 40
        self.freestyle_temperature1: str = "normal"
        self.freestyle_shots1: str = "one"
        self.freestyle_process2: str = "none"
        self.freestyle_intensity2: str = "medium"
        self.freestyle_aroma2: str = "standard"
        self.freestyle_portion2_ml: int = 0
        self.freestyle_temperature2: str = "normal"
        self.freestyle_shots2: str = "none"

        # BLE pairing state: skip pair=True on reconnect if already bonded
        self._paired = False

        # Pre-detect machine type from BLE device name if available
        if device_name:
            self._machine_type = detect_machine_type_from_name(device_name)

    @property
    def address(self) -> str:
        return self._address

    @property
    def connected(self) -> bool:
        client = self._client
        return self._connected and client is not None and client.is_connected

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

    @property
    def total_cups(self) -> int | None:
        return self._total_cups

    @property
    def cup_counters(self) -> dict[str, int]:
        return self._cup_counters

    @property
    def profile_names(self) -> dict[int, str]:
        return self._profile_names

    @property
    def directkey_recipes(self) -> dict[int, dict[int, MachineRecipe]]:
        return self._directkey_recipes

    def add_profile_callback(self, callback: Callable[[], None]) -> None:
        self._profile_callbacks.append(callback)

    def remove_profile_callback(self, callback: Callable[[], None]) -> None:
        try:
            self._profile_callbacks.remove(callback)
        except ValueError:
            pass

    def _notify_profile_callbacks(self) -> None:
        for cb in self._profile_callbacks:
            try:
                cb()
            except Exception:  # noqa: BLE900 — callback from user code
                _LOGGER.exception("Error in profile callback")

    def add_cups_callback(self, callback: Callable[[], None]) -> None:
        self._cups_callbacks.append(callback)

    def remove_cups_callback(self, callback: Callable[[], None]) -> None:
        try:
            self._cups_callbacks.remove(callback)
        except ValueError:
            pass

    def set_ble_device(self, ble_device: BLEDevice) -> None:
        """Update BLEDevice reference from advertisement callback.

        Called by __init__.py when HA sees a new advertisement from the device.
        This keeps the BLEDevice fresh for establish_connection() retries.
        If disconnected, triggers immediate reconnect attempt.
        """
        self._ble_device = ble_device
        if not self._connected and self._auto_reconnect:
            _LOGGER.info(
                "BLE advertisement from %s while disconnected, triggering reconnect",
                self._address,
            )
            self._reconnect_event.set()
            # Only schedule reconnect if no loop is already running
            # (_async_connect_and_poll or _reconnect_loop already listens
            # on _reconnect_event, so set() alone wakes them up)
            if not self._reconnect_task or self._reconnect_task.done():
                self._schedule_reconnect()

    def add_status_callback(self, callback: Callable[[MachineStatus], None]) -> None:
        self._status_callbacks.append(callback)

    def remove_status_callback(self, callback: Callable[[MachineStatus], None]) -> None:
        try:
            self._status_callbacks.remove(callback)
        except ValueError:
            pass

    def add_connection_callback(self, callback: Callable[[bool], None]) -> None:
        self._connection_callbacks.append(callback)

    def remove_connection_callback(self, callback: Callable[[bool], None]) -> None:
        try:
            self._connection_callbacks.remove(callback)
        except ValueError:
            pass

    def _on_status(self, status: MachineStatus) -> None:
        prev = self._status
        self._status = status
        # Refresh cup counters when brew finishes (PRODUCT → READY)
        if (
            prev is not None
            and prev.process == MachineProcess.PRODUCT
            and status.process == MachineProcess.READY
            and not self._brew_lock.locked()
        ):
            task = asyncio.create_task(self.read_cup_counters())
            task.add_done_callback(self._on_cup_refresh_done)
        for cb in self._status_callbacks:
            try:
                cb(status)
            except Exception:  # noqa: BLE900 — callback from user code
                _LOGGER.exception("Error in status callback")

    @staticmethod
    def _on_cup_refresh_done(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            _LOGGER.debug("Cup counter refresh failed: %s", exc)

    def _on_disconnect(self, client: BleakClient) -> None:
        if self._disconnecting:
            return
        if client is not self._client:
            _LOGGER.debug("Ignoring disconnect callback from stale client")
            return
        _LOGGER.info("Disconnected from %s", self._address)
        self._connected = False
        self._client = None
        for cb in self._connection_callbacks:
            try:
                cb(False)
            except Exception:  # noqa: BLE900 — callback from user code
                _LOGGER.exception("Error in connection callback")
        if self._auto_reconnect:
            self._schedule_reconnect()

    def _on_notification(self, _sender: int, data: bytearray) -> None:
        self._protocol.on_ble_data(bytes(data))

    async def _write_ble(self, data: bytes) -> None:
        async with self._write_lock:
            client = self._client
            if not client or not client.is_connected:
                raise BleakError("Not connected")
            try:
                await client.write_gatt_char(CHAR_WRITE, data, response=False)
            except AssertionError:
                # bleak internal: assert self._bus — D-Bus connection lost
                _LOGGER.error("D-Bus connection lost during write (assert self._bus)")
                raise BleakError("D-Bus connection lost")

    async def _establish_connection(self, *, pair: bool = False) -> BleakClient:
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
                    "Using establish_connection for %s (pair=%s, ble_device=%s)",
                    self._address,
                    pair,
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
                    pair=pair,
                )
                return client
            except ImportError:
                _LOGGER.warning(
                    "bleak_retry_connector not available, using raw BleakClient"
                )
            except (BleakError, OSError, asyncio.TimeoutError):
                _LOGGER.debug(
                    "establish_connection failed, falling back to raw BleakClient",
                    exc_info=True,
                )

        # Fallback: raw BleakClient (e.g. outside HA or missing retry-connector)
        _LOGGER.debug("Using raw BleakClient for %s (pair=%s)", self._address, pair)
        client = BleakClient(
            self._ble_device or self._address,
            disconnected_callback=self._on_disconnect,
            timeout=self._ble_connect_timeout,
            pair=pair,
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

        Flow: BLE connect (with auto-pair) -> subscribe notify -> HU handshake -> read version.
        Pairing is handled automatically by Bleak via pair=True (works with both
        local BlueZ adapter and ESPHome BLE proxy).
        """
        async with self._connect_lock:
            return await self._connect_impl()

    async def _try_connect_and_handshake(self, *, pair: bool) -> bool:
        """Try to establish BLE connection and perform HU handshake.

        Returns True on success, False on failure (cleans up client).
        """
        self._protocol = MelittaProtocol(frame_timeout=self._frame_timeout)
        self._protocol.set_status_callback(self._on_status)

        try:
            self._client = await self._establish_connection(pair=pair)
        except (BleakError, OSError, asyncio.TimeoutError):
            _LOGGER.debug(
                "BLE connect failed (pair=%s)", pair, exc_info=True,
            )
            self._client = None
            return False

        if not self._client.is_connected:
            _LOGGER.debug("BLE client not connected after establish (pair=%s)", pair)
            self._client = None
            return False

        _LOGGER.debug("BLE connected to %s (pair=%s)", self._address, pair)

        try:
            await self._start_notify(self._client)
        except (BleakError, OSError, asyncio.TimeoutError):
            _LOGGER.debug("start_notify failed (pair=%s)", pair, exc_info=True)
            await self._safe_disconnect()
            return False

        if not await self._protocol.perform_handshake(self._write_ble):
            _LOGGER.debug("HU handshake failed (pair=%s)", pair)
            await self._safe_disconnect()
            return False

        return True

    async def _safe_disconnect(self) -> None:
        """Disconnect current client, suppressing errors."""
        client = self._client
        self._client = None
        if client:
            try:
                await client.disconnect()
            except (BleakError, OSError):
                pass

    async def _try_unpair(self) -> None:
        """Clear stale bond on ESP32/BlueZ by connecting without pair and calling unpair.

        This is needed when pair=True fails because the proxy holds a stale bond
        that the peripheral rejects (error 82 / BluetoothConnectionDroppedError).
        """
        try:
            _LOGGER.info("Clearing stale bond for %s", self._address)
            client = await self._establish_connection(pair=False)
            try:
                await client.unpair()
                _LOGGER.info("Unpaired %s successfully", self._address)
            except (BleakError, OSError, NotImplementedError, AttributeError):
                _LOGGER.debug("unpair() failed or not supported", exc_info=True)
            finally:
                try:
                    await client.disconnect()
                except (BleakError, OSError):
                    pass
        except (BleakError, OSError, asyncio.TimeoutError):
            _LOGGER.debug("Could not connect for unpair", exc_info=True)

    async def _connect_impl(self) -> bool:
        """Internal connect implementation (must be called under _connect_lock).

        Pairing strategy:
        1. Try pair=False first (fast — reuses existing bond on ESP32/BlueZ).
        2. If handshake fails, retry with pair=True (first-ever or bond lost).
        3. If pair=True also fails, unpair (clear stale bond) then pair=True again.
        """
        if self._connected and self._client and self._client.is_connected:
            return True

        # Cancel any pending reconnect task to avoid interference with retry logic
        # (but skip if WE are the reconnect task — otherwise we cancel ourselves)
        if self._reconnect_task and not self._reconnect_task.done():
            if asyncio.current_task() is not self._reconnect_task:
                self._reconnect_task.cancel()
                self._reconnect_task = None

        try:
            _LOGGER.info("Connecting to Melitta at %s", self._address)

            # Attempt 1: without pairing (reuse existing bond)
            if await self._try_connect_and_handshake(pair=False):
                self._paired = True
            else:
                # Attempt 2: with pairing (create new bond)
                _LOGGER.info(
                    "Retrying connection to %s with pairing", self._address,
                )
                if not await self._try_connect_and_handshake(pair=True):
                    # Attempt 3: unpair stale bond, then pair fresh
                    await self._try_unpair()
                    _LOGGER.info(
                        "Retrying connection to %s after unpair", self._address,
                    )
                    if not await self._try_connect_and_handshake(pair=True):
                        _LOGGER.error("Connection failed for %s", self._address)
                        return False
                self._paired = True

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

            # Notify connection callbacks (entities become available)
            for cb in self._connection_callbacks:
                try:
                    cb(True)
                except Exception:  # noqa: BLE900 — callback from user code
                    _LOGGER.exception("Error in connection callback")

            # Load cup counters and profile data in background (non-blocking)
            self._post_connect_task = asyncio.create_task(
                self._load_post_connect_data()
            )

            return True

        except (BleakError, OSError, asyncio.TimeoutError):
            _LOGGER.exception("Connection failed for %s", self._address)
            self._connected = False
            await self._safe_disconnect()
            return False

    async def _load_post_connect_data(self) -> None:
        """Load cup counters and profile data after connect (non-blocking)."""
        try:
            await self.read_cup_counters()
            await self.read_profile_data()
        except (BleakError, OSError, asyncio.TimeoutError):
            _LOGGER.debug("Error loading post-connect data", exc_info=True)
        except Exception:
            _LOGGER.exception("Unexpected error loading post-connect data")

    def _schedule_reconnect(self) -> None:
        """Schedule a reconnect attempt."""
        if self._reconnect_task and not self._reconnect_task.done():
            return
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        """Try to reconnect with exponential backoff.

        The loop can be woken up early by setting _reconnect_event (e.g. when
        a BLE advertisement arrives, indicating the machine is back online).
        """
        delay = self._reconnect_delay
        while self._auto_reconnect and not self.connected:
            _LOGGER.info("Reconnecting to %s in %.0fs...", self._address, delay)
            self._reconnect_event.clear()
            try:
                await asyncio.wait_for(self._reconnect_event.wait(), timeout=delay)
                _LOGGER.debug("Reconnect woken up early (BLE advertisement received)")
                delay = self._reconnect_delay  # reset backoff
            except asyncio.TimeoutError:
                pass
            if not self._auto_reconnect:
                break
            try:
                if await self.connect():
                    _LOGGER.info("Reconnected to %s", self._address)
                    self.start_polling(interval=self._poll_interval)
                    return
            except (BleakError, OSError, asyncio.TimeoutError):
                _LOGGER.debug("Reconnect attempt failed", exc_info=True)
            except Exception:
                _LOGGER.exception("Unexpected error during reconnect")
            delay = min(delay * 2, self._reconnect_max_delay)

    async def disconnect(self) -> None:
        self._auto_reconnect = False
        self._disconnecting = True
        self._stop_polling()
        if self._post_connect_task and not self._post_connect_task.done():
            self._post_connect_task.cancel()
            self._post_connect_task = None
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            self._reconnect_task = None
        client = self._client
        self._client = None
        self._connected = False
        if client:
            try:
                if client.is_connected:
                    try:
                        await client.stop_notify(CHAR_NOTIFY)
                    except (BleakError, OSError):
                        _LOGGER.debug("stop_notify during disconnect failed", exc_info=True)
                await client.disconnect()
            except (BleakError, OSError):
                _LOGGER.debug("Error during disconnect", exc_info=True)
        self._disconnecting = False

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
        consecutive_errors = 0
        while self.connected:
            try:
                await self.poll_status()
                consecutive_errors = 0
            except (BleakError, OSError, asyncio.TimeoutError):
                consecutive_errors += 1
                _LOGGER.debug(
                    "Poll error (%d/%d)", consecutive_errors, self._max_consecutive_errors,
                    exc_info=True,
                )
                if consecutive_errors >= self._max_consecutive_errors:
                    _LOGGER.warning(
                        "Poll failed %d times in a row for %s, forcing disconnect",
                        self._max_consecutive_errors, self._address,
                    )
                    self._connected = False
                    await self._safe_disconnect()
                    for cb in self._connection_callbacks:
                        try:
                            cb(False)
                        except Exception:
                            _LOGGER.exception("Error in connection callback")
                    if self._auto_reconnect:
                        self._schedule_reconnect()
                    return
            await asyncio.sleep(interval)

    # High-level API methods are provided by mixins:
    # - BleCommandsMixin: brew, cancel, maintenance (_ble_commands.py)
    # - BleRecipesMixin: recipe/profile CRUD, cups (_ble_recipes.py)
    # - BleSettingsMixin: settings, alpha read/write (_ble_settings.py)


async def discover_melitta_devices(timeout: float = 10.0) -> list[BLEDevice]:
    """Discover Melitta Barista devices via BLE scan."""
    devices: dict[str, BLEDevice] = {}


    def detection_callback(device: BLEDevice, adv_data) -> None:
        if device.address in devices:
            return
        if adv_data.service_uuids and MELITTA_SERVICE_UUID in adv_data.service_uuids:
            devices[device.address] = device
        elif device.name and any(
            device.name.startswith(p) for p in BLE_PREFIXES_ALL
        ):
            devices[device.address] = device

    scanner = BleakScanner(detection_callback=detection_callback)
    await scanner.start()
    await asyncio.sleep(timeout)
    await scanner.stop()

    return list(devices.values())
