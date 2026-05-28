"""Button platform — brew / cancel / maintenance for supported machines."""

from __future__ import annotations

import asyncio
import logging

from bleak.exc import BleakError
from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from homeassistant.const import CONF_ADDRESS
from .ble_client import MelittaBleClient, resolve_caps_from_scanner
from .const import (
    FREESTYLE_RECIPE_TYPE, HE_CMD_FACTORY_RESET_RECIPES,
    HE_CMD_FACTORY_RESET_SETTINGS, PROMPT_MANIPULATIONS, RECIPE_NAMES,
    MachineProcess,
    PROCESS_MAP, INTENSITY_MAP, AROMA_MAP, TEMPERATURE_MAP, SHOTS_MAP,
)
from .entity import MelittaDeviceMixin
from .protocol import MachineStatus


PARALLEL_UPDATES = 0  # BLE: single connection, serialize via locks

_LOGGER = logging.getLogger("melitta_barista")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up button entities for the configured coffee machine."""
    client: MelittaBleClient = entry.runtime_data
    name = entry.data.get(CONF_NAME) or f"{client.brand.brand_name} Coffee Machine"

    entities: list[ButtonEntity] = []
    supports_recipe = "HC" in client.brand.supported_extensions

    # Brew button (works with Recipe select entity) — Melitta-style only.
    if supports_recipe:
        entities.append(MelittaBrewButton(client, entry, name))

    # Brew Freestyle button — requires HJ recipe writes.
    if "HJ" in client.brand.supported_extensions:
        entities.append(MelittaBrewFreestyleButton(client, entry, name))

    # Nivona brew button — always present for brand=nivona.
    # Recipe selection comes from NivonaRecipeSelect at press time.
    if (client.brand.brand_slug == "nivona"
            and "HC" not in client.brand.supported_extensions):
        entities.append(NivonaBrewButton(client, entry, name))

    # Cancel button — generic
    entities.append(MelittaCancelButton(client, entry, name))

    # Reset current recipe to factory defaults (HD command) — needs recipe context.
    if supports_recipe:
        entities.append(MelittaResetRecipeButton(client, entry, name))

    # Confirm machine prompt (HY command) — generic
    entities.append(MelittaConfirmPromptButton(client, entry, name))

    # Factory reset buttons — Nivona only. The vendor app exposes
    # these on families 600 / 700 / 79x / 900 / 900-light / 1030 /
    # 1040 but NOT on NIVO 8000, so we mirror that gating. Buttons
    # are gated at runtime via the `available` property so a
    # late-resolving family is handled gracefully.
    if client.brand.brand_slug == "nivona":
        entities.append(NivonaFactoryResetSettingsButton(client, entry, name))
        entities.append(NivonaFactoryResetRecipesButton(client, entry, name))

        # Per-slot MyCoffee brew buttons. Selector resolved as
        # `first_mycoffee_selector + slot` (vendor uses 20 as the
        # base for every Nivona model). Press is gated at runtime on
        # the slot's cached `enabled` flag — slots that aren't
        # "armed" stay unavailable so users don't accidentally fire
        # an empty recipe.
        caps_for_brew = client.capabilities
        if caps_for_brew is None:
            caps_for_brew = resolve_caps_from_scanner(
                hass, entry.data.get(CONF_ADDRESS, ""), client.brand,
            )
        if caps_for_brew is not None and caps_for_brew.my_coffee_slots > 0:
            for slot in range(caps_for_brew.my_coffee_slots):
                entities.append(NivonaBrewMyCoffeeButton(client, entry, name, slot))

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

    # Filter operations
    entities.append(MelittaMaintenanceButton(
        client, entry, name,
        key="filter_insert", label="Filter Insert",
        icon="mdi:filter-plus", process=MachineProcess.FILTER_INSERT,
    ))
    entities.append(MelittaMaintenanceButton(
        client, entry, name,
        key="filter_replace", label="Filter Replace",
        icon="mdi:filter-cog", process=MachineProcess.FILTER_REPLACE,
    ))
    entities.append(MelittaMaintenanceButton(
        client, entry, name,
        key="filter_remove", label="Filter Remove",
        icon="mdi:filter-remove", process=MachineProcess.FILTER_REMOVE,
    ))

    # Evaporating
    entities.append(MelittaMaintenanceButton(
        client, entry, name,
        key="evaporating", label="Evaporating",
        icon="mdi:air-humidifier", process=MachineProcess.EVAPORATING,
    ))

    # Power off
    entities.append(MelittaMaintenanceButton(
        client, entry, name,
        key="switch_off", label="Switch Off",
        icon="mdi:power", process=MachineProcess.SWITCH_OFF,
    ))

    async_add_entities(entities)


class _MelittaButtonBase(MelittaDeviceMixin, ButtonEntity):
    """Base class shared by all coffee-machine buttons."""

    _attr_has_entity_name = True

    def __init__(self, client: MelittaBleClient, entry: ConfigEntry, machine_name: str) -> None:
        self._client = client
        self._entry = entry
        self._machine_name = machine_name

    async def async_added_to_hass(self) -> None:
        self._client.add_status_callback(self._on_status_update)
        self._client.add_connection_callback(self._on_connection_change)

    async def async_will_remove_from_hass(self) -> None:
        self._client.remove_status_callback(self._on_status_update)
        self._client.remove_connection_callback(self._on_connection_change)

    @callback
    def _on_status_update(self, status: MachineStatus) -> None:
        self.async_write_ha_state()

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
        try:
            success = await self._client.brew_recipe(recipe_id)
            if not success:
                _LOGGER.error("Failed to start brewing %s", recipe_name)
        except (BleakError, OSError, asyncio.TimeoutError):
            _LOGGER.exception("BLE error while brewing %s", recipe_name)


_PROCESS_MAP = PROCESS_MAP
_INTENSITY_MAP = INTENSITY_MAP
_AROMA_MAP = AROMA_MAP
_TEMPERATURE_MAP = TEMPERATURE_MAP
_SHOTS_MAP = SHOTS_MAP


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
            aroma=_AROMA_MAP.get(c.freestyle_aroma1, 0),
            temperature=_TEMPERATURE_MAP.get(c.freestyle_temperature1, 1),
            portion=c.freestyle_portion1_ml // 5,
        )
        comp2 = RecipeComponent(
            process=_PROCESS_MAP.get(c.freestyle_process2, 0),
            shots=_SHOTS_MAP.get(c.freestyle_shots2, 0),
            blend=0,
            intensity=_INTENSITY_MAP.get(c.freestyle_intensity2, 2),
            aroma=_AROMA_MAP.get(c.freestyle_aroma2, 0),
            temperature=_TEMPERATURE_MAP.get(c.freestyle_temperature2, 1),
            portion=c.freestyle_portion2_ml // 5,
        )

        _LOGGER.info("Brewing freestyle: %s", c.freestyle_name)
        try:
            success = await c.brew_freestyle(
                name=c.freestyle_name,
                recipe_type=FREESTYLE_RECIPE_TYPE,
                component1=comp1,
                component2=comp2,
            )
            if not success:
                _LOGGER.error("Failed to brew freestyle recipe")
        except (BleakError, OSError, asyncio.TimeoutError):
            _LOGGER.exception("BLE error while brewing freestyle")


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
            try:
                await self._client.cancel_process(status.process)
            except (BleakError, OSError, asyncio.TimeoutError):
                _LOGGER.exception("BLE error while cancelling")


class MelittaResetRecipeButton(_MelittaButtonBase):
    """Reset the currently selected recipe to factory defaults (HD)."""

    _attr_name = "Reset Recipe"
    _attr_icon = "mdi:restore"
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def unique_id(self) -> str:
        return f"{self._client.address}_reset_recipe"

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
            _LOGGER.warning("No recipe selected, cannot reset")
            return
        recipe_name = RECIPE_NAMES.get(recipe_id, str(int(recipe_id)))
        _LOGGER.info("Resetting recipe %s to defaults", recipe_name)
        try:
            success = await self._client.reset_recipe_default(int(recipe_id))
            if not success:
                _LOGGER.warning(
                    "Reset recipe %s: machine returned NACK or timeout",
                    recipe_name,
                )
        except (BleakError, OSError, asyncio.TimeoutError):
            _LOGGER.exception("BLE error while resetting recipe %s", recipe_name)



# Nivona families that expose factory-reset buttons in the vendor app.
# NIVO 8000 does not — its settings screen has no reset entries — so
# we follow the same gating here.
_FACTORY_RESET_FAMILIES = frozenset(
    {"600", "700", "79x", "900", "900-light", "1030", "1040"}
)


class _NivonaFactoryResetButtonBase(_MelittaButtonBase):
    """Shared base for the two Nivona factory-reset buttons.

    Both buttons send the same HE opcode with different 18-byte
    command-id payloads (see `protocol._build_he_command_payload`).
    Available only on Nivona families that expose the corresponding
    menu in the vendor app — NIVO 8000 has no factory-reset menu
    there, so we hide the entity for it.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_icon = "mdi:restore-alert"

    # Subclasses provide these.
    _command_id: int = 0
    _slug: str = ""
    _log_label: str = ""

    @property
    def unique_id(self) -> str:
        return f"{self._client.address}_factory_reset_{self._slug}"

    @property
    def available(self) -> bool:
        if not self._client.connected:
            return False
        caps = getattr(self._client, "capabilities", None)
        if caps is None:
            # Capability hasn't resolved yet (usually moments after
            # connect). Stay unavailable until we know whether this is
            # an 8000-family machine.
            return False
        return caps.family_key in _FACTORY_RESET_FAMILIES

    async def async_press(self) -> None:
        _LOGGER.warning(
            "Initiating Nivona factory reset (%s, HE commandId=%d) on %s",
            self._log_label, self._command_id, self._client.address,
        )
        try:
            success = await self._client.execute_he_command(self._command_id)
        except (BleakError, OSError, asyncio.TimeoutError):
            _LOGGER.exception(
                "BLE error during factory reset %s", self._log_label,
            )
            return
        if not success:
            _LOGGER.warning(
                "Factory reset %s returned NACK or timeout — machine "
                "may not be in a ready state",
                self._log_label,
            )


