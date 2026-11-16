"""NivonaProfile — Brand profile for Nivona NICR / NIVO 8xxx machines.

All facts (constants, family tables, HU verifier algorithm) are
independently re-implemented in Python from observed protocol
behavior and external reference material; no third-party source is
copied verbatim.

Limitations:

- Initially untested on real hardware (released as alpha 2026-04-13);
  users with NICR/NIVO machines are invited to report results via
  GitHub issues.
- Recipe writes / DirectKey are NOT supported — Nivona machines do
  not expose recipe-edit affordances; ``supported_extensions`` is
  empty.
"""

from __future__ import annotations

import logging
import re
from typing import ClassVar

from dataclasses import replace

from ..base import MachineCapabilities, RecipeFieldLayout
from ...coffee_platform.domain import MachineStatus
from ._crypto import _NIVONA_HU_TABLE, _NIVONA_RC4_KEY
from ._prefixes import (
    _MODEL_OVERRIDES,
    _MODEL_SETTINGS_EXCLUDE,
    _PREFIX_TO_FAMILY,
)
# MY_COFFEE_* / TEMP_RECIPE_TYPE_REGISTER are re-exported from this
# package — sensor.py, _ble_commands.py, _ble_recipes.py, and the
# test suite import them through ``brands.nivona``.
from ._registers import (
    MY_COFFEE_BASE_REGISTER,  # noqa: F401  (re-export)
    MY_COFFEE_SLOT_STRIDE,    # noqa: F401  (re-export)
    TEMP_RECIPE_BASE_REGISTER,
    TEMP_RECIPE_TYPE_REGISTER,  # noqa: F401  (re-export)
    mycoffee_register,
    standard_recipe_register,
)
from . import (
    _settings,
    _storage,
    _recipe_fields,
    _status,
    _family_600,
    _family_700,
    _family_900,
    _family_1030,
    _family_8000,
    _family_9101,
)


_LOGGER = logging.getLogger("melitta_barista")


# ---------------------------------------------------------------------------
# Family dispatch — assembled by merging each family module's EXPORTS.
#
# Each EXPORTS dict is keyed by family-key (``"600"``, ``"79x"``,
# ``"900-light"``, …) and maps to ``{recipes, settings, stats,
# standard_layout, mycoffee_layout, capabilities}``. Splitting the
# merged result into per-aspect dispatch tables removes ~40 lines of
# mechanical per-family aliasing.
#
# Each family module owns its recipe, setting, quantity and process metadata.
# ---------------------------------------------------------------------------

_FAMILY_DATA: dict[str, dict] = {}
for _mod in (_family_600, _family_700, _family_900, _family_1030, _family_8000, _family_9101):
    _FAMILY_DATA.update(_mod.EXPORTS)
del _mod

_STANDARD_RECIPE_LAYOUTS: dict[str, RecipeFieldLayout] = {
    k: v["standard_layout"] for k, v in _FAMILY_DATA.items()
}
_MYCOFFEE_LAYOUTS: dict[str, RecipeFieldLayout] = {
    k: v["mycoffee_layout"] for k, v in _FAMILY_DATA.items()
}
_NIVONA_FAMILIES: dict[str, MachineCapabilities] = {
    k: replace(
        v["capabilities"], status_profile_key=_status.FAMILY_STATUS_PROFILES[k],
        brew_water_amount_max=240 if k in ("600", "900") else 500,
        recipe_amount_unit=None if k in ("600", "900") else "mL",
        settings=_settings.settings_for_family(k, v["capabilities"].settings),
        unsupported_setting_keys=(
            "off_rinse", "tank_light", "tank_light_color", "tank_light_brightness", "touch_lock",
        ) if k == "900" else v["capabilities"].unsupported_setting_keys,
    )
    for k, v in _FAMILY_DATA.items()
}


def standard_recipe_layout(family_key: str) -> RecipeFieldLayout | None:
    """Look up the standard-recipe layout for a family key. None if unknown."""
    return _STANDARD_RECIPE_LAYOUTS.get(family_key)


def mycoffee_layout(family_key: str) -> RecipeFieldLayout | None:
    """Look up the MyCoffee slot layout for a family key. None if unknown."""
    return _MYCOFFEE_LAYOUTS.get(family_key)

