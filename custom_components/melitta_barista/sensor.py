"""Sensor platform — state, activity, progress, cup counters, diagnostics."""

from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_NAME, PERCENTAGE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .ble_client import resolve_caps_from_scanner
from .coffee_platform.contract import CoffeeMachineClient
from .const import FeatureFlags, InfoMessage, MachineProcess, Manipulation, SubProcess
from .entity import MelittaDeviceMixin
from .protocol import MachineStatus


PARALLEL_UPDATES = 0  # BLE: single connection, serialize via locks

_LOGGER = logging.getLogger("melitta_barista")


PROCESS_LABELS: dict[MachineProcess, str] = {
    MachineProcess.READY: "Ready",
    MachineProcess.PRODUCT: "Brewing",
    MachineProcess.CLEANING: "Cleaning",
    MachineProcess.DESCALING: "Descaling",
    MachineProcess.FILTER_INSERT: "Filter Insert",
    MachineProcess.FILTER_REPLACE: "Filter Replace",
    MachineProcess.FILTER_REMOVE: "Filter Remove",
    MachineProcess.SWITCH_OFF: "Off",
    MachineProcess.EASY_CLEAN: "Easy Clean",
    MachineProcess.INTENSIVE_CLEAN: "Intensive Clean",
    MachineProcess.EVAPORATING: "Evaporating",
    MachineProcess.BUSY: "Busy",
}

SUBPROCESS_LABELS: dict[SubProcess, str] = {
    SubProcess.GRINDING: "Grinding",
    SubProcess.COFFEE: "Extracting",
    SubProcess.STEAM: "Steaming",
    SubProcess.WATER: "Dispensing Water",
    SubProcess.PREPARE: "Preparing",
}

