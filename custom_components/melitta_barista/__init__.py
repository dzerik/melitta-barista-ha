"""Home Assistant integration for Melitta & Nivona smart coffee machines."""

from __future__ import annotations

import asyncio
import logging
import pathlib
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from bleak.exc import BleakError
from homeassistant.components import bluetooth
from homeassistant.components.frontend import (
    async_register_built_in_panel,
    async_remove_panel,
)
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.websocket_api import (
    async_register_command,
    websocket_command,
)
import voluptuous as vol

from time import monotonic as _time_monotonic, perf_counter as _time_perf_counter

from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from homeassistant.const import CONF_ADDRESS, CONF_NAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.util import dt as dt_util

from .ble_client import MelittaBleClient
from .const import (
    DOMAIN,
    DEFAULT_POLL_INTERVAL,
    PROCESS_MAP, INTENSITY_MAP, AROMA_MAP, TEMPERATURE_MAP, SHOTS_MAP,
    CONF_POLL_INTERVAL,
    CONF_RECONNECT_DELAY,
    CONF_RECONNECT_MAX_DELAY,
    CONF_MAX_CONSECUTIVE_ERRORS,
    CONF_FRAME_TIMEOUT,
    CONF_BLE_CONNECT_TIMEOUT,
    CONF_RECIPE_RETRIES,
    CONF_INITIAL_CONNECT_DELAY,
    CONF_AUTO_CONFIRM_PROMPTS,
    CONF_BRAND,
    CONF_FAMILY_OVERRIDE,
    DEFAULT_BRAND,
    DEFAULT_RECONNECT_DELAY,
    DEFAULT_RECONNECT_MAX_DELAY,
    DEFAULT_MAX_CONSECUTIVE_ERRORS,
    DEFAULT_FRAME_TIMEOUT,
    DEFAULT_BLE_CONNECT_TIMEOUT,
    DEFAULT_RECIPE_RETRIES,
    DEFAULT_INITIAL_CONNECT_DELAY,
    DEFAULT_AUTO_CONFIRM_PROMPTS,
    CONF_AUTO_SYNC_CLOCK,
    CONF_AUTO_SYNC_DRIFT_MINUTES,
    CONF_AUTO_SYNC_DAILY_TIME,
    CLOCK_SYNC_THROTTLE_HOURS,
    DEFAULT_AUTO_SYNC_CLOCK,
    DEFAULT_AUTO_SYNC_DRIFT_MINUTES,
    DEFAULT_AUTO_SYNC_DAILY_TIME,
    SERVICE_SYNC_CLOCK,
)

_LOGGER = logging.getLogger("melitta_barista")

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SELECT,
    Platform.NUMBER,
    Platform.SWITCH,
    Platform.TEXT,
    Platform.TIME,
]


@callback
def _async_cleanup_legacy_recipe_buttons(
    hass: HomeAssistant, entry: ConfigEntry, address: str
) -> None:
    """Remove old per-recipe button entities left from versions before v0.6.0.

    Previously each recipe had its own button. Two unique_id formats existed:
    - Numeric: {address}_brew_{200..223}
    - Named:  {address}_brew_{recipe_name} (e.g. _brew_americano)
    Now there is a single Brew button + Recipe select entity.
    """
    registry = er.async_get(hass)
    removed = 0

    # Format 1: numeric IDs {address}_brew_200 .. {address}_brew_223
    for recipe_value in range(200, 224):
        unique_id = f"{address}_brew_{recipe_value}"
        entity_id = registry.async_get_entity_id("button", DOMAIN, unique_id)
        if entity_id:
            registry.async_remove(entity_id)
            removed += 1

    # Format 2: named IDs {address}_brew_{enum_name}
    from .const import RecipeId  # noqa: PLC0415
    for recipe in RecipeId:
        unique_id = f"{address}_brew_{recipe.name.lower()}"
        entity_id = registry.async_get_entity_id("button", DOMAIN, unique_id)
        if entity_id:
            registry.async_remove(entity_id)
            removed += 1

    # Format 3: any remaining button entities with _brew_ pattern in unique_id
    for ent in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            ent.domain == "button"
            and "_brew_" in (ent.unique_id or "")
            and ent.unique_id not in (f"{address}_brew", f"{address}_brew_freestyle")
        ):
            registry.async_remove(ent.entity_id)
            removed += 1

    if removed:
        _LOGGER.info("Cleaned up %d legacy recipe button entities", removed)


def _async_clock_coordinator_key(entry_id: str) -> str:
    """`hass.data[DOMAIN]` key for the per-entry ClockSyncCoordinator."""
    return f"clock_coordinator_{entry_id}"


def _async_check_clock_migration(
    hass: HomeAssistant, entry: ConfigEntry, address: str,
) -> None:
    """Create a repair issue if legacy clock number entities still exist.

    Versions before 0.52.0 exposed CLOCK / CLOCK_SEND as two writable
    number entities. 0.52.0 replaces them with a single `time` entity
    and a `sync_clock` service. We surface a repair card so users with
    existing automations can update them.
    """
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    registry = er.async_get(hass)
    legacy_uids = {f"{address}_setting_20", f"{address}_setting_21"}
    if not any(ent.unique_id in legacy_uids for ent in registry.entities.values()):
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        "clock_entity_migration",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="clock_entity_migration",
        learn_more_url="https://github.com/dzerik/melitta-ha-integration/blob/main/CHANGELOG.md#0520",
    )


def _clock_circular_drift(a: int, b: int) -> int:
    """Shortest circular distance between two minutes-of-day values."""
    diff = abs(a - b)
    return min(diff, 1440 - diff)


