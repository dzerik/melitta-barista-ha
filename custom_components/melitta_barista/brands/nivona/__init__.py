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
    _family_600,
    _family_700,
    _family_900,
    _family_1030,
    _family_8000,
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
# Family notes (preserved from the pre-loop version):
#   - 8000 (NICR 8101/8103/8107) uses brew_command_mode 0x04, all others 0x0B.
#   - 900 (NICR 920/930) writes fluid amounts as ml × 10.
#   - 79x has hasAromaBalance=True; others False.
#   - 600 has only 1 MyCoffee slot; 700/79x/900/8000 have 4.
#   - Maintenance stat gauges (600/601/610/611/620/621/640/641) are
#     universal; only the "filter dependency" id varies per family
#     (642 on 8000, 101 on 900/1000, 105 on 700/79X/600).
# ---------------------------------------------------------------------------

_FAMILY_DATA: dict[str, dict] = {}
for _mod in (_family_600, _family_700, _family_900, _family_1030, _family_8000):
    _FAMILY_DATA.update(_mod.EXPORTS)
del _mod

_STANDARD_RECIPE_LAYOUTS: dict[str, RecipeFieldLayout] = {
    k: v["standard_layout"] for k, v in _FAMILY_DATA.items()
}
_MYCOFFEE_LAYOUTS: dict[str, RecipeFieldLayout] = {
    k: v["mycoffee_layout"] for k, v in _FAMILY_DATA.items()
}
_NIVONA_FAMILIES: dict[str, MachineCapabilities] = {
    k: v["capabilities"] for k, v in _FAMILY_DATA.items()
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
        from ...const import (  # noqa: PLC0415
            InfoMessage, MachineProcess, Manipulation, SubProcess,
        )
        from ...protocol import MachineStatus  # noqa: PLC0415

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
            caps = replace(caps, recipes=_family_8000.RECIPES_8000_CHILLED)

        if override is None:
            return caps
        return replace(caps, **override)

    @staticmethod
    def is_chilled_selector(selector: int) -> bool:
        """True if `selector` requires the chilled-brew flag byte (0x00)
        instead of the normal-brew flag (0x01) when building the HE
        payload. Only relevant on NICR 8107."""
        return selector in _family_8000.CHILLED_SELECTORS
