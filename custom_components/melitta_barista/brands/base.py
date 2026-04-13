"""BrandProfile abstraction — single source of truth for brand-specific
behaviour (crypto, advertisement matching, recipe tables, capabilities).

See ADR 001 for design rationale.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


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
    image_transfer: bool | None = None               # None until HI answers

    # Protocol quirks
    fluid_scale_factor: int = 1                      # Nivona 900 = 10
    brew_command_mode: int = 0x0B                    # 0x04 for Nivona 8000
    recipe_text_encoding: str = "legacy_1byte"       # vs "utf16_le"

    # Tables (filled at construction time per family)
    recipes: tuple[RecipeDescriptor, ...] = ()
    settings: tuple[SettingDescriptor, ...] = ()
    stats: tuple[StatDescriptor, ...] = ()


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
