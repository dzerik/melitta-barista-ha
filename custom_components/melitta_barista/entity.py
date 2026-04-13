"""Base entity for Melitta Barista Smart."""

from __future__ import annotations

from homeassistant.helpers.entity import DeviceInfo

from .ble_client import MelittaBleClient
from .const import DOMAIN


class MelittaDeviceMixin:
    """Mixin providing common device_info for all Melitta entities."""

    _client: MelittaBleClient
    _machine_name: str

    @property
    def device_info(self) -> DeviceInfo:
        # Manufacturer tracks the active brand profile so a Nivona-
        # configured entry shows up in the UI as "Nivona" rather than
        # hard-coded "Melitta".
        manufacturer = getattr(self._client, "brand", None)
        manufacturer_name = (
            manufacturer.brand_name if manufacturer is not None else "Melitta"
        )
        return DeviceInfo(
            identifiers={(DOMAIN, self._client.address)},
            name=self._machine_name,
            manufacturer=manufacturer_name,
            model=self._client.model_name,
            sw_version=self._client.firmware_version,
        )
