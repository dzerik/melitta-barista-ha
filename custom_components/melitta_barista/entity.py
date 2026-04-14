"""Base entity mixin — device_info shared across all coffee-machine entities."""

from __future__ import annotations

from homeassistant.helpers.entity import DeviceInfo

from .ble_client import MelittaBleClient
from .const import DOMAIN


class MelittaDeviceMixin:
    """Mixin providing common device_info for all brand entities.

    Manufacturer and model are read at runtime from the active
    ``BrandProfile`` + ``MachineCapabilities`` so Melitta-configured
    entries display as "Melitta" and Nivona-configured ones as "Nivona"
    — the class name is kept as ``MelittaDeviceMixin`` only for
    historical compatibility with earlier releases.
    """

    _client: MelittaBleClient
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
