"""Brew, maintenance, and cancel commands for the BLE client (mixin)."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from bleak.exc import BleakError

from .brands.base import FeatureNotSupported
from .coffee_platform.domain import is_maintenance_process_verified

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

# Some machines begin brewing several seconds after returning NACK to HE.
NIVONA_START_CONFIRM_TIMEOUT = 15.0


def _directkey_name_key(category: DirectKeyCategory) -> str | None:
    """Authored ``recipes.name.*`` key for a DirectKey category, or None.

    The table lives in ``narration`` so there is exactly one owner of it;
    it is not derivable from the category token, because DirectKey MILK is
    the drink authored as ``warm_milk`` and WATER is ``hot_water`` (and
    ``recipes.name.milk`` is a different drink, so guessing would name the
    wrong one). Import failures degrade to "key unknown" rather than
    surfacing from a brew that has already started.
    """
    try:
        from .narration import DIRECTKEY_NAME_KEYS
    except ImportError:  # narration is not needed to brew
        return None
    return DIRECTKEY_NAME_KEYS.get(category.name.lower())


def _nivona_descriptor(caps: Any, recipe_selector: int) -> Any | None:
    """The capability descriptor for a Nivona selector, or None.

    Tolerant by design: the lookup only decorates a brew record, so a brand
    profile without a recipe catalogue must cost the drink's name, not the
    drink.
    """
    try:
        return next(
            (r for r in caps.recipes if r.recipe_id == recipe_selector), None,
        )
    except Exception:  # noqa: BLE001 - no catalogue, no name
        return None


class BleCommandsMixin(_MixinBase):
    """Mixin providing brew and maintenance commands."""

    def _is_brew_ready(self) -> bool:
        """True if the machine is ready, honoring brand-tolerated flags."""
        if not self._status:
            return True
        caps = getattr(self, "_capabilities", None)
        tolerated = caps.tolerated_brew_manipulations if caps else ()
        return self._status.is_ready_for_brew(tolerated)

    async def _prepare_recipe_write(self) -> bool:
        """Check a model's current state and await its optional write prerequisite."""
        caps = self._capabilities
        if caps is None or caps.recipe_write_precondition is None:
            return True
        status = await self._protocol.read_status(self._write_ble)
        if (
            status is None or status.status_profile_key != caps.status_profile_key
            or not status.is_ready_for_brew()
        ):
            return False
        return await self._protocol.prepare_write(
            self._write_ble, caps.recipe_write_precondition,
        )

    async def _seed_temporary_recipe(self, caps, selector: int, overrides: dict) -> dict | None:
        """Map saved fields into a complete independent temporary template."""
        fields = dict(caps.temporary_recipe_defaults)
        layout = self._brand.standard_recipe_layout(caps.family_key)
        for field in fields:
            offset = getattr(layout, f"{field}_offset", None)
            if offset is None:
                continue
            register = self._brand.standard_recipe_register(
                selector, offset, family_key=caps.family_key,
            )
            if register is None:
                continue
            value = await self._protocol.read_numerical(self._write_ble, register)
            if value is None:
                return None
            fields[field] = self._brand.validate_recipe_param(
                caps, "temporary", selector, field, value,
            )
        # Normalize the legacy alias before merging so an earlier explicit
        # field is never overwritten later by a seeded template field.
        if "temperature" in overrides:
            register = self._brand.temp_recipe_register(caps.family_key, selector, "temperature")
            target = next(field for field in fields if self._brand.temp_recipe_register(
                caps.family_key, selector, field,
            ) == register)
            overrides = dict(overrides)
            overrides[target] = overrides.pop("temperature")
        fields.update(overrides)
        return fields

    def _record_brew_intent(
        self,
        *,
        components: tuple[RecipeComponent | None, ...] | None = None,
        **facts: Any,
    ) -> None:
        """Stage known brew facts for lifecycle events without failing a start.

        Nivona stages immediately before HE to handle PRODUCT preceding the ACK;
        its caller removes the record if neither ACK nor PRODUCT confirms it.
        Other paths stage after acknowledgment. Bookkeeping is best-effort and
        must never surface as a brew failure. ``components`` takes raw protocol components and is tokenised
        here, because the staged record must stay JSON-serialisable and must
        never carry the raw portion byte (units of 5 ml).
        """
        try:
            from .lifecycle import BrewIntent, now_monotonic
            from .ui_contract import component_to_tokens

            tokens = None
            if components:
                tokens = [
                    component_to_tokens(component)
                    for component in components
                    if component is not None
                ]
            self._note_brew_intent(
                BrewIntent(noted_at=now_monotonic(), components=tokens, **facts),
            )
        except Exception:  # noqa: BLE001 - bookkeeping must never fail a brew
            _LOGGER.debug("Could not record the brew intent", exc_info=True)

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
        if not self._is_brew_ready():
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

                ok = await self._protocol.start_process(
                    self._write_ble, MachineProcess.PRODUCT,
                    two_cups=two_cups,
                )
                if ok:
                    from .ui_contract import MELITTA_RECIPE_NAME_KEYS

                    self._record_brew_intent(
                        recipe_source="base",
                        # RECIPE_NAMES.get, not the `name` written to the
                        # machine: that one falls back to str(recipe_id), and
                        # a bare number is not a drink name.
                        recipe_key=MELITTA_RECIPE_NAME_KEYS.get(int(recipe_id)),
                        recipe_name=RECIPE_NAMES.get(recipe_id),
                        two_cups=two_cups,
                        components=(recipe.component1, recipe.component2),
                    )
                return ok
            finally:
                if self.connected:
                    self.start_polling(interval=self._poll_interval)

    async def _start_nivona_brew(
        self, selector: int, brew_mode: int, *,
        use_temp_recipe: bool, chilled: bool, **intent_facts: Any,
    ) -> bool:
        """Send one start and resolve an uncertain ACK using fresh PRODUCT status.

        Subscribe and stage the intent before HE so even a pushed PRODUCT that
        precedes the ACK is attributed to HA. A NACK or missing ACK is only
        overridden by PRODUCT on the same connection, never by cached status.
        """
        connection = self._client
        observed_product = False
        accepted = False

        def observe(status) -> None:
            nonlocal observed_product
            if (
                self.connected and self._client is connection
                and status.process == MachineProcess.PRODUCT
            ):
                observed_product = True

        self.add_status_callback(observe)
        self._record_brew_intent(**intent_facts)
        pending_intent = self.brew_intent
        try:
            accepted = await self._protocol.start_process_nivona(
                self._write_ble, selector, brew_mode,
                use_temp_recipe=use_temp_recipe, chilled=chilled,
            )
            if accepted:
                return True
            try:
                async with asyncio.timeout(NIVONA_START_CONFIRM_TIMEOUT):
                    while (
                        not observed_product and self.connected
                        and self._client is connection
                    ):
                        await self.poll_status()
                        if not observed_product:
                            await asyncio.sleep(0.5)
            except (TimeoutError, BleakError, OSError):
                _LOGGER.debug("Could not confirm Nivona start via HX", exc_info=True)
            accepted = bool(
                observed_product and self.connected and self._client is connection
            )
            if accepted:
                _LOGGER.info("Nivona start confirmed by PRODUCT after unacknowledged HE")
            return accepted
        finally:
            self.remove_status_callback(observe)
            if not accepted and self.brew_intent is pending_intent:
                self.take_brew_intent()

    async def brew_nivona(
        self, recipe_selector: int,
        overrides: dict | None = None,
    ) -> bool:
        """Brew on Nivona by recipe selector (no HC/HJ — uses HE directly).

        ``overrides`` is an optional dict of {field: value} where ``field``
        is a RecipeFieldLayout field name (e.g. ``"strength"``,
        ``"coffee_amount"``, ``"temperature"``, ``"two_cups"``). When set,
        each override is written via HW to the per-family temporary-recipe
        register before the HE start_process is issued. The legacy temperature
        preset retains raw levels 0..2 and targets the selected recipe's
        primary process; unsupported temperatures raise ValueError before
        any command is sent. All explicit inputs are preflighted before the
        first HW. Quantities use mL; choices use raw zero-based values.

        Nivona machines don't have recipe read/write (HC/HJ) — they use
        HE with byte[3]=selector instead.
        """
        if self._brew_lock.locked():
            _LOGGER.warning("Brew already in progress, ignoring")
            return False
        if not self.connected:
            return False
        if not self._is_brew_ready():
            _LOGGER.warning("Machine not ready: %s", self._status)
            return False

        caps = getattr(self, "_capabilities", None)
        brew_mode = caps.brew_command_mode if caps else 0x0B
        family_key = caps.family_key if caps else None

        if not isinstance(recipe_selector, int) or not 0 <= recipe_selector <= 255:
            raise ValueError("recipe selector must be an integer between 0 and 255")
        if caps and caps.temporary_recipe_defaults and not any(
            recipe.recipe_id == recipe_selector for recipe in caps.recipes
        ):
            raise ValueError("Recipe selector is unsupported for this machine")

        if overrides and family_key:
            temperature = overrides.get("temperature")
            if temperature is not None:
                register = self._brand.temp_recipe_register(
                    family_key, recipe_selector, "temperature",
                )
                maximum = dict(caps.brew_temperature_max).get(recipe_selector, 2)
                if register is None:
                    raise ValueError(
                        f"temperature is unsupported for recipe {recipe_selector} on {family_key}"
                    )
                if (
                    not isinstance(temperature, (int, float))
                    or not 0 <= temperature <= maximum
                    or temperature != int(temperature)
                ):
                    raise ValueError(
                        f"temperature must be a raw preset from 0 to {maximum} "
                        f"for recipe {recipe_selector} on {family_key}"
                    )

        saved_recipe_fallback = bool(
            caps and recipe_selector in caps.saved_recipe_only_selectors
            and self._firmware in caps.saved_recipe_only_firmwares
            and any(r.recipe_id == recipe_selector for r in caps.recipes)
        )
        if overrides and saved_recipe_fallback:
            raise ValueError(
                "Temporary recipe overrides are unsupported for this recipe "
                f"on firmware {self._firmware}; reset overrides to use the saved recipe"
            )
        if overrides:
            if caps is None:
                raise ValueError("Recipe overrides require resolved machine capabilities")
            overrides = {
                field: self._brand.validate_recipe_param(
                    caps, "temporary", recipe_selector, field, value,
                )
                for field, value in overrides.items() if value is not None
            }
        if overrides and caps and not saved_recipe_fallback:
            overrides = dict(overrides)
            for selector, field, value in caps.required_brew_overrides:
                if selector == recipe_selector:
                    overrides.pop(field, None)
                    overrides[field] = value

        async with self._brew_lock:
            self._stop_polling()
            try:
                if not await self._prepare_recipe_write():
                    return False
                if overrides and caps.temporary_recipe_defaults:
                    overrides = await self._seed_temporary_recipe(caps, recipe_selector, overrides)
                    if overrides is None:
                        return False
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
                type_register = (
                    caps.temporary_recipe_type_register
                    if caps and caps.temporary_recipe_type_register is not None
                    else self._brand.temp_recipe_type_register
                )
                if overrides and family_key and type_register is not None:
                    scale = self._brand.fluid_write_scale(family_key)

                    # Step 1 — announce the recipe type with the
                    # selector as the value. write_numerical packs
                    # int32 BE; the machine reads the low byte of the
                    # pair so passing the selector suffices.
                    type_ok = await self._protocol.write_numerical(
                        self._write_ble,
                        type_register,
                        int(recipe_selector),
                    )
                    if not type_ok:
                        _LOGGER.warning(
                            "Temp-recipe type announce at reg %d failed",
                            type_register,
                        )
                        return False
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
                            return False
                        await asyncio.sleep(0.08)

                wrote_temp = bool(
                    overrides and family_key and type_register is not None
                    and any(
                        value is not None and self._brand.temp_recipe_register(
                            family_key, recipe_selector, field,
                        ) is not None
                        for field, value in overrides.items()
                    )
                )
                descriptor = _nivona_descriptor(caps, recipe_selector)
                return await self._start_nivona_brew(
                    (
                        caps.temporary_recipe_selector
                        if wrote_temp and caps.temporary_recipe_selector is not None
                        else recipe_selector
                    ), brew_mode,
                    use_temp_recipe=wrote_temp,
                    chilled=saved_recipe_fallback,
                    recipe_source="nivona",
                    recipe_key=(descriptor.name_key or None) if descriptor else None,
                    recipe_name=(descriptor.name or None) if descriptor else None,
                    two_cups=bool((overrides or {}).get("two_cups")),
                )
            finally:
                if self.connected:
                    self.start_polling(interval=self._poll_interval)

    async def brew_mycoffee_slot(self, slot: int) -> bool:
        """Brew the MyCoffee recipe saved in slot ``slot``.

        Wire path: HE with ``payload[3] = first_mycoffee_selector + slot``
        and ``payload[5] = 0`` (use saved recipe defaults — the slot's
        recipe is already persisted on the machine). No temp-recipe
        write involved; this is the simplest brew variant.

        Returns ``False`` if not connected, machine not ready, no
        capabilities resolved, or ``slot`` out of range.
        """
        if self._brew_lock.locked():
            _LOGGER.warning("Brew already in progress, ignoring")
            return False
        if not self.connected:
            return False
        caps = getattr(self, "_capabilities", None)
        if caps is None:
            return False
        if slot < 0 or slot >= caps.my_coffee_slots:
            return False
        if not self._is_brew_ready():
            _LOGGER.warning("Machine not ready: %s", self._status)
            return False

        brew_mode = caps.brew_command_mode
        selector = caps.first_mycoffee_selector + slot

        async with self._brew_lock:
            self._stop_polling()
            try:
                if not await self._prepare_recipe_write():
                    return False
                return await self._start_nivona_brew(
                    selector, brew_mode, use_temp_recipe=False, chilled=False,
                    recipe_source="mycoffee", slot=slot,
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
        if not self._is_brew_ready():
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

                ok = await self._protocol.start_process(
                    self._write_ble, MachineProcess.PRODUCT,
                    two_cups=two_cups,
                )
                if ok:
                    self._record_brew_intent(
                        recipe_source="directkey",
                        recipe_key=_directkey_name_key(category),
                        recipe_name=name,
                        profile=self.active_profile,
                        profile_name=self._profile_names.get(self.active_profile),
                        two_cups=two_cups,
                        components=(recipe.component1, recipe.component2),
                    )
                return ok
            finally:
                if self.connected:
                    self.start_polling(interval=self._poll_interval)

    async def brew_freestyle(
        self,
        name: str,
        recipe_type: int,
        component1: RecipeComponent,
        component2: RecipeComponent,
        *,
        two_cups: bool = False,
    ) -> bool:
        """Brew a freestyle (custom) recipe.

        Writes the provided components to the temp recipe slot and starts brewing.
        recipe_type is typically 24 (freestyle).

        ``two_cups`` sets the HE double-brew flag (passed through to
        ``start_process``). The ``brew_freestyle`` service exposes it; the
        button entity brews single by default. Kept keyword-only so the
        positional ``(name, recipe_type, comp1, comp2)`` call sites stay
        valid — a previous mismatch made the service raise ``TypeError``.
        """
        if self._brew_lock.locked():
            _LOGGER.warning("Brew already in progress, ignoring")
            return False

        if not self.connected:
            return False
        if not self._is_brew_ready():
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

                ok = await self._protocol.start_process(
                    self._write_ble, MachineProcess.PRODUCT, two_cups=two_cups,
                )
                if ok:
                    # No recipe_key: a freestyle drink is composed on the fly
                    # and has no authored name key.
                    self._record_brew_intent(
                        recipe_source="freestyle",
                        recipe_name=name,
                        two_cups=two_cups,
                        components=(component1, component2),
                    )
                return ok
            finally:
                if self.connected:
                    self.start_polling(interval=self._poll_interval)

    async def cancel_process(self, process: MachineProcess = MachineProcess.PRODUCT) -> bool:
        """Ask the machine to stop ``process``; record that HA cancelled the brew.

        The machine reports a cancel the same way whoever pressed the button
        did, so a brew that HA stopped is only distinguishable from one the
        user stopped at the machine by what is recorded here. Only a PRODUCT
        cancel is recorded — cancelling a maintenance procedure says nothing
        about the drink.

        Two records, because a cancel can land on either side of the PRODUCT
        edge: `annotate_brew_intent` covers the narrow window in which the
        brew is ACKed but the machine has not reported PRODUCT yet, and
        `_note_ha_cancel` covers the normal case, where the detector already
        holds its captured copy of the record and the stage is empty.
        """
        if not self.connected:
            return False
        ok = await self._protocol.cancel_process(self._write_ble, process)
        if ok and process == MachineProcess.PRODUCT:
            self.annotate_brew_intent(ha_cancelled=True)
            self._note_ha_cancel()
        return ok

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

    async def execute_he_command(self, command_id: int) -> bool:
        """Run an HE-with-commandId Nivona admin action.

        Currently used for the factory-reset buttons
        (`HE_CMD_FACTORY_RESET_SETTINGS` = 50, `HE_CMD_FACTORY_RESET_RECIPES`
        = 51). Returns ``True`` on machine ACK, ``False`` on NACK /
        timeout / disconnected.
        """
        if not self.connected:
            return False
        try:
            return await self._protocol.execute_command(
                self._write_ble, command_id,
            )
        except (BleakError, OSError, asyncio.TimeoutError):
            _LOGGER.exception(
                "BLE error executing HE commandId %d", command_id,
            )
            return False

    def _can_confirm_prompt(self) -> bool:
        status = self._status if self._status_connection is self._client else None
        return self.connected and self._brand.can_confirm_prompt(status, self._capabilities)

    async def confirm_prompt(self, *, auto: bool = False) -> bool:
        """Send HY to confirm the current machine prompt (move cup, flush, ...).

        The real machine treats HY as fire-and-forget — it always ACKs
        and the caller is expected to poll HX for the post-confirm
        state change rather than treat the ACK as "prompt cleared".
        The returned bool only reflects the ACK/NACK of the write
        itself; the caller should not interpret False as "the prompt
        is still showing". A subsequent status poll is authoritative.
        Eligibility is rechecked under the protocol lock on each attempt;
        automatic calls also honor changes to the auto-confirm option.
        """
        if not self._can_confirm_prompt():
            return False
        generation = self._prompt_generation
        auto_generation = self._auto_confirm_generation
        connection = self._client

        def eligible() -> bool:
            return (
                self.connected and self._client is connection
                and self._prompt_generation == generation
                and self._can_confirm_prompt()
                and (not auto or self._auto_confirm_prompts)
                and (not auto or self._auto_confirm_generation == auto_generation)
            )

        async def write_current_prompt(data: bytes) -> None:
            await self._write_ble(data, send_guard=eligible)

        return await self._protocol.confirm_prompt(write_current_prompt, send_guard=eligible)

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
        """Write one numeric standard-recipe parameter via HW.

        Quantities are mL and choices retain their raw numeric domain.
        Invalid or unsupported explicit parameters raise ValueError before
        transmission. Returns False when the brand has no recipe layout,
        capabilities are unresolved, or the client is disconnected.
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
        value = self._brand.validate_recipe_param(caps, "standard", selector, param_key, value)
        return await self._write_param_via_layout(
            layout, selector, param_key, value,
            base_register_fn=lambda selector, offset: self._brand.standard_recipe_register(
                selector, offset, family_key=caps.family_key,
            ),
        )

    async def write_mycoffee_param(
        self, slot: int, param_key: str, value: int,
    ) -> bool:
        """Write a numeric MyCoffee parameter, validating its own slot domain.

        Quantities use mL and choices retain raw values. Invalid or unsupported
        fields raise ValueError before HW; disconnected/out-of-range slots
        and brands without a layout return False.
        """
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
        value = self._brand.validate_recipe_param(caps, "mycoffee", slot, param_key, value)
        return await self._write_param_via_layout(
            layout, slot, param_key, value, base_register_fn=register_fn,
        )

    async def _write_param_via_layout(
        self, layout, slot_or_selector: int, param_key: str, value: int,
        *, base_register_fn,
    ) -> bool:
        """Write a validated layout field under any model-specific preparation lock."""
        offset_attr = f"{param_key}_offset"
        offset = getattr(layout, offset_attr, None)
        if offset is None:
            return False
        # Quantity conversion follows the selected layout, never a model label.
        if layout.fluid_write_scale_10 and param_key.endswith("_amount"):
            value = value * 10
        register = base_register_fn(slot_or_selector, offset)
        if register is None:
            return False
        if self._capabilities.recipe_write_precondition is not None:
            async with self._brew_lock:
                if not await self._prepare_recipe_write():
                    return False
                return await self._protocol.write_numerical(self._write_ble, register, value)
        return await self._protocol.write_numerical(self._write_ble, register, value)

    # Maintenance operations

    async def _start_verified_maintenance(self, process: MachineProcess) -> bool:
        """Apply the family's verification gate before emitting any HE command."""
        if not self.connected:
            return False
        caps = self._capabilities or self._resolve_capabilities() or self._fallback_capabilities()
        if not is_maintenance_process_verified(caps, process):
            _LOGGER.warning(
                "Maintenance %s is not verified for %s; command not sent",
                process.name, caps.family_key if caps else "unknown family",
            )
            return False
        return await self._protocol.start_process(self._write_ble, process)

    async def start_easy_clean(self) -> bool:
        """Request start easy clean only for a verified family."""
        return await self._start_verified_maintenance(MachineProcess.EASY_CLEAN)

    async def start_intensive_clean(self) -> bool:
        """Request start intensive clean only for a verified family."""
        return await self._start_verified_maintenance(MachineProcess.INTENSIVE_CLEAN)

    async def start_descaling(self) -> bool:
        """Request start descaling only for a verified family."""
        return await self._start_verified_maintenance(MachineProcess.DESCALING)

    async def start_filter_insert(self) -> bool:
        """Request start filter insert only for a verified family."""
        return await self._start_verified_maintenance(MachineProcess.FILTER_INSERT)

    async def start_filter_replace(self) -> bool:
        """Request start filter replace only for a verified family."""
        return await self._start_verified_maintenance(MachineProcess.FILTER_REPLACE)

    async def start_filter_remove(self) -> bool:
        """Request start filter remove only for a verified family."""
        return await self._start_verified_maintenance(MachineProcess.FILTER_REMOVE)

    async def start_evaporating(self) -> bool:
        """Request start evaporating only for a verified family."""
        return await self._start_verified_maintenance(MachineProcess.EVAPORATING)

    async def switch_off(self) -> bool:
        """Request switch off only for a verified family."""
        return await self._start_verified_maintenance(MachineProcess.SWITCH_OFF)
