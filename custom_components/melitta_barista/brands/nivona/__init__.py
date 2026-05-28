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

from ..base import (
    MachineCapabilities,
    RecipeDescriptor,
    RecipeFieldLayout,
    SettingDescriptor,
    StatDescriptor,
)
from ._crypto import _NIVONA_HU_TABLE, _NIVONA_RC4_KEY
from ._options import (
    _AUTO_OFF_8000_OPTIONS,
    _AUTO_OFF_STANDARD_OPTIONS,
    _HARDNESS_OPTIONS,
    _MILK_FOAM_TEMPERATURE_1040_OPTIONS,
    _MILK_TEMPERATURE_1030_OPTIONS,
    _MILK_TEMPERATURE_1040_OPTIONS,
    _OFF_ON_OPTIONS,
    _POWER_ON_FROTHER_TIME_1040_OPTIONS,
    _PROFILE_1040_OPTIONS,
    _PROFILE_STANDARD_OPTIONS,
    _TANK_LIGHT_BRIGHTNESS_900_OPTIONS,
    _TANK_LIGHT_COLOR_900_OPTIONS,
    _TEMP_ON_OFF,
    _TEMPERATURE_OPTIONS,
)
from ._prefixes import (
    _MODEL_OVERRIDES,
    _MODEL_SETTINGS_EXCLUDE,
    _PREFIX_TO_FAMILY,
)
from ._registers import (
    MY_COFFEE_BASE_REGISTER,
    MY_COFFEE_SLOT_STRIDE,
    RECIPE_BASE_REGISTER,
    RECIPE_SLOT_STRIDE,
    TEMP_RECIPE_BASE_REGISTER,
    TEMP_RECIPE_TYPE_REGISTER,
    mycoffee_register,
    standard_recipe_register,
)
from ._stats_helpers import _count, _flag, _pct
from . import (
    _family_600,
    _family_700,
    _family_900,
    _family_1030,
    _family_8000,
)


# ---------------------------------------------------------------------------
# Per-family recipe tables — selector byte → (key, display name).
# Per-family standard-recipe lists. Each entry is (selector, key, title).
# ---------------------------------------------------------------------------

_RECIPES_600: tuple[RecipeDescriptor, ...] = _family_600.RECIPES

_RECIPES_700: tuple[RecipeDescriptor, ...] = _family_700.RECIPES_700
_RECIPES_79X: tuple[RecipeDescriptor, ...] = _family_700.RECIPES_79X
_RECIPES_900: tuple[RecipeDescriptor, ...] = _family_900.RECIPES_900
_RECIPES_900_LIGHT: tuple[RecipeDescriptor, ...] = _family_900.RECIPES_900_LIGHT
_RECIPES_1030: tuple[RecipeDescriptor, ...] = _family_1030.RECIPES_1030
_RECIPES_1040: tuple[RecipeDescriptor, ...] = _family_1030.RECIPES_1040
_RECIPES_8000: tuple[RecipeDescriptor, ...] = _family_8000.RECIPES_8000
_RECIPES_8000_CHILLED: tuple[RecipeDescriptor, ...] = _family_8000.RECIPES_8000_CHILLED
_CHILLED_SELECTORS: frozenset[int] = _family_8000.CHILLED_SELECTORS

_LOGGER = logging.getLogger("melitta_barista")


# ---------------------------------------------------------------------------
# Family capabilities — 7 Nivona families covered by the upstream RE.
#
# Notes on per-family flags:
#   - 8000 (NICR 8101/8103/8107) uses brew_command_mode 0x04, all others 0x0B.
#   - 900 (NICR 920/930) writes fluid amounts as ml × 10.
#   - 79x has hasAromaBalance=True; others False.
#   - 600 has only 1 MyCoffee slot; 700/79x/900/8000 have 4.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Per-family settings register tables (HR-readable, HW-writable).
# Per-family settings probe sets.
# IDs are Nivona-specific and do NOT overlap with Melitta's setting IDs.
# ---------------------------------------------------------------------------