# ---------------------------------------------------------------------------
# NivonaProfile
# ---------------------------------------------------------------------------

def _temperature_override(family_key: str, recipe_id: int) -> str | None:
    """Resolve the primary process for the legacy raw temperature preset."""
    if family_key in ("900", "900-light", "1030", "1040"):
        if 0 <= recipe_id <= 5:
            return "coffee_temperature"
        # Integration layout aliases are opposite to the vendor family names.
        if family_key == "900":  # NICR 920/930
            return "coffee_temperature" if recipe_id == 7 else None
        if family_key == "900-light":  # NICR 960/965/970
            return {6: "milk_temperature", 7: "water_temperature"}.get(recipe_id)
        if recipe_id == 6:
            return "water_temperature"
        if family_key == "1040":
            return {7: "milk_temperature", 8: "milk_foam_temperature"}.get(recipe_id)
        return None
    if family_key == "8000" and recipe_id in _family_8000.CHILLED_SELECTORS:
        return None
    layout = _STANDARD_RECIPE_LAYOUTS.get(family_key)
    if layout and layout.temperature_offset is not None:
        return "temperature"
    return None


class NivonaProfile:
    """Brand profile for Nivona NICR/NIVO machines (alpha — untested live)."""

    brand_slug: ClassVar[str] = "nivona"
    brand_name: ClassVar[str] = "Nivona"
    service_uuid: ClassVar[str] = "0000ad00-b35c-11e4-9813-0002a5d5c51b"
    handshake_response_size: ClassVar[int] = 8

    # Nivona advertisement: a numeric serial (optionally prefixed with
    # "NIVONA-") followed by zero or more trailing dashes. We no longer
    # enumerate exact digit/dash counts — every new model series has
    # revealed a new combination:
    #   - NIVO 8107 / NICR 6xx-7xx: 10 digits + 5 dashes
    #   - NICR 930: 15 digits, no dashes
    #   - NICR 779 (#14): 15 digits + 5 dashes
    #   - NIVO 8001 (#15): 17 digits + 3 dashes
    # Brand discrimination from Melitta still works because Melitta's
    # regex is matched FIRST and requires one of 6 specific hex-digit
    # prefixes followed by hex characters — anything pure-numeric (with
    # optional dashes) that doesn't match Melitta falls through to
    # Nivona. Family/model identification happens separately via
    # `detect_family()` against `_PREFIX_TO_FAMILY`.
    ble_name_regex: ClassVar[re.Pattern[str]] = re.compile(
        r"^(?:NIVONA-)?\d{10,}-*$"
    )

    # Nivona machines do not expose recipe read/write commands.
    supported_extensions: ClassVar[frozenset[str]] = frozenset()

    families: ClassVar[dict[str, MachineCapabilities]] = _NIVONA_FAMILIES

    # Temp-recipe class-announcement register — written before HE brew
    # with per-brew overrides so the firmware knows which recipe class
    # the subsequent offsets belong to. Same register as the temp-recipe
    # base on Nivona (single fixed slot).
    temp_recipe_type_register: ClassVar[int | None] = TEMP_RECIPE_TYPE_REGISTER

    @property
    def runtime_rc4_key(self) -> bytes:
        return _NIVONA_RC4_KEY

    @property
    def hu_table(self) -> bytes:
        return _NIVONA_HU_TABLE

    def hu_verifier(self, buf: bytes, start: int, count: int) -> bytes:
        """2-round Nivona HU CRC fold.

        Same algorithm shape as Melitta but different table and offsets
        (+0x5D on first byte, +0xA7 on second).
        """
        table = _NIVONA_HU_TABLE

        s = table[buf[start] & 0xFF]
        for i in range(start + 1, start + count):
            s = table[(s ^ buf[i]) & 0xFF]
        out0 = (s + 0x5D) & 0xFF

        s = table[(buf[start] + 1) & 0xFF]
        for i in range(start + 1, start + count):
            s = table[(s ^ buf[i]) & 0xFF]
        out1 = (s + 0xA7) & 0xFF

        return bytes([out0, out1])

    def detect_family(
        self, ble_name: str, dis: dict[str, str] | None = None,
    ) -> str | None:
        """Identify Nivona family from serial-prefix tokenisation.

        Tries a 4-character serial prefix first (matches NIVO 8xxx
        serials), then a 3-character prefix (NICR series). If neither
        succeeds and DIS data is available, falls back to substring
        search on serial / model / ad06 fields.
        """
        # Strip the "NIVONA-" prefix if present (advertisement form),
        # or use the serial directly (DIS form).
        serial = ble_name
        if ble_name.startswith("NIVONA-"):
            serial = ble_name[len("NIVONA-"):]
        # The first token is the model code; the rest is factory ID.
        if len(serial) >= 4 and (key := _PREFIX_TO_FAMILY.get(serial[:4])):
            return key
        if len(serial) >= 3 and (key := _PREFIX_TO_FAMILY.get(serial[:3])):
            return key
        # DIS-based fallback
        if dis:
            haystack = " ".join(filter(None, [
                dis.get("serial"), dis.get("model"), dis.get("ad06_ascii"),
            ]))
            for prefix, family in _PREFIX_TO_FAMILY.items():
                if prefix in haystack:
                    return family
        return None

    def capabilities_for(self, family_key: str) -> MachineCapabilities:
        return _NIVONA_FAMILIES[family_key]

    def parse_status(
        self, family_key: str | None, data: bytes, *, status_profile_key: str | None = None,
    ) -> MachineStatus:
        """Decode HX using the model's incoming status profile."""
        return _status.parse_status(
            family_key, data, status_profile_key=status_profile_key,
        )

    def can_confirm_prompt(
        self, status: MachineStatus | None, caps: MachineCapabilities | None,
    ) -> bool:
        """Allow HY only for a recognized prompt from the resolved model."""
        return bool(
            caps is not None and status is not None
            and status.status_profile_key == caps.status_profile_key
            and status.prompt_confirmable is True
        )

    @staticmethod
    def can_read_setting(caps: MachineCapabilities | None, setting_id: int) -> bool:
        """Return whether a model's setting mapping is unambiguous."""
        return _settings.can_read_setting(caps, setting_id)

    @staticmethod
    def validate_setting(
        caps: MachineCapabilities | None, setting_id: int, value: object,
    ) -> int:
        """Validate a setting without interpreting an ordinal as a duration."""
        return _settings.validate_setting(caps, setting_id, value)

    # Recipe / MyCoffee write-path helpers (experimental — see mixin docs)
    @staticmethod
    def standard_recipe_layout(family_key: str):
        return standard_recipe_layout(family_key)

    @staticmethod
    def mycoffee_layout(family_key: str):
        return mycoffee_layout(family_key)

    @staticmethod
    def standard_recipe_register(selector: int, offset: int, *, family_key: str | None = None) -> int | None:
        """Resolve storage independently from the outgoing recipe selector."""
        if family_key == "9101":
            if offset not in _family_9101.STANDARD_OFFSETS.get(selector, ()):
                return None
            return _family_9101.STANDARD_BASES[selector] + offset
        if family_key is None:
            return standard_recipe_register(selector, offset)
        return _storage.standard_recipe_register(family_key, selector, offset)

    @staticmethod
    def temp_recipe_register(family_key: str, recipe_id: int, field: str) -> int | None:
        """Resolve the independent remote layout and legacy temperature alias."""
        if family_key == "9101":
            if recipe_id not in _family_9101.STANDARD_BASES:
                return None
            if field == "temperature":
                field = "coffee_temperature" if recipe_id in range(8) else "milk_temperature" if recipe_id == 8 else "unsupported"
            offset = getattr(_family_9101.REMOTE_LAYOUT, f"{field}_offset", None)
            return None if offset is None else 9000 + offset
        layout = _STANDARD_RECIPE_LAYOUTS.get(family_key)
        if layout is None:
            return None
        if field == "chilled_temperature":
            if family_key != "8000" or recipe_id not in _family_8000.CHILLED_SELECTORS:
                return None
            field = "temperature"
        elif field == "temperature":
            field = _temperature_override(family_key, recipe_id)
            if field is None:
                return None
        offset = getattr(layout, f"{field}_offset", None)
        return None if offset is None else TEMP_RECIPE_BASE_REGISTER + offset


    @staticmethod
    def fluid_write_scale(family_key: str) -> int:
        """Return 10 or 1 — fluid amounts are written scaled for some families."""
        layout = _STANDARD_RECIPE_LAYOUTS.get(family_key)
        if layout is None:
            return 1
        return 10 if getattr(layout, "fluid_write_scale_10", False) else 1

    def validate_recipe_param(
        self, caps: MachineCapabilities, context: str, selector: int,
        field: str, value: object,
    ) -> int:
        """Check explicit fields using their own temporary or persistent domain."""
        if caps.family_key == "9101":
            return _family_9101.validate_field(context, selector, field, value)
        if context == "temporary":
            register = self.temp_recipe_register(caps.family_key, selector, field)
        else:
            layout = (
                self.standard_recipe_layout(caps.family_key)
                if context == "standard" else self.mycoffee_layout(caps.family_key)
            )
            offset = getattr(layout, f"{field}_offset", None)
            register = None if offset is None else (
                self.standard_recipe_register(selector, offset, family_key=caps.family_key)
                if context == "standard" else self.mycoffee_register(selector, offset)
            )
            if context == "standard" and not any(
                recipe.recipe_id == selector for recipe in caps.recipes
            ):
                register = None
        return _recipe_fields.validate_recipe_param(
            caps, context, selector, field, value, register=register,
        )

    @staticmethod
    def mycoffee_register(slot: int, offset: int) -> int:
        return mycoffee_register(slot, offset)

    def capabilities_for_model(
        self, ble_name: str, dis: dict[str, str] | None = None,
    ) -> MachineCapabilities | None:
        """Return model-refined capabilities for a specific serial/advert.

        Looks up the family first, then applies model-specific overrides
        (from MODEL_RULES upstream) — primarily ``my_coffee_slots`` and
        ``strength_levels``, which vary per model within a family. The 8107
        also declares its chilled catalog and firmware preparation limits.
        Returns None if the family cannot be resolved.
        """
        family = self.detect_family(ble_name, dis)
        if family is None:
            return None
        caps = _NIVONA_FAMILIES[family]
        # Look up model override by the same prefix cascade used in detect_family
        serial = ble_name[len("NIVONA-"):] if ble_name.startswith("NIVONA-") else ble_name
        model_prefix: str | None = None
        override: dict | None = None
        if len(serial) >= 4:
            model_prefix = serial[:4]
            override = _MODEL_OVERRIDES.get(model_prefix)
        if override is None and len(serial) >= 3:
            model_prefix = serial[:3]
            override = _MODEL_OVERRIDES.get(model_prefix)

        # Per-model settings filter — e.g. NICR758 lacks id 106 (profile).
        # Apply by building a new tuple without the excluded ids.
        excluded = _MODEL_SETTINGS_EXCLUDE.get(model_prefix or "", frozenset())
        if excluded:
            filtered = tuple(
                s for s in caps.settings if s.setting_id not in excluded
            )
            caps = replace(caps, settings=filtered)

        # NICR 8107 — the only 8000-family model that exposes
        # chilled-brew recipe selectors (8/9/10). Swap in the extended
        # recipe table so NivonaRecipeSelect lists them.
        if model_prefix == "8107":
            caps = replace(
                caps, recipes=_family_8000.RECIPES_8000_CHILLED,
                saved_recipe_only_selectors=(8, 9, 10),
                saved_recipe_only_firmwares=(
                    "1040A015G15", "1040A016G16", "1040A017G17",
                ),
                required_brew_overrides=tuple(
                    (selector, "chilled_temperature", 99) for selector in (8, 9, 10)
                ),
            )

        if override is None:
            return caps
        return replace(caps, **override)

    @staticmethod
    def is_chilled_selector(selector: int) -> bool:
        """Classify selector numbers from the NIVO 8107 chilled catalog.

        This number-only helper does not select the HE recipe mode. The
        machine's model capabilities and firmware determine that mode.
        """
        return selector in _family_8000.CHILLED_SELECTORS
