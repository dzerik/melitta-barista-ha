"""Number platform for Melitta Barista Smart machine settings."""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME, UnitOfTime
from homeassistant.core import HomeAssistant, callback
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
    {
        "id": MachineSettingId.LANGUAGE,
        "name": "Language",
        "icon": "mdi:translate",
        "min": 0,
        "max": 15,
        "step": 1,
        "mode": NumberMode.BOX,
        "category": EntityCategory.CONFIG,
    },
    {
        "id": MachineSettingId.CLOCK,
        "name": "Clock",
        "icon": "mdi:clock-outline",
        "min": 0,
        "max": 1440,
        "step": 1,
        "mode": NumberMode.BOX,
        "category": EntityCategory.CONFIG,
    },
    {
        "id": MachineSettingId.CLOCK_SEND,
        "name": "Clock Send",
        "icon": "mdi:clock-check-outline",
        "min": 0,
        "max": 1440,
        "step": 1,
        "mode": NumberMode.BOX,
        "category": EntityCategory.CONFIG,
    },
    {
        "id": MachineSettingId.FILTER,
        "name": "Filter",
        "icon": "mdi:filter-outline",
        "min": 0,
        "max": 1,
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
    # Freestyle portion entities
    entities.append(MelittaFreestyleNumber(
        client, entry, name, "portion_1", "Freestyle Portion 1",
        "mdi:cup-water", 5, 250, 5, "freestyle_portion1_ml",
    ))
    entities.append(MelittaFreestyleNumber(
        client, entry, name, "portion_2", "Freestyle Portion 2",
        "mdi:cup-water", 0, 250, 5, "freestyle_portion2_ml",
    ))
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

    async def async_added_to_hass(self) -> None:
        self._client.add_connection_callback(self._on_connection_change)

    @callback
    def _on_connection_change(self, connected: bool) -> None:
        self.async_write_ha_state()

    async def async_update(self) -> None:
        value = await self._client.read_setting(self._setting_id)
        if value is not None:
            self._attr_native_value = float(value)

    async def async_set_native_value(self, value: float) -> None:
        if await self._client.write_setting(self._setting_id, int(value)):
            self._attr_native_value = value
            self.async_write_ha_state()


class MelittaFreestyleNumber(NumberEntity):
    """Number entity for a freestyle recipe portion parameter."""

    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = "ml"

    def __init__(
        self,
        client: MelittaBleClient,
        entry: ConfigEntry,
        machine_name: str,
        key: str,
        label: str,
        icon: str,
        min_val: int,
        max_val: int,
        step: int,
        client_attr: str,
    ) -> None:
        self._client = client
        self._entry = entry
        self._machine_name = machine_name
        self._key = key
        self._client_attr = client_attr
        self._attr_name = label
        self._attr_icon = icon
        self._attr_native_min_value = min_val
        self._attr_native_max_value = max_val
        self._attr_native_step = step

    @property
    def unique_id(self) -> str:
        return f"{self._client.address}_freestyle_{self._key}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._client.address)},
            name=self._machine_name,
            manufacturer="Melitta",
            model=self._client.model_name,
        )

    @property
    def native_value(self) -> float | None:
        return float(getattr(self._client, self._client_attr, 0))

    @property
    def available(self) -> bool:
        return self._client.connected

    async def async_added_to_hass(self) -> None:
        self._client.add_connection_callback(self._on_connection_change)

    @callback
    def _on_connection_change(self, connected: bool) -> None:
        self.async_write_ha_state()

    async def async_set_native_value(self, value: float) -> None:
        setattr(self._client, self._client_attr, int(value))
        self.async_write_ha_state()
