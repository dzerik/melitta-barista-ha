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

from .const import (
    BLE_PREFIXES_ALL,
    CHAR_NOTIFY,
    CHAR_WRITE,
    CUP_COUNTER_BASE_ID,
    CUP_COUNTER_RECIPES,
    DirectKeyCategory,
    MACHINE_MODEL_NAMES,
    MACHINE_TYPE_SETTING_ID,
    MachineProcess,
    MachineType,
    PROFILE_NAMES,
    RecipeId,
    TOTAL_CUPS_ID,
    USER_ACTIVITY_IDS,
    USER_NAME_IDS,
    detect_machine_type_from_name,
    get_directkey_id,
    get_user_profile_count,
)
from .protocol import MachineRecipe, MachineStatus, MelittaProtocol, RecipeComponent

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
        self._write_lock = asyncio.Lock()
        self._brew_lock = asyncio.Lock()
        self._status: MachineStatus | None = None
        self._firmware: str | None = None
        self._machine_type: MachineType | None = None
        self._status_callbacks: list[Callable[[MachineStatus], None]] = []
        self._connection_callbacks: list[Callable[[bool], None]] = []
        self._poll_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._auto_reconnect = True
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
        self.freestyle_portion1_ml: int = 40
        self.freestyle_temperature1: str = "normal"
        self.freestyle_shots1: str = "one"
        self.freestyle_process2: str = "none"
        self.freestyle_intensity2: str = "medium"
        self.freestyle_portion2_ml: int = 0
        self.freestyle_temperature2: str = "normal"
        self.freestyle_shots2: str = "none"

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
        """
        self._ble_device = ble_device

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
        ):
            task = asyncio.ensure_future(self.read_cup_counters())
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

    async def _establish_connection(self) -> BleakClient:
        """Establish BLE connection following the switchbot/led_ble pattern.

        Uses BleakClientWithServiceCache + establish_connection() with
        ble_device_callback for fresh device reference on each retry.
        Passes pair=True so Bleak handles bonding via any backend
        (BlueZ D-Bus or ESPHome BLE proxy).
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
                    pair=True,
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
        _LOGGER.debug("Using raw BleakClient for %s", self._address)
        client = BleakClient(
            self._ble_device or self._address,
            disconnected_callback=self._on_disconnect,
            timeout=15.0,
            pair=True,
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
                try:
                    await self._client.disconnect()
                except (BleakError, OSError):
                    pass
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

            # Read cup counters
            await self.read_cup_counters()

            # Read profile names and DirectKey recipes
            await self.read_profile_data()

            # Notify connection callbacks
            for cb in self._connection_callbacks:
                try:
                    cb(True)
                except Exception:  # noqa: BLE900 — callback from user code
                    _LOGGER.exception("Error in connection callback")

            return True

        except (BleakError, OSError, asyncio.TimeoutError):
            _LOGGER.exception("Connection failed for %s", self._address)
            self._connected = False
            client = self._client
            self._client = None
            if client:
                try:
                    await client.disconnect()
                except (BleakError, OSError):
                    pass
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
            except (BleakError, OSError, asyncio.TimeoutError):
                _LOGGER.debug("Reconnect attempt failed", exc_info=True)
            delay = min(delay * 2, max_delay)

    async def disconnect(self) -> None:
        self._auto_reconnect = False
        self._stop_polling()
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
            except (BleakError, OSError, asyncio.TimeoutError):
                _LOGGER.debug("Poll error", exc_info=True)
            await asyncio.sleep(interval)

    # High-level API

    async def brew_recipe(
        self, recipe_id: RecipeId, *, two_cups: bool = False,
    ) -> bool:
        """Brew a base recipe (always from default profile 0).

        Uses _brew_lock to prevent concurrent brew operations.
        Pauses polling during brew to avoid BLE contention.
        """
        if self._brew_lock.locked():
            _LOGGER.warning("Brew already in progress, ignoring")
            return False

        if not self.connected:
            return False
        if self._status and not self._status.is_ready:
            _LOGGER.warning("Machine not ready: %s", self._status)
            return False

        from .const import RECIPE_NAMES, TEMP_RECIPE_ID, FREESTYLE_NAME_ID

        async with self._brew_lock:
            self._stop_polling()
            try:
                recipe = await self._protocol.read_recipe(
                    self._write_ble, int(recipe_id),
                )
                if not recipe:
                    _LOGGER.error("Failed to read recipe %d", recipe_id)
                    return False

                from .const import get_recipe_key
                if not await self._protocol.write_recipe(
                    self._write_ble, TEMP_RECIPE_ID, recipe.recipe_type,
                    recipe.component1, recipe.component2,
                    recipe_key=get_recipe_key(recipe.recipe_type),
                ):
                    _LOGGER.error("Failed to write recipe to temp slot")
                    return False

                await asyncio.sleep(0.2)

                name = RECIPE_NAMES.get(recipe_id, str(recipe_id))
                if not await self._protocol.write_alphanumeric(
                    self._write_ble, FREESTYLE_NAME_ID, name,
                ):
                    _LOGGER.error("Failed to write recipe name")
                    return False

                await asyncio.sleep(0.2)

                return await self._protocol.start_process(
                    self._write_ble, MachineProcess.PRODUCT,
                    two_cups=two_cups,
                )
            finally:
                if self.connected:
                    self.start_polling(interval=5.0)

    async def brew_directkey(self, category: DirectKeyCategory, *, two_cups: bool = False) -> bool:
        """Brew from a DirectKey slot of the active profile.

        Reads the DirectKey recipe for the active profile and category,
        writes to temp slot, then starts brewing.
        """
        if self._brew_lock.locked():
            _LOGGER.warning("Brew already in progress, ignoring")
            return False

        if not self.connected:
            return False
        if self._status and not self._status.is_ready:
            _LOGGER.warning("Machine not ready: %s", self._status)
            return False

        from .const import TEMP_RECIPE_ID, FREESTYLE_NAME_ID

        dk_id = get_directkey_id(self.active_profile, category)

        async with self._brew_lock:
            self._stop_polling()
            try:
                recipe = await self._protocol.read_recipe(self._write_ble, dk_id)
                if not recipe:
                    _LOGGER.error("Failed to read DirectKey recipe %d", dk_id)
                    return False

                from .const import get_recipe_key
                if not await self._protocol.write_recipe(
                    self._write_ble, TEMP_RECIPE_ID, recipe.recipe_type,
                    recipe.component1, recipe.component2,
                    recipe_key=get_recipe_key(recipe.recipe_type),
                ):
                    _LOGGER.error("Failed to write DirectKey recipe to temp slot")
                    return False

                await asyncio.sleep(0.2)

                name = category.name.replace("_", " ").title()
                if not await self._protocol.write_alphanumeric(
                    self._write_ble, FREESTYLE_NAME_ID, name,
                ):
                    _LOGGER.error("Failed to write DirectKey recipe name")
                    return False

                await asyncio.sleep(0.2)

                return await self._protocol.start_process(
                    self._write_ble, MachineProcess.PRODUCT,
                    two_cups=two_cups,
                )
            finally:
                if self.connected:
                    self.start_polling(interval=5.0)

    async def brew_freestyle(
        self,
        name: str,
        recipe_type: int,
        component1: RecipeComponent,
        component2: RecipeComponent,
        *,
        two_cups: bool = False,
    ) -> bool:
        """Brew a freestyle (custom) recipe.

        Uses _brew_lock to prevent concurrent brew operations.
        Pauses polling during brew to avoid BLE contention.
        """
        if self._brew_lock.locked():
            _LOGGER.warning("Brew already in progress, ignoring")
            return False

        if not self.connected:
            return False
        if self._status and not self._status.is_ready:
            _LOGGER.warning("Machine not ready: %s", self._status)
            return False

        from .const import TEMP_RECIPE_ID, FREESTYLE_NAME_ID

        async with self._brew_lock:
            self._stop_polling()
            try:
                from .const import get_recipe_key
                if not await self._protocol.write_recipe(
                    self._write_ble, TEMP_RECIPE_ID, recipe_type,
                    component1, component2,
                    recipe_key=get_recipe_key(recipe_type),
                ):
                    _LOGGER.error("Failed to write freestyle recipe")
                    return False

                await asyncio.sleep(0.2)

                if not await self._protocol.write_alphanumeric(
                    self._write_ble, FREESTYLE_NAME_ID, name,
                ):
                    _LOGGER.error("Failed to write freestyle name")
                    return False

                await asyncio.sleep(0.2)

                return await self._protocol.start_process(
                    self._write_ble, MachineProcess.PRODUCT,
                    two_cups=two_cups,
                )
            finally:
                if self.connected:
                    self.start_polling(interval=5.0)

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

    # Profile management

    async def read_profile_name(self, profile_id: int) -> str | None:
        """Read profile name by ID. Profile 0 is always 'My Coffee'."""
        if profile_id == 0:
            return PROFILE_NAMES[0]
        if profile_id not in USER_NAME_IDS:
            _LOGGER.warning("Invalid profile_id %d", profile_id)
            return None
        if not self.connected:
            return None
        return await self._protocol.read_alphanumeric(
            self._write_ble, USER_NAME_IDS[profile_id],
        )

    async def read_all_profile_names(self) -> dict[int, str]:
        """Read all profile names. Returns {profile_id: name}."""
        if not self.connected:
            return {}
        count = get_user_profile_count(self._machine_type)
        result: dict[int, str] = {0: PROFILE_NAMES[0]}
        for i in range(1, count + 1):
            if i not in USER_NAME_IDS:
                continue
            try:
                name = await self._protocol.read_alphanumeric(
                    self._write_ble, USER_NAME_IDS[i],
                )
                if name:
                    result[i] = name
                else:
                    result[i] = f"Profile {i}"
            except (BleakError, OSError, asyncio.TimeoutError):
                _LOGGER.debug("Failed to read name for profile %d", i)
                result[i] = f"Profile {i}"
        return result

    async def write_profile_name(self, profile_id: int, name: str) -> bool:
        """Write profile name. Profile 0 cannot be renamed."""
        if profile_id == 0:
            _LOGGER.warning("Cannot rename default profile 0")
            return False
        if profile_id not in USER_NAME_IDS:
            _LOGGER.warning("Invalid profile_id %d", profile_id)
            return False
        if not self.connected:
            return False
        return await self._protocol.write_alphanumeric(
            self._write_ble, USER_NAME_IDS[profile_id], name,
        )

    # Profile activity

    async def read_profile_activity(self, profile_id: int) -> int | None:
        """Read profile activity value (numerical)."""
        if profile_id == 0:
            return None  # default profile has no activity ID
        if profile_id not in USER_ACTIVITY_IDS:
            _LOGGER.warning("Invalid profile_id %d for activity", profile_id)
            return None
        if not self.connected:
            return None
        return await self._protocol.read_numerical(
            self._write_ble, USER_ACTIVITY_IDS[profile_id],
        )

    async def write_profile_activity(self, profile_id: int, value: int) -> bool:
        """Write profile activity value (numerical)."""
        if profile_id == 0:
            _LOGGER.warning("Cannot write activity for default profile 0")
            return False
        if profile_id not in USER_ACTIVITY_IDS:
            _LOGGER.warning("Invalid profile_id %d for activity", profile_id)
            return False
        if not self.connected:
            return False
        return await self._protocol.write_numerical(
            self._write_ble, USER_ACTIVITY_IDS[profile_id], value,
        )

    # Profile recipe management

    async def read_profile_recipe(
        self, profile_id: int, category: DirectKeyCategory,
    ) -> MachineRecipe | None:
        """Read recipe for a profile and category via DirectKey."""
        if not self.connected:
            return None
        recipe_id = get_directkey_id(profile_id, category)
        return await self._protocol.read_recipe(self._write_ble, recipe_id)

    async def read_all_profile_recipes(
        self, profile_id: int,
    ) -> dict[DirectKeyCategory, MachineRecipe]:
        """Read all recipes for a profile via DirectKey."""
        if not self.connected:
            return {}
        result: dict[DirectKeyCategory, MachineRecipe] = {}
        for cat in DirectKeyCategory:
            recipe_id = get_directkey_id(profile_id, cat)
            try:
                recipe = await self._protocol.read_recipe(
                    self._write_ble, recipe_id,
                )
                if recipe:
                    result[cat] = recipe
            except (BleakError, OSError, asyncio.TimeoutError):
                _LOGGER.debug(
                    "Failed to read recipe for profile %d, category %s",
                    profile_id, cat.name,
                )
        return result

    async def write_profile_recipe(
        self,
        profile_id: int,
        category: DirectKeyCategory,
        component1: RecipeComponent,
        component2: RecipeComponent,
    ) -> bool:
        """Write recipe for a profile and category via DirectKey.

        Reads the current recipe first to preserve recipe_type,
        then writes back with updated components.
        Stops polling during the operation to avoid BLE command conflicts.
        """
        if not self.connected:
            return False
        if self._brew_lock.locked():
            _LOGGER.warning("Brew in progress, cannot save recipe")
            return False

        recipe_id = get_directkey_id(profile_id, category)

        async with self._brew_lock:
            self._stop_polling()
            try:
                # Read current recipe to get recipe_type (with retry)
                current = None
                for attempt in range(3):
                    current = await self._protocol.read_recipe(self._write_ble, recipe_id)
                    if current:
                        break
                    _LOGGER.debug("Read recipe %d attempt %d failed, retrying", recipe_id, attempt + 1)
                    await asyncio.sleep(0.3)

                from .const import get_recipe_key, DIRECTKEY_DEFAULT_RECIPE_TYPE

                if current:
                    recipe_type = current.recipe_type
                else:
                    # Fallback: use default recipe_type for the category
                    recipe_type = DIRECTKEY_DEFAULT_RECIPE_TYPE.get(category, 0)
                    _LOGGER.warning(
                        "Cannot read recipe %d, using default recipe_type=%d for %s",
                        recipe_id, recipe_type, category.name,
                    )

                _LOGGER.debug(
                    "Writing DK recipe id=%d type=%d (profile=%d, %s)",
                    recipe_id, recipe_type, profile_id, category.name,
                )

                # Write with retry — DK slots use no recipe_key (matches original app)
                result = False
                for attempt in range(3):
                    result = await self._protocol.write_recipe(
                        self._write_ble, recipe_id, recipe_type,
                        component1, component2,
                    )
                    if result:
                        break
                    _LOGGER.warning(
                        "Write recipe %d attempt %d failed (ACK timeout)",
                        recipe_id, attempt + 1,
                    )
                    await asyncio.sleep(0.5)

                if result:
                    _LOGGER.debug(
                        "Written DirectKey recipe id=%d (profile=%d, %s)",
                        recipe_id, profile_id, category.name,
                    )
                    # Update local cache: re-read the written recipe
                    await asyncio.sleep(0.3)
                    updated = await self._protocol.read_recipe(
                        self._write_ble, recipe_id,
                    )
                    if updated:
                        if profile_id not in self._directkey_recipes:
                            self._directkey_recipes[profile_id] = {}
                        self._directkey_recipes[profile_id][category] = updated
                        self._notify_profile_callbacks()
                else:
                    _LOGGER.error(
                        "Write recipe %d failed after 3 attempts (profile=%d, %s)",
                        recipe_id, profile_id, category.name,
                    )
                return result
            except (BleakError, OSError, asyncio.TimeoutError):
                _LOGGER.exception(
                    "BLE error writing DirectKey recipe id=%d", recipe_id,
                )
                return False
            finally:
                self.start_polling(interval=5.0)

    async def reset_profile_recipe(
        self, profile_id: int, category: DirectKeyCategory,
    ) -> bool:
        """Reset profile recipe to default (copy from profile 0)."""
        if profile_id == 0:
            _LOGGER.warning("Cannot reset default profile 0 recipe")
            return False
        if not self.connected:
            return False
        default_id = get_directkey_id(0, category)
        default_recipe = await self._protocol.read_recipe(
            self._write_ble, default_id,
        )
        if not default_recipe:
            _LOGGER.error(
                "Cannot read default recipe for category %s", category.name,
            )
            return False
        target_id = get_directkey_id(profile_id, category)
        return await self._protocol.write_recipe(
            self._write_ble, target_id, default_recipe.recipe_type,
            default_recipe.component1, default_recipe.component2,
        )

    async def update_profile_recipe(
        self,
        profile_id: int,
        category: DirectKeyCategory,
        *,
        process: int | None = None,
        shots: int | None = None,
        blend: int | None = None,
        intensity: int | None = None,
        aroma: int | None = None,
        temperature: int | None = None,
        portion_ml: int | None = None,
    ) -> bool:
        """Update individual parameters of a profile recipe (component 1).

        Reads the current recipe, merges provided parameters, writes back.
        Only component 1 is updated (the primary drink component).
        """
        if not self.connected:
            return False
        recipe_id = get_directkey_id(profile_id, category)
        current = await self._protocol.read_recipe(self._write_ble, recipe_id)
        if not current:
            _LOGGER.error(
                "Cannot read current recipe for profile %d, category %s",
                profile_id, category.name,
            )
            return False
        c = current.component1
        updated = RecipeComponent(
            process=process if process is not None else c.process,
            shots=shots if shots is not None else c.shots,
            blend=blend if blend is not None else c.blend,
            intensity=intensity if intensity is not None else c.intensity,
            aroma=aroma if aroma is not None else c.aroma,
            temperature=temperature if temperature is not None else c.temperature,
            portion=portion_ml // 5 if portion_ml is not None else c.portion,
            reserve=c.reserve,
        )
        return await self._protocol.write_recipe(
            self._write_ble, recipe_id, current.recipe_type,
            updated, current.component2,
        )

    async def copy_profile_recipe(
        self,
        from_profile: int,
        to_profile: int,
        category: DirectKeyCategory,
    ) -> bool:
        """Copy a recipe from one profile to another."""
        if not self.connected:
            return False
        source_id = get_directkey_id(from_profile, category)
        source = await self._protocol.read_recipe(self._write_ble, source_id)
        if not source:
            _LOGGER.error(
                "Cannot read source recipe from profile %d, category %s",
                from_profile, category.name,
            )
            return False
        target_id = get_directkey_id(to_profile, category)
        return await self._protocol.write_recipe(
            self._write_ble, target_id, source.recipe_type,
            source.component1, source.component2,
        )

    async def reset_all_profile_recipes(self, profile_id: int) -> bool:
        """Reset all recipes of a profile to defaults (from profile 0)."""
        if profile_id == 0:
            _LOGGER.warning("Cannot reset default profile 0 recipes")
            return False
        if not self.connected:
            return False
        all_ok = True
        for cat in DirectKeyCategory:
            try:
                if not await self.reset_profile_recipe(profile_id, cat):
                    all_ok = False
            except (BleakError, OSError, asyncio.TimeoutError):
                _LOGGER.debug(
                    "Failed to reset recipe for profile %d, category %s",
                    profile_id, cat.name,
                )
                all_ok = False
        return all_ok

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

    async def read_profile_data(self) -> None:
        """Read profile names and DirectKey recipes for all profiles."""
        if not self.connected:
            return
        profile_count = get_user_profile_count(self._machine_type)
        # Read profile names (profiles 1..N)
        for i in range(1, profile_count + 1):
            if i in USER_NAME_IDS:
                try:
                    name = await self._protocol.read_alphanumeric(
                        self._write_ble, USER_NAME_IDS[i],
                    )
                    if name:
                        self._profile_names[i] = name
                except (BleakError, OSError, asyncio.TimeoutError):
                    _LOGGER.debug("Failed to read name for profile %d", i)

        # Read DirectKey recipes for all profiles (0..N)
        for pid in range(0, profile_count + 1):
            recipes: dict[int, MachineRecipe] = {}
            for cat in DirectKeyCategory:
                dk_id = get_directkey_id(pid, cat)
                try:
                    recipe = await self._protocol.read_recipe(self._write_ble, dk_id)
                    if recipe:
                        recipes[cat] = recipe
                except (BleakError, OSError, asyncio.TimeoutError):
                    _LOGGER.debug("Failed to read DirectKey %d (profile %d, %s)", dk_id, pid, cat.name)
            self._directkey_recipes[pid] = recipes

        _LOGGER.info(
            "Loaded %d profile names, DirectKey recipes for %d profiles",
            len(self._profile_names) - 1, len(self._directkey_recipes),
        )
        self._notify_profile_callbacks()

    async def read_cup_counters(self) -> bool:
        """Read cup counters from the machine (HR IDs 100-123 + 150)."""
        if not self.connected:
            return False
        counters: dict[str, int] = {}
        for offset, name in CUP_COUNTER_RECIPES.items():
            try:
                val = await self._protocol.read_numerical(
                    self._write_ble, CUP_COUNTER_BASE_ID + offset,
                )
                if val is not None:
                    counters[name] = val
            except (BleakError, OSError, asyncio.TimeoutError):
                _LOGGER.debug("Failed to read cup counter for %s (id=%d)",
                              name, CUP_COUNTER_BASE_ID + offset)
        try:
            total = await self._protocol.read_numerical(
                self._write_ble, TOTAL_CUPS_ID,
            )
        except (BleakError, OSError, asyncio.TimeoutError):
            total = None
        self._cup_counters = counters
        self._total_cups = total
        _LOGGER.debug("Cup counters: total=%s, per_recipe=%s", total, counters)
        for cb in self._cups_callbacks:
            try:
                cb()
            except Exception:  # noqa: BLE900 — callback from user code
                _LOGGER.exception("Error in cups callback")
        return True

    async def read_alpha(self, value_id: int) -> str | None:
        if not self.connected:
            return None
        return await self._protocol.read_alphanumeric(self._write_ble, value_id)

    async def write_alpha(self, value_id: int, value: str) -> bool:
        if not self.connected:
            return False
        if self._brew_lock.locked():
            _LOGGER.warning("Brew in progress, cannot write alpha")
            return False
        async with self._brew_lock:
            self._stop_polling()
            try:
                result = await self._protocol.write_alphanumeric(
                    self._write_ble, value_id, value,
                )
                if result:
                    # Update cached profile name if applicable
                    for pid, name_id in USER_NAME_IDS.items():
                        if name_id == value_id:
                            self._profile_names[pid] = value
                            self._notify_profile_callbacks()
                            break
                return result
            except (BleakError, OSError, asyncio.TimeoutError):
                _LOGGER.exception("BLE error writing alpha id=%d", value_id)
                return False
            finally:
                self.start_polling(interval=5.0)


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
