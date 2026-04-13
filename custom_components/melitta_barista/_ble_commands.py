"""Brew, maintenance, and cancel commands for Melitta BLE client (mixin)."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._ble_typing import BleClientProtocol

    _MixinBase = BleClientProtocol
else:
    _MixinBase = object

from .const import (
    DirectKeyCategory,
    MachineProcess,
    RecipeId,
)

if TYPE_CHECKING:
    from .protocol import RecipeComponent

_LOGGER = logging.getLogger("melitta_barista")


class BleCommandsMixin(_MixinBase):
    """Mixin providing brew and maintenance commands."""

    async def brew_recipe(
        self, recipe_id: RecipeId, *, two_cups: bool = False,
    ) -> bool:
        """Brew a base recipe (always from default profile 0).

        Uses _brew_lock to prevent concurrent brew operations.
        Pauses polling during brew to avoid BLE contention.
        """
        if self._brew_lock.locked():
            _LOGGER.warning("Brew already in progress, ignoring")
            return False

        if not self.connected:
            return False
        if self._status and not self._status.is_ready:
            _LOGGER.warning("Machine not ready: %s", self._status)
            return False

        from .const import RECIPE_NAMES, TEMP_RECIPE_ID, FREESTYLE_NAME_ID

        async with self._brew_lock:
            self._stop_polling()
            try:
                recipe = await self._protocol.read_recipe(
                    self._write_ble, int(recipe_id),
                )
                if not recipe:
                    _LOGGER.error("Failed to read recipe %d", recipe_id)
                    return False

                from .const import get_recipe_key
                if not await self._protocol.write_recipe(
                    self._write_ble, TEMP_RECIPE_ID, recipe.recipe_type,
                    recipe.component1, recipe.component2,
                    recipe_key=get_recipe_key(recipe.recipe_type),
                ):
                    _LOGGER.error("Failed to write recipe to temp slot")
                    return False

                await asyncio.sleep(0.2)

                name = RECIPE_NAMES.get(recipe_id, str(recipe_id))
                if not await self._protocol.write_alphanumeric(
                    self._write_ble, FREESTYLE_NAME_ID, name,
                ):
                    _LOGGER.error("Failed to write recipe name")
                    return False

                await asyncio.sleep(0.2)

                return await self._protocol.start_process(
                    self._write_ble, MachineProcess.PRODUCT,
                    two_cups=two_cups,
                )
            finally:
                if self.connected:
                    self.start_polling(interval=self._poll_interval)

    async def brew_directkey(self, category: DirectKeyCategory, *, two_cups: bool = False) -> bool:
        """Brew from a DirectKey slot of the active profile.

        Reads the DirectKey recipe for the active profile and category,
        writes to temp slot, then starts brewing.
        """
        if self._brew_lock.locked():
            _LOGGER.warning("Brew already in progress, ignoring")
            return False

        if not self.connected:
            return False
        if self._status and not self._status.is_ready:
            _LOGGER.warning("Machine not ready: %s", self._status)
            return False

        from .const import TEMP_RECIPE_ID, FREESTYLE_NAME_ID

        async with self._brew_lock:
            self._stop_polling()
            try:
                from .const import get_directkey_id, get_recipe_key

                recipe_id = get_directkey_id(self.active_profile, category)
                recipe = None
                for attempt in range(self._recipe_retries):
                    recipe = await self._protocol.read_recipe(
                        self._write_ble, recipe_id,
                    )
                    if recipe:
                        break
                    _LOGGER.debug(
                        "Read DK recipe %d attempt %d failed", recipe_id, attempt + 1,
                    )
                    await asyncio.sleep(0.3)

                if not recipe:
                    _LOGGER.error(
                        "Failed to read DirectKey recipe %d (profile=%d, %s)",
                        recipe_id, self.active_profile, category.name,
                    )
                    return False

                if not await self._protocol.write_recipe(
                    self._write_ble, TEMP_RECIPE_ID, recipe.recipe_type,
                    recipe.component1, recipe.component2,
                    recipe_key=get_recipe_key(recipe.recipe_type),
                ):
                    _LOGGER.error("Failed to write recipe to temp slot")
                    return False

                await asyncio.sleep(0.2)

                name = category.name.replace("_", " ").title()
                if not await self._protocol.write_alphanumeric(
                    self._write_ble, FREESTYLE_NAME_ID, name,
                ):
                    _LOGGER.error("Failed to write recipe name")
                    return False

                await asyncio.sleep(0.2)

                return await self._protocol.start_process(
                    self._write_ble, MachineProcess.PRODUCT,
                    two_cups=two_cups,
                )
            finally:
                if self.connected:
                    self.start_polling(interval=self._poll_interval)

    async def brew_freestyle(
        self,
        name: str,
        recipe_type: int,
        component1: RecipeComponent,
        component2: RecipeComponent,
    ) -> bool:
        """Brew a freestyle (custom) recipe.

        Writes the provided components to the temp recipe slot and starts brewing.
        recipe_type is typically 24 (freestyle).
        """
        if self._brew_lock.locked():
            _LOGGER.warning("Brew already in progress, ignoring")
            return False

        if not self.connected:
            return False
        if self._status and not self._status.is_ready:
            _LOGGER.warning("Machine not ready: %s", self._status)
            return False

        from .const import TEMP_RECIPE_ID, FREESTYLE_NAME_ID, get_recipe_key

        async with self._brew_lock:
            self._stop_polling()
            try:
                if not await self._protocol.write_recipe(
                    self._write_ble, TEMP_RECIPE_ID, recipe_type,
                    component1, component2,
                    recipe_key=get_recipe_key(recipe_type),
                ):
                    _LOGGER.error("Failed to write freestyle recipe to temp slot")
                    return False

                await asyncio.sleep(0.2)

                if not await self._protocol.write_alphanumeric(
                    self._write_ble, FREESTYLE_NAME_ID, name,
                ):
                    _LOGGER.error("Failed to write freestyle recipe name")
                    return False

                await asyncio.sleep(0.2)

                return await self._protocol.start_process(
                    self._write_ble, MachineProcess.PRODUCT,
                )
            finally:
                if self.connected:
                    self.start_polling(interval=self._poll_interval)

    async def cancel_process(self, process: MachineProcess = MachineProcess.PRODUCT) -> bool:
        if not self.connected:
            return False
        return await self._protocol.cancel_process(self._write_ble, process)

    async def cancel_brewing(self) -> bool:
        return await self.cancel_process(MachineProcess.PRODUCT)

    async def reset_recipe_default(self, recipe_id: int) -> bool:
        """Reset a recipe to factory defaults via HD command.

        Returns True if the machine ACKed (A), False on NACK/timeout/
        disconnected state.
        """
        if not self.connected:
            return False
        return await self._protocol.reset_default(self._write_ble, recipe_id)

    # Maintenance operations

    async def start_easy_clean(self) -> bool:
        if not self.connected:
            return False
        return await self._protocol.start_process(
            self._write_ble, MachineProcess.EASY_CLEAN)

    async def start_intensive_clean(self) -> bool:
        if not self.connected:
            return False
        return await self._protocol.start_process(
            self._write_ble, MachineProcess.INTENSIVE_CLEAN)

    async def start_descaling(self) -> bool:
        if not self.connected:
            return False
        return await self._protocol.start_process(
            self._write_ble, MachineProcess.DESCALING)

    async def start_filter_insert(self) -> bool:
        if not self.connected:
            return False
        return await self._protocol.start_process(
            self._write_ble, MachineProcess.FILTER_INSERT)

    async def start_filter_replace(self) -> bool:
        if not self.connected:
            return False
        return await self._protocol.start_process(
            self._write_ble, MachineProcess.FILTER_REPLACE)

    async def start_filter_remove(self) -> bool:
        if not self.connected:
            return False
        return await self._protocol.start_process(
            self._write_ble, MachineProcess.FILTER_REMOVE)

    async def start_evaporating(self) -> bool:
        if not self.connected:
            return False
        return await self._protocol.start_process(
            self._write_ble, MachineProcess.EVAPORATING)

    async def switch_off(self) -> bool:
        if not self.connected:
            return False
        return await self._protocol.start_process(
            self._write_ble, MachineProcess.SWITCH_OFF)
