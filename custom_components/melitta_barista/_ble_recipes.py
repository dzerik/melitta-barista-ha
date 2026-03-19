"""Recipe, profile, and cup counter operations for Melitta BLE client (mixin)."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from bleak.exc import BleakError

if TYPE_CHECKING:
    from ._ble_typing import BleClientProtocol

    _MixinBase = BleClientProtocol
else:
    _MixinBase = object

from .const import (
    CUP_COUNTER_BASE_ID,
    CUP_COUNTER_RECIPES,
    DirectKeyCategory,
    PROFILE_NAMES,
    TOTAL_CUPS_ID,
    USER_ACTIVITY_IDS,
    USER_NAME_IDS,
    get_directkey_id,
    get_user_profile_count,
)
from .protocol import MachineRecipe, RecipeComponent

_LOGGER = logging.getLogger("melitta_barista")


class BleRecipesMixin(_MixinBase):
    """Mixin providing recipe, profile, and cup counter operations."""

    # Recipe operations

    async def read_recipe(self, recipe_id: int) -> MachineRecipe | None:
        if not self.connected:
            return None
        return await self._protocol.read_recipe(self._write_ble, recipe_id)

    # Profile name management

    async def read_profile_name(self, profile_id: int) -> str | None:
        """Read profile name by ID. Profile 0 is always 'My Coffee'."""
        if profile_id == 0:
            return PROFILE_NAMES[0]
        if profile_id not in USER_NAME_IDS:
            _LOGGER.warning("Invalid profile_id %d", profile_id)
            return None
        if not self.connected:
            return None
        return await self._protocol.read_alphanumeric(
            self._write_ble, USER_NAME_IDS[profile_id],
        )

    async def read_all_profile_names(self) -> dict[int, str]:
        """Read all profile names. Returns {profile_id: name}."""
        if not self.connected:
            return {}
        count = get_user_profile_count(self._machine_type)
        result: dict[int, str] = {0: PROFILE_NAMES[0]}
        for i in range(1, count + 1):
            if i not in USER_NAME_IDS:
                continue
            try:
                name = await self._protocol.read_alphanumeric(
                    self._write_ble, USER_NAME_IDS[i],
                )
                if name:
                    result[i] = name
                else:
                    result[i] = f"Profile {i}"
            except (BleakError, OSError, asyncio.TimeoutError):
                _LOGGER.debug("Failed to read name for profile %d", i)
                result[i] = f"Profile {i}"
        return result

    async def write_profile_name(self, profile_id: int, name: str) -> bool:
        """Write profile name. Profile 0 cannot be renamed."""
        if profile_id == 0:
            _LOGGER.warning("Cannot rename default profile 0")
            return False
        if profile_id not in USER_NAME_IDS:
            _LOGGER.warning("Invalid profile_id %d", profile_id)
            return False
        if not self.connected:
            return False
        return await self._protocol.write_alphanumeric(
            self._write_ble, USER_NAME_IDS[profile_id], name,
        )

    # Profile activity

    async def read_profile_activity(self, profile_id: int) -> int | None:
        """Read profile activity value (numerical)."""
        if profile_id == 0:
            return None
        if profile_id not in USER_ACTIVITY_IDS:
            _LOGGER.warning("Invalid profile_id %d for activity", profile_id)
            return None
        if not self.connected:
            return None
        return await self._protocol.read_numerical(
            self._write_ble, USER_ACTIVITY_IDS[profile_id],
        )

    async def write_profile_activity(self, profile_id: int, value: int) -> bool:
        """Write profile activity value (numerical)."""
        if profile_id == 0:
            _LOGGER.warning("Cannot write activity for default profile 0")
            return False
        if profile_id not in USER_ACTIVITY_IDS:
            _LOGGER.warning("Invalid profile_id %d for activity", profile_id)
            return False
        if not self.connected:
            return False
        return await self._protocol.write_numerical(
            self._write_ble, USER_ACTIVITY_IDS[profile_id], value,
        )

    # Profile recipe management

    async def read_profile_recipe(
        self, profile_id: int, category: DirectKeyCategory,
    ) -> MachineRecipe | None:
        """Read recipe for a profile and category via DirectKey."""
        if not self.connected:
            return None
        recipe_id = get_directkey_id(profile_id, category)
        return await self._protocol.read_recipe(self._write_ble, recipe_id)

    async def read_all_profile_recipes(
        self, profile_id: int,
    ) -> dict[DirectKeyCategory, MachineRecipe]:
        """Read all recipes for a profile via DirectKey."""
        if not self.connected:
            return {}
        result: dict[DirectKeyCategory, MachineRecipe] = {}
        for cat in DirectKeyCategory:
            recipe_id = get_directkey_id(profile_id, cat)
            try:
                recipe = await self._protocol.read_recipe(
                    self._write_ble, recipe_id,
                )
                if recipe:
                    result[cat] = recipe
            except (BleakError, OSError, asyncio.TimeoutError):
                _LOGGER.debug(
                    "Failed to read recipe for profile %d, category %s",
                    profile_id, cat.name,
                )
        return result

    async def write_profile_recipe(
        self,
        profile_id: int,
        category: DirectKeyCategory,
        component1: RecipeComponent,
        component2: RecipeComponent,
    ) -> bool:
        """Write recipe for a profile and category via DirectKey.

        Reads the current recipe first to preserve recipe_type,
        then writes back with updated components.
        """
        if not self.connected:
            return False
        if self._brew_lock.locked():
            _LOGGER.warning("Brew in progress, cannot save recipe")
            return False

        recipe_id = get_directkey_id(profile_id, category)

        was_polling = self._poll_task is not None and not self._poll_task.done()
        async with self._brew_lock:
            self._stop_polling()
            try:
                current = None
                for attempt in range(self._recipe_retries):
                    current = await self._protocol.read_recipe(self._write_ble, recipe_id)
                    if current:
                        break
                    _LOGGER.debug("Read recipe %d attempt %d failed, retrying", recipe_id, attempt + 1)
                    await asyncio.sleep(0.3)

                from .const import DIRECTKEY_DEFAULT_RECIPE_TYPE

                if current:
                    recipe_type = current.recipe_type
                else:
                    recipe_type = DIRECTKEY_DEFAULT_RECIPE_TYPE.get(category, 0)
                    _LOGGER.warning(
                        "Cannot read recipe %d, using default recipe_type=%d for %s",
                        recipe_id, recipe_type, category.name,
                    )

                _LOGGER.debug(
                    "Writing DK recipe id=%d type=%d (profile=%d, %s)",
                    recipe_id, recipe_type, profile_id, category.name,
                )

                result = False
                for attempt in range(self._recipe_retries):
                    result = await self._protocol.write_recipe(
                        self._write_ble, recipe_id, recipe_type,
                        component1, component2,
                    )
                    if result:
                        break
                    _LOGGER.warning(
                        "Write recipe %d attempt %d failed (ACK timeout)",
                        recipe_id, attempt + 1,
                    )
                    await asyncio.sleep(0.5)

                if result:
                    _LOGGER.debug(
                        "Written DirectKey recipe id=%d (profile=%d, %s)",
                        recipe_id, profile_id, category.name,
                    )
                    await asyncio.sleep(0.3)
                    updated = await self._protocol.read_recipe(
                        self._write_ble, recipe_id,
                    )
                    if updated:
                        if profile_id not in self._directkey_recipes:
                            self._directkey_recipes[profile_id] = {}
                        self._directkey_recipes[profile_id][category] = updated
                        self._notify_profile_callbacks()
                else:
                    _LOGGER.error(
                        "Write recipe %d failed after %d attempts (profile=%d, %s)",
                        recipe_id, self._recipe_retries, profile_id, category.name,
                    )
                return result
            except (BleakError, OSError, asyncio.TimeoutError):
                _LOGGER.exception(
                    "BLE error writing DirectKey recipe id=%d", recipe_id,
                )
                return False
            finally:
                if was_polling and self.connected:
                    self.start_polling(interval=self._poll_interval)

    async def reset_profile_recipe(
        self, profile_id: int, category: DirectKeyCategory,
    ) -> bool:
        """Reset profile recipe to default (copy from profile 0)."""
        if profile_id == 0:
            _LOGGER.warning("Cannot reset default profile 0 recipe")
            return False
        if not self.connected:
            return False
        if self._brew_lock.locked():
            _LOGGER.warning("Brew in progress, cannot reset recipe")
            return False

        was_polling = self._poll_task is not None and not self._poll_task.done()
        async with self._brew_lock:
            self._stop_polling()
            try:
                default_id = get_directkey_id(0, category)
                default_recipe = await self._protocol.read_recipe(
                    self._write_ble, default_id,
                )
                if not default_recipe:
                    _LOGGER.error(
                        "Cannot read default recipe for category %s", category.name,
                    )
                    return False
                target_id = get_directkey_id(profile_id, category)
                return await self._protocol.write_recipe(
                    self._write_ble, target_id, default_recipe.recipe_type,
                    default_recipe.component1, default_recipe.component2,
                )
            finally:
                if was_polling and self.connected:
                    self.start_polling(interval=self._poll_interval)

    async def update_profile_recipe(
        self,
        profile_id: int,
        category: DirectKeyCategory,
        *,
        process: int | None = None,
        shots: int | None = None,
        blend: int | None = None,
        intensity: int | None = None,
        aroma: int | None = None,
        temperature: int | None = None,
        portion_ml: int | None = None,
    ) -> bool:
        """Update individual parameters of a profile recipe (component 1)."""
        if not self.connected:
            return False
        if self._brew_lock.locked():
            _LOGGER.warning("Brew in progress, cannot update recipe")
            return False

        was_polling = self._poll_task is not None and not self._poll_task.done()
        async with self._brew_lock:
            self._stop_polling()
            try:
                recipe_id = get_directkey_id(profile_id, category)
                current = await self._protocol.read_recipe(self._write_ble, recipe_id)
                if not current:
                    _LOGGER.error(
                        "Cannot read current recipe for profile %d, category %s",
                        profile_id, category.name,
                    )
                    return False
                c = current.component1
                updated = RecipeComponent(
                    process=process if process is not None else c.process,
                    shots=shots if shots is not None else c.shots,
                    blend=blend if blend is not None else c.blend,
                    intensity=intensity if intensity is not None else c.intensity,
                    aroma=aroma if aroma is not None else c.aroma,
                    temperature=temperature if temperature is not None else c.temperature,
                    portion=portion_ml // 5 if portion_ml is not None else c.portion,
                    reserve=c.reserve,
                )
                return await self._protocol.write_recipe(
                    self._write_ble, recipe_id, current.recipe_type,
                    updated, current.component2,
                )
            finally:
                if was_polling and self.connected:
                    self.start_polling(interval=self._poll_interval)

    async def copy_profile_recipe(
        self,
        from_profile: int,
        to_profile: int,
        category: DirectKeyCategory,
    ) -> bool:
        """Copy a recipe from one profile to another."""
        if not self.connected:
            return False
        if self._brew_lock.locked():
            _LOGGER.warning("Brew in progress, cannot copy recipe")
            return False

        was_polling = self._poll_task is not None and not self._poll_task.done()
        async with self._brew_lock:
            self._stop_polling()
            try:
                source_id = get_directkey_id(from_profile, category)
                source = await self._protocol.read_recipe(self._write_ble, source_id)
                if not source:
                    _LOGGER.error(
                        "Cannot read source recipe from profile %d, category %s",
                        from_profile, category.name,
                    )
                    return False
                target_id = get_directkey_id(to_profile, category)
                return await self._protocol.write_recipe(
                    self._write_ble, target_id, source.recipe_type,
                    source.component1, source.component2,
                )
            finally:
                if was_polling and self.connected:
                    self.start_polling(interval=self._poll_interval)

    async def reset_all_profile_recipes(self, profile_id: int) -> bool:
        """Reset all recipes of a profile to defaults (from profile 0)."""
        if profile_id == 0:
            _LOGGER.warning("Cannot reset default profile 0 recipes")
            return False
        if not self.connected:
            return False
        all_ok = True
        for cat in DirectKeyCategory:
            try:
                if not await self.reset_profile_recipe(profile_id, cat):
                    all_ok = False
            except (BleakError, OSError, asyncio.TimeoutError):
                _LOGGER.debug(
                    "Failed to reset recipe for profile %d, category %s",
                    profile_id, cat.name,
                )
                all_ok = False
        return all_ok

    # Profile data bulk read

    async def read_profile_data(self) -> None:
        """Read profile names and DirectKey recipes for all profiles."""
        if not self.connected:
            return
        profile_count = get_user_profile_count(self._machine_type)
        for i in range(1, profile_count + 1):
            if i in USER_NAME_IDS:
                try:
                    name = await self._protocol.read_alphanumeric(
                        self._write_ble, USER_NAME_IDS[i],
                    )
                    if name:
                        self._profile_names[i] = name
                except (BleakError, OSError, asyncio.TimeoutError):
                    _LOGGER.debug("Failed to read name for profile %d", i)

        for pid in range(0, profile_count + 1):
            recipes: dict[int, MachineRecipe] = {}
            for cat in DirectKeyCategory:
                dk_id = get_directkey_id(pid, cat)
                try:
                    recipe = await self._protocol.read_recipe(self._write_ble, dk_id)
                    if recipe:
                        recipes[cat] = recipe
                except (BleakError, OSError, asyncio.TimeoutError):
                    _LOGGER.debug("Failed to read DirectKey %d (profile %d, %s)", dk_id, pid, cat.name)
            self._directkey_recipes[pid] = recipes

        _LOGGER.info(
            "Loaded %d profile names, DirectKey recipes for %d profiles",
            len(self._profile_names) - 1, len(self._directkey_recipes),
        )
        self._notify_profile_callbacks()

    # Cup counters

    async def read_cup_counters(self) -> bool:
        """Read cup counters from the machine (HR IDs 100-123 + 150)."""
        if not self.connected:
            return False
        counters: dict[str, int] = {}
        for offset, name in CUP_COUNTER_RECIPES.items():
            try:
                val = await self._protocol.read_numerical(
                    self._write_ble, CUP_COUNTER_BASE_ID + offset,
                )
                if val is not None:
                    counters[name] = val
            except (BleakError, OSError, asyncio.TimeoutError):
                _LOGGER.debug("Failed to read cup counter for %s (id=%d)",
                              name, CUP_COUNTER_BASE_ID + offset)
        try:
            total = await self._protocol.read_numerical(
                self._write_ble, TOTAL_CUPS_ID,
            )
        except (BleakError, OSError, asyncio.TimeoutError):
            total = None
        self._cup_counters = counters
        self._total_cups = total
        _LOGGER.debug("Cup counters: total=%s, per_recipe=%s", total, counters)
        for cb in self._cups_callbacks:
            try:
                cb()
            except Exception:  # noqa: BLE900 — callback from user code
                _LOGGER.exception("Error in cups callback")
        return True
