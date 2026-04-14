"""Number platform — machine settings (energy saving, portions, overrides)."""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .ble_client import MelittaBleClient
from .const import MachineSettingId
from .entity import MelittaDeviceMixin


PARALLEL_UPDATES = 0  # BLE: single connection, serialize via locks

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
    """Set up number entities for the configured coffee machine."""
    client: MelittaBleClient = entry.runtime_data
    name = entry.data.get(CONF_NAME) or f"{client.brand.brand_name} Coffee Machine"

    # Settings (HR/HW) — generic Eugster, every brand supports them.
    entities: list = [
        MelittaSettingNumber(client, entry, name, defn)
        for defn in SETTING_DEFINITIONS
    ]
    # Brand-capability-driven numeric settings (Nivona 111/112 AutoOn
    # hours/minutes and any future options-less setting descriptor).
    # Options-bearing descriptors become selects in select.py; here we
    # handle only the raw-number ones.
    caps = client.capabilities
    if caps is not None and caps.settings and client.brand.brand_slug != "melitta":
        for descriptor in caps.settings:
            if descriptor.options:
                continue
            entities.append(
                BrandSettingNumber(client, entry, name, descriptor),
            )

    # Nivona brew-override inputs — persist local values used by brew button.
    if client.brand.brand_slug == "nivona":
        entities.append(NivonaBrewOverrideNumber(
            client, entry, name, "strength", "Brew Strength",
            "mdi:gauge", 1, 5, 1, default=3,
        ))
        entities.append(NivonaBrewOverrideNumber(
            client, entry, name, "coffee_amount", "Brew Coffee Amount",
            "mdi:cup-water", 20, 240, 5, default=40, unit="mL",
        ))
        entities.append(NivonaBrewOverrideNumber(
            client, entry, name, "temperature", "Brew Temperature Preset",
            "mdi:thermometer", 0, 2, 1, default=1,
        ))
        entities.append(NivonaBrewOverrideNumber(
            client, entry, name, "milk_amount", "Brew Milk Amount",
            "mdi:cup", 0, 240, 5, default=80, unit="mL",
        ))

    # Freestyle portion entities require HJ recipe writes.
    if "HJ" in client.brand.supported_extensions:
        entities.append(MelittaFreestyleNumber(
            client, entry, name, "portion_1", "Freestyle Portion 1",
            "mdi:cup-water", 5, 250, 5, "freestyle_portion1_ml",
        ))
        entities.append(MelittaFreestyleNumber(
            client, entry, name, "portion_2", "Freestyle Portion 2",
            "mdi:cup-water", 0, 250, 5, "freestyle_portion2_ml",
        ))
    async_add_entities(entities)


class MelittaSettingNumber(MelittaDeviceMixin, NumberEntity):
    """Number entity for a machine setting."""

    _attr_has_entity_name = True
    _attr_should_poll = False

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
    def available(self) -> bool:
        return self._client.connected

    async def async_added_to_hass(self) -> None:
        self._client.add_connection_callback(self._on_connection_change)

    async def async_will_remove_from_hass(self) -> None:
        self._client.remove_connection_callback(self._on_connection_change)

    @callback
    def _on_connection_change(self, connected: bool) -> None:
        if connected:
            self.hass.async_create_task(self._async_read_value())
        self.async_write_ha_state()

    async def _async_read_value(self) -> None:
        """Read setting from the machine (once on connect)."""
        try:
            value = await self._client.read_setting(self._setting_id)
            if value is not None:
                self._attr_native_value = float(value)
                self.async_write_ha_state()
        except Exception:
            _LOGGER.debug("Failed to read setting %d", self._setting_id)

    async def async_set_native_value(self, value: float) -> None:
        if await self._client.write_setting(self._setting_id, int(value)):
            self._attr_native_value = value
            self.async_write_ha_state()


