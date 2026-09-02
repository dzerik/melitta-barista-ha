"""BLE client for Eugster-family coffee machines (Melitta, Nivona).

Architecture follows the switchbot/led_ble pattern:
- Store and update BLEDevice reference from HA bluetooth advertisements
- Use BleakClientWithServiceCache + establish_connection() for reliable connections
- Freeze the selected BLEDevice/source for each complete connect/pair ladder
- Force StartNotify to avoid bleak 2.0 AcquireNotify issues
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from enum import Enum
from typing import Any, TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .brands.base import BrandProfile, MachineCapabilities

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
    DEFAULT_PAIR_SETTLE_DELAY,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_RECIPE_RETRIES,
    DEFAULT_RECONNECT_DELAY,
    DEFAULT_RECONNECT_MAX_DELAY,
    DEFAULT_REPAIR_AFTER_FAILURES,
    FAILURE_AUTH,
    FAILURE_HANDSHAKE,
    FAILURE_LINK,
    FAILURE_TIMEOUT,
    FeatureFlags,
    MACHINE_MODEL_NAMES,
    MACHINE_TYPE_SETTING_ID,
    MachineProcess,
    MachineType,
    Manipulation,
    SOFT_AUTO_CONFIRM_MANIPULATIONS,
    PROFILE_NAMES,
    RecipeId,
    detect_machine_type_from_name,
)
from .bond_state import BondState, BondStateMachine
from .protocol import MachineRecipe, MachineStatus, MelittaProtocol

_LOGGER = logging.getLogger("melitta_barista")


class UnpairOutcome(str, Enum):
    """Result of one destructive bond-removal attempt."""

    CONFIRMED = "confirmed"
    ATTEMPTED_UNCONFIRMED = "attempted_unconfirmed"
    NOT_SENT = "not_sent"


class NoBleDeviceError(BleakError):
    """No BLEDevice is cached for the peer — connecting is impossible.

    Raised by ``_establish_connection`` instead of a generic BleakError so
    the connect ladder can short-circuit: without a BLEDevice every rung
    (pair=False, pair=True, unpair, pair=True) fails identically in
    microseconds, and running all four just spams the log four times per
    reconnect cycle (issue #35). The reconnect loops treat it as a normal
    failed attempt and wait for an advertisement.
    """


def _norm_ble_source(source: str | None) -> str:
    """Normalize scanner source identifiers for stable comparisons."""
    return (source or "").lower().replace(":", "").replace("-", "")


def _source_pinned_ha_client_class(source: str):
    """Return a HA service-caching Bleak client pinned to one scanner source.

    habluetooth normally chooses the best scanner backend for an address at
    connect time. Bonded Melitta devices cannot safely roam between Bluetooth
    centrals, so the candidate set is narrowed to the central that owns the
    bond. The hooks are private upstream APIs; if their shape changes, fall
    back to the normal HA selector and let the mandatory post-connect source
    verification reject a connection that landed on the wrong central.
    """
    try:
        from habluetooth.usage import (  # noqa: PLC0415
            HaBleakClientWithServiceCache,
        )
    except ImportError:
        # Compatibility with habluetooth releases that re-export the class.
        from habluetooth import HaBleakClientWithServiceCache  # noqa: PLC0415

    target = _norm_ble_source(source)

    class _SourcePinnedHaBleakClient(HaBleakClientWithServiceCache):
        """HA service-cache client that may connect through one scanner only."""

        def _async_get_best_available_backend_and_device(self, *args, **kwargs):
            try:
                # Keep the override invocation-compatible if habluetooth adds
                # parameters to this private hook. 5.9.1 passes ``manager`` as
                # the first positional argument; if that assumption ever stops
                # being true, the guarded private path below raises and we
                # delegate the untouched call to upstream.
                manager = args[0] if args else kwargs.get("manager")
                if manager is None:
                    raise TypeError("habluetooth manager argument is unavailable")
                address = self._HaBleakClientWrapper__address  # noqa: SLF001
                scanner_devices = manager.async_scanner_devices_by_address(
                    address, True,
                )
                matching = [
                    item
                    for item in scanner_devices
                    if _norm_ble_source(getattr(item.scanner, "source", None))
                    == target
                ]
                matching.sort(
                    key=lambda item: item.advertisement.rssi, reverse=True,
                )

                for item in matching:
                    backend = self._async_get_backend_for_ble_device(  # noqa: SLF001
                        manager, item.scanner, item.ble_device,
                    )
                    if backend is not None:
                        _LOGGER.debug(
                            "Pinned Bluetooth backend for %s to %s (%s)",
                            address,
                            source,
                            getattr(item.scanner, "name", source),
                        )
                        return backend

                if matching:
                    raise BleakError(
                        f"Pinned Bluetooth source {source} has no available "
                        f"connection slot for {address}"
                    )
                raise BleakError(
                    f"Pinned Bluetooth source {source} cannot currently reach "
                    f"{address}"
                )
            except (AttributeError, TypeError):
                _LOGGER.warning(
                    "habluetooth private backend-selection API changed; "
                    "falling back to normal HA selection for %s and verifying "
                    "the connected scanner afterwards",
                    source,
                    exc_info=True,
                )
                fallback = getattr(
                    super(), "_async_get_best_available_backend_and_device", None,
                )
                if fallback is None:
                    raise BleakError(
                        "habluetooth no longer exposes the backend-selection hook "
                        "required for BLE source affinity"
                    )
                return fallback(*args, **kwargs)

    return _SourcePinnedHaBleakClient


# Connect-failure classes (0.87.2 audit). The SMP rejection the machine
# sends when its single bond slot doesn't match (auth fail reason=82) IS
# distinguishable HA-side: the proxy forwards it as
# BluetoothDevicePairingResponse(error=<reason>), bleak-esphome raises
# BleakError("Pairing failed due to error: <reason>") and
# bleak-retry-connector preserves the message and __cause__ chain.
# Canonical definitions live in const.py (the bond state machine consumes
# them too); imported at the top and re-exported from this module for
# backwards compatibility.


def resolve_caps_from_scanner(
    hass: HomeAssistant,
    address: str,
    brand: BrandProfile,
) -> MachineCapabilities | None:
    """Resolve MachineCapabilities from the HA bluetooth scanner cache.

    Useful at async_setup_entry time when the BLE client has not
    connected yet (client.capabilities is None). Uses O(1) address
    lookup via async_ble_device_from_address.
    """
    from homeassistant.components import bluetooth  # noqa: PLC0415

    try:
        ble_device = bluetooth.async_ble_device_from_address(
            hass, address.upper(), connectable=True,
        )
        if ble_device and ble_device.name:
            family = brand.detect_family(ble_device.name, None)
            if family:
                return brand.capabilities_for(family)
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Early caps resolution failed", exc_info=True)
    return None


# Melitta service UUID for BLE discovery
MELITTA_SERVICE_UUID = "0000ad00-b35c-11e4-9813-0002a5d5c51b"


class MelittaBleClient(BleCommandsMixin, BleRecipesMixin, BleSettingsMixin):
    """BLE client managing connection and communication with the machine.

    Follows the HA BLE integration pattern (switchbot/led_ble):
    - BLE advertisements track all visible scanner sources
    - Bond-aware source affinity prevents authenticated connections from roaming
    - Each connect/pair ladder freezes one BLEDevice and scanner source
    - Persistent connection with notification subscription for HX status
    """

    def __init__(
        self,
        address: str,
        device_name: str | None = None,
        ble_device: BLEDevice | None = None,
        *,
        ble_device_source: str | None = None,
        ble_source_affinity: str | None = None,
        ble_source_hint: str | None = None,
        source_migration_pending: bool = False,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        ble_connect_timeout: float = DEFAULT_BLE_CONNECT_TIMEOUT,
        frame_timeout: int = DEFAULT_FRAME_TIMEOUT,
        max_consecutive_errors: int = DEFAULT_MAX_CONSECUTIVE_ERRORS,
        reconnect_delay: float = DEFAULT_RECONNECT_DELAY,
        reconnect_max_delay: float = DEFAULT_RECONNECT_MAX_DELAY,
        recipe_retries: int = DEFAULT_RECIPE_RETRIES,
        pair_settle_delay: float = DEFAULT_PAIR_SETTLE_DELAY,
        repair_after_failures: int = DEFAULT_REPAIR_AFTER_FAILURES,
        auto_confirm_prompts: bool = False,
        brand: "BrandProfile | None" = None,
        family_override: str | None = None,
    ) -> None:
        self._address = address
        self._device_name = device_name
        self._ble_device: BLEDevice | None = ble_device
        self._ble_device_source: str | None = ble_device_source
        # Bond-aware adapter affinity. Once a connection succeeds, the
        # integration persists the scanner source that actually owns the bond
        # and feeds it back here on subsequent setups. Advertisements from
        # other scanners remain useful for presence/diagnostics but may not
        # replace the BLEDevice used for authenticated connections.
        self._ble_source_affinity: str | None = ble_source_affinity
        # Upgrade-only BlueZ hint. Unlike affinity, this is not persisted and
        # is dropped after AUTH evidence so Automatic mode can try another
        # scanner. Only a successful encrypted handshake promotes a source to
        # real affinity.
        self._ble_source_hint: str | None = (
            ble_source_hint if ble_source_affinity is None else None
        )
        self._source_migration_pending = bool(source_migration_pending)
        self._last_connected_source: str | None = None
        self._seen_sources: dict[str, float] = {}
        if ble_device_source:
            self._seen_sources[ble_device_source] = time.time()
        self._source_learned_callback: Callable[[str], Any] | None = None
        self._source_available_callback: Callable[[str], bool] | None = None
        self._connect_cycle_device: BLEDevice | None = None
        self._connect_cycle_source: str | None = None
        self._client: BleakClient | None = None
        if brand is None:
            from .brands import get_profile  # noqa: PLC0415
            brand = get_profile("melitta")
        self._brand: BrandProfile = brand
        self._protocol = MelittaProtocol(frame_timeout=frame_timeout, brand=brand)

        # Configurable parameters
        self._poll_interval = poll_interval
        self._ble_connect_timeout = ble_connect_timeout
        self._frame_timeout = frame_timeout
        self._max_consecutive_errors = max_consecutive_errors
        self._reconnect_delay = reconnect_delay
        self._reconnect_max_delay = reconnect_max_delay
        self._recipe_retries = recipe_retries
        self._pair_settle_delay = pair_settle_delay
        self._repair_after_failures = repair_after_failures
        # Counts consecutive failed connect() calls — used by the reconnect
        # loop to trigger a one-shot pairing recovery (ESPHome entry reload)
        # after too many failures in a row. Reset on every successful connect.
        self._consecutive_connect_failures: int = 0
        # Callback set by __init__.py during entry setup; invoked by the
        # reconnect loop when _consecutive_connect_failures hits the
        # threshold. Wires the integration's recovery routine without
        # giving ble_client.py a hass / config_entries dependency.
        self._repair_callback: Callable[[], Any] | None = None
        # Callback set by __init__.py; returns True when the device is
        # currently advertising (HA bluetooth.async_address_present). The
        # reconnect loop uses it to distinguish a powered-off / out-of-range
        # device (quiet wait, no wedge) from a genuinely wedged device that
        # keeps advertising. See issue #12.
        self._presence_callback: Callable[[], bool] | None = None
        # Callback set by __init__.py; performs a connection-less bond wipe
        # for this peer via the ESPHome proxy API (bluetooth_device_unpair,
        # handled firmware-side without an open link). It returns an explicit
        # UnpairOutcome so a known ESPHome response timeout can mean "request
        # executed, confirmation lost" without triggering a second unpair.
        # Lets _try_unpair work in the wedge state where no BLEDevice is cached
        # and connecting is impossible. See issue #35.
        self._unpair_callback: Callable[[], Any] | None = None
        # Callback set by __init__.py; returns True when a connectable
        # scanner looks starved (zero detections of ANY device for minutes).
        # Overrides the presence gate: "device absent" cannot be trusted
        # when the scanner itself has gone silent — that is exactly what a
        # wedged proxy looks like from HA. See issue #35.
        self._scanner_starved_callback: Callable[[], bool] | None = None
        self._connected = False
        # True once any BLE-level link was established during the CURRENT
        # connect cycle (reset at the top of _connect_impl). Used as the
        # bond-vs-unreachability discriminator: a link that opens and then
        # fails auth/handshake points at a bond problem; no link at all
        # points at a powered-off / out-of-range machine, where clearing
        # bonds is destructive (0.86.0 regression — see _suspect_stale_bond).
        self._ble_link_seen: bool = False
        # True once a classified AUTH-class failure (SMP rejection) was seen
        # during the CURRENT connect cycle — the only evidence that
        # authorizes the destructive unpair rung (0.87.2 audit; the previous
        # link-seen gate wiped a valid bond on transient failures).
        self._auth_fail_seen: bool = False
        # Class of the most recent connect failure (FAILURE_* constant) —
        # kept for logging and diagnostics.
        self._last_failure_class: str | None = None
        # Latch: the unpair rung already ran during the current disconnected
        # episode. Reset on a successful handshake. Repeating unpair within
        # one episode cannot help and re-wipes any freshly created bond.
        self._unpaired_this_episode: bool = False
        # True while __init__.py's initial-connect loop
        # (_async_connect_and_poll) is running — set_ble_device must then
        # only wake it instead of spawning _reconnect_loop alongside it
        # (dual-ladder hammering, 0.87.2 audit).
        self._external_loop_active: bool = False
        # True after the presence gate skipped an attempt because the device
        # was absent. The first advertisement wake-up after an absence is
        # honored even with a non-zero failure counter — the counter belongs
        # to the previous episode (e.g. evening power-off timeouts), and
        # ignoring the reappearance delayed the morning reconnect by up to
        # reconnect_max_delay (0.88.0b2 field case).
        self._was_absent: bool = False
        # Explicit bond-health authority (0.88). __init__.py replaces this
        # default with a persisted instance wired to repair-issue and
        # HA-event listeners; standalone/test usage keeps the bare one.
        self.bond = BondStateMachine()
        self._connect_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._brew_lock = asyncio.Lock()
        self._status: MachineStatus | None = None
        self._firmware: str | None = None
        self._serial: str | None = None
        self._features: FeatureFlags | None = None
        self._machine_type: MachineType | None = None
        self._dis_info: dict[str, str] = {}
        self._capabilities = None  # type: ignore[assignment]  # MachineCapabilities | None
        self._family_override: str | None = (family_override or None)
        self._auto_confirm_prompts: bool = auto_confirm_prompts
        self._last_auto_confirmed: Manipulation = Manipulation.NONE
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
        # MyCoffee bulk-read cache (PR E). Populated by
        # `read_mycoffee_slots` on Nivona connect. ``None`` until the
        # first successful (or partial) bulk read completes; once
        # populated, indexed by slot 0..N-1 with a dict of param
        # name → int value per slot. Missing keys inside a slot dict
        # mean that particular HR read failed and the value is not
        # known.
        self._my_coffee_slots: list[dict[str, int]] | None = None
        self._mycoffee_callbacks: list[Callable[[], None]] = []
        self._cups_callbacks: list[Callable[[], None]] = []
        # Called after a base recipe is (re-)read post-HD reset; consumers use
        # this to refresh cached attributes in select entities / attributes.
        self._recipe_refresh_callbacks: list[
            Callable[[int, "MachineRecipe"], None]
        ] = []

        # Profile data: names and DirectKey recipes per profile
        self._profile_names: dict[int, str] = {0: PROFILE_NAMES[0]}
        self._directkey_recipes: dict[int, dict[int, MachineRecipe]] = {}
        self._profile_callbacks: list[Callable[[], None]] = []

        # Client-side base-recipe cache (UI Contract §7.1 Zone I-A0):
        # raw MachineRecipe objects keyed by RecipeId int, filled by
        # every successful base-recipe read (post-connect preload,
        # on-demand select reads, post-HD refresh). The generation
        # counter moves on every (re)fill and is a contract_fingerprint
        # input for the UI contract builder.
        self.base_recipes: dict[int, MachineRecipe] = {}
        self.recipe_cache_generation: int = 0
        # UI Contract §3.10: cached setup-time result of the user-supplied
        # brand-logo file check ("/local/melitta_barista/<brand>.png" or
        # None). Set by async_setup_entry via executor I/O; fixed for the
        # life of the entry runtime and a contract_fingerprint input.
        self.brand_logo_url: str | None = None
        # Keep the cache current on post-HD re-reads: reset_recipe_default
        # notifies refresh callbacks with the fresh recipe. Registered
        # before any entity subscriber so consumers always observe an
        # already-updated cache.
        self._recipe_refresh_callbacks.append(self._store_refreshed_base_recipe)

        # Freestyle recipe state (used by freestyle entities)
        self.freestyle_name: str = "Custom"
        self.freestyle_process1: str = "coffee"
        self.freestyle_intensity1: str = "medium"
        self.freestyle_aroma1: str = "standard"
        self.freestyle_portion1_ml: int = 40
        self.freestyle_temperature1: str = "normal"
        self.freestyle_shots1: str = "one"
        self.freestyle_blend1: str = "hopper_1"
        self.freestyle_process2: str = "none"
        self.freestyle_intensity2: str = "medium"
        self.freestyle_aroma2: str = "standard"
        self.freestyle_portion2_ml: int = 0
        self.freestyle_temperature2: str = "normal"
        self.freestyle_shots2: str = "none"
        self.freestyle_blend2: str = "hopper_1"

        # BLE pairing state: skip pair=True on reconnect if already bonded
        self._paired = False

        # Diagnostic ring buffers (consumed by panel_api /diagnostics).
        # Bounded so memory can't grow unbounded on long-running setups.
        self._recent_errors: deque[dict] = deque(maxlen=50)
        self._recent_frames: deque[dict] = deque(maxlen=100)
        self._last_handshake_at: float | None = None

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
    def serial_number(self) -> str | None:
        """Machine serial number (read via HL on connect).

        ``None`` until the first successful read or if the machine never
        answered HL on this firmware.
        """
        return self._serial

    @property
    def brand(self) -> "BrandProfile":
        """Active BrandProfile for this entry (Melitta / Nivona / …)."""
        return self._brand

    @property
    def features(self) -> FeatureFlags | None:
        """Machine capability bits (read via HI once at connect).

        ``None`` means the firmware did not answer HI within the timeout
        — not all machines support this command.
        """
        return self._features

    @property
    def auto_confirm_prompts(self) -> bool:
        return self._auto_confirm_prompts

    def set_auto_confirm_prompts(self, value: bool) -> None:
        """Update the auto-confirm flag (used by Options Flow listener)."""
        self._auto_confirm_prompts = bool(value)
        if not value:
            self._last_auto_confirmed = Manipulation.NONE

    @property
    def ble_source_affinity(self) -> str | None:
        """Scanner source that is allowed to own authenticated connections."""
        return self._ble_source_affinity

    @property
    def ble_source_hint(self) -> str | None:
        """Temporary, unpersisted BlueZ bond-source hint."""
        return self._ble_source_hint

    @property
    def source_migration_pending(self) -> bool:
        """Whether the configured source differs from the proven bond owner."""
        return self._source_migration_pending

    @property
    def ble_device_source(self) -> str | None:
        """Scanner source associated with the currently cached BLEDevice."""
        return self._ble_device_source

    @property
    def last_connected_source(self) -> str | None:
        """Scanner source that completed the most recent handshake."""
        return self._last_connected_source

    @property
    def seen_ble_sources(self) -> dict[str, float]:
        """Return a copy of scanner sources that recently saw the machine."""
        return dict(self._seen_sources)

    def set_ble_source_affinity(self, source: str | None) -> None:
        """Restrict future authenticated connections to ``source``.

        ``None`` means the source is not known yet. In that bootstrap state a
        connect cycle freezes whichever BLEDevice/source it starts with; the
        first successful encrypted handshake then becomes the affinity source.
        """
        if _norm_ble_source(source) == _norm_ble_source(self._ble_source_affinity):
            self._ble_source_affinity = source
            return
        old_source = self._ble_source_affinity
        self._ble_source_affinity = source
        if source is not None:
            self._ble_source_hint = None
        _LOGGER.info(
            "BLE source affinity for %s changed from %s to %s",
            self._address, old_source or "unbound", source or "unbound",
        )
        if (
            source is not None
            and _norm_ble_source(self._ble_device_source) != _norm_ble_source(source)
        ):
            # Never carry a BLEDevice from another central across an affinity
            # change: its backend details identify that central/proxy.
            self._ble_device = None
            self._ble_device_source = None
            self._connect_cycle_device = None
            self._connect_cycle_source = None

    def set_source_learned_callback(
        self, callback: Callable[[str], Any] | None,
    ) -> None:
        """Install callback used to persist a successfully proven source."""
        self._source_learned_callback = callback

    def set_source_available_callback(
        self, callback: Callable[[str], bool] | None,
    ) -> None:
        """Install callback that reports whether an affinity scanner exists."""
        self._source_available_callback = callback

    def set_repair_callback(self, callback: Callable[[], Any] | None) -> None:
        """Install the recovery routine the reconnect loop calls on wedge.

        Wired in __init__.py:async_setup_entry to a closure that reloads the
        ESPHome config entry owning the proxy. Callback may be a coroutine
        function or a plain callable; coroutines are scheduled as a task.
        """
        self._repair_callback = callback

    def set_presence_callback(self, callback: Callable[[], bool] | None) -> None:
        """Install a callback reporting whether the device is advertising.

        Wired in __init__.py to ``bluetooth.async_address_present``. The
        reconnect loop consults it before counting a failed connect toward
        the pairing-wedge threshold: a device that is not advertising is
        powered off or out of range, not wedged. Kept as a callback so
        ble_client.py stays free of a hass dependency.
        """
        self._presence_callback = callback

    def set_unpair_callback(self, callback: Callable[[], Any] | None) -> None:
        """Install a connection-less bond-wipe routine for this peer.

        Wired in __init__.py to the ESPHome proxy API
        (``bluetooth_device_unpair``), which the proxy firmware handles
        without an open BLE link. ``_try_unpair`` tries this first, so a
        stale bond can be cleared even when no BLEDevice is cached — the
        exact state the issue-#35 proxy wedge produces. The callback is an
        async callable returning truthy on success.
        """
        self._unpair_callback = callback

    def set_scanner_starved_callback(
        self, callback: Callable[[], bool] | None,
    ) -> None:
        """Install a callback reporting whether a connectable scanner is starved.

        Wired in __init__.py to a check over HA's registered scanners
        (``time_since_last_detection``). When the device looks absent BUT a
        scanner has seen nothing at all for minutes, the absence cannot be
        trusted — a wedged proxy stops forwarding every advertisement — so
        the reconnect loops keep attempting (and counting failures) instead
        of waiting forever. See issue #35.
        """
        self._scanner_starved_callback = callback

    @property
    def consecutive_connect_failures(self) -> int:
        """How many connect() calls in a row have failed since last success."""
        return self._consecutive_connect_failures

    @property
    def machine_type(self) -> MachineType | None:
        return self._machine_type

    @property
    def model_name(self) -> str:
        # Prefer brand/family-resolved capability name (v0.43.0+)
        if self._capabilities is not None:
            return self._capabilities.model_name
        # DIS-provided model, if machine advertised one
        if self._dis_info.get("model"):
            return self._dis_info["model"]
        # Legacy Melitta machine-type table (Melitta machines only).
        brand_name = self._brand.brand_name if hasattr(self, "_brand") else "Coffee"
        if self._machine_type:
            return MACHINE_MODEL_NAMES.get(
                self._machine_type, f"{brand_name} Coffee Machine",
            )
        return f"{brand_name} Coffee Machine"

    @property
    def capabilities(self):
        """Resolved MachineCapabilities (family-level + per-model overrides)."""
        return self._capabilities

    @property
    def dis_info(self) -> dict[str, str]:
        """Device Information Service read snapshot (empty if not yet read)."""
        return dict(self._dis_info)

    @property
    def total_cups(self) -> int | None:
        return self._total_cups

    @property
    def cup_counters(self) -> dict[str, int]:
        return self._cup_counters

    @property
    def my_coffee_slots(self) -> list[dict[str, int]] | None:
        """Cached MyCoffee slot params, or ``None`` if not yet read.

        Indexed by slot number 0..N-1; each slot dict maps a param
        name (currently only ``coffee_amount``) to the last value read
        from the machine. A param key missing inside a slot dict means
        the corresponding HR read failed.
        """
        return self._my_coffee_slots

    def add_mycoffee_callback(self, callback: Callable[[], None]) -> None:
        self._mycoffee_callbacks.append(callback)

    def remove_mycoffee_callback(self, callback: Callable[[], None]) -> None:
        if callback in self._mycoffee_callbacks:
            self._mycoffee_callbacks.remove(callback)

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

    def add_recipe_refresh_callback(
        self, callback: Callable[[int, MachineRecipe], None],
    ) -> None:
        self._recipe_refresh_callbacks.append(callback)

    def remove_recipe_refresh_callback(
        self, callback: Callable[[int, MachineRecipe], None],
    ) -> None:
        try:
            self._recipe_refresh_callbacks.remove(callback)
        except ValueError:
            pass

    def set_ble_device(
        self, ble_device: BLEDevice, *, source: str | None = None,
    ) -> bool:
        """Update the connectable BLEDevice from an advertisement.

        When source affinity is active, advertisements from other scanners are
        deliberately *observed but not adopted*. A ``BLEDevice`` contains
        backend/proxy-specific connection details, so replacing it with one
        from another scanner would silently move a bonded peer to a different
        BLE central and provoke an SMP authentication failure.

        Returns ``True`` when the device was accepted for future connections.
        """
        if source is None:
            details = getattr(ble_device, "details", None)
            if isinstance(details, dict):
                raw_source = details.get("source")
                if raw_source:
                    source = str(raw_source)
        if source:
            self._seen_sources[source] = time.time()

        affinity = self._ble_source_affinity
        preferred_source = affinity or self._ble_source_hint
        if (
            preferred_source is not None
            and _norm_ble_source(source) != _norm_ble_source(preferred_source)
        ):
            _LOGGER.debug(
                "Ignoring BLEDevice update for %s from source %s; "
                "bond affinity is %s",
                self._address, source or "unknown", preferred_source,
            )
            # HA suppresses duplicate Bluetooth callbacks and may surface an
            # advertisement from the best-scoring scanner rather than the bond
            # owner. A foreign-source advertisement is still useful as a wake
            # signal: the reconnect loop will query the owner-specific scanner
            # cache through its presence callback before attempting a connect.
            if not self._connected and self._auto_reconnect:
                self._reconnect_event.set()
                if (
                    not self._external_loop_active
                    and (not self._reconnect_task or self._reconnect_task.done())
                ):
                    self._schedule_reconnect()
            return False

        self._ble_device = ble_device
        self._ble_device_source = source
        if not self._connected and self._auto_reconnect:
            _LOGGER.info(
                "BLE advertisement from %s via %s while disconnected, "
                "triggering reconnect",
                self._address, source or "unknown source",
            )
            self._reconnect_event.set()
            # Only schedule reconnect if no loop is already running
            # (_async_connect_and_poll or _reconnect_loop already listens
            # on _reconnect_event, so set() alone wakes them up). The
            # initial-connect loop is external (a hass background task) and
            # invisible via _reconnect_task — its activity flag prevents
            # spawning a SECOND ladder alongside it (0.87.2 audit).
            if not self._external_loop_active and (
                not self._reconnect_task or self._reconnect_task.done()
            ):
                self._schedule_reconnect()
        return True

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
        self._maybe_auto_confirm(status)
        for cb in self._status_callbacks:
            try:
                cb(status)
            except Exception:  # noqa: BLE900 — callback from user code
                _LOGGER.exception("Error in status callback")

    def _maybe_auto_confirm(self, status: MachineStatus) -> None:
        """Auto-fire HY for soft prompts when the global option is enabled.

        Debounced: each soft prompt is auto-confirmed once per "appearance"
        — we don't re-trigger while the same code is still being reported
        by the machine.
        """
        if not self._auto_confirm_prompts:
            return
        manip = status.manipulation
        if manip not in SOFT_AUTO_CONFIRM_MANIPULATIONS:
            # Reset debounce when the prompt clears or switches to a non-soft one
            self._last_auto_confirmed = Manipulation.NONE
            return
        if manip == self._last_auto_confirmed:
            return
        self._last_auto_confirmed = manip
        _LOGGER.info("Auto-confirming prompt %s", manip.name)
        task = asyncio.create_task(self._auto_confirm_task(manip))
        task.add_done_callback(self._on_auto_confirm_done)

    async def _auto_confirm_task(self, manip: Manipulation) -> None:
        try:
            success = await self.confirm_prompt()
            if not success:
                _LOGGER.warning(
                    "Auto-confirm %s: NACK or timeout", manip.name,
                )
        except (BleakError, OSError, asyncio.TimeoutError):
            _LOGGER.exception("Auto-confirm %s failed", manip.name)

    @staticmethod
    def _on_cup_refresh_done(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            _LOGGER.debug("Cup counter refresh failed: %s", exc)

    @staticmethod
    def _on_auto_confirm_done(task: asyncio.Task) -> None:
        """Done-callback for fire-and-forget _auto_confirm_task.

        Without this, an unexpected exception inside the task is swallowed
        into asyncio's "Task exception was never retrieved" log line which
        is invisible in HA's own logs; keeping a reference also prevents
        the task from being garbage-collected mid-flight under aggressive
        event-loop GC.
        """
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            _LOGGER.error("Auto-confirm task crashed", exc_info=exc)

    def _on_disconnect(self, client: BleakClient) -> None:
        if self._disconnecting:
            return
        if client is not self._client:
            _LOGGER.debug("Ignoring disconnect callback from stale client")
            return
        _LOGGER.info("Disconnected from %s", self._address)
        self.record_error("ble", f"Disconnected from {self._address}")
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
        # Keep a short hex preview for the diagnostics tab. We avoid storing
        # full frames to cap memory; the first ~32 bytes are enough to spot
        # what command came through.
        if data:
            self._recent_frames.append({
                "ts": time.time(),
                "len": len(data),
                "hex": bytes(data[:32]).hex(),
            })

    def record_error(self, source: str, message: str) -> None:
        """Append a diagnostic error entry surfaced through the panel.

        Public so other modules (mixins, ble_agent, etc.) can append context
        without touching the underlying deque directly.
        """
        self._recent_errors.append({
            "ts": time.time(),
            "source": source,
            "message": message,
        })

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

        Inside Home Assistant we MUST use bleak_retry_connector.establish_connection
        (declared in manifest.json requirements). Calling raw BleakClient.connect()
        triggers a habluetooth.wrappers warning, bypasses BlueZ slot management
        (hurting other BT integrations), and skips internal transient-error retries.

        - If bleak_retry_connector is unavailable (tests / standalone scripts) we
          fall back to raw BleakClient.connect() — never reached inside HA.
        - If we have no cached BLEDevice we raise NoBleDeviceError so
          _connect_impl aborts the whole ladder and the reconnect loop waits
          for an advertisement (via set_ble_device) instead of blocking on
          raw BleakClient.connect() with a 30 s timeout.
        - If establish_connection raises we propagate — it already retries
          internally (max_attempts=3); a raw fallback would just double the work.
        """
        try:
            from bleak_retry_connector import (
                BleakClientWithServiceCache,
                establish_connection,
            )
        except ImportError:
            # Tests / standalone scripts: bleak_retry_connector not installed.
            # Inside HA this branch is unreachable (manifest requirement).
            _LOGGER.debug(
                "bleak_retry_connector unavailable, using raw BleakClient for %s",
                self._address,
            )
            return await self._raw_connect(pair=pair)

        # Freeze the device/source for the whole connect ladder. The old
        # ble_device_callback returned ``self._ble_device`` dynamically, which
        # allowed advertisements from a stronger proxy to move a retry to a
        # different BLE central mid-pairing. Bonded devices must never roam
        # between centrals inside one authentication attempt.
        ble_device = self._connect_cycle_device or self._ble_device
        source = self._connect_cycle_source or self._ble_device_source
        if ble_device is None:
            # establish_connection() requires a real BLEDevice. Without one,
            # raise — the reconnect loop will wait for an advertisement
            # (set_ble_device sets _reconnect_event) instead of burning a
            # 30 s connect timeout per attempt on a raw BleakClient. The
            # typed error lets _connect_impl short-circuit the whole ladder.
            raise NoBleDeviceError(
                f"No BLEDevice cached for {self._address}; "
                "waiting for advertisement"
            )

        _LOGGER.debug(
            "Using establish_connection for %s (pair=%s, source=%s, "
            "ble_device=%s)",
            self._address, pair, source or "unknown", ble_device,
        )
        client_class = BleakClientWithServiceCache
        if source:
            try:
                client_class = _source_pinned_ha_client_class(source)
            except ImportError:
                # Standalone/tests without habluetooth keep the old path. HA
                # always has habluetooth, so production source affinity is
                # enforced by the pinned wrapper above.
                _LOGGER.debug(
                    "habluetooth unavailable; cannot enforce source pin %s for %s",
                    source, self._address,
                )

        client = await establish_connection(
            client_class,
            ble_device,
            self._device_name or self._address,
            disconnected_callback=self._on_disconnect,
            use_services_cache=True,
            ble_device_callback=lambda: ble_device,
            max_attempts=3,
            pair=pair,
        )

        if source:
            # The private hook above is guarded because habluetooth changes
            # frequently. If it ever falls back to normal HA backend choice,
            # enforce the source invariant after the link comes up before any
            # Melitta authentication/handshake traffic is sent.
            connected_scanner = getattr(client, "_connected_scanner", None)
            connected_source = getattr(connected_scanner, "source", None)
            if (
                connected_source is None
                or _norm_ble_source(connected_source) != _norm_ble_source(source)
            ):
                try:
                    await client.disconnect()
                except (BleakError, OSError):
                    pass
                if connected_source is None:
                    raise BleakError(
                        "Could not verify the Bluetooth scanner used for "
                        f"source-pinned connection to {self._address}"
                    )
                raise BleakError(
                    f"Bluetooth connection for {self._address} landed on "
                    f"{connected_source}, expected pinned source {source}"
                )

        return client

    async def _raw_connect(self, *, pair: bool) -> BleakClient:
        """Last-resort raw BleakClient connect — only for tests/standalone.

        Inside HA bleak_retry_connector is always available (manifest requirement),
        so this path is never taken from HA. Kept so the client class works in
        unit tests and CLI scripts without bleak_retry_connector installed.
        """
        ble_device = self._connect_cycle_device or self._ble_device
        client = BleakClient(
            ble_device or self._address,
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
            # Freeze one central for the complete ladder (pair=False ->
            # pair=True -> optional unpair -> pair=True). Advertisements may
            # continue arriving while it runs, but they cannot move an SMP
            # exchange to a different adapter/proxy mid-cycle.
            self._connect_cycle_device = self._ble_device
            self._connect_cycle_source = self._ble_device_source
            try:
                return await self._connect_impl()
            finally:
                cycle_source = self._connect_cycle_source
                if (
                    self._ble_source_affinity is None
                    and self._ble_source_hint is not None
                    and self._auth_fail_seen
                    and _norm_ble_source(cycle_source)
                    == _norm_ble_source(self._ble_source_hint)
                ):
                    _LOGGER.warning(
                        "BlueZ bond-source hint %s failed authentication for %s; "
                        "dropping the hint and returning to Automatic discovery",
                        self._ble_source_hint,
                        self._address,
                    )
                    self._ble_source_hint = None
                    if _norm_ble_source(self._ble_device_source) == _norm_ble_source(
                        cycle_source
                    ):
                        self._ble_device = None
                        self._ble_device_source = None
                self._connect_cycle_device = None
                self._connect_cycle_source = None

    async def _try_connect_and_handshake(self, *, pair: bool) -> bool:
        """Try to establish BLE connection and perform HU handshake.

        Returns True on success, False on failure (cleans up client).
        """
        self._protocol = MelittaProtocol(frame_timeout=self._frame_timeout, brand=self._brand)
        self._protocol.set_status_callback(self._on_status)

        try:
            self._client = await self._establish_connection(pair=pair)
        except NoBleDeviceError:
            # Propagate so _connect_impl can abort the whole ladder — the
            # remaining rungs would fail identically without a BLEDevice.
            self._client = None
            raise
        except (BleakError, OSError, asyncio.TimeoutError) as err:
            self._record_connect_failure(err)
            if pair:
                _LOGGER.warning(
                    "BLE pairing/connect failed for %s via %s "
                    "(class=%s, %s: %s)",
                    self._address,
                    self._connect_cycle_source
                    or self._ble_device_source
                    or self._ble_source_affinity
                    or "automatic",
                    self._last_failure_class,
                    type(err).__name__,
                    err,
                )
            else:
                _LOGGER.debug(
                    "BLE connect failed (pair=%s, class=%s)",
                    pair, self._last_failure_class, exc_info=True,
                )
            self._client = None
            return False

        if not self._client.is_connected:
            _LOGGER.debug("BLE client not connected after establish (pair=%s)", pair)
            self._client = None
            return False

        _LOGGER.debug("BLE connected to %s (pair=%s)", self._address, pair)
        # Evidence for _suspect_stale_bond: the radio path works, so any
        # failure from here on is auth/handshake-class, not unreachability.
        self._ble_link_seen = True

        try:
            await self._start_notify(self._client)
        except (BleakError, OSError, asyncio.TimeoutError) as err:
            self._record_connect_failure(err)
            _LOGGER.debug(
                "start_notify failed (pair=%s, class=%s)",
                pair, self._last_failure_class, exc_info=True,
            )
            await self._safe_disconnect()
            return False

        if not await self._protocol.perform_handshake(self._write_ble):
            self._last_failure_class = FAILURE_HANDSHAKE
            _LOGGER.debug("HU handshake failed (pair=%s)", pair)
            await self._safe_disconnect()
            return False

        return True

    @staticmethod
    def _classify_connect_error(err: BaseException) -> str:
        """Classify a connect-ladder exception into a FAILURE_* class.

        Walks the ``__cause__``/``__context__`` chain because
        bleak-retry-connector wraps the original bleak-esphome error while
        preserving both the message and the chain. AUTH takes priority: an
        SMP rejection anywhere in the chain is bond-class evidence
        regardless of how it was re-wrapped.
        """
        seen: set[int] = set()
        chain: list[BaseException] = []
        node: BaseException | None = err
        while node is not None and id(node) not in seen and len(chain) < 10:
            seen.add(id(node))
            chain.append(node)
            node = node.__cause__ or node.__context__
        for exc in chain:
            text = str(exc)
            if "Pairing failed" in text or "auth fail" in text:
                return FAILURE_AUTH
        for exc in chain:
            if isinstance(exc, TimeoutError):
                return FAILURE_TIMEOUT
        return FAILURE_LINK

    def _record_connect_failure(self, err: BaseException) -> None:
        """Record the classified failure; AUTH-class arms the unpair gate."""
        cls = self._classify_connect_error(err)
        self._last_failure_class = cls
        if cls == FAILURE_AUTH:
            self._auth_fail_seen = True

    def _suspect_stale_bond(self) -> bool:
        """True when bond-mismatch evidence justifies the destructive unpair.

        Delegates to the BondStateMachine (0.88): destruction requires
        MISMATCH-grade evidence — at least two distinct connect cycles that
        ended in a classified SMP/auth rejection (``Pairing failed due to
        error: 82`` et al), counting the cycle currently in flight.

        History of this gate (three regressions):
        - presence-based (0.86.1) — racy, habluetooth keeps a device
          "present" ~195 s after power-off → wiped a sleeping machine's bond;
        - link-seen-based (0.86.3) — too broad, a transient notify/handshake
          failure with the machine ON wiped a valid bond (field case Jay),
          and too narrow at once: a genuine SMP rejection without our link
          flag skipped the legitimate unpair;
        - single-cycle auth (0.87.2) — correct class, but one cycle of
          evidence still allowed a single spurious classification to
          destroy a bond; the state machine demands a repeat.
        When in doubt, do NOT unpair: a skipped unpair costs one extra
        reconnect cycle, a wrong unpair costs the user a manual re-pairing
        session at the machine.
        """
        if self._source_migration_pending:
            _LOGGER.info(
                "Skipping bond-clear for %s: BLE source migration is pending",
                self._address,
            )
            return False
        if self._ble_source_affinity is None:
            # Until a successful handshake proves which central owns the bond,
            # an auth rejection may simply mean HA happened to try a different
            # proxy. Destructive unpair is forbidden in this bootstrap state.
            _LOGGER.info(
                "Skipping bond-clear for %s: BLE bond source is not proven yet",
                self._address,
            )
            return False
        return self.bond.allow_unpair(current_cycle_auth=self._auth_fail_seen)

    async def _safe_disconnect(self) -> None:
        """Disconnect current client, suppressing errors."""
        client = self._client
        self._client = None
        if client:
            try:
                await client.disconnect()
            except (BleakError, OSError):
                pass

    async def _try_unpair(self) -> UnpairOutcome:
        """Attempt one stale-bond removal without ever double-wiping.

        ESPHome proxy firmware is known to execute ``UNPAIR`` and then answer
        with the wrong message type, causing aioesphomeapi to time out. That
        timeout is therefore an *attempted but unconfirmed* destructive
        operation, not a reason to send a second unpair. Once a destructive
        request may have executed we latch the episode, record
        PAIRING_REQUIRED/audit evidence, and allow only the final non-
        destructive ``pair=True`` rung.
        """

        def _record_attempt(outcome: UnpairOutcome, *, op: str) -> UnpairOutcome:
            self._unpaired_this_episode = True
            self.bond.on_bond_destroyed(op=op, trigger="rung3")
            return outcome

        if self._unpair_callback is not None:
            try:
                outcome = await self._unpair_callback()
            except Exception:  # noqa: BLE001 — request may already have left HA
                _LOGGER.warning(
                    "Connection-less unpair callback failed for %s after it "
                    "may have sent a destructive request; not sending a second "
                    "unpair",
                    self._address,
                    exc_info=True,
                )
                return _record_attempt(
                    UnpairOutcome.ATTEMPTED_UNCONFIRMED,
                    op="proxy_unpair_attempted_unconfirmed",
                )

            # Backwards compatibility for third-party/tests callbacks that
            # still return bool while the integration migrates to the explicit
            # three-state contract.
            if outcome is True:
                outcome = UnpairOutcome.CONFIRMED
            elif outcome is False or outcome is None:
                outcome = UnpairOutcome.NOT_SENT
            elif not isinstance(outcome, UnpairOutcome):
                try:
                    outcome = UnpairOutcome(str(outcome))
                except ValueError:
                    outcome = UnpairOutcome.ATTEMPTED_UNCONFIRMED

            if outcome is UnpairOutcome.CONFIRMED:
                _LOGGER.info(
                    "Cleared bond for %s via proxy API (connection-less)",
                    self._address,
                )
                return _record_attempt(outcome, op="proxy_unpair")

            if outcome is UnpairOutcome.ATTEMPTED_UNCONFIRMED:
                _LOGGER.warning(
                    "Proxy UNPAIR for %s was sent but not confirmed; treating "
                    "the bond as requiring pairing, sending no further "
                    "destructive request, and continuing to pair=True",
                    self._address,
                )
                return _record_attempt(
                    outcome, op="proxy_unpair_attempted_unconfirmed",
                )

            _LOGGER.debug(
                "Connection-less unpair for %s was not sent; trying the "
                "connect-then-unpair fallback once",
                self._address,
            )

        # No connection-less request was sent. The legacy fallback is still
        # useful for a local BlueZ central. A failure before ``unpair()`` is
        # invoked remains NOT_SENT; once ``unpair()`` has been called, any
        # lost response is ATTEMPTED_UNCONFIRMED and must not trigger another
        # destructive operation.
        try:
            _LOGGER.info("Clearing stale bond for %s", self._address)
            client = await self._establish_connection(pair=False)
        except (BleakError, OSError, asyncio.TimeoutError) as err:
            _LOGGER.warning(
                "Could not connect to %s for fallback unpair (%s: %s); "
                "no destructive request was sent",
                self._address,
                type(err).__name__,
                err,
            )
            return UnpairOutcome.NOT_SENT

        try:
            try:
                await client.unpair()
            except NotImplementedError as err:
                # Backend explicitly says it cannot perform unpairing. No
                # destructive operation was dispatched, so recovery must not
                # arm the once-per-episode destruction latch.
                _LOGGER.warning(
                    "unpair() is not implemented for %s (%s); no destructive "
                    "request was sent",
                    self._address,
                    err,
                )
                return UnpairOutcome.NOT_SENT
            except (
                BleakError,
                OSError,
                asyncio.TimeoutError,
                AttributeError,
            ) as err:
                _LOGGER.warning(
                    "unpair() for %s did not return a usable confirmation "
                    "(%s: %s); the request may have executed, so no second "
                    "destructive request will be sent",
                    self._address,
                    type(err).__name__,
                    err,
                )
                return _record_attempt(
                    UnpairOutcome.ATTEMPTED_UNCONFIRMED,
                    op="connect_unpair_attempted_unconfirmed",
                )

            _LOGGER.info("Unpaired %s successfully", self._address)
            return _record_attempt(
                UnpairOutcome.CONFIRMED, op="connect_unpair",
            )
        finally:
            try:
                await client.disconnect()
            except (BleakError, OSError):
                pass

    async def _connect_impl(self) -> bool:
        """Internal connect implementation (must be called under _connect_lock).

        Pairing strategy:
        1. Try pair=False first (fast — reuses existing bond on ESP32/BlueZ).
        2. If handshake fails, retry with pair=True (first-ever or bond lost).
        3. If pair=True also fails, unpair (clear stale bond) then pair=True again.

        The whole ladder short-circuits on NoBleDeviceError (issue #35):
        without a cached BLEDevice every rung fails identically, so we log
        one line and wait for the next advertisement instead.

        Rung 3 (unpair) additionally requires bond-class evidence — see
        _suspect_stale_bond. On plain unreachability (machine powered off)
        clearing the proxy bond would orphan the machine-side LTK and turn
        a transient outage into a permanent `auth fail reason=82` mismatch.
        """
        if self._connected and self._client and self._client.is_connected:
            return True

        self._ble_link_seen = False
        self._auth_fail_seen = False

        # Cancel any pending reconnect task to avoid interference with retry logic
        # (but skip if WE are the reconnect task — otherwise we cancel ourselves)
        if self._reconnect_task and not self._reconnect_task.done():
            if asyncio.current_task() is not self._reconnect_task:
                self._reconnect_task.cancel()
                self._reconnect_task = None

        try:
            _LOGGER.info(
                "Connecting to %s machine at %s via BLE source %s",
                self._brand.brand_name,
                self._address,
                self._connect_cycle_source
                or self._ble_device_source
                or self._ble_source_affinity
                or "automatic",
            )

            # Attempt 1: without pairing (reuse existing bond)
            if await self._try_connect_and_handshake(pair=False):
                self._paired = True
            else:
                # On ESPHome/BlueZ, pair=True is also the normal way to
                # re-establish encryption with an already-bonded peer.  It does
                # NOT by itself delete or replace the bond.  Therefore a proven
                # bond owner must still be allowed to run the pair=True rung
                # after pair=False fails.  Source affinity makes this safe: the
                # attempt cannot roam to another central, and the destructive
                # unpair rung below remains gated by repeated AUTH evidence.
                # Settle delay: let the ESP proxy / BlueZ release the previous
                # connection slot before we initiate a fresh pair=True. Without
                # this gap we routinely hit a 60 s
                # `TimeoutAPIError waiting for BluetoothDevicePairingResponse`
                # because the proxy is still holding the BLE socket from the
                # pair=False attempt that just collapsed.
                if self._pair_settle_delay > 0:
                    _LOGGER.debug(
                        "Settling for %.1fs before retrying with pair=True",
                        self._pair_settle_delay,
                    )
                    await asyncio.sleep(self._pair_settle_delay)

                # Attempt 2: with pairing (create new bond)
                _LOGGER.info(
                    "Retrying connection to %s with pairing/authentication "
                    "on BLE source %s",
                    self._address,
                    self._connect_cycle_source
                    or self._ble_device_source
                    or self._ble_source_affinity
                    or "automatic",
                )
                if not await self._try_connect_and_handshake(pair=True):
                    if self._source_migration_pending:
                        # A manually selected replacement central is allowed to
                        # forget *its own* stale local bond before pairing. This
                        # does not touch the previous bond owner because the
                        # unpair callback is source-affine. It is important when
                        # migrating back to a proxy/adapter that was used in the
                        # past and still has an obsolete LTK.
                        if not self._auth_fail_seen:
                            _LOGGER.info(
                                "Bluetooth source migration for %s failed "
                                "without SMP/auth rejection; keeping the "
                                "target bond intact and waiting to retry",
                                self._address,
                            )
                            return False
                        if self._unpaired_this_episode:
                            _LOGGER.info(
                                "Bluetooth source migration for %s already "
                                "cleared the target bond once this episode; "
                                "waiting for pairing mode",
                                self._address,
                            )
                            return False
                        _LOGGER.info(
                            "Clearing stale bond on migration target %s for %s",
                            self._connect_cycle_source
                            or self._ble_device_source
                            or "unknown source",
                            self._address,
                        )
                        unpair_outcome = await self._try_unpair()
                        if unpair_outcome is UnpairOutcome.NOT_SENT:
                            _LOGGER.warning(
                                "Bluetooth source migration for %s cannot "
                                "continue: no stale-bond removal request could "
                                "be sent to target %s",
                                self._address,
                                self._connect_cycle_source
                                or self._ble_device_source
                                or "unknown source",
                            )
                            return False
                        if self._pair_settle_delay > 0:
                            await asyncio.sleep(self._pair_settle_delay)
                        _LOGGER.info(
                            "Retrying Bluetooth source migration for %s after "
                            "target unpair outcome %s",
                            self._address, unpair_outcome.value,
                        )
                        if not await self._try_connect_and_handshake(pair=True):
                            _LOGGER.error(
                                "Bluetooth source migration failed for %s",
                                self._address,
                            )
                            return False
                    else:
                        if not self._suspect_stale_bond():
                            # No SMP/auth rejection seen — the failures are
                            # transient (timeout / link / handshake), and
                            # clearing bonds on transients orphans the
                            # machine-side key (0.87.2 audit, field case Jay).
                            _LOGGER.info(
                                "Skipping bond-clear for %s: no SMP/auth "
                                "rejection this cycle (last failure class: %s)",
                                self._address, self._last_failure_class,
                            )
                            return False
                        if self._unpaired_this_episode:
                            # One wipe per episode: repeating cannot help and
                            # would re-wipe any bond a parallel re-pair created.
                            _LOGGER.info(
                                "Skipping bond-clear for %s: already cleared "
                                "once this episode — machine-side reset is "
                                "required to recover from a persistent SMP "
                                "rejection",
                                self._address,
                            )
                            return False
                        # Attempt 3: unpair stale bond, then pair fresh. A
                        # known ESPHome timeout means the destructive request
                        # executed but its response was unusable; that outcome
                        # still proceeds to the final non-destructive pair=True
                        # while the episode latch prevents another unpair.
                        unpair_outcome = await self._try_unpair()
                        if unpair_outcome is UnpairOutcome.NOT_SENT:
                            _LOGGER.warning(
                                "Cannot retry pairing for %s: no stale-bond "
                                "removal request could be sent",
                                self._address,
                            )
                            return False
                        # Same settle delay between unpair-flush and the final
                        # pair=True attempt, for the same reason as above.
                        if self._pair_settle_delay > 0:
                            await asyncio.sleep(self._pair_settle_delay)
                        _LOGGER.info(
                            "Retrying connection to %s after unpair outcome %s",
                            self._address, unpair_outcome.value,
                        )
                        if not await self._try_connect_and_handshake(pair=True):
                            _LOGGER.error("Connection failed for %s", self._address)
                            return False
                self._paired = True

            self._connected = True
            self._last_handshake_at = time.time()
            # Connect succeeded — reset the failure counter so the next outage
            # gets a fresh threshold instead of immediately triggering repair.
            self._consecutive_connect_failures = 0
            # New episode: the unpair rung is available again (see
            # _unpaired_this_episode).
            self._unpaired_this_episode = False
            self._was_absent = False
            # The encrypted handshake proves the bond — back to TRUSTED.
            self.bond.on_handshake_success()

            # The source that completed this frozen connect cycle is now
            # cryptographically proven to be usable for this bond. In automatic
            # mode the first success becomes the affinity source; in manual
            # mode the callback still persists the successful source so a
            # later switch back to Automatic keeps the migrated bond owner.
            connected_source = self._connect_cycle_source or self._ble_device_source
            if connected_source:
                self._last_connected_source = connected_source
                self._source_migration_pending = False
                if self._ble_source_affinity is None:
                    self.set_ble_source_affinity(connected_source)
                if self._source_learned_callback is not None:
                    try:
                        learned = self._source_learned_callback(connected_source)
                        if asyncio.iscoroutine(learned):
                            asyncio.create_task(learned)
                    except Exception:
                        _LOGGER.exception(
                            "BLE source persistence callback failed for %s",
                            self._address,
                        )
            _LOGGER.info(
                "Connected and handshake complete for %s via %s",
                self._address, connected_source or "unknown source",
            )

            # Read firmware version
            self._firmware = await self._protocol.read_version(self._write_ble)
            _LOGGER.debug("Firmware: %s", self._firmware)

            # Read serial number via HL. Some firmwares don't answer — that's
            # fine, we fall back to the DIS string field and the sensor stays
            # at None.
            try:
                self._serial = await self._protocol.read_serial(self._write_ble)
                if self._serial:
                    _LOGGER.debug("Serial: %s", self._serial)
            except Exception:  # noqa: BLE001 — protocol can raise anything
                _LOGGER.debug("Failed to read serial via HL", exc_info=True)

            # Read Device Information Service (0x180A) for precise
            # manufacturer / model / serial / FW-HW-SW revision strings.
            await self._read_dis_service()

            # Resolve capabilities via brand (advertisement + DIS + override).
            self._capabilities = self._resolve_capabilities()
            if self._capabilities is not None:
                _LOGGER.info(
                    "Resolved capabilities: %s (family=%s, slots=%d)",
                    self._capabilities.model_name,
                    self._capabilities.family_key,
                    self._capabilities.my_coffee_slots,
                )
                # Tell the protocol about the family so brand-specific
                # HX parsing (process-code tables) picks the right map.
                self._protocol.set_family(self._capabilities.family_key)

            # Read feature capability bits (HI — optional, may time out)
            self._features = await self._protocol.read_features(self._write_ble)
            if self._features is not None:
                _LOGGER.info(
                    "Machine features: %s (raw byte0=0x%02x)",
                    self._features.name or "none",
                    int(self._features),
                )
            else:
                _LOGGER.debug("HI not supported or timed out — features=None")

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

            # Name-based family detection can fail on localized device names
            # or proxy advertisements without a local_name; for Melitta the
            # HR machine type is enough to pick the family, and without
            # capabilities the UI Contract stays contract_not_ready forever.
            if self._capabilities is None:
                fallback = self._fallback_capabilities()
                if fallback is not None:
                    self._capabilities = fallback
                    self._protocol.set_family(fallback.family_key)
                    _LOGGER.info(
                        "Capabilities resolved via machine-type fallback: "
                        "%s (family=%s)",
                        fallback.model_name, fallback.family_key,
                    )

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

        except NoBleDeviceError:
            # Short-circuit (issue #35): without a BLEDevice every rung of
            # the ladder fails identically in microseconds — one quiet
            # failure per cycle instead of four tracebacks. The reconnect
            # loops count this attempt and wake up on the next advertisement.
            _LOGGER.info(
                "No BLEDevice cached for %s — waiting for an advertisement "
                "before attempting to connect",
                self._address,
            )
            self._connected = False
            return False
        except (BleakError, OSError, asyncio.TimeoutError):
            _LOGGER.exception("Connection failed for %s", self._address)
            self._connected = False
            await self._safe_disconnect()
            return False

    async def _load_post_connect_data(self) -> None:
        """Load cup counters, profile data and MyCoffee slot cache after connect."""
        try:
            await self.read_cup_counters()
            await self.read_profile_data()
            await self.read_mycoffee_slots()
        except (BleakError, OSError, asyncio.TimeoutError):
            _LOGGER.debug("Error loading post-connect data", exc_info=True)
        except Exception:
            _LOGGER.exception("Unexpected error loading post-connect data")

    def _schedule_reconnect(self) -> None:
        """Schedule a reconnect attempt."""
        if self._reconnect_task and not self._reconnect_task.done():
            return
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _wait_backoff(self, delay: float) -> bool:
        """Serve a reconnect backoff delay; return True on an honored wake-up.

        The advertisement wake-up (``_reconnect_event``) is honored only
        OUTSIDE a failure episode — i.e. when ``_consecutive_connect_failures``
        is zero and the wake means "the device just reappeared, reconnect
        now". During a failure episode the machine keeps advertising every
        ~1-2 s, so honoring wake-ups would collapse the exponential backoff
        into a constant ~5 s hammer against a machine that is rejecting us
        (0.87.2 audit); the full delay is served instead.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + delay
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            self._reconnect_event.clear()
            try:
                await asyncio.wait_for(
                    self._reconnect_event.wait(), timeout=remaining,
                )
            except asyncio.TimeoutError:
                return False
            if self._consecutive_connect_failures == 0 or self._was_absent:
                # Zero failures = normal fast-reconnect; _was_absent = the
                # device just reappeared after an absence — stale failure
                # counts from the previous episode must not delay it.
                self._was_absent = False
                return True

    def _should_attempt_connect(self) -> bool:
        """Presence gate shared by both connect loops (issues #12 / #35).

        A device that is not advertising is normally powered off or out of
        range — attempting to connect would just hammer establish_connection
        and spam the log, so we skip (issue #12). BUT when a connectable
        scanner itself has gone silent, "not advertising" cannot be trusted:
        a wedged proxy forwards no advertisements from ANY device (issue
        #35), and skipping forever would structurally exclude the wedge from
        auto-repair. In that case we attempt anyway — the attempt fails fast
        locally and counts toward the repair threshold.
        """
        affinity = self._ble_source_affinity
        if (
            affinity is not None
            and self._source_available_callback is not None
            and not self._source_available_callback(affinity)
        ):
            _LOGGER.info(
                "Bonded BLE source %s for %s is unavailable; waiting instead "
                "of roaming to another adapter",
                affinity, self._address,
            )
            self._was_absent = True
            return False

        if self._presence_callback is None or self._presence_callback():
            return True
        if (
            self._scanner_starved_callback is not None
            and self._scanner_starved_callback()
        ):
            _LOGGER.debug(
                "%s looks absent but a connectable scanner is starved — "
                "treating as a suspected proxy wedge and attempting anyway",
                self._address,
            )
            return True
        _LOGGER.debug(
            "%s not advertising (powered off / out of range); "
            "waiting for advertisement instead of connecting",
            self._address,
        )
        self._was_absent = True
        return False

    def _note_connect_failure(self) -> None:
        """Count a failed connect() and fire the repair callback at threshold.

        Shared by ``_reconnect_loop`` and the integration's initial-connect
        loop (``_async_connect_and_poll``) — before issue #35 only the former
        counted failures, so a machine that never connected after setup could
        loop forever without ever escalating to repair. The counter resets
        only on a successful connect (see ``_connect_impl``).

        Also feeds the bond state machine with the classified outcome of
        the cycle — only AUTH-class failures move it towards MISMATCH.
        """
        failure_class = (
            FAILURE_AUTH if self._auth_fail_seen else self._last_failure_class
        )
        if failure_class == FAILURE_AUTH and self._source_migration_pending:
            # During an explicit migration, AUTH failure on the replacement
            # central is expected until the machine is in pairing mode. Do not
            # classify that as corruption of the previously proven bond.
            failure_class = FAILURE_LINK
        self.bond.on_cycle_failure(failure_class)
        self._consecutive_connect_failures += 1
        _LOGGER.debug(
            "Connect failure %d/%d",
            self._consecutive_connect_failures,
            self._repair_after_failures,
        )
        if (
            self._repair_after_failures > 0
            and self._consecutive_connect_failures >= self._repair_after_failures
            and self._repair_callback is not None
        ):
            # Fire-and-forget: the callback owns its own task lifetime
            # (it reloads the ESPHome entry, which can take seconds).
            # Zero the counter so we don't keep re-triggering while
            # the proxy is busy reloading.
            _LOGGER.warning(
                "Pairing wedged after %d failed connects to %s — "
                "triggering recovery",
                self._consecutive_connect_failures, self._address,
            )
            self._consecutive_connect_failures = 0
            try:
                result = self._repair_callback()
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)
            except Exception:
                _LOGGER.exception("Repair callback raised")

    async def _reconnect_loop(self) -> None:
        """Try to reconnect with exponential backoff.

        The loop can be woken up early by setting _reconnect_event (e.g. when
        a BLE advertisement arrives, indicating the machine is back online).

        After ``_repair_after_failures`` consecutive failed connect() calls
        we invoke ``_repair_callback`` (installed by the integration setup)
        which reloads the ESPHome BLE proxy entry. That eviction is the only
        way to drop the scanner's cached BLEDevice — see docs/PAIRING.md.
        Counter resets on the next successful connect.
        """
        delay = self._reconnect_delay
        while self._auto_reconnect and not self.connected:
            _LOGGER.info("Reconnecting to %s in %.0fs...", self._address, delay)
            if await self._wait_backoff(delay):
                _LOGGER.debug("Reconnect woken up early (BLE advertisement received)")
                delay = self._reconnect_delay  # reset backoff
            if not self._auto_reconnect:
                break

            # Presence gate (issues #12 / #35): skip attempts while the
            # device is genuinely off — but keep the accrued failure counter
            # (it resets only on success) and override the gate when the
            # scanner itself looks starved. Waking up happens via
            # set_ble_device -> _reconnect_event on the next advertisement.
            if not self._should_attempt_connect():
                delay = min(delay * 2, self._reconnect_max_delay)
                continue

            connect_ok = False
            try:
                connect_ok = await self.connect()
                if connect_ok:
                    _LOGGER.info("Reconnected to %s", self._address)
                    self.start_polling(interval=self._poll_interval)
                    return
            except (BleakError, OSError, asyncio.TimeoutError):
                _LOGGER.debug("Reconnect attempt failed", exc_info=True)
            except Exception:
                _LOGGER.exception("Unexpected error during reconnect")
            if not connect_ok:
                self._note_connect_failure()
            delay = min(delay * 2, self._reconnect_max_delay)
            if self.bond.state is BondState.PAIRING_REQUIRED:
                # The bond is destroyed and only user action (machine-side
                # re-pair) can fix it — no point retrying quickly.
                delay = self._reconnect_max_delay

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
        # Hygiene: drop the bond-tracking flag so the next connect starts
        # from a clean assumption. _connect_impl always begins with pair=False
        # then escalates, so this doesn't actually skip steps — but it keeps
        # the field accurate for future logic and for diagnostics.
        self._paired = False
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

    # ── Device Information Service (0x180A) ──────────────────────────

    # Standard Bluetooth SIG characteristic UUIDs
    _DIS_CHARS: dict[str, str] = {
        "manufacturer": "00002a29-0000-1000-8000-00805f9b34fb",
        "model":        "00002a24-0000-1000-8000-00805f9b34fb",
        "serial":       "00002a25-0000-1000-8000-00805f9b34fb",
        "hw_revision":  "00002a27-0000-1000-8000-00805f9b34fb",
        "fw_revision":  "00002a26-0000-1000-8000-00805f9b34fb",
        "sw_revision":  "00002a28-0000-1000-8000-00805f9b34fb",
    }

    async def _read_dis_service(self) -> None:
        """Read standard Device Information Service characteristics.

        Best-effort — each characteristic read is guarded; missing chars
        are silently skipped. Populates ``self._dis_info`` dict.
        """
        client = self._client
        if client is None or not client.is_connected:
            return
        for key, uuid in self._DIS_CHARS.items():
            try:
                raw = await client.read_gatt_char(uuid)
                text = bytes(raw).rstrip(b"\x00").decode("utf-8", errors="replace").strip()
                if text:
                    self._dis_info[key] = text
            except Exception:  # noqa: BLE001 — char may not exist
                _LOGGER.debug("DIS read failed for %s", key, exc_info=True)
        if self._dis_info:
            _LOGGER.info("DIS: %s", self._dis_info)

    def _resolve_capabilities(self):
        """Resolve MachineCapabilities from brand + DIS + override."""
        profile = self._brand
        # 1. Explicit user override
        if self._family_override and self._family_override in profile.families:
            _LOGGER.info(
                "Using family override %s from Options Flow",
                self._family_override,
            )
            return profile.capabilities_for(self._family_override)
        # 2. Nivona-style per-model lookup (serial-prefix cascade) if supported
        # Include BLE advertisement name from the BLEDevice reference, which
        # may differ from _device_name (friendly name stored in config entry).
        ble_adv_name = getattr(self._ble_device, "name", None)
        model_lookup = getattr(profile, "capabilities_for_model", None)
        if model_lookup is not None:
            # Try DIS serial first (most accurate), then advertisement name.
            for candidate in (self._dis_info.get("serial"), self._device_name, ble_adv_name):
                if not candidate:
                    continue
                try:
                    caps = model_lookup(candidate, self._dis_info)
                except Exception:  # noqa: BLE001
                    caps = None
                if caps is not None:
                    return caps
        # 3. Family detect via ble_name
        for candidate in (self._device_name or "", ble_adv_name or ""):
            family = profile.detect_family(candidate, self._dis_info or None)
            if family:
                return profile.capabilities_for(family)
        return None

    def _fallback_capabilities(self):
        """Melitta-only capability fallback from the HR machine type.

        Used when name-prefix detection fails (localized device name, proxy
        advertisement without local_name). BARISTA_T maps to the T family;
        anything else — including an unanswered HR id=6 — defaults to the
        TS set, consistent with get_available_recipes(None). Non-Melitta
        brands return None: their families differ materially and a wrong
        guess is worse than no capabilities.
        """
        if self._brand.brand_slug != "melitta":
            return None
        family = (
            "barista_t"
            if self._machine_type is MachineType.BARISTA_T
            else "barista_ts"
        )
        return self._brand.capabilities_for(family)

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
    """Discover supported coffee machines via a direct BLE scan."""
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
