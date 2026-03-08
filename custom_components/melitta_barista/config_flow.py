"""Config flow for Melitta Barista Smart."""

import logging
from typing import Any

import voluptuous as vol
from bleak import BleakScanner
from bleak.exc import BleakError
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.data_entry_flow import FlowResult

from .ble_client import MELITTA_SERVICE_UUID
from .const import BLE_PREFIXES_ALL, DOMAIN

_LOGGER = logging.getLogger("melitta_barista")

PAIR_TIMEOUT = 30.0


class MelittaBaristaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Melitta Barista Smart."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered_devices: dict[str, str] = {}
        self._address: str | None = None
        self._name: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle user-initiated setup."""
        errors: dict[str, str] = {}

        if user_input is not None:
            if user_input.get(CONF_ADDRESS) == "manual":
                return await self.async_step_manual()

            address = user_input[CONF_ADDRESS]
            name = self._discovered_devices.get(address, "Melitta Barista Smart")

            await self.async_set_unique_id(address.replace(":", "").lower())
            self._abort_if_unique_id_configured()

            self._address = address
            self._name = name
            return await self.async_step_pair()

        # Scan for devices
        await self._async_discover_devices()

        if not self._discovered_devices:
            return await self.async_step_manual()

        # Add manual option
        options = {
            addr: f"{name} ({addr})"
            for addr, name in self._discovered_devices.items()
        }
        options["manual"] = "Enter address manually..."

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_ADDRESS): vol.In(options),
            }),
            errors=errors,
        )

    async def _async_discover_devices(self) -> None:
        """Discover Melitta devices via HA bluetooth and BLE scan."""
        self._discovered_devices = {}

        # Try HA bluetooth integration first
        try:
            for info in async_discovered_service_info(self.hass):
                for uuid in info.service_uuids:
                    if MELITTA_SERVICE_UUID in uuid.lower():
                        self._discovered_devices[info.address] = (
                            info.name or f"Melitta ({info.address})"
                        )
        except (AttributeError, ValueError):
            _LOGGER.debug("HA bluetooth not available, falling back to direct scan")

        # Fallback: direct BLE scan
        if not self._discovered_devices:
            try:
                devices = await BleakScanner.discover(timeout=10.0)
                for device in devices:
                    if device.name and (
                        any(device.name.startswith(p) for p in BLE_PREFIXES_ALL)
                        or "melitta" in device.name.lower()
                        or "barista" in device.name.lower()
                    ):
                        self._discovered_devices[device.address] = device.name
            except (OSError, BleakError):
                _LOGGER.exception("BLE scan failed")

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle manual address entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            address = user_input[CONF_ADDRESS].strip().upper()
            name = user_input.get(CONF_NAME, "Melitta Barista Smart")

            # Basic MAC address validation
            if len(address.replace(":", "").replace("-", "")) != 12:
                errors[CONF_ADDRESS] = "invalid_address"
            else:
                # Normalize address format
                clean = address.replace(":", "").replace("-", "")
                address = ":".join(clean[i:i+2] for i in range(0, 12, 2))

                await self.async_set_unique_id(clean.lower())
                self._abort_if_unique_id_configured()

                self._address = address
                self._name = name
                return await self.async_step_pair()

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({
                vol.Required(CONF_ADDRESS): str,
                vol.Optional(CONF_NAME, default="Melitta Barista Smart"): str,
            }),
            errors=errors,
        )

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> FlowResult:
        """Handle bluetooth discovery."""
        address = discovery_info.address
        name = discovery_info.name or "Melitta Barista Smart"

        await self.async_set_unique_id(address.replace(":", "").lower())
        self._abort_if_unique_id_configured()

        self._address = address
        self._name = name

        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm bluetooth discovery."""
        if user_input is not None:
            return await self.async_step_pair()

        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={
                "name": self._name or "Melitta Barista Smart",
            },
        )

    async def async_step_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Pair with the coffee machine via BLE."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # User pressed submit — attempt pairing
            pair_result = await self._async_try_pair()

            if pair_result == "ok":
                return self.async_create_entry(
                    title=self._name or "Melitta Barista Smart",
                    data={
                        CONF_ADDRESS: self._address,
                        CONF_NAME: self._name,
                    },
                )
            else:
                errors["base"] = pair_result

        return self.async_show_form(
            step_id="pair",
            description_placeholders={
                "name": self._name or "Melitta Barista Smart",
                "address": self._address or "",
            },
            errors=errors,
        )

    async def _async_try_pair(self) -> str:
        """Attempt pre-pairing with the device.

        Tries D-Bus BlueZ Agent1 pairing first (works with local BT adapter).
        If D-Bus is unavailable (ESPHome proxy, non-Linux, container),
        returns "ok" — pairing will be handled automatically by Bleak's
        pair=True parameter during the actual BLE connection.
        """
        if not self._address:
            return "cannot_connect"

        try:
            from .ble_agent import async_pair_device
        except ImportError:
            _LOGGER.info(
                "dbus-fast not available — pairing will be handled "
                "automatically by Bleak on connect (pair=True)"
            )
            return "ok"

        return await async_pair_device(self._address, timeout=PAIR_TIMEOUT)