MANIPULATION_LABELS: dict[Manipulation, str] = {
    Manipulation.NONE: "None",
    Manipulation.BU_REMOVED: "Brew Unit Removed",
    Manipulation.TRAYS_MISSING: "Trays Missing",
    Manipulation.EMPTY_TRAYS: "Empty Trays",
    Manipulation.FILL_WATER: "Fill Water",
    Manipulation.CLOSE_POWDER_LID: "Close Powder Lid",
    Manipulation.FILL_POWDER: "Fill Powder",
    Manipulation.MOVE_CUP_TO_FROTHER: "Move Cup to Frother",
    Manipulation.FLUSH_REQUIRED: "Flush Required",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities for the configured coffee machine."""
    client: CoffeeMachineClient = entry.runtime_data
    name = entry.data.get(CONF_NAME) or f"{client.brand.brand_name} Coffee Machine"

    entities = [
        MelittaStateSensor(client, entry, name),
        MelittaActivitySensor(client, entry, name),
        MelittaProgressSensor(client, entry, name),
        MelittaActionRequiredSensor(client, entry, name),
        MelittaConnectionSensor(client, entry, name),
        MelittaFirmwareSensor(client, entry, name),
        MelittaSerialSensor(client, entry, name),
        MelittaFeaturesSensor(client, entry, name),
    ]

    # Legacy MelittaTotalCupsSensor reads HR id=150 (TOTAL_CUPS_ID) which
    # is a Melitta-specific register. On Nivona the register doesn't
    # exist and the sensor stayed at `unknown` (reported in #15). For
    # non-Melitta brands the equivalent counter is exposed via the
    # capability-driven `BrandStatSensor` (`total_beverages`, id 213 on
    # 8000 family, id 215 on 1030, etc.) — no need to register the
    # Melitta-specific sensor at all.
    # Capabilities may be None at setup time (resolved after BLE connect).
    # For brands that don't use the legacy total-cups sensor, fall back
    # to early family detection via the BLE scanner cache so generic
    # capability-driven stat sensors can register without waiting.
    caps = client.capabilities
    if caps is None:
        caps = resolve_caps_from_scanner(hass, entry.data.get(CONF_ADDRESS, ""), client.brand)

    # Legacy Melitta total-cups sensor reads HR id 150 — capability-flagged
    # because the register doesn't exist on other brands and would surface
    # as "unknown".
    if caps is not None and caps.uses_legacy_total_cups_sensor:
        entities.append(MelittaTotalCupsSensor(client, entry, name))

    # Generic capability-driven stat sensors — only for brands that
    # expose a stats table AND don't already have a hand-tailored
    # total-cups sensor (Melitta).
    if (
        caps is not None
        and caps.stats
        and not caps.uses_legacy_total_cups_sensor
    ):
        for descriptor in caps.stats:
            entities.append(BrandStatSensor(client, entry, name, descriptor))

    # MyCoffee slot amount sensors — Nivona only. For each slot 0..N-1,
    # register one sensor per amount param (coffee / water / milk /
    # milk_foam) that the family's MyCoffee layout actually exposes
    # (the 600 family for example has no ``milk_amount_offset``). The
    # cache is populated by the post-connect bulk read in
    # ``BleRecipesMixin.read_mycoffee_slots``; sensors stay
    # ``unavailable`` until the first read completes.
    # MyCoffee bulk-read sensors — only register for brands whose profile
    # advertises a MyCoffee layout (Nivona). Melitta's mycoffee_layout
    # returns None and the block short-circuits.
    if caps is not None:
        layout = client.brand.mycoffee_layout(caps.family_key)
        if layout is not None and caps.my_coffee_slots > 0:
            from ._ble_recipes import _MYCOFFEE_AMOUNT_PARAMS  # noqa: PLC0415
            for slot in range(caps.my_coffee_slots):
                for param_key in _MYCOFFEE_AMOUNT_PARAMS:
                    if getattr(layout, f"{param_key}_offset") is None:
                        continue
                    entities.append(
                        NivonaMyCoffeeAmountSensor(
                            client, entry, name, slot, param_key,
                        )
                    )

    async_add_entities(entities)


class _MelittaSensorBase(MelittaDeviceMixin, SensorEntity):
    """Base class shared by all sensor entities."""

    _attr_has_entity_name = True

    def __init__(self, client: CoffeeMachineClient, entry: ConfigEntry, name: str) -> None:
        self._client = client
        self._entry = entry
        self._machine_name = name

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


class MelittaStateSensor(_MelittaSensorBase):
    """Machine state (Ready, Brewing, Cleaning, etc.)."""

    _attr_name = "State"
    _attr_icon = "mdi:coffee-maker"

    @property
    def unique_id(self) -> str:
        return f"{self._client.address}_state"

    @property
    def available(self) -> bool:
        return self._client.connected and self._client.status is not None

    @property
    def native_value(self) -> str | None:
        status = self._client.status
        if status is None or status.process is None:
            return None
        return PROCESS_LABELS.get(status.process, status.process.name)

    @property
    def extra_state_attributes(self) -> dict:
        status = self._client.status
        if status is None:
            return {}
        attrs = {}
        if status.process is not None:
            attrs["process_id"] = status.process.value
        if status.info_messages:
            flags = [m.name for m in InfoMessage if status.info_messages & m]
            if flags:
                attrs["info_messages"] = flags
        return attrs


class MelittaActivitySensor(_MelittaSensorBase):
    """Current sub-activity (Grinding, Extracting, Steaming, etc.)."""

    _attr_name = "Activity"
    _attr_icon = "mdi:coffee"

    @property
    def unique_id(self) -> str:
        return f"{self._client.address}_activity"

    @property
    def available(self) -> bool:
        return self._client.connected

    @property
    def native_value(self) -> str | None:
        status = self._client.status
        if status is None or status.sub_process is None:
            return "Idle"
        return SUBPROCESS_LABELS.get(status.sub_process, status.sub_process.name)


class MelittaProgressSensor(_MelittaSensorBase):
    """Progress percentage during brewing/cleaning."""

    _attr_name = "Progress"
    _attr_icon = "mdi:progress-clock"
    _attr_native_unit_of_measurement = PERCENTAGE

    @property
    def unique_id(self) -> str:
        return f"{self._client.address}_progress"

    @property
    def native_value(self) -> int | None:
        status = self._client.status
        if status is None:
            return None
        if status.process in (MachineProcess.READY, None):
            return None
        return status.progress


class MelittaActionRequiredSensor(_MelittaSensorBase):
    """Required user action (Fill Water, Empty Trays, etc.)."""

    _attr_name = "Action Required"
    _attr_icon = "mdi:alert-circle"

    @property
    def unique_id(self) -> str:
        return f"{self._client.address}_manipulation"

    @property
    def native_value(self) -> str | None:
        status = self._client.status
        if status is None:
            return None
        return MANIPULATION_LABELS.get(status.manipulation, "None")

    @property
    def icon(self) -> str:
        status = self._client.status
        if status and status.manipulation != Manipulation.NONE:
            return "mdi:alert-circle"
        return "mdi:check-circle"


class MelittaConnectionSensor(_MelittaSensorBase):
    """BLE connection state."""

    _attr_name = "Connection"
    _attr_icon = "mdi:bluetooth-connect"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._client.address}_connection"

    @property
    def native_value(self) -> str:
        return "Connected" if self._client.connected else "Disconnected"

    @property
    def icon(self) -> str:
        return "mdi:bluetooth-connect" if self._client.connected else "mdi:bluetooth-off"


class MelittaFirmwareSensor(_MelittaSensorBase):
    """Firmware version."""

    _attr_name = "Firmware"
    _attr_icon = "mdi:chip"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._client.address}_firmware"

    @property
    def native_value(self) -> str | None:
        return self._client.firmware_version


class MelittaSerialSensor(_MelittaSensorBase):
    """Machine serial number (read via HL on connect)."""

    _attr_translation_key = "serial_number"
    _attr_icon = "mdi:identifier"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._client.address}_serial"

    @property
    def native_value(self) -> str | None:
        return self._client.serial_number


class MelittaFeaturesSensor(_MelittaSensorBase):
    """Machine capability flags (HI). Disabled by default."""

    _attr_name = "Features"
    _attr_icon = "mdi:feature-search-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    @property
    def unique_id(self) -> str:
        return f"{self._client.address}_features"

    @property
    def native_value(self) -> str | None:
        f = self._client.features
        if f is None:
            return None
        names = [flag.name for flag in FeatureFlags if flag in f and flag.name]
        return ", ".join(names) if names else "none"

    @property
    def extra_state_attributes(self) -> dict:
        f = self._client.features
        if f is None:
            return {}
        return {"raw": f"0x{int(f):02x}"}


class MelittaTotalCupsSensor(_MelittaSensorBase):
    """Total cups brewed with per-recipe breakdown."""

    _attr_name = "Total Cups"
    _attr_icon = "mdi:coffee"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "cups"

    @property
    def unique_id(self) -> str:
        return f"{self._client.address}_total_cups"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._client.add_cups_callback(self._on_cups_update)

    async def async_will_remove_from_hass(self) -> None:
        await super().async_will_remove_from_hass()
        self._client.remove_cups_callback(self._on_cups_update)

    @callback
    def _on_cups_update(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self._client.connected and self._client.total_cups is not None

    @property
    def native_value(self) -> int | None:
        return self._client.total_cups

    @property
    def extra_state_attributes(self) -> dict:
        counters = self._client.cup_counters
        if not counters:
            return {}
        return {name: count for name, count in counters.items() if count > 0}


# ---------------------------------------------------------------------------
# Brand capability-driven generic stat sensor
# ---------------------------------------------------------------------------

class BrandStatSensor(_MelittaSensorBase):
    """Generic stat sensor driven by a ``StatDescriptor`` from the active
    BrandProfile capabilities. Polls via HR at integration's poll interval
    plus refreshes on connection events. Used for Nivona families that
    expose per-recipe cup counters, maintenance counters, and percent/flag
    gauges through the shared Eugster numeric-register protocol.
    """

    def __init__(self, client: CoffeeMachineClient, entry, name: str, descriptor) -> None:
        super().__init__(client, entry, name)
        self._desc = descriptor
        self._value: int | None = None
        self._attr_name = descriptor.title
        # Translation key so HA looks up localized names under
        # `entity.sensor.<descriptor.key>.name` in translations/*.json.
        # Falls back to `_attr_name` (English) for keys not present.
        self._attr_translation_key = descriptor.key
        if descriptor.is_diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        if descriptor.unit == "%":
            self._attr_native_unit_of_measurement = PERCENTAGE
        elif descriptor.unit == "count":
            self._attr_native_unit_of_measurement = None
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        elif descriptor.unit is None:
            # flag (0/1) — render as binary count
            self._attr_native_unit_of_measurement = None

    @property
    def unique_id(self) -> str:
        return f"{self._client.address}_stat_{self._desc.key}"

    @property
    def native_value(self) -> int | None:
        return self._value

    @property
    def icon(self) -> str:
        unit = self._desc.unit
        if unit == "%":
            return "mdi:percent"
        if unit == "count":
            return "mdi:counter"
        return "mdi:alert-circle-outline"   # flag

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._client.connected:
            await self._refresh()

    @callback
    def _on_status_update(self, status) -> None:
        # Status updates arrive every poll; piggyback to refresh the
        # stat lazily (rate-limited by HR single-flight lock upstream).
        super()._on_status_update(status)

    @callback
    def _on_connection_change(self, connected: bool) -> None:
        super()._on_connection_change(connected)
        if connected:
            self.hass.async_create_task(self._refresh())

    async def _refresh(self) -> None:
        try:
            value = await self._client.read_setting(self._desc.stat_id)
            if value is not None:
                self._value = value
                self.async_write_ha_state()
        except Exception:  # noqa: BLE001
            _LOGGER.debug(
                "BrandStatSensor %s refresh failed", self._desc.key, exc_info=True,
            )


class NivonaMyCoffeeAmountSensor(_MelittaSensorBase):
    """Per-(slot, param) MyCoffee amount sensor for Nivona (read-only).

    Reads from the client's ``my_coffee_slots`` cache, which is filled
    once per connect by ``BleRecipesMixin.read_mycoffee_slots``. Stays
    ``unavailable`` until that first read completes.

    Currently exposes the four "amount" params per slot
    (coffee / water / milk / milk_foam) — only those whose offset is
    defined in the family's MyCoffee layout. Future PRs will add
    strength / temperature / enabled flag and write support.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = None  # raw byte value; vendor-specific scale

    # Human-readable suffix per param + an icon hint.
    _LABELS = {
        "coffee_amount": "coffee amount",
        "water_amount": "water amount",
        "milk_amount": "milk amount",
        "milk_foam_amount": "milk foam amount",
        "enabled": "enabled",
        "strength": "strength",
        "temperature": "temperature",
    }

    _ICONS = {
        "coffee_amount": "mdi:cup-outline",
        "water_amount": "mdi:cup-water",
        "milk_amount": "mdi:bottle-tonic-plus-outline",
        "milk_foam_amount": "mdi:bottle-tonic-outline",
        "enabled": "mdi:check-circle-outline",
        "strength": "mdi:weight",
        "temperature": "mdi:thermometer",
    }

    def __init__(
        self,
        client: CoffeeMachineClient,
        entry: ConfigEntry,
        name: str,
        slot: int,
        param_key: str,
    ) -> None:
        super().__init__(client, entry, name)
        self._slot = slot
        self._param_key = param_key
        label = self._LABELS.get(param_key, param_key.replace("_", " "))
        self._attr_name = f"MyCoffee slot {slot + 1} {label}"
        self._attr_translation_key = f"mycoffee_{param_key}"
        self._attr_translation_placeholders = {"slot": str(slot + 1)}
        self._attr_icon = self._ICONS.get(param_key, "mdi:cup-outline")

    @property
    def unique_id(self) -> str:
        return (
            f"{self._client.address}_mycoffee_slot_{self._slot}_{self._param_key}"
        )

    @property
    def available(self) -> bool:
        slots = self._client.my_coffee_slots
        return (
            self._client.connected
            and slots is not None
            and self._slot < len(slots)
            and self._param_key in slots[self._slot]
        )

    @property
    def native_value(self) -> int | None:
        slots = self._client.my_coffee_slots
        if slots is None or self._slot >= len(slots):
            return None
        return slots[self._slot].get(self._param_key)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._client.add_mycoffee_callback(self._on_mycoffee_update)

    async def async_will_remove_from_hass(self) -> None:
        await super().async_will_remove_from_hass()
        self._client.remove_mycoffee_callback(self._on_mycoffee_update)

    @callback
    def _on_mycoffee_update(self) -> None:
        self.async_write_ha_state()
