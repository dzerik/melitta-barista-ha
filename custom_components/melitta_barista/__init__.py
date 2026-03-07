"""The Melitta Barista Smart integration."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_NAME, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .ble_client import MelittaBleClient
from .const import DOMAIN

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

    Previously each recipe had its own button (unique_id = {address}_brew_{200..223}).
    Now there is a single Brew button + Recipe select entity.
    """
    registry = er.async_get(hass)
    # Old recipe button unique_ids: {address}_brew_200 .. {address}_brew_223
    removed = 0
    for recipe_value in range(200, 224):
        unique_id = f"{address}_brew_{recipe_value}"
        entity_id = registry.async_get_entity_id("button", DOMAIN, unique_id)
        if entity_id:
            registry.async_remove(entity_id)
            removed += 1
    if removed:
        _LOGGER.info("Cleaned up %d legacy recipe button entities", removed)


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
    except Exception:
        _LOGGER.debug("Could not get BLEDevice from cache", exc_info=True)

    client = MelittaBleClient(
        address,
        device_name=device_name,
        ble_device=ble_device,
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
    except Exception:
        _LOGGER.warning(
            "Could not register bluetooth callback for %s, "
            "BLEDevice updates from advertisements won't work",
            address,
            exc_info=True,
        )

    entry.runtime_data = client

    # Clean up legacy per-recipe button entities (v0.5.x → v0.6.0 migration)
    _async_cleanup_legacy_recipe_buttons(hass, entry, address)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _LOGGER.debug("Platforms forwarded")

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # Connect in background so we don't block HA setup
    hass.async_create_task(
        _async_connect_and_poll(client),
        f"melitta_barista_connect_{address}",
    )
    _LOGGER.info("Setup complete for %s, connecting in background", address)

    return True


async def _async_connect_and_poll(client: MelittaBleClient) -> None:
    """Connect to the machine in background -- does not block setup."""
    try:
        # Wait for the machine to release any prior BLE connection (e.g. from pairing)
        await asyncio.sleep(3)
        _LOGGER.debug("Background connect starting for %s", client.address)
        if await client.connect():
            _LOGGER.info("Connected to %s, starting polling", client.address)
            client.start_polling(interval=5.0)
        else:
            _LOGGER.warning(
                "Initial connection to %s failed, will retry in background",
                client.address,
            )
    except Exception:
        _LOGGER.exception(
            "Unexpected error connecting to %s", client.address
        )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        client: MelittaBleClient = entry.runtime_data
        await client.disconnect()

    return unload_ok


async def _async_update_listener(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)
