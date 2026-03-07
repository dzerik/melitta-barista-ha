"""Number platform for Melitta Barista Smart machine settings."""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .ble_client import MelittaBleClient
from .const import DOMAIN, MachineSettingId

_LOGGER = logging.getLogger("melitta_barista")


SETTING_DEFINITIONS: list[dict] = [
    {
        "id": MachineSettingId.WATER_HARDNESS,
        "name": "Water Hardness",
        "icon": "mdi:water-opacity",
        "min": 1,
        "max": 4,
        "step": 1,
        "mode": NumberMode.SLIDER,
        "category": EntityCategory.CONFIG,
    },
    {
        "id": MachineSettingId.AUTO_OFF_AFTER,
        "name": "Auto Off After",
        "icon": "mdi:timer-off-outline",
        "min": 15,
        "max": 240,
        "step": 15,
        "unit": UnitOfTime.MINUTES,
        "mode": NumberMode.BOX,
        "category": EntityCategory.CONFIG,
    },
    {
        "id": MachineSettingId.TEMPERATURE,
        "name": "Brew Temperature",
        "icon": "mdi:thermometer",
        "min": 0,
        "max": 2,
        "step": 1,
        "mode": NumberMode.SLIDER,
        "category": EntityCategory.CONFIG,
    },
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Melitta Barista number entities."""
    client: MelittaBleClient = entry.runtime_data
    name = entry.data.get(CONF_NAME, "Melitta Barista")

    entities = [
        MelittaSettingNumber(client, entry, name, defn)
        for defn in SETTING_DEFINITIONS
    ]
    async_add_entities(entities)


class MelittaSettingNumber(NumberEntity):
    """Number entity for a machine setting."""

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
        self._attr_native_min_value = defn["min"]
        self._attr_native_max_value = defn["max"]
        self._attr_native_step = defn["step"]
        self._attr_mode = defn.get("mode", NumberMode.AUTO)
        self._attr_entity_category = defn.get("category")
        if "unit" in defn:
            self._attr_native_unit_of_measurement = defn["unit"]
        self._attr_native_value: float | None = None

    @property
    def unique_id(self) -> str:
        return f"{self._client.address}_setting_{self._setting_id}"

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

    async def async_update(self) -> None:
        value = await self._client.read_setting(self._setting_id)
        if value is not None:
            self._attr_native_value = float(value)

    async def async_set_native_value(self, value: float) -> None:
        if await self._client.write_setting(self._setting_id, int(value)):
            self._attr_native_value = value
            self.async_write_ha_state()
