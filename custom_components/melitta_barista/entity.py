"""Base entity mixin — device_info shared across all coffee-machine entities."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity

from .coffee_platform.contract import CoffeeMachineClient
from .const import DOMAIN


class MelittaDeviceMixin:
    """Mixin providing common device_info for all brand entities.

    Manufacturer and model are read at runtime from the active
    ``BrandProfile`` + ``MachineCapabilities`` so Melitta-configured
    entries display as "Melitta" and Nivona-configured ones as "Nivona"
    — the class name is kept as ``MelittaDeviceMixin`` only for
    historical compatibility with earlier releases.
    """

    _client: CoffeeMachineClient
    _machine_name: str

    @property
    def device_info(self) -> DeviceInfo:
        brand = getattr(self._client, "brand", None)
        # If brand is missing we have a bootstrap bug — fall back to a
        # neutral label rather than mislabelling the device.
        manufacturer_name = brand.brand_name if brand is not None else "Coffee Machine"
        return DeviceInfo(
            identifiers={(DOMAIN, self._client.address)},
            name=self._machine_name,
            manufacturer=manufacturer_name,
            model=self._client.model_name,
            sw_version=self._client.firmware_version,
        )


@dataclass
class LocalControlData(ExtraStoredData):
    """Keep the underlying choice even when the entity is unavailable."""

    value: str | int | float | None

    def as_dict(self) -> dict[str, str | int | float | None]:
        return {"value": self.value}


class MelittaLocalControl(MelittaDeviceMixin, RestoreEntity):
    """Restore local choices; machine-owned settings keep their live reads."""

    _client_attr: str
    _attr_should_poll = False

    @property
    def extra_restore_state_data(self) -> LocalControlData:
        return LocalControlData(getattr(self._client, self._client_attr))

    async def async_get_last_control_value(
        self, legacy_attribute: str | None = None,
    ) -> object:
        """Read native data, falling back to state recorded before this feature."""
        if (extra := await self.async_get_last_extra_data()) is not None:
            data = extra.as_dict()
            if "value" in data:
                return data["value"]
        last = await self.async_get_last_state()
        if last is None or last.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return None
        return last.attributes.get(legacy_attribute) if legacy_attribute else last.state