class ClockSyncCoordinator:
    """Reconnect-throttled + daily auto-sync of the machine RTC.

    Reads setting 20 (current machine clock) to compute drift against
    Home Assistant local time, then writes setting 21 if needed.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: MelittaBleClient,
        options: Mapping[str, Any],
    ) -> None:
        self._hass = hass
        self._client = client
        self._opts = options
        self._last_sync: datetime | None = None
        self._unsub_daily = None

    def start(self) -> None:
        """Subscribe to connection callback + register daily timer."""
        from homeassistant.helpers.event import async_track_time_change  # noqa: PLC0415

        self._client.add_connection_callback(self._on_connect)
        daily = str(
            self._opts.get(CONF_AUTO_SYNC_DAILY_TIME, DEFAULT_AUTO_SYNC_DAILY_TIME)
        )
        h_str, m_str = daily.split(":")
        self._unsub_daily = async_track_time_change(
            self._hass,
            self._on_daily_tick,
            hour=int(h_str),
            minute=int(m_str),
            second=0,
        )

    def stop(self) -> None:
        """Tear down listeners. Idempotent."""
        self._client.remove_connection_callback(self._on_connect)
        if self._unsub_daily is not None:
            self._unsub_daily()
            self._unsub_daily = None

    async def _trigger_sync(self, source: str, force: bool = False) -> None:
        """Read drift, optionally write, update `_last_sync`.

        `source` is a free-form tag for logging ("reconnect" / "daily" / "service" / "test").
        `force=True` bypasses both the drift threshold and the reconnect throttle.
        """
        if not self._opts.get(CONF_AUTO_SYNC_CLOCK, DEFAULT_AUTO_SYNC_CLOCK):
            return
        if not self._client.connected:
            return

        if source == "reconnect" and not force and self._last_sync is not None:
            elapsed = dt_util.now() - self._last_sync
            if elapsed < timedelta(hours=CLOCK_SYNC_THROTTLE_HOURS):
                _LOGGER.debug("Clock sync throttled (last %s ago)", elapsed)
                return

        try:
            machine_minutes = await self._client.read_setting(20)
        except Exception:
            _LOGGER.warning("Clock sync: read failed", exc_info=True)
            return
        if machine_minutes is None:
            _LOGGER.debug("Clock sync: machine returned no value")
            return

        now = dt_util.now()
        ha_minutes = now.hour * 60 + now.minute
        threshold = int(
            self._opts.get(
                CONF_AUTO_SYNC_DRIFT_MINUTES, DEFAULT_AUTO_SYNC_DRIFT_MINUTES,
            )
        )
        drift = _clock_circular_drift(int(machine_minutes) % 1440, ha_minutes)

        if drift < threshold and not force:
            _LOGGER.debug(
                "Clock sync skipped (drift=%d < threshold=%d)", drift, threshold,
            )
            self._last_sync = now
            return

        ok = await self._client.write_setting(21, ha_minutes)
        self._last_sync = now
        if ok:
            _LOGGER.info(
                "Clock sync (%s): wrote %02d:%02d (drift was %d min)",
                source, now.hour, now.minute, drift,
            )
        else:
            _LOGGER.warning("Clock sync (%s): write rejected", source)

    @callback
    def _on_connect(self, connected: bool) -> None:
        """Connection callback hook.

        Fired on every BLE connection state transition. We schedule a
        reconnect-source sync only on the rising edge (False→True).
        """
        if not connected:
            return
        self._hass.async_create_task(self._trigger_sync("reconnect", force=False))

    @callback
    def _on_daily_tick(self, _now: datetime) -> None:
        """Scheduled daily tick handler.

        Bypasses throttle and drift threshold (`force=True`) so the
        machine RTC is guaranteed at least one sync per 24 h even if
        the BLE connection has been stable and reconnect throttle has
        suppressed every reconnect sync.
        """
        self._hass.async_create_task(self._trigger_sync("daily", force=True))


PANEL_URL_PATH = "melitta-barista"
PANEL_STATIC_PATH = "/melitta_barista/panel"


async def _async_register_panel(hass: HomeAssistant) -> None:
    """Register the admin SPA panel and serve its static assets.

    Idempotent — repeated calls (e.g. when a second config entry is set up
    while the integration is already loaded) are safely ignored.
    """
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get("panel_registered"):
        return

    panel_dir = str(pathlib.Path(__file__).parent / "www")
    await hass.http.async_register_static_paths(
        [StaticPathConfig(PANEL_STATIC_PATH, panel_dir, cache_headers=False)]
    )

    # Read version from HA's cached integration manifest — no blocking
    # filesystem I/O on the event loop. Used to cache-bust the panel's
    # JS module URL on upgrade.
    from homeassistant.loader import async_get_integration  # noqa: PLC0415
    integration = await async_get_integration(hass, DOMAIN)
    version = integration.manifest.get("version", "unknown")

    async_register_built_in_panel(
        hass,
        component_name="custom",
        sidebar_title="Melitta",
        sidebar_icon="mdi:coffee-maker",
        frontend_url_path=PANEL_URL_PATH,
        config={
            "_panel_custom": {
                "name": "melitta-panel",
                "module_url": f"{PANEL_STATIC_PATH}/melitta-panel.js?v={version}",
                "embed_iframe": False,
                "trust_external": False,
            },
        },
        require_admin=True,
    )
    domain_data["panel_registered"] = True
    _LOGGER.debug("Melitta admin panel registered at /%s", PANEL_URL_PATH)


def _async_unregister_panel(hass: HomeAssistant) -> None:
    """Remove the panel when the last config entry is unloaded."""
    domain_data = hass.data.get(DOMAIN) or {}
    if not domain_data.get("panel_registered"):
        return
    try:
        async_remove_panel(hass, PANEL_URL_PATH)
    except KeyError:
        _LOGGER.debug("Panel %s already removed", PANEL_URL_PATH)
    domain_data.pop("panel_registered", None)


@callback
def _async_register_panel_websocket(hass: HomeAssistant) -> None:
    """Register the panel's bootstrap WS handler.

    Returns the list of melitta_barista config entries the panel can target.
    Per-tab WS handlers will be registered alongside their feature work.
    """
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get("panel_ws_registered"):
        return

    @websocket_command(
        {vol.Required("type"): "melitta_barista/entries"}
    )
    @callback
    def _ws_list_entries(hass_, connection, msg):
        entries = [
            {
                "entry_id": entry.entry_id,
                "title": entry.title,
                "address": entry.data.get(CONF_ADDRESS),
                "brand": entry.data.get(CONF_BRAND, DEFAULT_BRAND),
            }
            for entry in hass_.config_entries.async_entries(DOMAIN)
        ]
        connection.send_result(msg["id"], {"entries": entries})

    async_register_command(hass, _ws_list_entries)
    domain_data["panel_ws_registered"] = True


@callback
def _async_register_sommelier(hass: HomeAssistant) -> None:
    """Register AI Coffee Sommelier WebSocket handlers (once).

    DB initialization is deferred to the first WebSocket call to avoid
    blocking HA setup and to work in test environments without aiosqlite.
    """
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get("sommelier_registered"):
        return

    try:
        from .sommelier_api import async_register_websocket_handlers
        async_register_websocket_handlers(hass)
        domain_data["sommelier_registered"] = True
        _LOGGER.debug("AI Coffee Sommelier WS handlers registered")
    except Exception:
        _LOGGER.warning(
            "Could not register AI Coffee Sommelier handlers",
            exc_info=True,
        )


def _find_proxy_entry_for_address(
    hass: HomeAssistant, address: str,
) -> ConfigEntry | None:
    """Locate the ESPHome config entry whose proxy advertised the given peer.

    Tries three matchers, in order, against every BluetoothScannerDevice
    HA knows about for `address`:

    1. Normalised string compare on `scanner.source` vs `entry.unique_id`
       (this is the ESP MAC in the historical / DHCP-discovery path).
    2. Same normalisation against `entry.runtime_data.device_info.mac_address`
       (zeroconf-discovery / re-configured entries can drift from
       unique_id even though their `device_info` is correct).
    3. Compare against the ESPHome entry's primary device row in the
       device registry — `dev_reg.async_get_device({(DOMAIN_ESPHOME, mac)})`
       returns the same device the scanner is registered against, so
       walking up to its `config_entries` set is a last-resort safety net.

    Returns None when running on a local BlueZ adapter or when nothing
    matches — both are legitimate "no proxy to reload" cases.
    """
    try:
        scanner_devices = bluetooth.async_scanner_devices_by_address(
            hass, address, connectable=True,
        )
    except Exception:  # noqa: BLE001 — defensive: bluetooth API may not be ready
        _LOGGER.debug("Bluetooth API not available for scanner lookup", exc_info=True)
        return None

    if not scanner_devices:
        _LOGGER.debug(
            "find_proxy_entry: no scanners report address %s — "
            "either it's a local adapter setup or HA bluetooth has not "
            "seen the device yet",
            address,
        )
        return None

    esphome_entries = hass.config_entries.async_entries("esphome")
    if not esphome_entries:
        _LOGGER.debug("find_proxy_entry: no esphome ConfigEntries at all")
        return None

    def _norm(s: str | None) -> str:
        return (s or "").lower().replace(":", "").replace("-", "")

    # Collect candidate (entry, normalised_keys) pairs once so every scanner
    # iteration is O(entries) not O(entries * lookups).
    #
    # IMPORTANT: ESP32 has separate WiFi and Bluetooth MACs (BT = WiFi + 2),
    # and the scanner.source is the BT MAC, while entry.unique_id is the
    # WiFi MAC. We MUST include device_info.bluetooth_mac_address as a key —
    # otherwise the matcher silently misses every ESPHome proxy.
    candidates: list[tuple[ConfigEntry, set[str]]] = []
    for entry in esphome_entries:
        keys: set[str] = set()
        if entry.unique_id:
            keys.add(_norm(entry.unique_id))
        # entry.data may carry CONF_BLUETOOTH_MAC_ADDRESS (persisted at
        # discovery / re-config time) — covers the case where runtime_data
        # isn't ready yet because the ESPHome entry is mid-setup.
        bt_mac_persisted = entry.data.get("bluetooth_mac_address")
        if bt_mac_persisted:
            keys.add(_norm(bt_mac_persisted))
        runtime = getattr(entry, "runtime_data", None)
        device_info = getattr(runtime, "device_info", None) if runtime else None
        if device_info is not None:
            for attr in ("bluetooth_mac_address", "mac_address", "name"):
                value = getattr(device_info, attr, None)
                if value:
                    keys.add(_norm(value))
        if keys:
            candidates.append((entry, keys))

    for scanner_device in scanner_devices:
        scanner = scanner_device.scanner
        source = _norm(getattr(scanner, "source", None))
        if not source:
            continue
        for entry, keys in candidates:
            if source in keys:
                _LOGGER.debug(
                    "find_proxy_entry: matched %s → ESPHome entry %s "
                    "(scanner.source=%s)",
                    address, entry.entry_id, scanner.source,
                )
                return entry
        _LOGGER.debug(
            "find_proxy_entry: scanner source %s did not match any "
            "ESPHome entry keys (tried %d entries)",
            scanner.source, len(candidates),
        )
    return None


async def _async_force_repair(
    hass: HomeAssistant, entry: ConfigEntry,
) -> dict[str, Any]:
    """Full re-pair routine — clears BOTH the ESP-side bond and the HA cache.

    Hard recovery for when soft `_async_repair_pairing` doesn't help: the
    proxy is holding a stale LTK in NVS that the machine refuses, and no
    amount of scanner-evicting on the HA side fixes that. We need to wipe
    the ESP bond table for a real fresh-SMP exchange on the next pair=True.

    Sequence:

    1. Disconnect the Melitta client so it doesn't fight us during the reload.
    2. Find the ESPHome proxy ConfigEntry that owns the scanner for this peer.
    3. If the proxy ships a user-defined `clear_ble_bonds` action (see
       `esphome/ble-proxy-xiao-c6.yaml` reference), call the matching HA
       service so the ESP wipes its NVS bond table.
    4. Reload the ESPHome proxy entry. That unregisters the scanner (evicts
       the cached BLEDevice from `_previous_service_info`) and re-establishes
       the API connection — clean slate on both sides.
    5. Re-arm the Melitta reconnect loop. Next advertisement triggers a
       connect from `pair=False` (fails fast because no bond) then escalates
       to `pair=True`, which now provokes a fresh SMP exchange.

    Returns a result dict with keys:
        ``bond_cleared`` (bool) — did the ESP service actually run?
        ``proxy_reloaded`` (bool) — did we reload the proxy ConfigEntry?
        ``service_name`` (str | None) — the service we tried to call.
        ``service_missing`` (bool) — True if the action isn't wired in
            the user's ESPHome YAML; the abort message tells them how.
    """
    result: dict[str, Any] = {
        "bond_cleared": False,
        "peer_disconnected": False,
        "proxy_reloaded": False,
        "service_name": None,
        "service_missing": False,
    }

    client: MelittaBleClient | None = getattr(entry, "runtime_data", None)
    if client is None:
        _LOGGER.warning("force_repair: no runtime_data on entry %s", entry.entry_id)
        return result

    # Step 1 — quiesce our own client so the reload doesn't race with us.
    try:
        await client.disconnect()
    except Exception:  # noqa: BLE001
        _LOGGER.debug("force_repair: disconnect failed", exc_info=True)

    proxy_entry = _find_proxy_entry_for_address(hass, client.address)
    if proxy_entry is None:
        _LOGGER.info(
            "force_repair: no ESPHome proxy entry for %s — falling back to "
            "a local disconnect; the reconnect loop will pick up the next "
            "advertisement.",
            client.address,
        )
        client._auto_reconnect = True  # noqa: SLF001
        client._reconnect_event.set()  # noqa: SLF001
        if not client._reconnect_task or client._reconnect_task.done():  # noqa: SLF001
            client._schedule_reconnect()  # noqa: SLF001
        return result

    # Step 2 — build the esphome service name from the proxy device name
    # (ESPHome registers user actions as `esphome.<device-with-dashes-as-underscores>_<action>`).
    device_name: str | None = None
    runtime_data = getattr(proxy_entry, "runtime_data", None)
    if runtime_data is not None:
        device_info = getattr(runtime_data, "device_info", None)
        if device_info is not None:
            device_name = getattr(device_info, "name", None)

    if device_name:
        prefix = device_name.replace("-", "_")
        service_name = f"{prefix}_clear_ble_bonds"
        disconnect_service = f"{prefix}_disconnect_ble_peer"
        result["service_name"] = service_name

        # Step 2a — wipe NVS bond table.
        if hass.services.has_service("esphome", service_name):
            _LOGGER.warning(
                "force_repair: calling esphome.%s to wipe NVS bond table on %s",
                service_name, device_name,
            )
            try:
                await hass.services.async_call(
                    "esphome", service_name, {}, blocking=True,
                )
                result["bond_cleared"] = True
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "force_repair: esphome.%s call raised", service_name,
                )
        else:
            _LOGGER.warning(
                "force_repair: esphome.%s is not registered. Add the "
                "`clear_ble_bonds` action to your ESPHome YAML (see "
                "esphome/ble-proxy-xiao-c6.yaml in the repo) and flash to "
                "make this service available.",
                service_name,
            )
            result["service_missing"] = True

        # Step 2b — surgical GAP disconnect of our peer. Without this the
        # bluetooth_proxy connection slot can stay in `state: ESTABLISHED`
        # after an `auth fail reason=82` rejection from the machine, and
        # every fresh client-side connect request gets "ignored". Calling
        # disconnect_ble_peer drops the half-closed link so the next
        # establish_connection actually opens a new SMP exchange.
        if hass.services.has_service("esphome", disconnect_service):
            _LOGGER.warning(
                "force_repair: calling esphome.%s to drop stuck "
                "ESTABLISHED slot for %s",
                disconnect_service, client.address,
            )
            try:
                await hass.services.async_call(
                    "esphome", disconnect_service,
                    {"peer_mac": client.address}, blocking=True,
                )
                result["peer_disconnected"] = True
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "force_repair: esphome.%s call raised", disconnect_service,
                )

    # Step 3 — reload the proxy entry. Even if bond clearing wasn't done,
    # this evicts the cached BLEDevice and gives the next pair attempt a
    # fresh source/address_type pair.
    hass.async_create_task(
        hass.config_entries.async_reload(proxy_entry.entry_id),
        name=f"melitta_barista_force_repair_{client.address}",
    )
    result["proxy_reloaded"] = True

    # Step 4 — re-arm our own reconnect loop. The reload will tear down
    # _async_update_ble briefly, then re-register on setup; the next
    # advertisement will hit `_reconnect_event.set()` and we'll connect.
    client._auto_reconnect = True  # noqa: SLF001
    client._reconnect_event.set()  # noqa: SLF001
    if not client._reconnect_task or client._reconnect_task.done():  # noqa: SLF001
        client._schedule_reconnect()  # noqa: SLF001

    return result


async def _async_repair_pairing(
    hass: HomeAssistant, entry: ConfigEntry,
) -> bool:
    """Recover a wedged pairing by reloading the ESPHome BLE proxy entry.

    Root cause of the wedge (see docs/PAIRING.md or issue #10): habluetooth
    caches a BLEDevice instance per peer address with a frozen
    `details["source"]` / `details["address_type"]`. After long BLE silence
    the cached source can point at a dead scanner UUID, or the address_type
    can drift after the machine resets its bond — and every reconnect
    handed that stale device. Reloading the ESPHome entry unregisters the
    scanner, which evicts the cached BLEDevice; the next advertisement
    rebuilds it with fresh details.

    Returns True if a proxy entry was found and a reload was scheduled;
    False if there is no proxy entry (local BlueZ adapter) — in that case
    the caller should fall back to disconnect+reconnect.
    """
    client: MelittaBleClient | None = getattr(entry, "runtime_data", None)
    if client is None:
        _LOGGER.warning("repair_pairing: no runtime_data on entry %s", entry.entry_id)
        return False

    proxy_entry = _find_proxy_entry_for_address(hass, client.address)
    if proxy_entry is None:
        _LOGGER.info(
            "repair_pairing: no ESPHome proxy entry found for %s — "
            "doing a local disconnect+reconnect instead",
            client.address,
        )
        # Local adapter path: a plain disconnect (clearing _paired etc.) and
        # letting the reconnect loop wake up on the next advertisement is
        # the cheapest recovery we have.
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            _LOGGER.debug("disconnect during repair failed", exc_info=True)
        # disconnect() sets _auto_reconnect=False; re-arm so the reconnect
        # loop can take over once an advertisement arrives.
        client._auto_reconnect = True  # noqa: SLF001
        client._reconnect_event.set()  # noqa: SLF001
        if not client._reconnect_task or client._reconnect_task.done():  # noqa: SLF001
            client._schedule_reconnect()  # noqa: SLF001
        return False

    _LOGGER.warning(
        "repair_pairing: reloading ESPHome proxy %s to evict stale BLEDevice for %s",
        proxy_entry.title or proxy_entry.entry_id, client.address,
    )
    # Run the reload as a background task so we don't block whichever
    # context invoked us (reconnect loop, service call, etc.). HA's
    # async_reload itself is safe to call from any event-loop context.
    hass.async_create_task(
        hass.config_entries.async_reload(proxy_entry.entry_id),
        name=f"melitta_barista_repair_{client.address}",
    )
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entries forward.

    v1 → v2: introduce ``brand`` field. All pre-existing entries are
    Melitta (the only previously supported brand).
    """
    if entry.version < 2:
        new_data = {**entry.data}
        new_data.setdefault(CONF_BRAND, DEFAULT_BRAND)
        hass.config_entries.async_update_entry(entry, data=new_data, version=2)
        _LOGGER.info(
            "Migrated entry %s to v2 (brand=%s)",
            entry.entry_id, new_data[CONF_BRAND],
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a coffee-machine integration from a config entry."""
    from .brands import get_profile  # noqa: PLC0415

    address: str = entry.data[CONF_ADDRESS]
    device_name: str | None = entry.data.get(CONF_NAME)
    brand_slug: str = entry.data.get(CONF_BRAND, DEFAULT_BRAND)
    try:
        brand = get_profile(brand_slug)
    except KeyError:
        _LOGGER.error("Unknown brand %r in config entry; falling back to %s",
                      brand_slug, DEFAULT_BRAND)
        brand = get_profile(DEFAULT_BRAND)

    _LOGGER.info(
        "Setting up %s for %s (%s)",
        brand.brand_name, device_name or "unknown", address,
    )

    # Get initial BLEDevice from HA bluetooth cache (may be None)
    ble_device = None
    try:
        ble_device = bluetooth.async_ble_device_from_address(
            hass, address.upper(), connectable=True
        )
        if ble_device is None:
            ble_device = bluetooth.async_ble_device_from_address(
                hass, address, connectable=True
            )
        _LOGGER.debug("Initial BLEDevice from cache: %s", ble_device)
    except (AttributeError, ValueError):
        _LOGGER.debug("Could not get BLEDevice from cache", exc_info=True)

    opts = entry.options
    client = MelittaBleClient(
        address,
        device_name=device_name,
        ble_device=ble_device,
        poll_interval=opts.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
        ble_connect_timeout=opts.get(CONF_BLE_CONNECT_TIMEOUT, DEFAULT_BLE_CONNECT_TIMEOUT),
        frame_timeout=opts.get(CONF_FRAME_TIMEOUT, DEFAULT_FRAME_TIMEOUT),
        max_consecutive_errors=opts.get(CONF_MAX_CONSECUTIVE_ERRORS, DEFAULT_MAX_CONSECUTIVE_ERRORS),
        reconnect_delay=opts.get(CONF_RECONNECT_DELAY, DEFAULT_RECONNECT_DELAY),
        reconnect_max_delay=opts.get(CONF_RECONNECT_MAX_DELAY, DEFAULT_RECONNECT_MAX_DELAY),
        recipe_retries=opts.get(CONF_RECIPE_RETRIES, DEFAULT_RECIPE_RETRIES),
        auto_confirm_prompts=opts.get(
            CONF_AUTO_CONFIRM_PROMPTS, DEFAULT_AUTO_CONFIRM_PROMPTS,
        ),
        brand=brand,
        family_override=(opts.get(CONF_FAMILY_OVERRIDE) or None),
    )

    # Register bluetooth callback to keep BLEDevice reference fresh.
    try:

        @callback
        def _async_update_ble(service_info, change) -> None:
            """Update BLEDevice when new advertisement arrives."""
            client.set_ble_device(service_info.device)

        entry.async_on_unload(
            bluetooth.async_register_callback(
                hass,
                _async_update_ble,
                {"address": address},
                bluetooth.BluetoothScanningMode.ACTIVE,
            )
        )
        _LOGGER.debug("Bluetooth callback registered for %s", address)
    except (AttributeError, ValueError, KeyError):
        _LOGGER.warning(
            "Could not register bluetooth callback for %s, "
            "BLEDevice updates from advertisements won't work",
            address,
            exc_info=True,
        )

    entry.runtime_data = client

    # Wire the recovery callback so the reconnect loop can reload the
    # ESPHome proxy entry after N consecutive failed connect()s. The
    # threshold itself lives on the client (configurable via Options).
    pairing_issue_id = f"pairing_wedged_{address}"

    def _trigger_repair() -> None:
        # Surface a Repair card so the user knows we hit the wedge even if
        # the auto-recovery (proxy reload) succeeds — gives them a chance
        # to flag the issue / share logs.
        ir.async_create_issue(
            hass, DOMAIN, pairing_issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="pairing_wedged",
            translation_placeholders={"address": address},
            learn_more_url=(
                "https://github.com/dzerik/melitta-barista-ha/issues/10"
            ),
        )
        hass.async_create_task(
            _async_repair_pairing(hass, entry),
            name=f"melitta_barista_repair_trigger_{address}",
        )

    client.set_repair_callback(_trigger_repair)
    entry.async_on_unload(lambda: client.set_repair_callback(None))

    # Presence gate (issue #12): tell the reconnect loop whether the device is
    # actually advertising. A powered-off machine is "not present" and must
    # not escalate to a pairing_wedged repair — only a still-advertising but
    # unconnectable device is a genuine wedge.
    def _is_present() -> bool:
        try:
            return bluetooth.async_address_present(hass, address, connectable=True)
        except Exception:  # noqa: BLE001 — never let a presence check break reconnect
            # If presence can't be determined, fall back to "present" so the
            # original (pre-#12) wedge behaviour is preserved.
            return True

    client.set_presence_callback(_is_present)
    entry.async_on_unload(lambda: client.set_presence_callback(None))

    # Track disconnects for repair issue (connection instability warning)
    disconnect_times: list[float] = []
    max_disconnects_per_hour = 5
    issue_id = f"connection_unstable_{address}"

    @callback
    def _track_connection(connected: bool) -> None:
        if connected:
            # Clear repair issues on successful reconnect — both the
            # disconnect-storm warning and the stuck-pairing warning.
            ir.async_delete_issue(hass, DOMAIN, issue_id)
            ir.async_delete_issue(hass, DOMAIN, pairing_issue_id)
            return
        disconnect_times.append(_time_monotonic())
        # Keep only last hour
        cutoff = _time_monotonic() - 3600
        disconnect_times[:] = [t for t in disconnect_times if t > cutoff]
        if len(disconnect_times) >= max_disconnects_per_hour:
            ir.async_create_issue(
                hass, DOMAIN, issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="connection_unstable",
                translation_placeholders={"count": str(len(disconnect_times))},
            )

    client.add_connection_callback(_track_connection)
    entry.async_on_unload(
        lambda: client.remove_connection_callback(_track_connection)
    )

    # Setup-phase timing diagnostics (DEBUG level — enable
    # `logger.melitta_barista: debug` in HA to see them).
    _t_setup_start = _time_perf_counter()

    # Clean up legacy per-recipe button entities (v0.5.x → v0.6.0 migration)
    _t0 = _time_perf_counter()
    _async_cleanup_legacy_recipe_buttons(hass, entry, address)
    _LOGGER.debug("[TIMING] %s cleanup_legacy: %.0fms", address, (_time_perf_counter() - _t0) * 1000)
    _async_check_clock_migration(hass, entry, address)

    clock_coord = ClockSyncCoordinator(hass, client, entry.options)
    clock_coord.start()
    entry.async_on_unload(clock_coord.stop)
    hass.data.setdefault(DOMAIN, {})[
        _async_clock_coordinator_key(entry.entry_id)
    ] = clock_coord

    _t0 = _time_perf_counter()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _LOGGER.debug(
        "[TIMING] %s forward_entry_setups: %.0fms",
        address, (_time_perf_counter() - _t0) * 1000,
    )

    # Register freestyle service (once per integration)
    _t0 = _time_perf_counter()
    _async_register_services(hass)
    _LOGGER.debug("[TIMING] %s register_services: %.0fms", address, (_time_perf_counter() - _t0) * 1000)

    # Register AI Coffee Sommelier WebSocket handlers (DB init is lazy on first call)
    _t0 = _time_perf_counter()
    _async_register_sommelier(hass)
    _LOGGER.debug("[TIMING] %s register_sommelier: %.0fms", address, (_time_perf_counter() - _t0) * 1000)

    # Register admin panel (sidebar entry + static assets) and its bootstrap
    # WS handler. Both are idempotent — repeat calls when a second config
    # entry is added do nothing.
    _t0 = _time_perf_counter()
    _async_register_panel_websocket(hass)
    from .panel_api import async_register_panel_websocket as _register_panel_api  # noqa: PLC0415
    _register_panel_api(hass)
    await _async_register_panel(hass)
    _LOGGER.debug("[TIMING] %s register_panel: %.0fms", address, (_time_perf_counter() - _t0) * 1000)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # Connect in a *background* task so HA's startup doesn't wait for our
    # infinite reconnect loop to finish. async_create_task tracks tasks in
    # hass._tasks and blocks the transition to "running" state until they
    # complete — using it for a never-returning loop produces "Setup of
    # domain X is taking over N minutes" warnings and delays HA startup.
    # See: https://developers.home-assistant.io/docs/asyncio_blocking_operations/
    connect_task = hass.async_create_background_task(
        _async_connect_and_poll(
            client,
            poll_interval=opts.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
            initial_delay=opts.get(CONF_INITIAL_CONNECT_DELAY, DEFAULT_INITIAL_CONNECT_DELAY),
            reconnect_delay=opts.get(CONF_RECONNECT_DELAY, DEFAULT_RECONNECT_DELAY),
            reconnect_max_delay=opts.get(CONF_RECONNECT_MAX_DELAY, DEFAULT_RECONNECT_MAX_DELAY),
        ),
        f"melitta_barista_connect_{address}",
    )

    @callback
    def _cancel_connect_task() -> None:
        if not connect_task.done():
            connect_task.cancel()

    entry.async_on_unload(_cancel_connect_task)
    _LOGGER.debug(
        "[TIMING] %s async_setup_entry TOTAL: %.0fms",
        address, (_time_perf_counter() - _t_setup_start) * 1000,
    )
    _LOGGER.info("Setup complete for %s, connecting in background", address)

    return True


async def _async_connect_and_poll(
    client: MelittaBleClient,
    *,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    initial_delay: float = DEFAULT_INITIAL_CONNECT_DELAY,
    reconnect_delay: float = DEFAULT_RECONNECT_DELAY,
    reconnect_max_delay: float = DEFAULT_RECONNECT_MAX_DELAY,
) -> None:
    """Connect to the machine in background -- does not block setup.

    Retries with exponential backoff if initial connection fails.
    Can be woken up early by BLE advertisement via client._reconnect_event.
    """
    delay = reconnect_delay

    # Wait for the machine to release any prior BLE connection (e.g. from pairing)
    await asyncio.sleep(initial_delay)

    while True:
        try:
            _LOGGER.debug("Background connect starting for %s", client.address)
            if await client.connect():
                _LOGGER.info("Connected to %s, starting polling", client.address)
                client.start_polling(interval=poll_interval)
                return
            _LOGGER.warning(
                "Connection to %s failed, retrying in %.0fs",
                client.address, delay,
            )
        except (BleakError, OSError, asyncio.TimeoutError):
            _LOGGER.debug(
                "Connection error for %s, retrying in %.0fs",
                client.address, delay, exc_info=True,
            )
        except Exception:
            _LOGGER.exception(
                "Unexpected error connecting to %s, retrying in %.0fs",
                client.address, delay,
            )
        # Wait for backoff delay, but wake up early on BLE advertisement
        client._reconnect_event.clear()
        try:
            await asyncio.wait_for(client._reconnect_event.wait(), timeout=delay)
            _LOGGER.debug("Initial connect woken up early (BLE advertisement)")
            delay = reconnect_delay
        except asyncio.TimeoutError:
            pass
        delay = min(delay * 2, reconnect_max_delay)


SERVICE_BREW_FREESTYLE = "brew_freestyle"
SERVICE_BREW_DIRECTKEY = "brew_directkey"
SERVICE_SAVE_DIRECTKEY = "save_directkey"
SERVICE_RESET_RECIPE = "reset_recipe"
SERVICE_CONFIRM_PROMPT = "confirm_prompt"
SERVICE_WRITE_RECIPE_PARAM = "nivona_write_recipe_param"
SERVICE_WRITE_MYCOFFEE_PARAM = "nivona_write_mycoffee_param"
SERVICE_REPAIR_CONNECTION = "repair_connection"

_NIVONA_PARAM_KEYS = [
    "strength", "profile", "two_cups", "temperature",
    "coffee_temperature", "water_temperature", "milk_temperature",
    "milk_foam_temperature", "overall_temperature",
    "coffee_amount", "water_amount", "milk_amount", "milk_foam_amount",
    "preparation", "enabled", "icon",
]

_PROCESS_MAP = PROCESS_MAP
_INTENSITY_MAP = INTENSITY_MAP
_AROMA_MAP = AROMA_MAP
_TEMPERATURE_MAP = TEMPERATURE_MAP
_SHOTS_MAP = SHOTS_MAP

_DIRECTKEY_CATEGORIES = [
    "espresso", "cafe_creme", "cappuccino",
    "latte_macchiato", "milk_froth", "milk", "water",
]

BREW_DIRECTKEY_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.entity_id,
    vol.Required("category"): vol.In(_DIRECTKEY_CATEGORIES),
    vol.Optional("two_cups", default=False): cv.boolean,
})

RESET_RECIPE_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.entity_id,
    vol.Optional("recipe_id"): vol.All(int, vol.Range(min=200, max=223)),
})

CONFIRM_PROMPT_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.entity_id,
})

NIVONA_WRITE_RECIPE_PARAM_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.entity_id,
    vol.Required("selector"): vol.All(int, vol.Range(min=0, max=255)),
    vol.Required("param_key"): vol.In(_NIVONA_PARAM_KEYS),
    vol.Required("value"): vol.All(int, vol.Range(min=-32768, max=2147483647)),
})

NIVONA_WRITE_MYCOFFEE_PARAM_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.entity_id,
    vol.Required("slot"): vol.All(int, vol.Range(min=0, max=31)),
    vol.Required("param_key"): vol.In(_NIVONA_PARAM_KEYS),
    vol.Required("value"): vol.All(int, vol.Range(min=-32768, max=2147483647)),
})

SAVE_DIRECTKEY_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.entity_id,
    vol.Required("category"): vol.In(_DIRECTKEY_CATEGORIES),
    vol.Optional("profile_id"): vol.All(int, vol.Range(min=0, max=8)),
    vol.Required("process1", default="coffee"): vol.In(_PROCESS_MAP),
    vol.Optional("intensity1", default="medium"): vol.In(_INTENSITY_MAP),
    vol.Optional("aroma1", default="standard"): vol.In(_AROMA_MAP),
    vol.Optional("portion1_ml", default=40): vol.All(int, vol.Range(min=5, max=250)),
    vol.Optional("temperature1", default="normal"): vol.In(_TEMPERATURE_MAP),
    vol.Optional("shots1", default="one"): vol.In(_SHOTS_MAP),
    vol.Optional("process2", default="none"): vol.In(_PROCESS_MAP),
    vol.Optional("intensity2", default="medium"): vol.In(_INTENSITY_MAP),
    vol.Optional("aroma2", default="standard"): vol.In(_AROMA_MAP),
    vol.Optional("portion2_ml", default=0): vol.All(int, vol.Range(min=0, max=250)),
    vol.Optional("temperature2", default="normal"): vol.In(_TEMPERATURE_MAP),
    vol.Optional("shots2", default="none"): vol.In(_SHOTS_MAP),
})

BREW_FREESTYLE_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.entity_id,
    vol.Required("name", default="Custom"): cv.string,
    vol.Required("process1", default="coffee"): vol.In(_PROCESS_MAP),
    vol.Optional("intensity1", default="medium"): vol.In(_INTENSITY_MAP),
    vol.Optional("aroma1", default="standard"): vol.In(_AROMA_MAP),
    vol.Optional("portion1_ml", default=40): vol.All(int, vol.Range(min=5, max=250)),
    vol.Optional("temperature1", default="normal"): vol.In(_TEMPERATURE_MAP),
    vol.Optional("shots1", default="one"): vol.In(_SHOTS_MAP),
    vol.Optional("process2", default="none"): vol.In(_PROCESS_MAP),
    vol.Optional("intensity2", default="medium"): vol.In(_INTENSITY_MAP),
    vol.Optional("aroma2", default="standard"): vol.In(_AROMA_MAP),
    vol.Optional("portion2_ml", default=0): vol.All(int, vol.Range(min=0, max=250)),
    vol.Optional("temperature2", default="normal"): vol.In(_TEMPERATURE_MAP),
    vol.Optional("shots2", default="none"): vol.In(_SHOTS_MAP),
    vol.Optional("two_cups", default=False): cv.boolean,
})


def _async_resolve_clients_for_service(
    hass: HomeAssistant, call: "ServiceCall",
) -> list["MelittaBleClient"]:
    """Resolve target devices in a ServiceCall to MelittaBleClient objects.

    If the call has ``device_id`` targets, resolve them via the device
    registry; otherwise return all configured entries' clients
    (broadcast — useful for a service without explicit targeting).
    """
    from homeassistant.helpers import device_registry as dr  # noqa: PLC0415

    device_ids = set(call.data.get("device_id", []) or [])
    clients: list[MelittaBleClient] = []
    if device_ids:
        dev_reg = dr.async_get(hass)
        for did in device_ids:
            device = dev_reg.async_get(did)
            if device is None:
                continue
            for cfg_entry_id in device.config_entries:
                entry = hass.config_entries.async_get_entry(cfg_entry_id)
                if entry and entry.domain == DOMAIN and hasattr(entry, "runtime_data"):
                    clients.append(entry.runtime_data)
    else:
        for entry in hass.config_entries.async_entries(DOMAIN):
            if hasattr(entry, "runtime_data"):
                clients.append(entry.runtime_data)
    return clients


def _async_register_services(hass: HomeAssistant) -> None:
    """Register integration services (idempotent)."""
    if hass.services.has_service(DOMAIN, SERVICE_BREW_FREESTYLE):
        return

    from .protocol import RecipeComponent  # noqa: PLC0415

    def _find_client(entity_id: str) -> MelittaBleClient | None:
        """Find the MelittaBleClient owning a given entity_id.

        Resolves through the entity registry's `config_entry_id`. Earlier
        versions matched by `c.address in ent.unique_id` (substring), which
        is fragile when two machines share a MAC prefix — every entity's
        unique_id contains its address, so a shorter address could be a
        substring of a longer machine's entity unique_id and return the
        wrong client.
        """
        registry = er.async_get(hass)
        ent = registry.async_get(entity_id)
        if ent is None or ent.config_entry_id is None:
            return None
        entry = hass.config_entries.async_get_entry(ent.config_entry_id)
        if entry is None or entry.domain != DOMAIN:
            return None
        if not hasattr(entry, "runtime_data") or not entry.runtime_data:
            return None
        return entry.runtime_data

    async def _handle_brew_freestyle(call: ServiceCall) -> None:
        """Handle brew_freestyle service call."""
        client = _find_client(call.data["entity_id"])
        if client is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="device_not_found",
            )

        comp1 = RecipeComponent(
            process=_PROCESS_MAP[call.data["process1"]],
            shots=_SHOTS_MAP[call.data.get("shots1", "one")],
            blend=1,  # BLEND_1
            intensity=_INTENSITY_MAP[call.data.get("intensity1", "medium")],
            aroma=_AROMA_MAP[call.data.get("aroma1", "standard")],
            temperature=_TEMPERATURE_MAP[call.data.get("temperature1", "normal")],
            portion=call.data.get("portion1_ml", 40) // 5,
        )

        comp2 = RecipeComponent(
            process=_PROCESS_MAP[call.data.get("process2", "none")],
            shots=_SHOTS_MAP[call.data.get("shots2", "none")],
            blend=0,  # BARISTA_T
            intensity=_INTENSITY_MAP[call.data.get("intensity2", "medium")],
            aroma=_AROMA_MAP[call.data.get("aroma2", "standard")],
            temperature=_TEMPERATURE_MAP[call.data.get("temperature2", "normal")],
            portion=call.data.get("portion2_ml", 0) // 5,
        )

        from .const import FREESTYLE_RECIPE_TYPE  # noqa: PLC0415
        two_cups = call.data.get("two_cups", False)
        success = await client.brew_freestyle(
            name=call.data["name"],
            recipe_type=FREESTYLE_RECIPE_TYPE,
            component1=comp1,
            component2=comp2,
            two_cups=two_cups,
        )
        if not success:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="brew_failed",
            )

    hass.services.async_register(
        DOMAIN, SERVICE_BREW_FREESTYLE, _handle_brew_freestyle,
        schema=BREW_FREESTYLE_SCHEMA,
    )

    from .const import DirectKeyCategory  # noqa: PLC0415

    _CATEGORY_MAP = {
        "espresso": DirectKeyCategory.ESPRESSO,
        "cafe_creme": DirectKeyCategory.CAFE_CREME,
        "cappuccino": DirectKeyCategory.CAPPUCCINO,
        "latte_macchiato": DirectKeyCategory.LATTE_MACCHIATO,
        "milk_froth": DirectKeyCategory.MILK_FROTH,
        "milk": DirectKeyCategory.MILK,
        "water": DirectKeyCategory.WATER,
    }

    async def _handle_brew_directkey(call: ServiceCall) -> None:
        """Handle brew_directkey service call."""
        client = _find_client(call.data["entity_id"])
        if client is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="device_not_found",
            )

        category = _CATEGORY_MAP[call.data["category"]]
        two_cups = call.data.get("two_cups", False)
        success = await client.brew_directkey(category, two_cups=two_cups)
        if not success:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="brew_failed",
            )

    async def _handle_save_directkey(call: ServiceCall) -> None:
        """Handle save_directkey service call."""
        client = _find_client(call.data["entity_id"])
        if client is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="device_not_found",
            )

        category = _CATEGORY_MAP[call.data["category"]]
        profile_id = call.data.get("profile_id", client.active_profile)

        comp1 = RecipeComponent(
            process=_PROCESS_MAP[call.data["process1"]],
            shots=_SHOTS_MAP[call.data.get("shots1", "one")],
            blend=1,
            intensity=_INTENSITY_MAP[call.data.get("intensity1", "medium")],
            aroma=_AROMA_MAP[call.data.get("aroma1", "standard")],
            temperature=_TEMPERATURE_MAP[call.data.get("temperature1", "normal")],
            portion=call.data.get("portion1_ml", 40) // 5,
        )

        comp2 = RecipeComponent(
            process=_PROCESS_MAP[call.data.get("process2", "none")],
            shots=_SHOTS_MAP[call.data.get("shots2", "none")],
            blend=0,
            intensity=_INTENSITY_MAP[call.data.get("intensity2", "medium")],
            aroma=_AROMA_MAP[call.data.get("aroma2", "standard")],
            temperature=_TEMPERATURE_MAP[call.data.get("temperature2", "normal")],
            portion=call.data.get("portion2_ml", 0) // 5,
        )

        success = await client.write_profile_recipe(
            profile_id, category, comp1, comp2,
        )
        if not success:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="save_failed",
            )
        _LOGGER.info(
            "Saved DirectKey recipe: profile=%d, category=%s",
            profile_id, call.data["category"],
        )

    async def _handle_reset_recipe(call: ServiceCall) -> None:
        """Handle reset_recipe service call."""
        client = _find_client(call.data["entity_id"])
        if client is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="device_not_found",
            )
        recipe_id = call.data.get("recipe_id")
        if recipe_id is None:
            current = client.selected_recipe
            if current is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="no_recipe_selected",
                )
            recipe_id = int(current)
        success = await client.reset_recipe_default(recipe_id)
        if not success:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="reset_failed",
            )
        _LOGGER.info("Reset recipe %d to factory defaults", recipe_id)

    hass.services.async_register(
        DOMAIN, SERVICE_BREW_DIRECTKEY, _handle_brew_directkey,
        schema=BREW_DIRECTKEY_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SAVE_DIRECTKEY, _handle_save_directkey,
        schema=SAVE_DIRECTKEY_SCHEMA,
    )
    async def _handle_confirm_prompt(call: ServiceCall) -> None:
        """Handle confirm_prompt service call."""
        client = _find_client(call.data["entity_id"])
        if client is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="device_not_found",
            )
        success = await client.confirm_prompt()
        if not success:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="confirm_failed",
            )
        _LOGGER.info("Confirmed machine prompt via service call")

    hass.services.async_register(
        DOMAIN, SERVICE_RESET_RECIPE, _handle_reset_recipe,
        schema=RESET_RECIPE_SCHEMA,
    )
    async def _handle_write_recipe_param(call: ServiceCall) -> None:
        client = _find_client(call.data["entity_id"])
        if client is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="device_not_found",
            )
        success = await client.write_standard_recipe_param(
            call.data["selector"], call.data["param_key"], call.data["value"],
        )
        if not success:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="write_failed",
            )
        _LOGGER.info(
            "Wrote standard recipe param: selector=%d, %s=%d",
            call.data["selector"], call.data["param_key"], call.data["value"],
        )

    async def _handle_write_mycoffee_param(call: ServiceCall) -> None:
        client = _find_client(call.data["entity_id"])
        if client is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="device_not_found",
            )
        success = await client.write_mycoffee_param(
            call.data["slot"], call.data["param_key"], call.data["value"],
        )
        if not success:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="write_failed",
            )
        _LOGGER.info(
            "Wrote MyCoffee param: slot=%d, %s=%d",
            call.data["slot"], call.data["param_key"], call.data["value"],
        )

    hass.services.async_register(
        DOMAIN, SERVICE_CONFIRM_PROMPT, _handle_confirm_prompt,
        schema=CONFIRM_PROMPT_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_WRITE_RECIPE_PARAM, _handle_write_recipe_param,
        schema=NIVONA_WRITE_RECIPE_PARAM_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_WRITE_MYCOFFEE_PARAM, _handle_write_mycoffee_param,
        schema=NIVONA_WRITE_MYCOFFEE_PARAM_SCHEMA,
    )

    async def _handle_repair_connection(call: ServiceCall) -> None:
        """Manual recovery for a wedged pairing.

        Walks every melitta_barista config entry and triggers the repair
        routine (reload the ESPHome proxy entry that owns the peer).
        Lightweight: at most one ESPHome reload per unique proxy entry,
        even if several Melitta machines share the same proxy.
        """
        reloaded: set[str] = set()
        for entry in hass.config_entries.async_entries(DOMAIN):
            client: MelittaBleClient | None = getattr(entry, "runtime_data", None)
            if client is None:
                continue
            proxy_entry = _find_proxy_entry_for_address(hass, client.address)
            if proxy_entry and proxy_entry.entry_id in reloaded:
                _LOGGER.info(
                    "repair_connection: proxy %s already scheduled for reload",
                    proxy_entry.entry_id,
                )
                continue
            triggered = await _async_repair_pairing(hass, entry)
            if proxy_entry is not None and triggered:
                reloaded.add(proxy_entry.entry_id)

    hass.services.async_register(
        DOMAIN, SERVICE_REPAIR_CONNECTION, _handle_repair_connection,
    )

    async def _handle_sync_clock(call: ServiceCall) -> None:
        """Push HA local time to the machine RTC (setting 21)."""
        clients = _async_resolve_clients_for_service(hass, call)
        if not clients:
            raise HomeAssistantError("No Melitta machine configured")
        now = dt_util.now()
        minutes = now.hour * 60 + now.minute
        any_fail = False
        for client in clients:
            if not client.connected:
                raise HomeAssistantError(
                    f"Machine {client.address} not connected",
                )
            ok = await client.write_setting(21, minutes)
            if not ok:
                any_fail = True
        if any_fail:
            raise HomeAssistantError("Clock sync write rejected by machine")

    if not hass.services.has_service(DOMAIN, SERVICE_SYNC_CLOCK):
        hass.services.async_register(
            DOMAIN, SERVICE_SYNC_CLOCK, _handle_sync_clock,
        )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        clock_coord = hass.data.get(DOMAIN, {}).pop(
            _async_clock_coordinator_key(entry.entry_id), None,
        )
        if clock_coord is not None:
            clock_coord.stop()
        client: MelittaBleClient = entry.runtime_data
        await client.disconnect()

    # On the last config entry: tear down domain-wide stuff (panel, Sommelier DB,
    # and the services registered globally for the domain). Gated on unload_ok so
    # we don't rip out shared resources while platform entities are still live.
    if unload_ok:
        remaining = [
            e for e in hass.config_entries.async_entries(DOMAIN)
            if e.entry_id != entry.entry_id
        ]
        if not remaining:
            _async_unregister_panel(hass)
            domain_data = hass.data.get(DOMAIN, {})
            db = domain_data.pop("sommelier_db", None)
            if db is not None:
                await db.async_close()
                _LOGGER.debug("Sommelier DB closed")

            for service in (
                SERVICE_BREW_FREESTYLE,
                SERVICE_BREW_DIRECTKEY,
                SERVICE_SAVE_DIRECTKEY,
                SERVICE_RESET_RECIPE,
                SERVICE_CONFIRM_PROMPT,
                SERVICE_WRITE_RECIPE_PARAM,
                SERVICE_WRITE_MYCOFFEE_PARAM,
                SERVICE_REPAIR_CONNECTION,
                SERVICE_SYNC_CLOCK,
            ):
                if hass.services.has_service(DOMAIN, service):
                    hass.services.async_remove(DOMAIN, service)

    return unload_ok


async def _async_update_listener(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Handle options update.

    Triggers a full reload of the config entry, which destroys and
    recreates the ClockSyncCoordinator with the new options.
    """
    await hass.config_entries.async_reload(entry.entry_id)
