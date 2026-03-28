"""The Melitta Barista Smart integration."""

from __future__ import annotations

import asyncio
import logging

from bleak.exc import BleakError
from homeassistant.components import bluetooth
import voluptuous as vol

import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from homeassistant.const import CONF_ADDRESS, CONF_NAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv, entity_registry as er

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
    DEFAULT_RECONNECT_DELAY,
    DEFAULT_RECONNECT_MAX_DELAY,
    DEFAULT_MAX_CONSECUTIVE_ERRORS,
    DEFAULT_FRAME_TIMEOUT,
    DEFAULT_BLE_CONNECT_TIMEOUT,
    DEFAULT_RECIPE_RETRIES,
    DEFAULT_INITIAL_CONNECT_DELAY,
)

_LOGGER = logging.getLogger("melitta_barista")

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BUTTON,
    Platform.SELECT,
    Platform.NUMBER,
    Platform.SWITCH,
    Platform.TEXT,
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


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Melitta Barista Smart from a config entry."""
    address: str = entry.data[CONF_ADDRESS]
    device_name: str | None = entry.data.get(CONF_NAME)

    _LOGGER.info(
        "Setting up Melitta Barista for %s (%s)", device_name or "unknown", address
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

    # Track disconnects for repair issue (connection instability warning)
    disconnect_times: list[float] = []
    max_disconnects_per_hour = 5
    issue_id = f"connection_unstable_{address}"

    @callback
    def _track_connection(connected: bool) -> None:
        if connected:
            # Clear repair issue on successful reconnect
            ir.async_delete_issue(hass, DOMAIN, issue_id)
            return
        disconnect_times.append(time.monotonic())
        # Keep only last hour
        cutoff = time.monotonic() - 3600
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

    # Clean up legacy per-recipe button entities (v0.5.x → v0.6.0 migration)
    _async_cleanup_legacy_recipe_buttons(hass, entry, address)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _LOGGER.debug("Platforms forwarded")

    # Register freestyle service (once per integration)
    _async_register_services(hass)

    # Register AI Coffee Sommelier WebSocket handlers (DB init is lazy on first call)
    _async_register_sommelier(hass)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # Connect in background so we don't block HA setup
    connect_task = hass.async_create_task(
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


def _async_register_services(hass: HomeAssistant) -> None:
    """Register integration services (idempotent)."""
    if hass.services.has_service(DOMAIN, SERVICE_BREW_FREESTYLE):
        return

    from .protocol import RecipeComponent  # noqa: PLC0415

    def _find_client(entity_id: str) -> MelittaBleClient | None:
        """Find the MelittaBleClient for a given entity_id."""
        registry = er.async_get(hass)
        ent = registry.async_get(entity_id)
        for entry in hass.config_entries.async_entries(DOMAIN):
            if hasattr(entry, "runtime_data") and entry.runtime_data:
                c: MelittaBleClient = entry.runtime_data
                if ent and c.address in (ent.unique_id or ""):
                    return c
        return None

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

    hass.services.async_register(
        DOMAIN, SERVICE_BREW_DIRECTKEY, _handle_brew_directkey,
        schema=BREW_DIRECTKEY_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SAVE_DIRECTKEY, _handle_save_directkey,
        schema=SAVE_DIRECTKEY_SCHEMA,
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        client: MelittaBleClient = entry.runtime_data
        await client.disconnect()

    # Close Sommelier DB if this is the last config entry
    remaining = [
        e for e in hass.config_entries.async_entries(DOMAIN)
        if e.entry_id != entry.entry_id
    ]
    if not remaining:
        domain_data = hass.data.get(DOMAIN, {})
        db = domain_data.pop("sommelier_db", None)
        if db is not None:
            await db.async_close()
            _LOGGER.debug("Sommelier DB closed")

    return unload_ok


async def _async_update_listener(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)