_SETTINGS_8000: tuple[SettingDescriptor, ...] = _family_8000.SETTINGS_8000
_SETTINGS_600: tuple[SettingDescriptor, ...] = _family_600.SETTINGS
_SETTINGS_700: tuple[SettingDescriptor, ...] = _family_700.SETTINGS_700
_SETTINGS_79X: tuple[SettingDescriptor, ...] = _family_700.SETTINGS_79X
# Legacy alias preserved for older tests / docstrings.
_SETTINGS_600_700_BASE: tuple[SettingDescriptor, ...] = _SETTINGS_600
_SETTINGS_900: tuple[SettingDescriptor, ...] = _family_900.SETTINGS_900
_SETTINGS_900_LIGHT: tuple[SettingDescriptor, ...] = _family_900.SETTINGS_900_LIGHT
_SETTINGS_1030: tuple[SettingDescriptor, ...] = _family_1030.SETTINGS_1030
_SETTINGS_1040: tuple[SettingDescriptor, ...] = _family_1030.SETTINGS_1040


# ---------------------------------------------------------------------------
# Per-family stats register tables (HR-readable counters / percentages).
# Each family exposes a different set of HR IDs — stat IDs overlap across
# families but describe different counters (e.g. id 213 is
# "total beverages" on 8000/900 but "single-cup brews" on 1000-family).
# Maintenance gauges 600/601/610/611/620/621/640/641 are universal; only
# the "filter dependency" id varies (642 on 8000, 101 on 900/1000,
# 105 on 700/79X/600).
# ---------------------------------------------------------------------------

_STATS_8000: tuple[StatDescriptor, ...] = _family_8000.STATS_8000
_STATS_700: tuple[StatDescriptor, ...] = _family_700.STATS_700
_STATS_79X: tuple[StatDescriptor, ...] = _family_700.STATS_79X
_STATS_600: tuple[StatDescriptor, ...] = _family_600.STATS
_STATS_900: tuple[StatDescriptor, ...] = _family_900.STATS_900
_STATS_900_LIGHT: tuple[StatDescriptor, ...] = _family_900.STATS_900_LIGHT
_STATS_1030: tuple[StatDescriptor, ...] = _family_1030.STATS_1030
_STATS_1040: tuple[StatDescriptor, ...] = _family_1030.STATS_1040


# ---------------------------------------------------------------------------
# Per-family standard-recipe layouts (resolveStandardRecipeLayout upstream).
# Maps family_key → RecipeFieldLayout with byte-offsets inside
# `RECIPE_BASE_REGISTER + selector*RECIPE_SLOT_STRIDE`.
# ---------------------------------------------------------------------------

_STANDARD_RECIPE_LAYOUTS: dict[str, RecipeFieldLayout] = {
    "600": _family_600.STANDARD_LAYOUT,
    "700": _family_700.STANDARD_LAYOUT_700,
    "79x": _family_700.STANDARD_LAYOUT_79X,
    "900": _family_900.STANDARD_LAYOUT_900,
    "900-light": _family_900.STANDARD_LAYOUT_900_LIGHT,
    "1030": _family_1030.STANDARD_LAYOUT_1030,
    "1040": _family_1030.STANDARD_LAYOUT_1040,
    "8000": _family_8000.STANDARD_LAYOUT_8000,
}


# ---------------------------------------------------------------------------
# Per-family MyCoffee slot layouts (resolveMyCoffeeLayout upstream).
# Offsets inside `MY_COFFEE_BASE_REGISTER + slot*MY_COFFEE_SLOT_STRIDE`.
# ---------------------------------------------------------------------------

