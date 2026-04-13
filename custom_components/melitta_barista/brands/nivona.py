"""NivonaProfile — Brand profile for Nivona NICR / NIVO 8xxx machines.

Built from public RE in mpapierski/esp-coffee-bridge (docs/NIVONA.md +
src/nivona.cpp). All facts (constants, family tables, HU verifier
algorithm) are independently re-implemented in Python; no source is
copied verbatim.

Limitations:

- Untested on real hardware as of 2026-04-13. Released as alpha; users
  with NICR/NIVO machines are invited to report results via GitHub
  issues.
- Recipe writes / DirectKey are NOT supported — Nivona machines do not
  expose recipe-edit affordances; ``supported_extensions`` is empty.
"""

from __future__ import annotations

import logging
import re
from typing import ClassVar

from .base import MachineCapabilities

_LOGGER = logging.getLogger("melitta_barista")


# ---------------------------------------------------------------------------
# Crypto — Nivona-specific runtime RC4 key.
#
# Recovered from de.nivona.mobileapp 3.8.6 APK in the upstream RE; this
# is the 32-byte ASCII key fed to the EFLibrary stream cipher after
# customer-key bootstrap.
# ---------------------------------------------------------------------------

_NIVONA_RC4_KEY: bytes = b"NIV_060616_V10_1*9#3!4$6+4res-?3"
assert len(_NIVONA_RC4_KEY) == 32, "Nivona RC4 key must be 32 bytes"


# Nivona HU lookup table (256 bytes). Reconstructed from the upstream
# `HU_TABLE` constant in src/nivona.cpp.
_NIVONA_HU_TABLE = bytes([
    0x62, 0x06, 0x55, 0x96, 0x24, 0x17, 0x70, 0xA4, 0x87, 0xCF, 0xA9, 0x05, 0x1A, 0x40, 0xA5, 0xDB,
    0x3D, 0x14, 0x44, 0x59, 0x82, 0x3F, 0x34, 0x66, 0x18, 0xE5, 0x84, 0xF5, 0x50, 0xD8, 0xC3, 0x73,
    0x5A, 0xA8, 0x9C, 0xCB, 0xB1, 0x78, 0x02, 0xBE, 0xBC, 0x07, 0x64, 0xB9, 0xAE, 0xF3, 0xA2, 0x0A,
    0xED, 0x12, 0xFD, 0xE1, 0x08, 0xD0, 0xAC, 0xF4, 0xFF, 0x7E, 0x65, 0x4F, 0x91, 0xEB, 0xE4, 0x79,
    0x7B, 0xFB, 0x43, 0xFA, 0xA1, 0x00, 0x6B, 0x61, 0xF1, 0x6F, 0xB5, 0x52, 0xF9, 0x21, 0x45, 0x37,
    0x3B, 0x99, 0x1D, 0x09, 0xD5, 0xA7, 0x54, 0x5D, 0x1E, 0x2E, 0x5E, 0x4B, 0x97, 0x72, 0x49, 0xDE,
    0xC5, 0x60, 0xD2, 0x2D, 0x10, 0xE3, 0xF8, 0xCA, 0x33, 0x98, 0xFC, 0x7D, 0x51, 0xCE, 0xD7, 0xBA,
    0x27, 0x9E, 0xB2, 0xBB, 0x83, 0x88, 0x01, 0x31, 0x32, 0x11, 0x8D, 0x5B, 0x2F, 0x81, 0x3C, 0x63,
    0x9A, 0x23, 0x56, 0xAB, 0x69, 0x22, 0x26, 0xC8, 0x93, 0x3A, 0x4D, 0x76, 0xAD, 0xF6, 0x4C, 0xFE,
    0x85, 0xE8, 0xC4, 0x90, 0xC6, 0x7C, 0x35, 0x04, 0x6C, 0x4A, 0xDF, 0xEA, 0x86, 0xE6, 0x9D, 0x8B,
    0xBD, 0xCD, 0xC7, 0x80, 0xB0, 0x13, 0xD3, 0xEC, 0x7F, 0xC0, 0xE7, 0x46, 0xE9, 0x58, 0x92, 0x2C,
    0xB7, 0xC9, 0x16, 0x53, 0x0D, 0xD6, 0x74, 0x6D, 0x9F, 0x20, 0x5F, 0xE2, 0x8C, 0xDC, 0x39, 0x0C,
    0xDD, 0x1F, 0xD1, 0xB6, 0x8F, 0x5C, 0x95, 0xB8, 0x94, 0x3E, 0x71, 0x41, 0x25, 0x1B, 0x6A, 0xA6,
    0x03, 0x0E, 0xCC, 0x48, 0x15, 0x29, 0x38, 0x42, 0x1C, 0xC1, 0x28, 0xD9, 0x19, 0x36, 0xB3, 0x75,
    0xEE, 0x57, 0xF0, 0x9B, 0xB4, 0xAA, 0xF2, 0xD4, 0xBF, 0xA3, 0x4E, 0xDA, 0x89, 0xC2, 0xAF, 0x6E,
    0x2B, 0x77, 0xE0, 0x47, 0x7A, 0x8E, 0x2A, 0xA0, 0x68, 0x30, 0xF7, 0x67, 0x0F, 0x0B, 0x8A, 0xEF,
])
assert len(_NIVONA_HU_TABLE) == 256, "Nivona HU table must be 256 bytes"