class BrandSettingNumber(MelittaDeviceMixin, NumberEntity):
    """Number entity for a brand capability setting without a discrete
    options list (e.g. AutoOn hour / minute fields on Nivona 900 /
    900-Light / 1030 / 1040).

    Range is derived from the key name — hours → 0..23, minutes → 0..59,
    otherwise 0..255 as a safe fallback.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        client: MelittaBleClient,
        entry: ConfigEntry,
        machine_name: str,
        descriptor,
    ) -> None:
        self._client = client
        self._entry = entry
        self._machine_name = machine_name
        self._desc = descriptor
        self._setting_id: int = descriptor.setting_id
        self._attr_translation_key = descriptor.key
        self._attr_name = descriptor.title
        if "hour" in descriptor.key:
            self._attr_native_min_value = 0
            self._attr_native_max_value = 23
            self._attr_native_unit_of_measurement = UnitOfTime.HOURS
            self._attr_icon = "mdi:clock-outline"
        elif "minute" in descriptor.key:
            self._attr_native_min_value = 0
            self._attr_native_max_value = 59
            self._attr_native_unit_of_measurement = UnitOfTime.MINUTES
            self._attr_icon = "mdi:clock-time-four-outline"
        else:
            self._attr_native_min_value = 0
            self._attr_native_max_value = 255
            self._attr_icon = "mdi:cog"
        if descriptor.unit:
            self._attr_native_unit_of_measurement = descriptor.unit
        self._attr_native_value: float | None = None

    @property
    def unique_id(self) -> str:
        return f"{self._client.address}_brand_setting_{self._setting_id}"

    @property
    def available(self) -> bool:
        return self._client.connected

    async def async_added_to_hass(self) -> None:
        self._client.add_connection_callback(self._on_connection_change)

    async def async_will_remove_from_hass(self) -> None:
        self._client.remove_connection_callback(self._on_connection_change)

    @callback
    def _on_connection_change(self, connected: bool) -> None:
        if connected:
            self.hass.async_create_task(self._async_read_value())
        self.async_write_ha_state()

    async def _async_read_value(self) -> None:
        try:
            value = await self._client.read_setting(self._setting_id)
            if value is not None:
                self._attr_native_value = float(value)
                self.async_write_ha_state()
        except Exception:  # noqa: BLE001 — defensive against driver errors
            _LOGGER.debug("BrandSettingNumber read failed id=%d", self._setting_id)

    async def async_set_native_value(self, value: float) -> None:
        if not self._desc.is_writable:
            return
        if await self._client.write_setting(self._setting_id, int(value)):
            self._attr_native_value = value
            self.async_write_ha_state()


class MelittaFreestyleNumber(MelittaDeviceMixin, NumberEntity):
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
    def native_value(self) -> float | None:
        return float(getattr(self._client, self._client_attr, 0))

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

    async def async_set_native_value(self, value: float) -> None:
        setattr(self._client, self._client_attr, int(value))
        self.async_write_ha_state()


class NivonaBrewOverrideNumber(MelittaDeviceMixin, NumberEntity, RestoreEntity):
    """Persistent number for Nivona brew overrides (HW temp-recipe writes).

    Holds a user-chosen value (strength / coffee_amount / temperature / etc.)
    that NivonaBrewButton reads at press time and writes via HW before HE.
    Survives restarts via HA's RestoreEntity.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_should_poll = False

    def __init__(
        self, client: MelittaBleClient, entry: ConfigEntry,
        machine_name: str, field: str, label: str, icon: str,
        min_v: float, max_v: float, step: float,
        default: float, unit: str | None = None,
    ) -> None:
        self._client = client
        self._entry = entry
        self._machine_name = machine_name
        self._field = field
        self._attr_name = label
        self._attr_icon = icon
        self._attr_native_min_value = min_v
        self._attr_native_max_value = max_v
        self._attr_native_step = step
        self._attr_mode = NumberMode.SLIDER
        self._attr_native_unit_of_measurement = unit
        self._attr_unique_id = f"{client.address}_brew_{field}"
        self._attr_native_value = default

    @property
    def field_name(self) -> str:
        return self._field

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state not in (None, "unknown", "unavailable"):
            try:
                self._attr_native_value = float(last.state)
            except ValueError:
                pass

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.async_write_ha_state()
