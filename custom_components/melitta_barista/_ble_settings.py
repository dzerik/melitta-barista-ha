"""Settings and alphanumeric read/write operations for Melitta BLE client (mixin)."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from bleak.exc import BleakError

from .const import USER_NAME_IDS

if TYPE_CHECKING:
    from ._ble_typing import BleClientProtocol

    _MixinBase = BleClientProtocol
else:
    _MixinBase = object

_LOGGER = logging.getLogger("melitta_barista")


class BleSettingsMixin(_MixinBase):
    """Mixin providing settings and alphanumeric read/write."""

    # Numerical settings

    async def read_setting(self, setting_id: int) -> int | None:
        if not self.connected:
            return None
        return await self._protocol.read_numerical(self._write_ble, setting_id)

    async def write_setting(self, setting_id: int, value: int) -> bool:
        if not self.connected:
            return False
        return await self._protocol.write_numerical(self._write_ble, setting_id, value)

    # Alphanumeric operations

    async def read_alpha(self, value_id: int) -> str | None:
        if not self.connected:
            return None
        return await self._protocol.read_alphanumeric(self._write_ble, value_id)

    async def write_alpha(self, value_id: int, value: str) -> bool:
        if not self.connected:
            return False
        if self._brew_lock.locked():
            _LOGGER.warning("Brew in progress, cannot write alpha")
            return False
        was_polling = self._poll_task is not None and not self._poll_task.done()
        async with self._brew_lock:
            self._stop_polling()
            try:
                result = await self._protocol.write_alphanumeric(
                    self._write_ble, value_id, value,
                )
                if result:
                    for pid, name_id in USER_NAME_IDS.items():
                        if name_id == value_id:
                            self._profile_names[pid] = value
                            self._notify_profile_callbacks()
                            break
                return result
            except (BleakError, OSError, asyncio.TimeoutError):
                _LOGGER.exception("BLE error writing alpha id=%d", value_id)
                return False
            finally:
                if was_polling and self.connected:
                    self.start_polling(interval=self._poll_interval)
