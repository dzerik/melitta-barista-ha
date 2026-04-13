"""Binary sensor platform for Melitta Barista Smart."""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .ble_client import MelittaBleClient
from .const import PROMPT_MANIPULATIONS
from .entity import MelittaDeviceMixin
from .protocol import MachineStatus

PARALLEL_UPDATES = 0  # BLE: single connection, serialize via locks

_LOGGER = logging.getLogger("melitta_barista")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Melitta Barista binary sensors."""
    client: MelittaBleClient = entry.runtime_data
    name = entry.data.get(CONF_NAME, "Melitta Barista")
    async_add_entities(
        [MelittaAwaitingConfirmationBinarySensor(client, entry, name)]
    )


class MelittaAwaitingConfirmationBinarySensor(MelittaDeviceMixin, BinarySensorEntity):
    """Reports whether the machine is waiting on a user-confirmable prompt."""

    _attr_name = "Awaiting Confirmation"
    _attr_icon = "mdi:hand-back-right-outline"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_has_entity_name = True

    def __init__(
        self, client: MelittaBleClient, entry: ConfigEntry, name: str,
    ) -> None:
        self._client = client
        self._entry = entry
        self._machine_name = name

    async def async_added_to_hass(self) -> None:
        self._client.add_status_callback(self._on_status_update)
        self._client.add_connection_callback(self._on_connection_change)

    async def async_will_remove_from_hass(self) -> None:
        self._client.remove_status_callback(self._on_status_update)
        self._client.remove_connection_callback(self._on_connection_change)

    @callback
    def _on_status_update(self, status: MachineStatus) -> None:
        self.async_write_ha_state()

    @callback
    def _on_connection_change(self, connected: bool) -> None:
        self.async_write_ha_state()

    @property
    def unique_id(self) -> str:
        return f"{self._client.address}_awaiting_confirmation"

    @property
    def is_on(self) -> bool:
        status = self._client.status
        return bool(status and status.manipulation in PROMPT_MANIPULATIONS)
