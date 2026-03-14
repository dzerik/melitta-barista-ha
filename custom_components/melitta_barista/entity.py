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
        return DeviceInfo(
            identifiers={(DOMAIN, self._client.address)},
            name=self._machine_name,
            manufacturer="Melitta",
            model=self._client.model_name,
            sw_version=self._client.firmware_version,
        )
