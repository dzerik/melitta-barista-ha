"""MelittaProfile — Brand profile for Melitta Caffeo Barista (T/TS Smart).

Hosts brand-specific crypto, HU verifier, advertisement regex, supported
extensions (HC/HJ recipe read/write), and per-family MachineCapabilities.

Recipe / DirectKey constants remain in ``const.py`` for backward
compatibility (entity classes import them directly). Future iterations
may move them in here, but for the multi-brand refactor that is not
required — what matters is that ``BrandRegistry`` can instantiate
``NivonaProfile`` independently and route the right crypto + capabilities.
"""

from __future__ import annotations

import logging
import re
from typing import ClassVar

from Crypto.Cipher import AES

from .base import MachineCapabilities

_LOGGER = logging.getLogger("melitta_barista")


# ---------------------------------------------------------------------------
# Crypto constants — Melitta-specific (lifted from legacy const.py)
# ---------------------------------------------------------------------------

_AES_KEY_PART_B = bytes([
    125, 57, 51, 41, 121, 78, -30 & 0xFF, 10, -62 & 0xFF, -22 & 0xFF,
    -27 & 0xFF, -19 & 0xFF, -89 & 0xFF, -85 & 0xFF, 3, 40, -12 & 0xFF,
])
_AES_KEY_PART_A = bytes([
    99, -127 & 0xFF, 119, 125, 118, 101, -102 & 0xFF, -108 & 0xFF,
    -39 & 0xFF, 100, -61 & 0xFF, -117 & 0xFF, -95 & 0xFF, -65 & 0xFF,
    -14 & 0xFF,
])
_AES_IV = bytes([
    -72 & 0xFF, -1 & 0xFF, -122 & 0xFF, -122 & 0xFF, 64, -10 & 0xFF,
    12, -118 & 0xFF, 25, 69, -117 & 0xFF, -123 & 0xFF, 58, -99 & 0xFF,
    93, -2 & 0xFF,
])
_ENCRYPTED_RC4_KEY = bytes([
    -81 & 0xFF, -14 & 0xFF, 21, -30 & 0xFF, 26, 60, 54, -89 & 0xFF,
    11, -42 & 0xFF, 95, -65 & 0xFF, 125, -6 & 0xFF, -99 & 0xFF, -111 & 0xFF,
    65, -16 & 0xFF, 14, 36, -126 & 0xFF, -40 & 0xFF, 13, -28 & 0xFF,
    15, 114, -48 & 0xFF, 48, -28 & 0xFF, -9 & 0xFF, -87 & 0xFF, 63,
    72, 122, -75 & 0xFF, 57, -13 & 0xFF, 101, 23, -7 & 0xFF,
    123, -9 & 0xFF, -66 & 0xFF, -30 & 0xFF, -87 & 0xFF, 5, -113 & 0xFF, -47 & 0xFF,
])


def _derive_rc4_key() -> bytes:
    """Decrypt the embedded RC4 stream key using the Melitta AES wrapper."""
    aes_key = _AES_KEY_PART_B + _AES_KEY_PART_A
    cipher = AES.new(aes_key, AES.MODE_CBC, iv=_AES_IV)
    decrypted = cipher.decrypt(_ENCRYPTED_RC4_KEY)
    pad_len = decrypted[-1]
    if 1 <= pad_len <= 16 and all(b == pad_len for b in decrypted[-pad_len:]):
        decrypted = decrypted[:-pad_len]
    return decrypted


# Melitta HU CRC table — 256-byte lookup for handshake verifier.
_MELITTA_HU_TABLE = bytes(
    b & 0xFF for b in [
        98, 6, 85, -106, 36, 23, 112, -92, -121, -49, -87, 5, 26, 64, -91,
        -37, 61, 20, 68, 89, -126, 63, 52, 102, 24, -27, -124, -11, 80, -40,
        -61, 115, 90, -88, -100, -53, -79, 120, 2, -66, -68, 7, 100, -71, -82,
        -13, -94, 10, -19, 18, -3, -31, 8, -48, -84, -12, -1, 126, 101, 79,
        -111, -21, -28, 121, 123, -5, 67, -6, -95, 0, 107, 97, -15, 111, -75,
        82, -7, 33, 69, 55, 59, -103, 29, 9, -43, -89, 84, 93, 30, 46, 94,
        75, -105, 114, 73, -34, -59, 96, -46, 45, 16, -29, -8, -54, 51, -104,
        -4, 125, 81, -50, -41, -70, 39, -98, -78, -69, -125, -120, 1, 49, 50,
        17, -115, 91, 47, -127, 60, 99, -102, 35, 86, -85, 105, 34, 38, -56,
        -109, 58, 77, 118, -83, -10, 76, -2, -123, -24, -60, -112, -58, 124,
        53, 4, 108, 74, -33, -22, -122, -26, -99, -117, -67, -51, -57, -128,
        -80, 19, -45, -20, 127, -64, -25, 70, -23, 88, -110, 44, -73, -55, 22,
        83, 13, -42, 116, 109, -97, 32, 95, -30, -116, -36, 57, 12, -35, 31,
        -47, -74, -113, 92, -107, -72, -108, 62, 113, 65, 37, 27, 106, -90, 3,
        14, -52, 72, 21, 41, 56, 66, 28, -63, 40, -39, 25, 54, -77, 117, -18,
        87, -16, -101, -76, -86, -14, -44, -65, -93, 78, -38, -119, -62, -81,
        110, 43, 119, -32, 71, 122, -114, 42, -96, 104, 48, -9, 103, 15, 11,
        -118, -17,
    ]
)


