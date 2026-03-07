"""Select platform for Melitta Barista Smart."""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .ble_client import MelittaBleClient
from .const import DOMAIN, PROFILE_NAMES, RECIPE_NAMES, RecipeId, get_available_recipes, get_user_profile_count

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
        MelittaProfileSelect(client, entry, name),
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

    async def async_added_to_hass(self) -> None:
        self._client.add_connection_callback(self._on_connection_change)

    @callback
    def _on_connection_change(self, connected: bool) -> None:
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        """Select a recipe (does not brew — use the Brew button)."""
        self._selected = option
        self._client.selected_recipe = _NAME_TO_RECIPE.get(option)
        self.async_write_ha_state()


class MelittaProfileSelect(SelectEntity):
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
        profile_count = get_user_profile_count(client.machine_type)
        # Build options: "My Coffee", "Profile 1", ..., "Profile N"
        self._profile_options: list[str] = [PROFILE_NAMES[0]]
        for i in range(1, profile_count + 1):
            self._profile_options.append(f"Profile {i}")
        self._attr_options = list(self._profile_options)

    @property
    def unique_id(self) -> str:
        return f"{self._client.address}_profile_select"

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
        idx = self._client.active_profile
        if 0 <= idx < len(self._profile_options):
            return self._profile_options[idx]
        return self._profile_options[0]

    @property
    def available(self) -> bool:
        return self._client.connected

    async def async_added_to_hass(self) -> None:
        self._client.add_connection_callback(self._on_connection_change)
        # Try to read profile names from machine
        if self._client.connected:
            await self._refresh_profile_names()

    async def _refresh_profile_names(self) -> None:
        """Read profile names from the machine and update options."""
        from .const import USER_NAME_IDS
        for i in range(1, len(self._profile_options)):
            if i in USER_NAME_IDS:
                name = await self._client.read_alpha(USER_NAME_IDS[i])
                if name:
                    self._profile_options[i] = name
        self._attr_options = list(self._profile_options)

    @callback
    def _on_connection_change(self, connected: bool) -> None:
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        """Select the active profile."""
        try:
            idx = self._profile_options.index(option)
        except ValueError:
            idx = 0
        self._client.active_profile = idx
        _LOGGER.info("Active profile set to %d (%s)", idx, option)
        self.async_write_ha_state()