_MYCOFFEE_LAYOUTS: dict[str, RecipeFieldLayout] = {
    "600": _family_600.MYCOFFEE_LAYOUT,
    "700": _family_700.MYCOFFEE_LAYOUT_700,
    "79x": _family_700.MYCOFFEE_LAYOUT_79X,
    "900": _family_900.MYCOFFEE_LAYOUT_900,
    "900-light": _family_900.MYCOFFEE_LAYOUT_900_LIGHT,
    "1030": _family_1030.MYCOFFEE_LAYOUT_1030,
    "1040": _family_1030.MYCOFFEE_LAYOUT_1040,
    "8000": _family_8000.MYCOFFEE_LAYOUT_8000,
}


def standard_recipe_layout(family_key: str) -> RecipeFieldLayout | None:
    """Look up the standard-recipe layout for a family key. None if unknown."""
    return _STANDARD_RECIPE_LAYOUTS.get(family_key)


def mycoffee_layout(family_key: str) -> RecipeFieldLayout | None:
    """Look up the MyCoffee slot layout for a family key. None if unknown."""
    return _MYCOFFEE_LAYOUTS.get(family_key)


# ---------------------------------------------------------------------------
# Per-family settings + stats dispatch
# ---------------------------------------------------------------------------

_FAMILY_SETTINGS: dict[str, tuple[SettingDescriptor, ...]] = {
    "600": _SETTINGS_600,
    "700": _SETTINGS_700,
    "79x": _SETTINGS_79X,
    "900": _SETTINGS_900,
    "900-light": _SETTINGS_900_LIGHT,
    "1030": _SETTINGS_1030,
    "1040": _SETTINGS_1040,
    "8000": _SETTINGS_8000,
}

_FAMILY_STATS: dict[str, tuple[StatDescriptor, ...]] = {
    "600": _STATS_600,
    "700": _STATS_700,
    "79x": _STATS_79X,
    "900": _STATS_900,
    "900-light": _STATS_900_LIGHT,
    "1030": _STATS_1030,
    "1040": _STATS_1040,
    "8000": _STATS_8000,
}


_NIVONA_FAMILIES: dict[str, MachineCapabilities] = {
    "600": _family_600.CAPABILITIES,
    "700": _family_700.CAPABILITIES_700,
    "79x": _family_700.CAPABILITIES_79X,
    "900": _family_900.CAPABILITIES_900,
    "900-light": _family_900.CAPABILITIES_900_LIGHT,
    "1030": _family_1030.CAPABILITIES_1030,
    "1040": _family_1030.CAPABILITIES_1040,
    "8000": _family_8000.CAPABILITIES_8000,
}

