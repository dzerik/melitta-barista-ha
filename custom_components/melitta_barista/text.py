"""Text platform — Melitta-only user-profile names (HA/HJ extensions)."""

from __future__ import annotations

import logging

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .ble_client import MelittaBleClient
from .const import USER_NAME_IDS, get_user_profile_count
from .entity import MelittaDeviceMixin


PARALLEL_UPDATES = 0  # BLE: single connection, serialize via locks

_LOGGER = logging.getLogger("melitta_barista")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up user-profile-name text entities (Melitta HA/HJ feature)."""
    client: MelittaBleClient = entry.runtime_data
    name = entry.data.get(CONF_NAME) or f"{client.brand.brand_name} Coffee Machine"

    entities: list = []
    if "HA" in client.brand.supported_extensions or "HC" in client.brand.supported_extensions:
        # Profile names + freestyle name require Melitta-style HA strings & HJ writes
        profile_count = get_user_profile_count(client.machine_type)
        entities = [
            MelittaProfileNameText(client, entry, name, profile_num)
            for profile_num in range(1, profile_count + 1)
        ]
        if "HJ" in client.brand.supported_extensions:
            entities.append(MelittaFreestyleNameText(client, entry, name))
    async_add_entities(entities)


class MelittaProfileNameText(MelittaDeviceMixin, TextEntity):
    """Text entity for a user profile name.

    Reads from the cached profile_names dict (populated at connect time)
    instead of doing BLE reads on every HA poll cycle.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_max = 64
    _attr_should_poll = False

    def __init__(
        self,
        client: MelittaBleClient,
        entry: ConfigEntry,
        machine_name: str,
        profile_num: int,
    ) -> None:
        self._client = client
        self._entry = entry
        self._machine_name = machine_name
        self._profile_num = profile_num
        self._name_id = USER_NAME_IDS[profile_num]
        self._attr_name = f"Profile {profile_num} Name"
        self._attr_icon = "mdi:account"

    @property
    def unique_id(self) -> str:
        return f"{self._client.address}_profile_{self._profile_num}_name"

    @property
    def native_value(self) -> str | None:
        return self._client.profile_names.get(self._profile_num)

    @property
    def available(self) -> bool:
        return self._client.connected

    async def async_added_to_hass(self) -> None:
        self._client.add_connection_callback(self._on_connection_change)
        self._client.add_profile_callback(self._on_profile_data_change)

    async def async_will_remove_from_hass(self) -> None:
        self._client.remove_connection_callback(self._on_connection_change)
        self._client.remove_profile_callback(self._on_profile_data_change)

    @callback
    def _on_connection_change(self, connected: bool) -> None:
        self.async_write_ha_state()

    @callback
    def _on_profile_data_change(self) -> None:
        self.async_write_ha_state()

    async def async_set_value(self, value: str) -> None:
        if await self._client.write_alpha(self._name_id, value):
            self.async_write_ha_state()


class MelittaFreestyleNameText(MelittaDeviceMixin, TextEntity):
    """Text entity for the freestyle recipe name."""

    _attr_has_entity_name = True
    _attr_name = "Freestyle Name"
    _attr_icon = "mdi:label-outline"
    _attr_native_max = 30

    def __init__(
        self,
        client: MelittaBleClient,
        entry: ConfigEntry,
        machine_name: str,
    ) -> None:
        self._client = client
        self._entry = entry
        self._machine_name = machine_name

    @property
    def unique_id(self) -> str:
        return f"{self._client.address}_freestyle_name"

    @property
    def native_value(self) -> str | None:
        return self._client.freestyle_name

    @property
    def available(self) -> bool:
        return self._client.connected

    async def async_added_to_hass(self) -> None:
        self._client.add_connection_callback(self._on_connection_change)

    async def async_will_remove_from_hass(self) -> None:
        self._client.remove_connection_callback(self._on_connection_change)

    @callback
    def _on_connection_change(self, connected: bool) -> None:
        self.async_write_ha_state()

    async def async_set_value(self, value: str) -> None:
        self._client.freestyle_name = value
        self.async_write_ha_state()
