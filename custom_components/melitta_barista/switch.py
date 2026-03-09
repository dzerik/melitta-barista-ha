"""Switch platform for Melitta Barista Smart machine settings."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from bleak.exc import BleakError

from .ble_client import MelittaBleClient
from .const import DOMAIN, MachineSettingId, MachineType, TS_ONLY_SETTINGS, get_user_profile_count

_LOGGER = logging.getLogger("melitta_barista")

SWITCH_DEFINITIONS: list[dict] = [
    {
        "id": MachineSettingId.ENERGY_SAVING,
        "name": "Energy Saving",
        "icon": "mdi:leaf",
        "category": EntityCategory.CONFIG,
    },
    {
        "id": MachineSettingId.AUTO_BEAN_SELECT,
        "name": "Auto Bean Select",
        "icon": "mdi:grain",
        "category": EntityCategory.CONFIG,
    },
    {
        "id": MachineSettingId.RINSING_OFF,
        "name": "Rinsing Disabled",
        "icon": "mdi:water-off",
        "category": EntityCategory.CONFIG,
    },
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Melitta Barista switch entities."""
    client: MelittaBleClient = entry.runtime_data
    name = entry.data.get(CONF_NAME, "Melitta Barista")

    entities = [
        MelittaSettingSwitch(client, entry, name, defn)
        for defn in SWITCH_DEFINITIONS
        if not (
            defn["id"] in TS_ONLY_SETTINGS
            and client.machine_type == MachineType.BARISTA_T
        )
    ]

    # Profile activity switches (profiles 1..N)
    profile_count = get_user_profile_count(client.machine_type)
    for i in range(1, profile_count + 1):
        entities.append(MelittaProfileActivitySwitch(client, entry, name, i))

    async_add_entities(entities)


class MelittaSettingSwitch(SwitchEntity):
    """Switch entity for a boolean machine setting."""

    _attr_has_entity_name = True

    def __init__(
        self,
        client: MelittaBleClient,
        entry: ConfigEntry,
        machine_name: str,
        defn: dict,
    ) -> None:
        self._client = client
        self._entry = entry
        self._machine_name = machine_name
        self._setting_id: int = defn["id"]
        self._attr_name = defn["name"]
        self._attr_icon = defn["icon"]
        self._attr_entity_category = defn.get("category")
        self._attr_is_on: bool | None = None

    @property
    def unique_id(self) -> str:
        return f"{self._client.address}_switch_{self._setting_id}"

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
        value = await self._client.read_setting(self._setting_id)
        if value is not None:
            self._attr_is_on = value != 0

    async def async_turn_on(self, **kwargs) -> None:
        if await self._client.write_setting(self._setting_id, 1):
            self._attr_is_on = True
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        if await self._client.write_setting(self._setting_id, 0):
            self._attr_is_on = False
            self.async_write_ha_state()


class MelittaProfileActivitySwitch(SwitchEntity):
    """Switch to enable/disable a user profile."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:account-check"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        client: MelittaBleClient,
        entry: ConfigEntry,
        machine_name: str,
        profile_id: int,
    ) -> None:
        self._client = client
        self._entry = entry
        self._machine_name = machine_name
        self._profile_id = profile_id
        self._attr_name = f"Profile {profile_id} Active"
        self._attr_is_on: bool | None = None

    @property
    def unique_id(self) -> str:
        return f"{self._client.address}_profile_{self._profile_id}_activity"

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
        try:
            value = await self._client.read_profile_activity(self._profile_id)
            if value is not None:
                self._attr_is_on = value != 0
        except (BleakError, OSError, asyncio.TimeoutError):
            _LOGGER.debug("Failed to read activity for profile %d", self._profile_id)

    async def async_turn_on(self, **kwargs) -> None:
        try:
            if await self._client.write_profile_activity(self._profile_id, 1):
                self._attr_is_on = True
                self.async_write_ha_state()
        except (BleakError, OSError, asyncio.TimeoutError):
            _LOGGER.exception("Failed to enable profile %d", self._profile_id)

    async def async_turn_off(self, **kwargs) -> None:
        try:
            if await self._client.write_profile_activity(self._profile_id, 0):
                self._attr_is_on = False
                self.async_write_ha_state()
        except (BleakError, OSError, asyncio.TimeoutError):
            _LOGGER.exception("Failed to disable profile %d", self._profile_id)