# ---------------------------------------------------------------------------
# NivonaProfile
# ---------------------------------------------------------------------------

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

    def parse_status(self, family_key, data):
        """Map Nivona HX payload to an abstract MachineStatus.

        HX is 8 bytes of four big-endian int16 fields:
            process, sub_process, message, progress.

        Family-specific process codes:
            NIVO 8000:  3 = READY, 4  = PRODUCT.
            Other Nivona (600/700/79x/900/1030/1040):
                        8 = READY, 11 = PRODUCT.

        Message is a 16-bit "what does the machine want from the user"
        field. Only values 0 (none), 11 (move cup to frother) and 20
        (flush required) are surfaced today; the remaining Manipulation
        enum values (1–6) are derived from the Melitta side and have
        not been verified on real Nivona hardware — if a Nivona machine
        reports a Message ≥ 256 we fall back to Manipulation.NONE
        rather than silently splitting high and low bytes into
        mis-assigned fields (what the old ``>hhBBh`` parser did).

        Unknown process codes → process=None (status still returned so
        sub_process / manipulation / progress remain usable).
        """
        import struct  # noqa: PLC0415
        from ..const import (  # noqa: PLC0415
            InfoMessage, MachineProcess, Manipulation, SubProcess,
        )
        from ..protocol import MachineStatus  # noqa: PLC0415

        if len(data) < 8:
            return MachineStatus()

        process_val, sub_val, message, progress = (
            struct.unpack(">hhhh", data[:8])
        )

        if family_key == "8000":
            table = {3: MachineProcess.READY, 4: MachineProcess.PRODUCT}
        else:
            table = {8: MachineProcess.READY, 11: MachineProcess.PRODUCT}
        process = table.get(process_val)

        try:
            sub_process = SubProcess(sub_val)
        except ValueError:
            sub_process = None

        # Map Message → Manipulation when the value fits; high bytes
        # above 0 only appear on firmware that extended the enum past
        # 255 — we don't try to guess what that means for Nivona.
        manipulation: Manipulation = Manipulation.NONE
        if 0 <= message <= 0xFF:
            try:
                manipulation = Manipulation(message)
            except ValueError:
                manipulation = Manipulation.NONE

        # ``info_messages`` is a Melitta-side info-flag bitmap that is
        # NOT emitted by Nivona firmware — always zero for Nivona.
        return MachineStatus(
            process=process,
            sub_process=sub_process,
            info_messages=InfoMessage(0),
            manipulation=manipulation,
            progress=progress,
        )

    # Recipe / MyCoffee write-path helpers (experimental — see mixin docs)
    @staticmethod
    def standard_recipe_layout(family_key: str):
        return standard_recipe_layout(family_key)

    @staticmethod
    def mycoffee_layout(family_key: str):
        return mycoffee_layout(family_key)

    @staticmethod
    def standard_recipe_register(selector: int, offset: int) -> int:
        return standard_recipe_register(selector, offset)

    @staticmethod
    def temp_recipe_register(
        family_key: str, recipe_id: int, field: str,
    ) -> int | None:
        """Return HW register ID for a **temporary-override** recipe field.

        Per-brew overrides (strength / two_cups / fluid amounts /
        temperatures) go into a single fixed temp slot, with the
        family-specific field offset added. ``recipe_id`` is kept in
        the signature for API compatibility but is IGNORED — the temp
        slot is selector-independent.

        ``field`` is one of the layout field names without the ``_offset``
        suffix (``"strength"``, ``"coffee_amount"``, ``"temperature"``,
        ``"two_cups"``, ``"milk_amount"``, …). Returns ``None`` if the
        family does not expose that field.

        **Bug history:** previous implementation returned
        ``RECIPE_BASE_REGISTER + recipe_id*RECIPE_SLOT_STRIDE + offset``
        — the persistent slot — which silently corrupted the standard
        recipe definitions on real hardware. Fixed in v0.49.0.
        """
        del recipe_id  # explicitly ignored — see docstring.
        layout = _STANDARD_RECIPE_LAYOUTS.get(family_key)
        if layout is None:
            return None
        offset = getattr(layout, f"{field}_offset", None)
        if offset is None:
            return None
        return TEMP_RECIPE_BASE_REGISTER + offset

    @staticmethod
    def fluid_write_scale(family_key: str) -> int:
        """Return 10 or 1 — fluid amounts are written scaled for some families."""
        layout = _STANDARD_RECIPE_LAYOUTS.get(family_key)
        if layout is None:
            return 1
        return 10 if getattr(layout, "fluid_write_scale_10", False) else 1

    @staticmethod
    def mycoffee_register(slot: int, offset: int) -> int:
        return mycoffee_register(slot, offset)

    def capabilities_for_model(
        self, ble_name: str, dis: dict[str, str] | None = None,
    ) -> MachineCapabilities | None:
        """Return model-refined capabilities for a specific serial/advert.

        Looks up the family first, then applies model-specific overrides
        (from MODEL_RULES upstream) — primarily ``my_coffee_slots`` and
        ``strength_levels``, which vary per model within a family.
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
            caps = replace(caps, recipes=_RECIPES_8000_CHILLED)

        if override is None:
            return caps
        return replace(caps, **override)

    @staticmethod
    def is_chilled_selector(selector: int) -> bool:
        """True if `selector` requires the chilled-brew flag byte (0x00)
        instead of the normal-brew flag (0x01) when building the HE
        payload. Only relevant on NICR 8107."""
        return selector in _CHILLED_SELECTORS
