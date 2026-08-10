"""coffee_platform.domain — brand-agnostic domain vocabulary.

Canonical home of the types the platform contract and every brand
provider share: capability descriptors, MachineCapabilities, the
BrandProfile Protocol, FeatureNotSupported, status enums, and
MachineStatus.

Depends ONLY on the Python standard library — never on melitta_barista
internals — so coffee_platform/ can be lifted into a standalone repo.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from enum import IntEnum, IntFlag
from typing import Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Machine status enums
# ---------------------------------------------------------------------------

class MachineProcess(IntEnum):
    """Machine process states."""
    READY = 2
    PRODUCT = 4
    CLEANING = 9
    DESCALING = 10
    FILTER_INSERT = 11
    FILTER_REPLACE = 12
    FILTER_REMOVE = 13
    SWITCH_OFF = 16
    EASY_CLEAN = 17
    INTENSIVE_CLEAN = 19
    EVAPORATING = 20
    BUSY = 99


class SubProcess(IntEnum):
    """Sub-process states during preparation."""
    GRINDING = 1
    COFFEE = 2
    STEAM = 3
    WATER = 4
    PREPARE = 5


class InfoMessage(IntFlag):
    """Info message bitfield."""
    FILL_BEANS_1 = 1 << 0
    FILL_BEANS_2 = 1 << 1
    EASY_CLEAN = 1 << 2
    POWDER_FILLED = 1 << 3
    PREPARATION_CANCELLED = 1 << 4


class Manipulation(IntEnum):
    """Manipulation states requiring user action.

    Values 0 / 11 / 20 are observed and named the same way on both
    Melitta and Nivona hardware. Values 1–6 are Melitta-derived labels;
    real Nivona machines may emit different codes or a different
    1-6 → meaning mapping. HA currently applies the Melitta labels
    for any value in 1..6; this is a best-effort placeholder until
    per-brand parse_status overrides verify the mapping.
    """
    NONE = 0
    BU_REMOVED = 1            # Melitta-observed
    TRAYS_MISSING = 2         # Melitta-observed
    EMPTY_TRAYS = 3           # Melitta-observed
    FILL_WATER = 4            # Melitta-observed
    CLOSE_POWDER_LID = 5      # Melitta-observed
    FILL_POWDER = 6           # Melitta-observed
    MOVE_CUP_TO_FROTHER = 11  # observed on both brands
    FLUSH_REQUIRED = 20       # observed on both brands


# ---------------------------------------------------------------------------
# Machine status model
# ---------------------------------------------------------------------------

@dataclass
class MachineStatus:
    """Machine status parsed from HX response."""
    process: MachineProcess | None = None
    sub_process: SubProcess | None = None
    info_messages: InfoMessage = InfoMessage(0)
    manipulation: Manipulation = Manipulation.NONE
    progress: int = 0

    @property
    def is_ready(self) -> bool:
        return self.process == MachineProcess.READY and self.manipulation == Manipulation.NONE

    def is_ready_for_brew(self, tolerated: tuple[int, ...] = ()) -> bool:
        """Like is_ready but accepts brand-tolerated manipulation flags.

        Some Nivona models leave MOVE_CUP_TO_FROTHER set after a
        completed brew until the next status frame; the brand profile
        declares such flags via tolerated_brew_manipulations so the
        brew gate doesn't reject the next brew falsely.
        """
        if self.process != MachineProcess.READY:
            return False
        if self.manipulation == Manipulation.NONE:
            return True
        return self.manipulation.value in tolerated

    @property
    def is_brewing(self) -> bool:
        return self.process == MachineProcess.PRODUCT

    @classmethod
    def from_payload(cls, data: bytes) -> MachineStatus:
        if len(data) < 8:
            return cls()
        process_val, sub_val, info_byte, manip_byte, progress = struct.unpack(">hhBBh", data[:8])
        try:
            process = MachineProcess(process_val)
        except ValueError:
            process = None
        try:
            sub_process = SubProcess(sub_val)
        except ValueError:
            sub_process = None
        try:
            manipulation = Manipulation(manip_byte)
        except ValueError:
            manipulation = Manipulation.NONE
        return cls(
            process=process,
            sub_process=sub_process,
            info_messages=InfoMessage(info_byte),
            manipulation=manipulation,
            progress=progress,
        )


# ---------------------------------------------------------------------------
# Capability descriptors
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RecipeDescriptor:
    """One drink recipe, brand-agnostic representation."""
    recipe_id: int
    name: str
    category: str = ""               # "espresso", "milk_drink", etc.


@dataclass(frozen=True)
class SettingDescriptor:
    """One numeric/categorical machine setting."""
    setting_id: int
    key: str
    title: str
    options: tuple[tuple[int, str], ...] = ()   # (value, label) pairs
    is_writable: bool = True
    unit: str | None = None


@dataclass(frozen=True)
class StatDescriptor:
    """One read-only stat counter."""
    stat_id: int
    key: str
    title: str
    unit: str | None = None
    is_diagnostic: bool = True


@dataclass(frozen=True)
class RecipeFieldLayout:
    """Per-family byte offsets for standard-recipe and MyCoffee slot
    parameters (strength / profile / temperature / fluid amounts...).

    Each field holds the byte-offset inside the recipe slot register
    block (`base_register + offset`). ``None`` means the family does
    not expose that parameter. Populated from upstream
    `resolveStandardRecipeLayout` / `resolveMyCoffeeLayout`.
    """
    family_key: str
    # Shared fields (always present on all families)
    strength_offset: int | None = None
    profile_offset: int | None = None
    two_cups_offset: int | None = None
    # Temperature (single byte on 600/700/79x/8000; per-fluid on 900/1030/1040)
    temperature_offset: int | None = None
    coffee_temperature_offset: int | None = None
    water_temperature_offset: int | None = None
    milk_temperature_offset: int | None = None
    milk_foam_temperature_offset: int | None = None
    overall_temperature_offset: int | None = None
    # Fluid amounts
    coffee_amount_offset: int | None = None
    water_amount_offset: int | None = None
    milk_amount_offset: int | None = None
    milk_foam_amount_offset: int | None = None
    # Misc
    preparation_offset: int | None = None
    # MyCoffee-only fields
    enabled_offset: int | None = None
    icon_offset: int | None = None
    name_offset: int | None = None
    type_offset: int | None = None
    # Write-path helpers
    fluid_write_scale_10: bool = False


@dataclass(frozen=True)
class MachineCapabilities:
    """Per-machine-family capability bag.

    Driven by family detection at config-flow time. Used by entity
    factories to decide what to register and by mixins to decide which
    optional commands are available.
    """
    family_key: str                                  # "8604" / "700" / "1040"
    model_name: str                                  # "Barista TS Smart" / "NICR 756"

    # Feature flags
    supports_recipe_writes: bool = False
    supports_stats: bool = False
    my_coffee_slots: int = 0
    strength_levels: int = 5
    has_aroma_balance: bool = False
    # Brew-selector base for MyCoffee slots. On Nivona, MyCoffee slot N
    # is brewed by sending HE with `payload[3] = first_mycoffee_selector
    # + N`. The vendor protocol uses 20 for every Nivona model. Melitta
    # has its own MyCoffee scheme (per-slot DirectKey IDs) and doesn't
    # use this field — the default sits unused.
    #
    # Added in v0.77.1.
    first_mycoffee_selector: int = 20
    image_transfer: bool | None = None               # None until HI answers

    # Protocol quirks
    fluid_scale_factor: int = 1                      # Nivona 900 = 10
    brew_command_mode: int = 0x0B                    # 0x04 for Nivona 8000
    recipe_text_encoding: str = "legacy_1byte"       # vs "utf16_le"
    # Manipulation flags that should NOT block a new brew. Some Nivona
    # models leave MOVE_CUP_TO_FROTHER set after a completed brew until
    # the next status frame; gating brew on it would falsely block the
    # user. Set per-family (default empty = strict).
    tolerated_brew_manipulations: tuple[int, ...] = ()

    # Tables (filled at construction time per family)
    recipes: tuple[RecipeDescriptor, ...] = ()
    settings: tuple[SettingDescriptor, ...] = ()
    stats: tuple[StatDescriptor, ...] = ()

    # Generic Eugster HR setting IDs (const.MachineSettingId namespace,
    # consumed by number.py SETTING_DEFINITIONS) that this family's
    # firmware does NOT implement — reads NACK/time out and writes are
    # ignored, so entity factories must not register them (issue #10:
    # LANGUAGE dead on a live NICR 790). Sibling mechanism to the Nivona
    # 100+-namespace `_MODEL_SETTINGS_EXCLUDE` in brands/nivona/_prefixes.py.
    unsupported_generic_setting_ids: frozenset[int] = frozenset()

    # ----------------------------------------------------------------- #
    # Feature flags (introduced v0.79.0 — see PR-31).
    #
    # Boolean capabilities consumed by HA entity factories to drive
    # registration without testing `brand_slug == "X"`. Each flag is
    # set in the family-specific MachineCapabilities literal at the
    # point the family is declared (per-family for Nivona, per-model
    # for Melitta).
    # ----------------------------------------------------------------- #

    # Whether the firmware exposes factory-reset HE commands (50/51).
    # Nivona families 600 / 700 / 79x / 900 / 900-light / 1030 / 1040 = True;
    # NIVO 8000 = False (vendor app does not expose it either);
    # Melitta = False.
    supports_factory_reset: bool = False

    # Whether the family supports per-brew overrides via the
    # temp-recipe slot (HW writes to 9001 + field offset before HE).
    # All Nivona families = True; Melitta = False (uses HC/HJ writes).
    supports_brew_overrides: bool = False

    # Legacy total-cups sensor reads HR register id 150 — a Melitta
    # convention. Non-Melitta brands derive total_beverages from the
    # capability-driven `stats` table instead. Melitta = True; all
    # Nivona families = False.
    uses_legacy_total_cups_sensor: bool = False


# ---------------------------------------------------------------------------
# Brand profile Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class BrandProfile(Protocol):
    """Single source of truth for brand-specific behaviour.

    Implementations live under ``brands/`` (one file per brand). Profiles
    are stateless dataclass-like objects — instantiated once in
    ``BrandRegistry`` and shared across all client instances.
    """

    # Identity
    brand_slug: str                                  # "melitta" / "nivona"
    brand_name: str                                  # "Melitta" / "Nivona"
    ble_name_regex: re.Pattern[str]
    service_uuid: str                                # 0000ad00-...

    # Crypto
    runtime_rc4_key: bytes                           # 32 ASCII bytes
    hu_table: bytes                                  # 256-byte lookup
    handshake_response_size: int                     # 8 for Melitta, may differ

    def hu_verifier(self, buf: bytes, start: int, count: int) -> bytes:
        """Compute the 2-byte HU verifier for a (start, count) slice of
        the buffer. Brand-specific table-driven fold."""

    # Capabilities
    supported_extensions: frozenset[str]
    families: dict[str, MachineCapabilities]

    def detect_family(
        self, ble_name: str, dis: dict[str, str] | None,
    ) -> str | None:
        """Identify the machine family from advertisement local_name and
        (optionally) Device Information Service data. Returns a key into
        ``families`` or None if unknown."""

    def capabilities_for(self, family_key: str) -> MachineCapabilities:
        """Return the capability bag for the given family. Raises KeyError
        if the key is not in ``families``."""

    def parse_status(self, family_key: str | None, data: bytes):  # noqa: ANN201
        """Parse an HX payload into a ``MachineStatus`` with brand/family-
        specific process-code semantics.

        Different families use different raw numbers for ``process``:
        Melitta uses 2=READY/4=PRODUCT, NIVO 8000 uses 3=READY/4=PRODUCT,
        other Nivona families use 8=READY/11=PRODUCT. Implementations
        translate raw codes to the abstract ``MachineProcess`` enum.
        """

    # ----------------------------------------------------------------- #
    # Recipe write-path contract (introduced v0.79.0 — see PR-32).
    #
    # Brands that don't expose recipe writes / brew overrides
    # implement these as no-op stubs returning ``None`` / ``1`` /
    # ``False``. Callers in shared mixins (``_ble_commands``,
    # ``_ble_recipes``, ``sensor``) use them to drive behaviour
    # without testing ``brand_slug``.
    # ----------------------------------------------------------------- #

    # Fixed HW register that announces the temp-recipe class before
    # an HE brew with per-brew overrides. ``None`` on brands that
    # don't use this single-slot temp-recipe pattern.
    temp_recipe_type_register: int | None

    def temp_recipe_register(
        self,
        family_key: str,
        recipe_id: int,
        field: str,
    ) -> int | None:
        """HW register for a per-brew override field, or ``None`` if
        the family / field is not writable on this brand."""

    def fluid_write_scale(self, family_key: str) -> int:
        """Per-family fluid-amount scaling factor for write paths.
        Returns 1 for brands that use ml directly, 10 for families
        like Nivona 900 that historically wrote ml × 10."""

    def mycoffee_layout(self, family_key: str) -> RecipeFieldLayout | None:
        """RecipeFieldLayout describing per-slot MyCoffee parameter
        offsets, or ``None`` if the brand doesn't expose bulk MyCoffee
        read / per-slot parameter sensors."""

    def mycoffee_register(self, slot: int, offset: int) -> int | None:
        """Absolute HW register for ``(slot, offset)`` in the
        MyCoffee region, or ``None`` if the brand doesn't have a
        contiguous MyCoffee register block."""

    def is_chilled_selector(self, selector: int) -> bool:
        """True if ``selector`` requires the chilled-brew flag byte
        when building the HE payload. False for brands without chilled
        recipes."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def supports_extension(profile: BrandProfile, opcode: str) -> bool:
    """Sugar: whether the brand's command-set includes a given opcode."""
    return opcode in profile.supported_extensions


class FeatureNotSupported(Exception):
    """Raised when a mixin tries an opcode the active brand does not
    advertise (e.g. HC recipe read on Nivona)."""

    def __init__(self, opcode: str, brand_slug: str) -> None:
        super().__init__(
            f"Command {opcode!r} is not supported by brand {brand_slug!r}",
        )
        self.opcode = opcode
        self.brand_slug = brand_slug
