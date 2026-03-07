"""Select platform for Melitta Barista Smart."""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .ble_client import MelittaBleClient
from .const import DOMAIN, RECIPE_NAMES, RecipeId, get_available_recipes

_LOGGER = logging.getLogger("melitta_barista")

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
    ])


class MelittaRecipeSelect(SelectEntity):
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
        available = get_available_recipes(client.machine_type)
        self._attr_options = [
            RECIPE_NAMES[r] for r in available if r in RECIPE_NAMES
        ]

    @property
    def unique_id(self) -> str:
        return f"{self._client.address}_recipe_select"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._client.address)},
            name=self._machine_name,
            manufacturer="Melitta",
            model=self._client.model_name,
        )

    @property
    def current_option(self) -> str | None:
        return self._selected

    @property
    def available(self) -> bool:
        return self._client.connected

    async def async_select_option(self, option: str) -> None:
        """Select a recipe (does not brew — use the Brew button)."""
        self._selected = option
        self._client.selected_recipe = _NAME_TO_RECIPE.get(option)
        self.async_write_ha_state()
