"""Brew, maintenance, and cancel commands for the BLE client (mixin)."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from bleak.exc import BleakError

from .brands.base import FeatureNotSupported

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

    async def brew_nivona(
        self, recipe_selector: int,
        overrides: dict | None = None,
    ) -> bool:
        """Brew on Nivona by recipe selector (no HC/HJ — uses HE directly).

        ``overrides`` is an optional dict of {field: value} where ``field``
        is a RecipeFieldLayout field name (e.g. ``"strength"``,
        ``"coffee_amount"``, ``"temperature"``, ``"two_cups"``). When set,
        each override is written via HW to the per-family temporary-recipe
        register BEFORE the HE start_process is issued — this matches the
        SendTemporaryRecipe flow in the Android app.

        Nivona machines don't have recipe read/write (HC/HJ) — they use
        HE with byte[3]=selector instead.
        """
        if self._brew_lock.locked():
            _LOGGER.warning("Brew already in progress, ignoring")
            return False
        if not self.connected:
            return False
        if self._status and not self._status.is_ready:
            _LOGGER.warning("Machine not ready: %s", self._status)
            return False

        caps = getattr(self, "_capabilities", None)
        brew_mode = caps.brew_command_mode if caps else 0x0B
        family_key = caps.family_key if caps else None

        async with self._brew_lock:
            self._stop_polling()
            try:
                # Pre-write temp-recipe overrides via HW. The machine
                # exposes a single fixed temp slot shared across all
                # selectors:
                #   1. Announce the recipe class by HW-writing the
                #      selector at the temp-recipe TYPE register.
                #   2. For each override field, HW-write at
                #      TEMP_BASE + offset with the int32 value.
                #   3. Issue HE with payload[3] = selector.
                # Previously HA wrote to 10000 + selector*100 + offset
                # (the PERSISTENT standard-recipe slot) and silently
                # corrupted the machine's default recipe definitions.
                # Fixed in v0.49.0 after audit Finding 10.
                if overrides and family_key and hasattr(self._brand, "temp_recipe_register"):
                    from .brands.nivona import TEMP_RECIPE_TYPE_REGISTER  # noqa: PLC0415

                    scale = getattr(self._brand, "fluid_write_scale",
                                    lambda _: 1)(family_key)

                    # Step 1 — announce the recipe type with the
                    # selector as the value. write_numerical packs
                    # int32 BE; the machine reads the low byte of the
                    # pair so passing the selector suffices.
                    type_ok = await self._protocol.write_numerical(
                        self._write_ble,
                        TEMP_RECIPE_TYPE_REGISTER,
                        int(recipe_selector),
                    )
                    if not type_ok:
                        _LOGGER.warning(
                            "Temp-recipe type announce at reg %d failed",
                            TEMP_RECIPE_TYPE_REGISTER,
                        )
                    await asyncio.sleep(0.08)

                    # Step 2 — per-field overrides at 9001 + offset.
                    for field, value in overrides.items():
                        if value is None:
                            continue
                        reg = self._brand.temp_recipe_register(
                            family_key, recipe_selector, field,
                        )
                        if reg is None:
                            _LOGGER.debug(
                                "Skip override %s: no register for family=%s",
                                field, family_key,
                            )
                            continue
                        scaled = int(value)
                        if field in ("coffee_amount", "water_amount",
                                     "milk_amount", "milk_foam_amount"):
                            scaled = int(value) * scale
                        ok = await self._protocol.write_numerical(
                            self._write_ble, reg, scaled,
                        )
                        if not ok:
                            _LOGGER.warning(
                                "HW write failed for %s=%d (reg=%d)",
                                field, scaled, reg,
                            )
                        await asyncio.sleep(0.08)

                # Chilled-brew flag (selectors 8/9/10 on NICR 8107).
                # Other brands / families have no chilled concept.
                is_chilled = bool(
                    hasattr(self._brand, "is_chilled_selector")
                    and self._brand.is_chilled_selector(recipe_selector)
                )
                return await self._protocol.start_process_nivona(
                    self._write_ble, recipe_selector, brew_mode,
                    chilled=is_chilled,
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

        On ACK, the recipe is re-read via HC so that subscribers of
        ``add_recipe_refresh_callback`` (e.g. the Recipe select entity's
        cached ``recipes`` attribute) see factory values without waiting
        for a full reconnect.

        Returns True if the machine ACKed (A), False on NACK/timeout/
        disconnected state.
        """
        if not self.connected:
            return False
        success = await self._protocol.reset_default(self._write_ble, recipe_id)
        if not success:
            return False
        # HC (read_recipe) is a Melitta-only opcode — Nivona brand
        # profiles have an empty `supported_extensions` tuple and the
        # protocol layer raises FeatureNotSupported. Swallow that so
        # the caller doesn't blow up when HD is wired for a brand
        # that can't follow up with HC.
        try:
            recipe = await self._protocol.read_recipe(self._write_ble, recipe_id)
        except FeatureNotSupported:
            _LOGGER.debug(
                "Recipe %d post-HD re-read skipped: brand does not expose HC",
                recipe_id,
            )
            recipe = None
        except (BleakError, OSError, asyncio.TimeoutError):
            _LOGGER.debug(
                "Failed to re-read recipe %d after HD ACK", recipe_id,
                exc_info=True,
            )
            recipe = None
        if recipe is not None:
            for cb in self._recipe_refresh_callbacks:
                try:
                    cb(recipe_id, recipe)
                except Exception:  # noqa: BLE001 — callback from user code
                    _LOGGER.exception("Error in recipe refresh callback")
        return True

    async def confirm_prompt(self) -> bool:
        """Send HY to confirm the current machine prompt (move cup, flush, ...).

        The real machine treats HY as fire-and-forget — it always ACKs
        and the caller is expected to poll HX for the post-confirm
        state change rather than treat the ACK as "prompt cleared".
        The returned bool only reflects the ACK/NACK of the write
        itself; the caller should not interpret False as "the prompt
        is still showing". A subsequent status poll is authoritative.
        """
        if not self.connected:
            return False
        return await self._protocol.confirm_prompt(self._write_ble)

    # ── Nivona experimental recipe-write primitives (v0.43.0+) ──────
    #
    # These methods expose the per-family byte-offset layouts for
    # standard-recipe and MyCoffee slots (upstream resolveStandardRecipeLayout
    # and resolveMyCoffeeLayout). They are EXPERIMENTAL and have not been
    # validated on live Nivona hardware by the maintainer. HW writes to
    # the 10000+ / 20000+ register space are persistent; incorrect
    # offsets could leave a slot in an odd state, recoverable only via
    # the machine's "Reset Recipes" menu.
    #
    # Param keys (must match RecipeFieldLayout attribute names):
    #   strength, profile, two_cups, temperature,
    #   coffee_temperature, water_temperature, milk_temperature,
    #   milk_foam_temperature, overall_temperature,
    #   coffee_amount, water_amount, milk_amount, milk_foam_amount,
    #   preparation, enabled, icon (MyCoffee only)

    async def write_standard_recipe_param(
        self, selector: int, param_key: str, value: int,
    ) -> bool:
        """Write a single byte of a standard recipe slot via HW.

        Returns False (no exception) when the active brand does not
        expose recipe layouts, the family is unknown, or the param_key
        is not supported by this family.
        """
        if not self.connected:
            return False
        layout_fn = getattr(self._brand, "standard_recipe_layout", None)
        if layout_fn is None:
            return False
        caps = getattr(self, "_capabilities", None)
        if caps is None:
            return False
        layout = layout_fn(caps.family_key)
        if layout is None:
            return False
        return await self._write_param_via_layout(
            layout, selector, param_key, value,
            base_register_fn=self._brand.standard_recipe_register,
        )

    async def write_mycoffee_param(
        self, slot: int, param_key: str, value: int,
    ) -> bool:
        """Write a single byte of a MyCoffee slot via HW."""
        if not self.connected:
            return False
        layout_fn = getattr(self._brand, "mycoffee_layout", None)
        register_fn = getattr(self._brand, "mycoffee_register", None)
        caps = getattr(self, "_capabilities", None)
        if layout_fn is None or register_fn is None or caps is None:
            return False
        if slot < 0 or slot >= caps.my_coffee_slots:
            return False
        layout = layout_fn(caps.family_key)
        if layout is None:
            return False
        return await self._write_param_via_layout(
            layout, slot, param_key, value, base_register_fn=register_fn,
        )

    async def _write_param_via_layout(
        self, layout, slot_or_selector: int, param_key: str, value: int,
        *, base_register_fn,
    ) -> bool:
        offset_attr = f"{param_key}_offset"
        offset = getattr(layout, offset_attr, None)
        if offset is None:
            return False
        # Fluid amounts on 900 family are written as ml×10 per upstream.
        if layout.fluid_write_scale_10 and param_key.endswith("_amount"):
            value = value * 10
        register = base_register_fn(slot_or_selector, offset)
        return await self._protocol.write_numerical(
            self._write_ble, register, value,
        )

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
