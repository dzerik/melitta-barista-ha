"""Number platform — machine settings (energy saving, portions, overrides)."""

from __future__ import annotations

import logging
import math

from homeassistant.components.number import NumberEntity, NumberMode, RestoreNumber
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME, MAX_LENGTH_EVENT_EVENT_TYPE, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.exceptions import ServiceValidationError

from homeassistant.const import CONF_ADDRESS

from .ble_client import resolve_caps_from_scanner
from .const import CONF_FAMILY_OVERRIDE
from .brands.nivona._options import nivona_number_range
from .coffee_platform.contract import CoffeeMachineClient
from .const import DOMAIN, MELITTA_SETTING_TABLES
from .entity import MelittaDeviceMixin


PARALLEL_UPDATES = 0  # BLE: single connection, serialize via locks

_LOGGER = logging.getLogger("melitta_barista")

# Contract display hint ("slider"/"box") → HA NumberMode; contract unit
# token → HA unit constant. The table stores the pure-data spellings
# (UI Contract §9.1) so const.py needs no homeassistant imports.
_DISPLAY_TO_MODE: dict[str, NumberMode] = {
    "slider": NumberMode.SLIDER,
    "box": NumberMode.BOX,
}
_UNIT_MAP: dict[str, str] = {
    "min": UnitOfTime.MINUTES,
    "h": UnitOfTime.HOURS,
}


def _override_reset_event_type(unique_id: str) -> str:
    """Keep legacy reset events where valid and shorten names above HA's limit."""
    event_type = f"{DOMAIN}_reset_override_{unique_id}"
    if len(event_type) <= MAX_LENGTH_EVENT_EVENT_TYPE:
        return event_type
    return f"{DOMAIN}_reset_{unique_id}"


def _number_definition(row: dict) -> dict:
    """Entity definition dict for one MELITTA_SETTING_TABLES number row."""
    definition = {
        "id": row["id"],
        "name": row["name"],
        "icon": row["icon"],
        "min": row["min"],
        "max": row["max"],
        "step": row["step"],
        "mode": _DISPLAY_TO_MODE[row["display"]],
        "category": EntityCategory.CONFIG,
    }
    if "unit" in row:
        definition["unit"] = _UNIT_MAP[row["unit"]]
    return definition