class NivonaFactoryResetSettingsButton(_NivonaFactoryResetButtonBase):
    """Reset machine-wide settings to factory defaults (HE commandId 50)."""

    _attr_name = "Factory Reset Settings"
    _command_id = HE_CMD_FACTORY_RESET_SETTINGS
    _slug = "settings"
    _log_label = "settings"


class NivonaFactoryResetRecipesButton(_NivonaFactoryResetButtonBase):
    """Reset per-recipe customizations to factory defaults (HE commandId 51)."""

    _attr_name = "Factory Reset Recipes"
    _command_id = HE_CMD_FACTORY_RESET_RECIPES
    _slug = "recipes"
    _log_label = "recipes"


class MelittaConfirmPromptButton(_MelittaButtonBase):
    """Confirm an active machine prompt (move cup, flush, fill water...) via HY."""

    _attr_name = "Confirm Prompt"
    _attr_icon = "mdi:check-circle-outline"

    @property
    def unique_id(self) -> str:
        return f"{self._client.address}_confirm_prompt"

    @property
    def available(self) -> bool:
        if not self._client.connected or not self._client.status:
            return False
        return self._client.status.manipulation in PROMPT_MANIPULATIONS

    async def async_press(self) -> None:
        status = self._client.status
        manip = status.manipulation if status else None
        if manip is None or manip not in PROMPT_MANIPULATIONS:
            _LOGGER.debug("No active prompt to confirm")
            return
        _LOGGER.info("Confirming prompt: %s", manip.name)
        try:
            success = await self._client.confirm_prompt()
            if not success:
                _LOGGER.warning(
                    "Confirm prompt %s: machine returned NACK or timeout",
                    manip.name,
                )
        except (BleakError, OSError, asyncio.TimeoutError):
            _LOGGER.exception("BLE error while confirming prompt %s", manip.name)


