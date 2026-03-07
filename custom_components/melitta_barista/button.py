"""Button platform for Melitta Barista Smart."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .ble_client import MelittaBleClient
from .const import DOMAIN, FREESTYLE_RECIPE_TYPE, RECIPE_NAMES, MachineProcess

_LOGGER = logging.getLogger("melitta_barista")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Melitta Barista buttons."""
    client: MelittaBleClient = entry.runtime_data
    name = entry.data.get(CONF_NAME, "Melitta Barista")

    entities: list[ButtonEntity] = []

    # Brew button (works with Recipe select entity)
    entities.append(MelittaBrewButton(client, entry, name))

    # Brew Freestyle button
    entities.append(MelittaBrewFreestyleButton(client, entry, name))

    # Cancel button
    entities.append(MelittaCancelButton(client, entry, name))

    # Maintenance buttons
    entities.append(MelittaMaintenanceButton(
        client, entry, name,
        key="easy_clean", label="Easy Clean",
        icon="mdi:shimmer", process=MachineProcess.EASY_CLEAN,
    ))
    entities.append(MelittaMaintenanceButton(
        client, entry, name,
        key="intensive_clean", label="Intensive Clean",
        icon="mdi:dishwasher", process=MachineProcess.INTENSIVE_CLEAN,
    ))
    entities.append(MelittaMaintenanceButton(
        client, entry, name,
        key="descaling", label="Descaling",
        icon="mdi:water-sync", process=MachineProcess.DESCALING,
    ))

    # Power off
    entities.append(MelittaMaintenanceButton(
        client, entry, name,
        key="switch_off", label="Switch Off",
        icon="mdi:power", process=MachineProcess.SWITCH_OFF,
    ))

    async_add_entities(entities)


class _MelittaButtonBase(ButtonEntity):
    """Base for Melitta buttons."""

    _attr_has_entity_name = True

    def __init__(self, client: MelittaBleClient, entry: ConfigEntry, machine_name: str) -> None:
        self._client = client
        self._entry = entry
        self._machine_name = machine_name

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._client.address)},
            name=self._machine_name,
            manufacturer="Melitta",
            model=self._client.model_name,
        )

    async def async_added_to_hass(self) -> None:
        self._client.add_connection_callback(self._on_connection_change)

    @callback
    def _on_connection_change(self, connected: bool) -> None:
        self.async_write_ha_state()


class MelittaBrewButton(_MelittaButtonBase):
    """Button to brew the recipe selected in the Recipe select entity."""

    _attr_name = "Brew"
    _attr_icon = "mdi:coffee"

    @property
    def unique_id(self) -> str:
        return f"{self._client.address}_brew"

    @property
    def available(self) -> bool:
        return (
            self._client.connected
            and self._client.status is not None
            and self._client.status.is_ready
            and self._client.selected_recipe is not None
        )

    async def async_press(self) -> None:
        recipe_id = self._client.selected_recipe
        if recipe_id is None:
            _LOGGER.warning("No recipe selected, cannot brew")
            return
        recipe_name = RECIPE_NAMES.get(recipe_id, recipe_id.name)
        _LOGGER.info("Brewing %s", recipe_name)
        success = await self._client.brew_recipe(recipe_id)
        if not success:
            _LOGGER.error("Failed to start brewing %s", recipe_name)


_PROCESS_MAP = {"none": 0, "coffee": 1, "milk": 2, "water": 3}
_INTENSITY_MAP = {"very_mild": 0, "mild": 1, "medium": 2, "strong": 3, "very_strong": 4}
_TEMPERATURE_MAP = {"cold": 0, "normal": 1, "high": 2}
_SHOTS_MAP = {"none": 0, "one": 1, "two": 2, "three": 3}


class MelittaBrewFreestyleButton(_MelittaButtonBase):
    """Button to brew a freestyle recipe using current freestyle entity values."""

    _attr_name = "Brew Freestyle"
    _attr_icon = "mdi:coffee-maker-outline"

    @property
    def unique_id(self) -> str:
        return f"{self._client.address}_brew_freestyle"

    @property
    def available(self) -> bool:
        return (
            self._client.connected
            and self._client.status is not None
            and self._client.status.is_ready
        )

    async def async_press(self) -> None:
        from .protocol import RecipeComponent  # noqa: PLC0415

        c = self._client
        comp1 = RecipeComponent(
            process=_PROCESS_MAP.get(c.freestyle_process1, 1),
            shots=_SHOTS_MAP.get(c.freestyle_shots1, 1),
            blend=1,
            intensity=_INTENSITY_MAP.get(c.freestyle_intensity1, 2),
            aroma=0,
            temperature=_TEMPERATURE_MAP.get(c.freestyle_temperature1, 1),
            portion=c.freestyle_portion1_ml // 5,
        )
        comp2 = RecipeComponent(
            process=_PROCESS_MAP.get(c.freestyle_process2, 0),
            shots=_SHOTS_MAP.get(c.freestyle_shots2, 0),
            blend=0,
            intensity=_INTENSITY_MAP.get(c.freestyle_intensity2, 2),
            aroma=0,
            temperature=_TEMPERATURE_MAP.get(c.freestyle_temperature2, 1),
            portion=c.freestyle_portion2_ml // 5,
        )

        _LOGGER.info("Brewing freestyle: %s", c.freestyle_name)
        success = await c.brew_freestyle(
            name=c.freestyle_name,
            recipe_type=FREESTYLE_RECIPE_TYPE,
            component1=comp1,
            component2=comp2,
        )
        if not success:
            _LOGGER.error("Failed to brew freestyle recipe")


class MelittaCancelButton(_MelittaButtonBase):
    """Button to cancel current operation."""

    _attr_name = "Cancel"
    _attr_icon = "mdi:stop-circle"

    @property
    def unique_id(self) -> str:
        return f"{self._client.address}_cancel"

    @property
    def available(self) -> bool:
        if not self._client.connected or not self._client.status:
            return False
        return self._client.status.process not in (MachineProcess.READY, None)

    async def async_press(self) -> None:
        status = self._client.status
        if status and status.process:
            _LOGGER.info("Cancelling process %s", status.process)
            await self._client.cancel_process(status.process)


class MelittaMaintenanceButton(_MelittaButtonBase):
    """Button for maintenance operations (cleaning, descaling, power off)."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, client: MelittaBleClient, entry: ConfigEntry,
        machine_name: str, *, key: str, label: str, icon: str,
        process: MachineProcess,
    ) -> None:
        super().__init__(client, entry, machine_name)
        self._process = process
        self._key = key
        self._attr_name = label
        self._attr_icon = icon

    @property
    def unique_id(self) -> str:
        return f"{self._client.address}_{self._key}"

    @property
    def available(self) -> bool:
        return self._client.connected and (
            self._client.status is not None and self._client.status.is_ready
        )

    async def async_press(self) -> None:
        _LOGGER.info("Starting %s", self._attr_name)
        method_map = {
            MachineProcess.EASY_CLEAN: self._client.start_easy_clean,
            MachineProcess.INTENSIVE_CLEAN: self._client.start_intensive_clean,
            MachineProcess.DESCALING: self._client.start_descaling,
            MachineProcess.SWITCH_OFF: self._client.switch_off,
        }
        method = method_map.get(self._process)
        if method:
            success = await method()
        else:
            _LOGGER.error("Unknown process %s", self._process)
            return
        if not success:
            _LOGGER.error("Failed to start %s", self._attr_name)
