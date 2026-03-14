"""Select platform for Melitta Barista Smart."""

from __future__ import annotations

import asyncio
import logging

from bleak.exc import BleakError

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .ble_client import MelittaBleClient
from .const import (
    DOMAIN,
    PROFILE_NAMES,
    RECIPE_NAMES,
    RecipeId,
    Aroma,
    ComponentProcess,
    Intensity,
    Temperature,
    Shots,
    get_available_recipes,
    get_user_profile_count,
)
from .entity import MelittaDeviceMixin
from .protocol import RecipeComponent

# Freestyle option lists
_PROCESS_OPTIONS = ["coffee", "milk", "water"]
_PROCESS_OPTIONS_WITH_NONE = ["none", "coffee", "milk", "water"]
_INTENSITY_OPTIONS = ["very_mild", "mild", "medium", "strong", "very_strong"]
_AROMA_OPTIONS = ["standard", "intense"]
_TEMPERATURE_OPTIONS = ["cold", "normal", "high"]
_SHOTS_OPTIONS = ["none", "one", "two", "three"]

_LOGGER = logging.getLogger("melitta_barista")

_PROCESS_NAMES = {
    ComponentProcess.NONE: "none",
    ComponentProcess.COFFEE: "coffee",
    ComponentProcess.STEAM: "milk",
    ComponentProcess.WATER: "water",
}

_INTENSITY_NAMES = {
    Intensity.VERY_MILD: "very_mild",
    Intensity.MILD: "mild",
    Intensity.MEDIUM: "medium",
    Intensity.STRONG: "strong",
    Intensity.VERY_STRONG: "very_strong",
}

_AROMA_NAMES = {
    Aroma.STANDARD: "standard",
    Aroma.INTENSE: "intense",
}

_TEMPERATURE_NAMES = {
    Temperature.COLD: "cold",
    Temperature.NORMAL: "normal",
    Temperature.HIGH: "high",
}

_SHOTS_NAMES = {
    Shots.NONE: 0,
    Shots.ONE: 1,
    Shots.TWO: 2,
    Shots.THREE: 3,
}


def _component_attrs(comp: RecipeComponent, prefix: str) -> dict[str, str | int]:
    """Convert a RecipeComponent to entity attribute dict."""
    return {
        f"{prefix}_process": _PROCESS_NAMES.get(comp.process, str(comp.process)),
        f"{prefix}_intensity": _INTENSITY_NAMES.get(comp.intensity, str(comp.intensity)),
        f"{prefix}_aroma": _AROMA_NAMES.get(comp.aroma, str(comp.aroma)),
        f"{prefix}_temperature": _TEMPERATURE_NAMES.get(comp.temperature, str(comp.temperature)),
        f"{prefix}_shots": _SHOTS_NAMES.get(comp.shots, comp.shots),
        f"{prefix}_portion_ml": comp.portion_ml,
    }