# ---------------------------------------------------------------------------
# Family capabilities — 7 Nivona families covered by the upstream RE.
#
# Notes on per-family flags:
#   - 8000 (NICR 8101/8103/8107) uses brew_command_mode 0x04, all others 0x0B.
#   - 900 (NICR 920/930) writes fluid amounts as ml × 10.
#   - 79x has hasAromaBalance=True; others False (per src/nivona.cpp).
#   - 600 has only 1 MyCoffee slot; 700/79x/900/8000 have 4.
# ---------------------------------------------------------------------------

_NIVONA_FAMILIES: dict[str, MachineCapabilities] = {
    "600": MachineCapabilities(
        family_key="600",
        model_name="Nivona NICR 6xx",
        supports_recipe_writes=False,
        supports_stats=False,
        my_coffee_slots=1,
        strength_levels=3,
        has_aroma_balance=False,
        brew_command_mode=0x0B,
    ),
    "700": MachineCapabilities(
        family_key="700",
        model_name="Nivona NICR 7xx",
        supports_recipe_writes=False,
        supports_stats=True,
        my_coffee_slots=4,
        strength_levels=3,
        has_aroma_balance=True,
        brew_command_mode=0x0B,
    ),
    "79x": MachineCapabilities(
        family_key="79x",
        model_name="Nivona NICR 79x",
        supports_recipe_writes=False,
        supports_stats=True,
        my_coffee_slots=4,
        strength_levels=5,
        has_aroma_balance=True,
        brew_command_mode=0x0B,
    ),
    "900": MachineCapabilities(
        family_key="900",
        model_name="Nivona NICR 9xx",
        supports_recipe_writes=False,
        supports_stats=False,
        my_coffee_slots=4,
        strength_levels=5,
        has_aroma_balance=False,
        brew_command_mode=0x0B,
        fluid_scale_factor=10,
    ),
    "900-light": MachineCapabilities(
        family_key="900-light",
        model_name="Nivona NICR 9xx Light",
        supports_recipe_writes=False,
        supports_stats=False,
        my_coffee_slots=4,
        strength_levels=3,
        brew_command_mode=0x0B,
    ),
    "1030": MachineCapabilities(
        family_key="1030",
        model_name="Nivona NICR 1030",
        supports_recipe_writes=False,
        supports_stats=False,
        my_coffee_slots=4,
        strength_levels=5,
        brew_command_mode=0x0B,
    ),
    "1040": MachineCapabilities(
        family_key="1040",
        model_name="Nivona NICR 1040",
        supports_recipe_writes=False,
        supports_stats=False,
        my_coffee_slots=4,
        strength_levels=5,
        brew_command_mode=0x0B,
    ),
    "8000": MachineCapabilities(
        family_key="8000",
        model_name="Nivona NIVO 8xxx",
        supports_recipe_writes=False,
        supports_stats=True,
        my_coffee_slots=4,
        strength_levels=5,
        brew_command_mode=0x04,    # NIVO8000 uses different brew opcode byte
    ),
}

# Serial-prefix → family. Exhaustive 4-char then 3-char cascade matches
# the upstream `detectModelInfo` cascade.
_PREFIX_TO_FAMILY: dict[str, str] = {
    # 4-char (NIVO 8xxx serials)
    "8101": "8000", "8103": "8000", "8107": "8000",
    # 3-char (NICR series; matched after 4-char miss)
    "660": "600", "670": "600", "675": "600", "680": "600",
    "756": "700", "758": "700", "759": "700",
    "768": "700", "769": "700", "778": "700", "779": "700",
    "788": "700", "789": "700",
    "790": "79x", "791": "79x", "792": "79x", "793": "79x",
    "794": "79x", "795": "79x", "796": "79x", "797": "79x", "799": "79x",
    "920": "900", "930": "900",
    "960": "900-light", "965": "900-light", "970": "900-light",
    "1030": "1030",
    "1040": "1040",
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

    # Standard Nivona advertisement: NIVONA-NNNNNNNNNN-----  (10 digits + 5 dashes)
    ble_name_regex: ClassVar[re.Pattern[str]] = re.compile(
        r"^NIVONA-\d{10}-----$"
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
        (+0x5D on first byte, +0xA7 on second). Reverse-engineered from
        `deriveHuVerifier()` in nivona.cpp.
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
