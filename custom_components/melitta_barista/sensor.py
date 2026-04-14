"""Sensor platform — state, activity, progress, cup counters, diagnostics."""

from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME, PERCENTAGE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .ble_client import MelittaBleClient
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
    client: MelittaBleClient = entry.runtime_data
    name = entry.data.get(CONF_NAME) or f"{client.brand.brand_name} Coffee Machine"

    entities = [
        MelittaStateSensor(client, entry, name),
        MelittaActivitySensor(client, entry, name),
        MelittaProgressSensor(client, entry, name),
        MelittaActionRequiredSensor(client, entry, name),
        MelittaConnectionSensor(client, entry, name),
        MelittaFirmwareSensor(client, entry, name),
        MelittaFeaturesSensor(client, entry, name),
        MelittaTotalCupsSensor(client, entry, name),
    ]

    # Brand-capability-driven stat sensors (Nivona). Melitta has its own
    # hand-tailored total_cups + per-recipe counters; generic stat sensors
    # only register for non-Melitta brands with populated stats tables.
    caps = client.capabilities
    if caps is not None and caps.stats and client.brand.brand_slug != "melitta":
        for descriptor in caps.stats:
            entities.append(BrandStatSensor(client, entry, name, descriptor))

    async_add_entities(entities)


class _MelittaSensorBase(MelittaDeviceMixin, SensorEntity):
    """Base class shared by all sensor entities."""

    _attr_has_entity_name = True

    def __init__(self, client: MelittaBleClient, entry: ConfigEntry, name: str) -> None:
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
        return self._client.total_cups is not None

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

    def __init__(self, client: MelittaBleClient, entry, name: str, descriptor) -> None:
        super().__init__(client, entry, name)
        self._desc = descriptor
        self._value: int | None = None
        self._attr_name = descriptor.title
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