class NivonaBrewButton(_MelittaButtonBase):
    """Brew the recipe selected in NivonaRecipeSelect via HE."""

    _attr_name = "Brew"
    _attr_icon = "mdi:coffee"

    _OVERRIDE_FIELDS = ("strength", "coffee_amount", "temperature", "milk_amount")

    @property
    def unique_id(self) -> str:
        return f"{self._client.address}_nivona_brew"

    def _collect_user_overrides(self, registry) -> dict:
        """Collect overrides from NivonaBrewOverrideNumber entities.

        Only includes fields the user explicitly set (via the slider);
        defaults are skipped so the machine uses its saved recipe.
        """
        overrides: dict = {}
        for field in self._OVERRIDE_FIELDS:
            uid = f"{self._client.address}_brew_{field}"
            for eid, reg_entry in registry.entities.items():
                if reg_entry.unique_id != uid:
                    continue
                st = self.hass.states.get(eid)
                if not st or st.state in (None, "unknown", "unavailable"):
                    break
                if not st.attributes.get("user_set"):
                    break
                try:
                    overrides[field] = int(float(st.state))
                except ValueError:
                    pass
                break
        return overrides

    async def async_press(self) -> None:
        # Locate the recipe select entity in HA's state machine
        from homeassistant.helpers import entity_registry as er
        registry = er.async_get(self.hass)
        target_uid = f"{self._client.address}_nivona_recipe_select"
        entity_id = None
        for eid, entry in registry.entities.items():
            if entry.unique_id == target_uid:
                entity_id = eid
                break
        if not entity_id:
            _LOGGER.warning("NivonaRecipeSelect not found")
            return
        state = self.hass.states.get(entity_id)
        if not state:
            _LOGGER.warning("recipe select state unavailable")
            return

        caps = self._client.capabilities
        if not caps or not caps.recipes:
            _LOGGER.warning("no recipes in capabilities")
            return
        recipe_id = None
        for r in caps.recipes:
            if r.name == state.state:
                recipe_id = r.recipe_id
                break
        if recipe_id is None:
            _LOGGER.warning("recipe %s not matched", state.state)
            return

        overrides = self._collect_user_overrides(registry)
        try:
            success = await self._client.brew_nivona(recipe_id, overrides or None)
            if not success:
                _LOGGER.error("Nivona brew failed for recipe_id=%d", recipe_id)
        except (BleakError, OSError, asyncio.TimeoutError):
            _LOGGER.exception("BLE error during Nivona brew")


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
            MachineProcess.FILTER_INSERT: self._client.start_filter_insert,
            MachineProcess.FILTER_REPLACE: self._client.start_filter_replace,
            MachineProcess.FILTER_REMOVE: self._client.start_filter_remove,
            MachineProcess.EVAPORATING: self._client.start_evaporating,
            MachineProcess.SWITCH_OFF: self._client.switch_off,
        }
        method = method_map.get(self._process)
        if not method:
            _LOGGER.error("Unknown process %s", self._process)
            return
        try:
            success = await method()
            if not success:
                _LOGGER.error("Failed to start %s", self._attr_name)
        except (BleakError, OSError, asyncio.TimeoutError):
            _LOGGER.exception("BLE error while starting %s", self._attr_name)


