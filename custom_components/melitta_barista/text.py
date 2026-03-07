"""Text platform for Melitta Barista Smart user profile names."""

from __future__ import annotations

import logging

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .ble_client import MelittaBleClient
from .const import DOMAIN, USER_NAME_IDS, get_user_profile_count

_LOGGER = logging.getLogger("melitta_barista")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Melitta Barista text entities for user profile names."""
    client: MelittaBleClient = entry.runtime_data
    name = entry.data.get(CONF_NAME, "Melitta Barista")

    profile_count = get_user_profile_count(client.machine_type)

    entities = [
        MelittaProfileNameText(client, entry, name, profile_num)
        for profile_num in range(1, profile_count + 1)
    ]
    entities.append(MelittaFreestyleNameText(client, entry, name))
    async_add_entities(entities)


class MelittaProfileNameText(TextEntity):
    """Text entity for a user profile name."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_max = 64

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
        self._attr_native_value: str | None = None

    @property
    def unique_id(self) -> str:
        return f"{self._client.address}_profile_{self._profile_num}_name"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._client.address)},
            name=self._machine_name,
            manufacturer="Melitta",
            model=self._client.model_name,
        )

    @property
    def available(self) -> bool:
        return self._client.connected

    async def async_added_to_hass(self) -> None:
        self._client.add_connection_callback(self._on_connection_change)

    @callback
    def _on_connection_change(self, connected: bool) -> None:
        self.async_write_ha_state()

    async def async_update(self) -> None:
        value = await self._client.read_alpha(self._name_id)
        if value is not None:
            self._attr_native_value = value

    async def async_set_value(self, value: str) -> None:
        if await self._client.write_alpha(self._name_id, value):
            self._attr_native_value = value
            self.async_write_ha_state()


class MelittaFreestyleNameText(TextEntity):
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
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._client.address)},
            name=self._machine_name,
            manufacturer="Melitta",
            model=self._client.model_name,
        )

    @property
    def native_value(self) -> str | None:
        return self._client.freestyle_name

    @property
    def available(self) -> bool:
        return self._client.connected

    async def async_added_to_hass(self) -> None:
        self._client.add_connection_callback(self._on_connection_change)

    @callback
    def _on_connection_change(self, connected: bool) -> None:
        self.async_write_ha_state()

    async def async_set_value(self, value: str) -> None:
        self._client.freestyle_name = value
        self.async_write_ha_state()