# Numeric machine-setting entities, derived from the shared
# MELITTA_SETTING_TABLES in const.py (single source for entities and the
# UI-contract settings builder — UI Contract §5.2 rule 9 / §9.1.2.5).
# Entity ids, names, icons, ranges and behaviour are byte-identical to
# the pre-move hand-coded list.
SETTING_DEFINITIONS: list[dict] = [
    _number_definition(row)
    for row in MELITTA_SETTING_TABLES
    if row["control"] == "number"
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up number entities for the configured coffee machine.

    Generic Eugster settings are filtered through the family's
    ``unsupported_generic_setting_ids`` capability (issue #10: the LANGUAGE
    register is not implemented by any Nivona family — the entity was
    permanently dead on a live NICR 790). Stale registry entries for
    excluded settings are removed so they don't linger as unavailable.
    """
    client: CoffeeMachineClient = entry.runtime_data
    name = entry.data.get(CONF_NAME) or f"{client.brand.brand_name} Coffee Machine"

    # Capability resolution shared by the exclusion filter and the
    # brew-override block below: prefer live capabilities, fall back to
    # scanner-cached family detection when the platform sets up before
    # the first connect.
    caps_resolved = client.capabilities or resolve_caps_from_scanner(
        hass, entry.data.get(CONF_ADDRESS, ""), client.brand,
        entry.options.get(CONF_FAMILY_OVERRIDE),
    )
    excluded_ids: frozenset[int] = (
        caps_resolved.unsupported_generic_setting_ids
        if caps_resolved is not None else frozenset()
    )

    # Settings (HR/HW) — generic Eugster, minus family-declared holes.
    entities: list = [
        MelittaSettingNumber(client, entry, name, defn)
        for defn in SETTING_DEFINITIONS
        if int(defn["id"]) not in excluded_ids
    ]

    # Drop stale registry entries for settings this family can't serve —
    # otherwise an entity created before the exclusion (or before caps
    # were resolvable) survives forever as unavailable.
    if excluded_ids:
        ent_reg = er.async_get(hass)
        for setting_id in excluded_ids:
            stale = ent_reg.async_get_entity_id(
                "number", DOMAIN, f"{client.address}_setting_{setting_id}",
            )
            if stale:
                _LOGGER.debug(
                    "Removing stale number entity %s (setting %d unsupported "
                    "by family %s)",
                    stale, setting_id, caps_resolved.family_key,
                )
                ent_reg.async_remove(stale)
    # Brand-capability-driven numeric settings (Nivona 111/112 AutoOn
    # hours/minutes and any future options-less setting descriptor).
    # Options-bearing descriptors become selects in select.py; here we
    # handle only the raw-number ones.
    caps = client.capabilities
    # Generic capability-driven setting numbers — register for brands
    # whose families publish a non-empty settings tuple (Nivona). Melitta
    # has its own hand-tailored MachineSettingNumber entities below and
    # leaves `caps.settings = ()`, so this block naturally skips it.
    if caps is not None and caps.settings:
        for descriptor in caps.settings:
            if descriptor.options:
                continue
            entities.append(
                BrandSettingNumber(client, entry, name, descriptor),
            )

    # Brew-override inputs — register for families that support per-brew
    # temp-recipe overrides (currently every Nivona family; Melitta uses
    # its own HC/HJ write path). Falls back to scanner-cached caps when
    # `client.capabilities` is None at platform-setup time.
    caps_for_overrides = caps_resolved
    if (
        caps_for_overrides is not None
        and caps_for_overrides.supports_brew_overrides
    ):
        entities.append(NivonaBrewOverrideNumber(
            client, entry, name, "strength", "Brew Strength",
            "mdi:gauge", 1, caps_for_overrides.strength_levels, 1,
            default=(caps_for_overrides.strength_levels + 1) // 2,
        ))
        entities.append(NivonaBrewOverrideNumber(
            client, entry, name, "coffee_amount", "Brew Coffee Amount",
            "mdi:cup-water", 20, 240, 5, default=40, unit="mL",
        ))
        entities.append(NivonaBrewOverrideNumber(
            client, entry, name, "water_amount", "Brew Water Amount",
            "mdi:water", 0, caps_for_overrides.brew_water_amount_max, 5,
            default=100, unit="mL",
        ))
        entities.append(NivonaBrewOverrideNumber(
            client, entry, name, "temperature", "Brew Temperature Preset",
            "mdi:thermometer", 0, 2, 1, default=1,
        ))
        entities.append(NivonaBrewOverrideNumber(
            client, entry, name, "milk_amount", "Brew Milk Amount",
            "mdi:cup", 0, 240, 5, default=80, unit="mL",
        ))
        if any(
            client.brand.temp_recipe_register(
                caps_for_overrides.family_key, recipe.recipe_id, "two_cups",
            ) is not None
            for recipe in caps_for_overrides.recipes
        ):
            entities.append(NivonaBrewOverrideNumber(
                client, entry, name, "two_cups", "Brew Two Cups",
                "mdi:coffee", 0, 1, 1, default=0,
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
        client: CoffeeMachineClient,
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

    Range and unit come from the shared pure helper
    ``nivona_number_range`` (UI Contract §9.1.3) — hours → 0..23,
    minutes → 0..59, otherwise 0..255 — the same rule the contract
    settings builder serves, so entity and contract can never diverge.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        client: CoffeeMachineClient,
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
        min_value, max_value, unit = nivona_number_range(descriptor)
        self._attr_native_min_value = min_value
        self._attr_native_max_value = max_value
        if unit == "h":
            self._attr_native_unit_of_measurement = UnitOfTime.HOURS
            self._attr_icon = "mdi:clock-outline"
        elif unit == "min":
            self._attr_native_unit_of_measurement = UnitOfTime.MINUTES
            self._attr_icon = "mdi:clock-time-four-outline"
        else:
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


class MelittaFreestyleNumber(MelittaDeviceMixin, RestoreNumber):
    """Number entity for a freestyle recipe portion parameter."""

    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = "ml"
    _attr_should_poll = False

    def __init__(
        self,
        client: CoffeeMachineClient,
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
        await super().async_added_to_hass()
        data = await self.async_get_last_number_data()
        if data is not None:
            saved = data.native_value
        else:
            last = await self.async_get_last_state()
            saved = last.state if last else None
        try:
            value = float(saved) if not isinstance(saved, bool) else math.nan
        except (TypeError, ValueError, OverflowError):
            value = math.nan
        if (
            math.isfinite(value)
            and self.native_min_value <= value <= self.native_max_value
            and (value - self.native_min_value) % self.native_step == 0
        ):
            setattr(self._client, self._client_attr, int(value))
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
        self, client: CoffeeMachineClient, entry: ConfigEntry,
        machine_name: str, field: str, label: str, icon: str,
        min_v: float, max_v: float, step: float,
        default: float, unit: str | None = None,
    ) -> None:
        self._client = client
        self._entry = entry
        self._machine_name = machine_name
        self._field = field
        self._default = default
        self._user_set = False
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

    @property
    def is_user_set(self) -> bool:
        """True if the user has explicitly set this value (vs. default)."""
        return self._user_set

    @property
    def extra_state_attributes(self) -> dict:
        return {"user_set": self._user_set}

    @property
    def native_max_value(self) -> float:
        """Use live model limits after serial identification refines the family."""
        caps = self._client.capabilities
        if caps is not None:
            if self._field == "strength":
                return caps.strength_levels
            if self._field == "water_amount":
                return caps.brew_water_amount_max
        return self._attr_native_max_value

    def _model_default(self) -> float:
        if self._field == "strength" and self.native_max_value != self._attr_native_max_value:
            return (self.native_max_value + 1) // 2
        return self._default

    @property
    def native_value(self) -> float:
        """Keep untouched defaults consistent with refined model capabilities."""
        if not self._user_set and self._attr_native_value == self._default:
            return self._model_default()
        return self._attr_native_value

    @property
    def available(self) -> bool:
        """Keep unsupported controls registered without offering an inert input."""
        caps = self._client.capabilities
        return caps is not None and self._client.brand.temp_recipe_register(
            caps.family_key, 0, self._field,
        ) is not None

    def _valid_value(self, value: object) -> bool:
        try:
            self._validate_value(value)
        except ServiceValidationError:
            return False
        return True

    async def async_added_to_hass(self) -> None:
        """Restore the override and listen for its length-safe reset event."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state not in (None, "unknown", "unavailable"):
            try:
                restored = float(last.state)
            except ValueError:
                pass
            else:
                unresolved = self._client.capabilities is None
                if (unresolved and restored.is_integer()) or self._valid_value(restored):
                    self._attr_native_value = restored
                    self._user_set = bool(last.attributes.get("user_set"))
        # Listen for the per-slider reset event fired by
        # NivonaResetOverridesButton — clears user_set and restores default.
        self.async_on_remove(
            self.hass.bus.async_listen(
                _override_reset_event_type(self._attr_unique_id),
                self._handle_reset_event,
            )
        )
        self._client.add_connection_callback(self._on_connection_change)
        self.async_on_remove(
            lambda: self._client.remove_connection_callback(self._on_connection_change)
        )

    @callback
    def _on_connection_change(self, connected: bool) -> None:
        if self._client.capabilities is not None and not self._valid_value(self._attr_native_value):
            self._user_set = False
            self._attr_native_value = self._model_default()
        self.async_write_ha_state()

    @callback
    def _handle_reset_event(self, event) -> None:
        """Clear the user_set flag and restore the default on reset."""
        self._user_set = False
        self._attr_native_value = self._model_default()
        self.async_write_ha_state()

    def _validate_value(self, value: object) -> None:
        if (
            not isinstance(value, (int, float))
            or not self.native_min_value <= value <= self.native_max_value
            or value != int(value)
        ):
            raise ServiceValidationError(
                f"{self._field} must be an integer between "
                f"{self.native_min_value} and {self.native_max_value}"
            )
        if not self.available:
            raise ServiceValidationError(f"{self._field} is unsupported on this machine")
        caps = self._client.capabilities
        if caps is not None:
            raw = int(value) - 1 if self._field == "strength" else int(value)
            try:
                self._client.brand.validate_recipe_param(caps, "temporary", 0, self._field, raw)
            except ValueError as err:
                raise ServiceValidationError(str(err)) from err

    async def async_set_native_value(self, value: float) -> None:
        """Accept finite integral input within this model's displayed limits."""
        self._validate_value(value)
        self._attr_native_value = value
        self._user_set = True
        self.async_write_ha_state()