class NivonaBrewMyCoffeeButton(_MelittaButtonBase):
    """Brew a saved MyCoffee recipe by slot (Nivona only).

    One button per slot 0..N-1, where N is the family's
    `my_coffee_slots`. Gated at runtime on:
    - client.connected
    - machine status `is_ready_for_brew`
    - the slot's cached `enabled` flag (so slots that aren't armed
      stay unavailable instead of firing an empty recipe)
    """

    _attr_icon = "mdi:coffee-to-go"

    def __init__(
        self,
        client: MelittaBleClient,
        entry: ConfigEntry,
        name: str,
        slot: int,
    ) -> None:
        super().__init__(client, entry, name)
        self._slot = slot
        self._attr_name = f"Brew MyCoffee slot {slot + 1}"

    @property
    def unique_id(self) -> str:
        return f"{self._client.address}_brew_mycoffee_slot_{self._slot}"

    @property
    def available(self) -> bool:
        if not self._client.connected:
            return False
        status = self._client.status
        # Be permissive on tolerated manipulation flags so the button
        # follows the same readiness contract as the main brew button.
        caps = getattr(self._client, "capabilities", None)
        tolerated = caps.tolerated_brew_manipulations if caps else ()
        if status is None or not status.is_ready_for_brew(tolerated):
            return False
        slots = self._client.my_coffee_slots
        if slots is None or self._slot >= len(slots):
            return False
        return slots[self._slot].get("enabled", 0) == 1

    async def async_press(self) -> None:
        _LOGGER.info("Brewing MyCoffee slot %d", self._slot + 1)
        try:
            success = await self._client.brew_mycoffee_slot(self._slot)
            if not success:
                _LOGGER.error(
                    "Failed to start MyCoffee brew for slot %d", self._slot + 1,
                )
        except (BleakError, OSError, asyncio.TimeoutError):
            _LOGGER.exception(
                "BLE error while brewing MyCoffee slot %d", self._slot + 1,
            )
