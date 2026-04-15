"""Select platform — recipes, profiles, freestyle options, per-brand settings."""

from __future__ import annotations

import asyncio
import logging

from bleak.exc import BleakError

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .ble_client import MelittaBleClient, resolve_caps_from_scanner
from .const import (
    PROFILE_NAMES,
    RECIPE_NAMES,
    RecipeId,
    Aroma,
    ComponentProcess,
    DirectKeyCategory,
    Intensity,
    Temperature,
    Shots,
    get_available_recipes,
    get_user_profile_count,
)
from .entity import MelittaDeviceMixin
from .protocol import RecipeComponent


PARALLEL_UPDATES = 0  # BLE: single connection, serialize via locks

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
    Shots.NONE: "none",
    Shots.ONE: "one",
    Shots.TWO: "two",
    Shots.THREE: "three",
}


_DK_CATEGORY_NAMES: dict[int, str] = {
    DirectKeyCategory.ESPRESSO: "Espresso",
    DirectKeyCategory.CAFE_CREME: "Café Crème",
    DirectKeyCategory.CAPPUCCINO: "Cappuccino",
    DirectKeyCategory.LATTE_MACCHIATO: "Latte Macchiato",
    DirectKeyCategory.MILK: "Milk",
    DirectKeyCategory.MILK_FROTH: "Milk Froth",
    DirectKeyCategory.WATER: "Hot Water",
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
    """Set up select entities for the configured coffee machine."""
    client: MelittaBleClient = entry.runtime_data
    name = entry.data.get(CONF_NAME) or f"{client.brand.brand_name} Coffee Machine"

    entities: list = []
    if "HC" in client.brand.supported_extensions:
        entities.extend([
            MelittaRecipeSelect(client, entry, name),
            MelittaProfileSelect(client, entry, name),
        ])
    # Brand capability-driven settings selects (Nivona + future brands).
    # For Melitta these are already exposed via the legacy MelittaSettingSwitch
    # / hand-coded number entities; we only register generic settings selects
    # when the brand has a populated per-family table AND Melitta-native
    # setting entities have not claimed the same IDs (i.e. Nivona only).
    caps = client.capabilities
    if caps is None and client.brand.brand_slug != "melitta":
        caps = resolve_caps_from_scanner(hass, entry.data.get(CONF_ADDRESS, ""), client.brand)
    if caps is not None and caps.settings and client.brand.brand_slug != "melitta":
        for descriptor in caps.settings:
            # Only descriptors with a discrete options list become
            # selects. Options-less descriptors (raw numeric settings
            # like AutoOn hours/minutes) are surfaced as number
            # entities in number.py instead.
            if not descriptor.options:
                continue
            entities.append(
                BrandSettingSelect(client, entry, name, descriptor)
            )
    # Nivona-style brew: always add the recipe select for Nivona brand.
    # Recipe list is fetched from capabilities at the time HA renders options
    # (or dynamically on each update if caps aren't resolved yet at setup time).
    if (client.brand.brand_slug == "nivona"
            and "HC" not in client.brand.supported_extensions):
        entities.append(NivonaRecipeSelect(client, entry, name))

    if "HJ" in client.brand.supported_extensions:
        entities.extend([
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
    async_add_entities(entities)


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
        # Include details of the selected recipe
        if self._selected and self._selected in self._all_recipes:
            attrs.update(self._all_recipes[self._selected])
        # Expose all preloaded recipes for external consumers (PWA app)
        if self._all_recipes:
            attrs["recipes"] = self._all_recipes
        return attrs

    async def async_added_to_hass(self) -> None:
        self._client.add_connection_callback(self._on_connection_change)
        self._client.add_recipe_refresh_callback(self._on_recipe_refresh)

    async def async_will_remove_from_hass(self) -> None:
        self._client.remove_connection_callback(self._on_connection_change)
        self._client.remove_recipe_refresh_callback(self._on_recipe_refresh)

    @callback
    def _on_connection_change(self, connected: bool) -> None:
        if connected:
            self.hass.async_create_task(self._preload_recipes())
        self.async_write_ha_state()

    @callback
    def _on_recipe_refresh(self, recipe_id: int, recipe) -> None:
        """Update cached attributes when a recipe has been re-read (post-HD)."""
        name = RECIPE_NAMES.get(recipe_id)
        if name is None:
            return
        attrs: dict[str, str | int] = {}
        attrs.update(_component_attrs(recipe.component1, "c1"))
        attrs.update(_component_attrs(recipe.component2, "c2"))
        self._all_recipes[name] = attrs
        self.async_write_ha_state()

    async def _preload_recipes(self) -> None:
        """Read all base recipe details from the machine (always profile 0)."""
        _LOGGER.info("Preloading %d base recipes...", len(self._attr_options))
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
        _LOGGER.info("Preloaded %d/%d base recipes", len(self._all_recipes), len(self._attr_options))
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
        attrs: dict = {"active_profile": self._client.active_profile}
        # Expose DirectKey recipes for external consumers (PWA app)
        dk = self._client.directkey_recipes
        if dk:
            dk_out: dict[int, dict[str, dict]] = {}
            for pid, categories in dk.items():
                dk_out[pid] = {}
                for cat_int, recipe in categories.items():
                    cat_name = _DK_CATEGORY_NAMES.get(cat_int, str(cat_int))
                    entry: dict[str, str | int] = {"category": cat_int}
                    entry.update(_component_attrs(recipe.component1, "c1"))
                    entry.update(_component_attrs(recipe.component2, "c2"))
                    dk_out[pid][cat_name] = entry
                dk_out[pid] = dict(sorted(dk_out[pid].items()))
            attrs["directkey_recipes"] = dk_out
        return attrs

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


# ---------------------------------------------------------------------------
# Brand capability-driven generic setting select
# (used for Nivona; Melitta has its own hand-tailored entities)
# ---------------------------------------------------------------------------

class BrandSettingSelect(MelittaDeviceMixin, SelectEntity):
    """Generic setting select driven by a ``SettingDescriptor`` tuple from
    the active BrandProfile's capabilities. Reads via HR, writes via HW."""

    _attr_has_entity_name = True
    _attr_entity_category = None

    def __init__(self, client: MelittaBleClient, entry: ConfigEntry, name: str, descriptor) -> None:
        self._client = client
        self._entry = entry
        self._machine_name = name
        self._desc = descriptor
        self._value_code: int | None = None
        # option_label → value_code for write path
        self._label_to_code: dict[str, int] = {label: code for code, label in descriptor.options}
        self._code_to_label: dict[int, str] = {code: label for code, label in descriptor.options}
        self._attr_options = list(self._label_to_code.keys())
        self._attr_name = descriptor.title
        # Localized entity name lookup path — HA reads
        # `entity.select.<descriptor.key>.name` from translations.
        self._attr_translation_key = descriptor.key

    @property
    def unique_id(self) -> str:
        return f"{self._client.address}_setting_{self._desc.key}"

    @property
    def icon(self) -> str:
        return "mdi:tune"

    @property
    def current_option(self) -> str | None:
        if self._value_code is None:
            return None
        # Low-16-bit decode per upstream (ReadSettingAsync rule)
        code = self._value_code & 0xFFFF
        return self._code_to_label.get(code)

    @property
    def available(self) -> bool:
        return self._client.connected

    async def async_added_to_hass(self) -> None:
        self._client.add_connection_callback(self._on_connection_change)
        if self._client.connected:
            await self._refresh()

    async def async_will_remove_from_hass(self) -> None:
        self._client.remove_connection_callback(self._on_connection_change)

    @callback
    def _on_connection_change(self, connected: bool) -> None:
        if connected:
            self.hass.async_create_task(self._refresh())
        self.async_write_ha_state()

    async def _refresh(self) -> None:
        try:
            value = await self._client.read_setting(self._desc.setting_id)
            if value is not None:
                self._value_code = value
                self.async_write_ha_state()
        except (BleakError, OSError, asyncio.TimeoutError):
            _LOGGER.debug(
                "BrandSettingSelect %s refresh failed", self._desc.key, exc_info=True,
            )

    async def async_select_option(self, option: str) -> None:
        code = self._label_to_code.get(option)
        if code is None:
            _LOGGER.warning("Unknown option %s for %s", option, self._desc.key)
            return
        try:
            success = await self._client.write_setting(self._desc.setting_id, code)
        except (BleakError, OSError, asyncio.TimeoutError):
            _LOGGER.exception("BLE error writing %s", self._desc.key)
            return
        if success:
            self._value_code = code
            self.async_write_ha_state()
        else:
            _LOGGER.warning(
                "Machine rejected %s=%s (NACK/timeout)", self._desc.key, option,
            )


class NivonaRecipeSelect(MelittaDeviceMixin, SelectEntity):
    """Recipe selector for Nivona (no HC/HJ — picks recipe_id for HE brew).

    Recipe list comes from capabilities.recipes which is resolved lazily
    after first BLE connect. We expose a placeholder option until ready.
    """

    _attr_has_entity_name = True
    _attr_name = "Recipe"
    _attr_icon = "mdi:coffee-maker-outline"
    _attr_should_poll = True  # poll periodically until caps resolve

    def __init__(self, client: MelittaBleClient, entry: ConfigEntry, machine_name: str) -> None:
        self._client = client
        self._entry = entry
        self._machine_name = machine_name
        self._attr_unique_id = f"{client.address}_nivona_recipe_select"
        self._attr_options = ["(loading...)"]
        self._attr_current_option = "(loading...)"

    def _refresh_options(self) -> None:
        caps = self._client.capabilities
        if caps and caps.recipes:
            new_opts = [r.name for r in caps.recipes]
            if new_opts != self._attr_options:
                self._attr_options = new_opts
                if self._attr_current_option not in new_opts:
                    self._attr_current_option = new_opts[0]
                self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._refresh_options()

    async def async_update(self) -> None:
        self._refresh_options()

    @property
    def available(self) -> bool:
        caps = self._client.capabilities
        return bool(caps and caps.recipes)

    async def async_select_option(self, option: str) -> None:
        self._refresh_options()
        if option in self._attr_options:
            self._attr_current_option = option
            self.async_write_ha_state()

    @property
    def selected_recipe_id(self) -> int | None:
        caps = self._client.capabilities
        if not (caps and caps.recipes) or self._attr_current_option is None:
            return None
        for r in caps.recipes:
            if r.name == self._attr_current_option:
                return r.recipe_id
        return None