# Reverse lookup: name -> RecipeId
_NAME_TO_RECIPE: dict[str, RecipeId] = {
    name: rid for rid, name in RECIPE_NAMES.items()
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Melitta Barista select entities."""
    client: MelittaBleClient = entry.runtime_data
    name = entry.data.get(CONF_NAME, "Melitta Barista")

    async_add_entities([
        MelittaRecipeSelect(client, entry, name),
        MelittaProfileSelect(client, entry, name),
        # Freestyle parameter selects
        MelittaFreestyleSelect(client, entry, name, "process_1", "Process 1", "mdi:coffee", _PROCESS_OPTIONS, "freestyle_process1"),
        MelittaFreestyleSelect(client, entry, name, "intensity_1", "Intensity 1", "mdi:gauge", _INTENSITY_OPTIONS, "freestyle_intensity1"),
        MelittaFreestyleSelect(client, entry, name, "aroma_1", "Aroma 1", "mdi:scent", _AROMA_OPTIONS, "freestyle_aroma1"),
        MelittaFreestyleSelect(client, entry, name, "temperature_1", "Temperature 1", "mdi:thermometer", _TEMPERATURE_OPTIONS, "freestyle_temperature1"),
        MelittaFreestyleSelect(client, entry, name, "shots_1", "Shots 1", "mdi:numeric", _SHOTS_OPTIONS, "freestyle_shots1"),
        MelittaFreestyleSelect(client, entry, name, "process_2", "Process 2", "mdi:coffee-outline", _PROCESS_OPTIONS_WITH_NONE, "freestyle_process2"),
        MelittaFreestyleSelect(client, entry, name, "intensity_2", "Intensity 2", "mdi:gauge", _INTENSITY_OPTIONS, "freestyle_intensity2"),
        MelittaFreestyleSelect(client, entry, name, "aroma_2", "Aroma 2", "mdi:scent", _AROMA_OPTIONS, "freestyle_aroma2"),
        MelittaFreestyleSelect(client, entry, name, "temperature_2", "Temperature 2", "mdi:thermometer", _TEMPERATURE_OPTIONS, "freestyle_temperature2"),
        MelittaFreestyleSelect(client, entry, name, "shots_2", "Shots 2", "mdi:numeric", _SHOTS_OPTIONS, "freestyle_shots2"),
    ])


class MelittaRecipeSelect(MelittaDeviceMixin, SelectEntity):
    """Select and brew a recipe."""

    _attr_has_entity_name = True
    _attr_name = "Recipe"
    _attr_icon = "mdi:coffee-maker-outline"

    def __init__(
        self,
        client: MelittaBleClient,
        entry: ConfigEntry,
        machine_name: str,
    ) -> None:
        self._client = client
        self._entry = entry
        self._machine_name = machine_name
        self._selected: str | None = None
        self._all_recipes: dict[str, dict[str, str | int]] = {}
        available = get_available_recipes(client.machine_type)
        self._attr_options = [
            RECIPE_NAMES[r] for r in available if r in RECIPE_NAMES
        ]

    @property
    def unique_id(self) -> str:
        return f"{self._client.address}_recipe_select"

    @property
    def current_option(self) -> str | None:
        return self._selected

    @property
    def available(self) -> bool:
        return self._client.connected

    @property
    def extra_state_attributes(self) -> dict:
        attrs: dict = {}
        # Include details of the selected recipe only
        if self._selected and self._selected in self._all_recipes:
            attrs.update(self._all_recipes[self._selected])
        return attrs

    async def async_added_to_hass(self) -> None:
        self._client.add_connection_callback(self._on_connection_change)

    async def async_will_remove_from_hass(self) -> None:
        self._client.remove_connection_callback(self._on_connection_change)

    @callback
    def _on_connection_change(self, connected: bool) -> None:
        if connected:
            self.hass.async_create_task(self._preload_recipes())
        self.async_write_ha_state()

    async def _preload_recipes(self) -> None:
        """Read all base recipe details from the machine (always profile 0)."""
        _LOGGER.debug("Preloading base recipes...")
        for option in self._attr_options:
            recipe_id = _NAME_TO_RECIPE.get(option)
            if recipe_id is None:
                continue
            try:
                recipe = await self._client.read_recipe(int(recipe_id))
                if recipe:
                    attrs: dict[str, str | int] = {}
                    attrs.update(_component_attrs(recipe.component1, "c1"))
                    attrs.update(_component_attrs(recipe.component2, "c2"))
                    self._all_recipes[option] = attrs
            except (BleakError, OSError, asyncio.TimeoutError):
                _LOGGER.debug("Failed to preload recipe %s", option)
        _LOGGER.debug("Preloaded %d base recipes", len(self._all_recipes))
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        """Select a recipe."""
        self._selected = option
        recipe_id = _NAME_TO_RECIPE.get(option)
        self._client.selected_recipe = recipe_id
        self.async_write_ha_state()

        # If recipe not in cache, read it now (always base recipe)
        if option not in self._all_recipes and recipe_id is not None and self._client.connected:
            recipe = await self._client.read_recipe(int(recipe_id))
            if recipe:
                attrs: dict[str, str | int] = {}
                attrs.update(_component_attrs(recipe.component1, "c1"))
                attrs.update(_component_attrs(recipe.component2, "c2"))
                self._all_recipes[option] = attrs
                self.async_write_ha_state()


class MelittaProfileSelect(MelittaDeviceMixin, SelectEntity):
    """Select the active user profile."""

    _attr_has_entity_name = True
    _attr_name = "Profile"
    _attr_icon = "mdi:account-circle"

    def __init__(
        self,
        client: MelittaBleClient,
        entry: ConfigEntry,
        machine_name: str,
    ) -> None:
        self._client = client
        self._entry = entry
        self._machine_name = machine_name
        self._profile_count = get_user_profile_count(client.machine_type)

    def _build_options(self) -> list[str]:
        """Build profile options from client data."""
        names = self._client.profile_names
        opts = [names.get(0, PROFILE_NAMES[0])]
        for i in range(1, self._profile_count + 1):
            opts.append(names.get(i, f"Profile {i}"))
        return opts

    @property
    def options(self) -> list[str]:
        return self._build_options()

    @property
    def unique_id(self) -> str:
        return f"{self._client.address}_profile_select"

    @property
    def current_option(self) -> str | None:
        opts = self._build_options()
        idx = self._client.active_profile
        if 0 <= idx < len(opts):
            return opts[idx]
        return opts[0]

    @property
    def available(self) -> bool:
        return self._client.connected

    @property
    def extra_state_attributes(self) -> dict:
        return {"active_profile": self._client.active_profile}

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

    async def async_select_option(self, option: str) -> None:
        """Select the active profile."""
        opts = self._build_options()
        try:
            idx = opts.index(option)
        except ValueError:
            idx = 0
        self._client.active_profile = idx
        _LOGGER.info("Active profile set to %d (%s)", idx, option)
        self.async_write_ha_state()


class MelittaFreestyleSelect(MelittaDeviceMixin, SelectEntity):
    """Select entity for a freestyle recipe parameter."""

    _attr_has_entity_name = True

    def __init__(
        self,
        client: MelittaBleClient,
        entry: ConfigEntry,
        machine_name: str,
        key: str,
        label: str,
        icon: str,
        options: list[str],
        client_attr: str,
    ) -> None:
        self._client = client
        self._entry = entry
        self._machine_name = machine_name
        self._key = key
        self._client_attr = client_attr
        self._attr_name = f"Freestyle {label}"
        self._attr_icon = icon
        self._attr_options = list(options)

    @property
    def unique_id(self) -> str:
        return f"{self._client.address}_freestyle_{self._key}"

    @property
    def current_option(self) -> str | None:
        return getattr(self._client, self._client_attr, self._attr_options[0])

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

    async def async_select_option(self, option: str) -> None:
        setattr(self._client, self._client_attr, option)
        self.async_write_ha_state()