# ---------------------------------------------------------------------------
# Family capabilities
# ---------------------------------------------------------------------------

_MELITTA_FAMILIES: dict[str, MachineCapabilities] = {
    "barista_t": MachineCapabilities(
        family_key="barista_t",
        model_name="Barista T Smart",
        supports_recipe_writes=True,
        supports_stats=True,
        my_coffee_slots=4,
        strength_levels=5,
        has_aroma_balance=True,
    ),
    "barista_ts": MachineCapabilities(
        family_key="barista_ts",
        model_name="Barista TS Smart",
        supports_recipe_writes=True,
        supports_stats=True,
        my_coffee_slots=8,
        strength_levels=5,
        has_aroma_balance=True,
    ),
}

# BLE local-name prefix → family key.
_PREFIX_TO_FAMILY: dict[str, str] = {
    "8301": "barista_t",   # Caffeo Barista T
    "8311": "barista_t",   # Caffeo Barista T (variant)
    "8401": "barista_t",   # Barista Smart
    "8501": "barista_ts",  # Barista T Smart
    "8601": "barista_ts",  # Barista TS Smart
    "8604": "barista_ts",  # Barista TS Smart (later revision)
}


# ---------------------------------------------------------------------------
# MelittaProfile
# ---------------------------------------------------------------------------

class MelittaProfile:
    """Brand profile for Melitta Caffeo Barista machines.

    Implements ``BrandProfile`` structurally (Protocol-compliant). One
    instance is registered in ``BrandRegistry`` and shared across all
    config entries with ``brand == "melitta"``.
    """

    brand_slug: ClassVar[str] = "melitta"
    brand_name: ClassVar[str] = "Melitta"
    service_uuid: ClassVar[str] = "0000ad00-b35c-11e4-9813-0002a5d5c51b"
    handshake_response_size: ClassVar[int] = 8

    # Match any of the 6 known model-code prefixes followed by hex/digits.
    ble_name_regex: ClassVar[re.Pattern[str]] = re.compile(
        r"^(8301|8311|8401|8501|8601|8604)[0-9A-Fa-f]"
    )

    supported_extensions: ClassVar[frozenset[str]] = frozenset({"HC", "HJ"})

    families: ClassVar[dict[str, MachineCapabilities]] = _MELITTA_FAMILIES

    # Lazily computed on first access — RC4 key derivation is cheap but
    # touching pycryptodome at import time keeps test isolation cleaner.
    _rc4_key_cache: bytes | None = None

    @property
    def runtime_rc4_key(self) -> bytes:
        if MelittaProfile._rc4_key_cache is None:
            MelittaProfile._rc4_key_cache = _derive_rc4_key()
        return MelittaProfile._rc4_key_cache

    @property
    def hu_table(self) -> bytes:
        return _MELITTA_HU_TABLE

    def hu_verifier(self, buf: bytes, start: int, count: int) -> bytes:
        """2-round Melitta HU CRC fold (legacy ``_compute_handshake_crc``)."""
        table = _MELITTA_HU_TABLE

        b5 = table[(buf[start] + 256) % 256]
        for i in range(start + 1, start + count):
            idx = ((b5 ^ buf[i]) + 256) % 256
            b5 = table[idx]
        byte1 = (b5 + 93) & 0xFF

        b7 = table[(buf[start] + 257) % 256]
        for i in range(start + 1, start + count):
            idx = ((b7 ^ buf[i]) + 256) % 256
            b7 = table[idx]
        byte2 = (b7 + 167) & 0xFF

        return bytes([byte1, byte2])

    def detect_family(
        self, ble_name: str, dis: dict[str, str] | None = None,
    ) -> str | None:
        """Map a BLE local_name prefix to a Melitta family key."""
        if not ble_name:
            return None
        prefix = ble_name[:4]
        return _PREFIX_TO_FAMILY.get(prefix)

    def capabilities_for(self, family_key: str) -> MachineCapabilities:
        return _MELITTA_FAMILIES[family_key]

    def parse_status(self, family_key, data):
        """Melitta uses the raw MachineProcess enum values directly
        (READY=2, PRODUCT=4, …) — delegate to the canonical parser."""
        from ..protocol import MachineStatus  # noqa: PLC0415
        return MachineStatus.from_payload(data)
