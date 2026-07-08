"""Serial-prefix tables for Nivona family / model resolution.

Two layers:

- ``_PREFIX_TO_FAMILY`` — exhaustive 3- or 4-char serial prefix to
  family key (``"600"``, ``"700"``, …). Consumed by
  ``NivonaProfile.detect_family``.
- ``_MODEL_OVERRIDES`` — per-prefix surgical patches on top of the
  family default (currently ``my_coffee_slots`` and ``strength_levels``;
  newer hardware regularly differs from the family baseline in those
  two fields).
- ``_MODEL_SETTINGS_EXCLUDE`` — per-prefix HW setting IDs to drop. The
  only entry today is NICR758 missing the aroma-balance profile (id 106).

A 4-char prefix match wins over a 3-char one (``NIVO 8xxx`` serials
share the leading digits with ``NICR 8xxx``-class hypothetical models).
"""

from __future__ import annotations

# Per-model overrides applied on top of the family default.
# Key: 3- or 4-char serial prefix; value: ``{field: override_value}``.
_MODEL_OVERRIDES: dict[str, dict] = {
    # 4-char NIVO 8xxx
    "8101": {"my_coffee_slots": 9, "strength_levels": 5},
    "8103": {"my_coffee_slots": 9, "strength_levels": 5},
    "8107": {"my_coffee_slots": 9, "strength_levels": 5},
    # 4-char NIVO 9xxx (2025 9000 series). ALPHA: mapped onto the 8000
    # family baseline (same NIVO line, brew opcode 0x04) pending live
    # confirmation from real hardware — requested on the HA community
    # forum. Slot/strength values mirror the 8xxx baseline.
    "9101": {"my_coffee_slots": 9, "strength_levels": 5},
    # NICR 600 — all 5 strength levels; MyCoffee slot count varies.
    "660": {"my_coffee_slots": 1, "strength_levels": 5},
    "670": {"my_coffee_slots": 5, "strength_levels": 5},
    "675": {"my_coffee_slots": 5, "strength_levels": 5},
    "680": {"my_coffee_slots": 5, "strength_levels": 5},
    # NICR 700 (single-slot variants, 3 strength levels)
    "756": {"my_coffee_slots": 1, "strength_levels": 3},
    "758": {"my_coffee_slots": 1, "strength_levels": 3},
    "759": {"my_coffee_slots": 1, "strength_levels": 3},
    # NICR 700 late revisions — single MyCoffee slot but full
    # 5-band strength range (hardware parity with 788/789 and up).
    "768": {"my_coffee_slots": 1, "strength_levels": 5},
    "769": {"my_coffee_slots": 1, "strength_levels": 5},
    "778": {"my_coffee_slots": 1, "strength_levels": 5},
    "779": {"my_coffee_slots": 1, "strength_levels": 5},
    # NICR 700 (five-slot variants)
    "788": {"my_coffee_slots": 5, "strength_levels": 5},
    "789": {"my_coffee_slots": 5, "strength_levels": 5},
    # NICR 79x
    "790": {"my_coffee_slots": 5, "strength_levels": 5},
    "791": {"my_coffee_slots": 5, "strength_levels": 5},
    "792": {"my_coffee_slots": 5, "strength_levels": 5},
    "793": {"my_coffee_slots": 5, "strength_levels": 5},
    "794": {"my_coffee_slots": 5, "strength_levels": 5},
    "795": {"my_coffee_slots": 5, "strength_levels": 5},
    "796": {"my_coffee_slots": 5, "strength_levels": 5},
    "797": {"my_coffee_slots": 5, "strength_levels": 5},
    "799": {"my_coffee_slots": 5, "strength_levels": 5},
    # NICR 900 / 900-light
    "920": {"my_coffee_slots": 9, "strength_levels": 5},
    "930": {"my_coffee_slots": 9, "strength_levels": 5},
    "960": {"my_coffee_slots": 9, "strength_levels": 5},
    "965": {"my_coffee_slots": 9, "strength_levels": 5},
    "970": {"my_coffee_slots": 9, "strength_levels": 5},
    # NICR 1030 / 1040
    "030": {"my_coffee_slots": 18, "strength_levels": 5},
    "040": {"my_coffee_slots": 18, "strength_levels": 5},
}


# Per-model settings overrides applied on top of the family table.
# Currently the only surgical filter is dropping ``profile`` (id 106)
# for NICR758 — that specific model omits the aroma-balance profile
# feature, so reading id 106 would NACK/timeout on real hardware.
_MODEL_SETTINGS_EXCLUDE: dict[str, frozenset[int]] = {
    "758": frozenset({106}),
}


# Serial-prefix → family. Exhaustive 4-char then 3-char cascade.
_PREFIX_TO_FAMILY: dict[str, str] = {
    # 4-char (NIVO 8xxx serials). Only 8101 / 8103 / 8107 are confirmed
    # model codes in the 8000 family. Newer 81xx variants reported in
    # the field (#15) fall through to "unknown family"; we'd rather
    # surface that honestly than guess at capability layouts.
    "8101": "8000", "8103": "8000", "8107": "8000",
    # NIVO 9xxx (2025) — alpha, mapped to the 8000 family baseline.
    "9101": "8000",
    # 3-char (NICR series; matched after 4-char miss)
    "660": "600", "670": "600", "675": "600", "680": "600",
    "756": "700", "758": "700", "759": "700",
    "768": "700", "769": "700", "778": "700", "779": "700",
    "788": "700", "789": "700",
    "790": "79x", "791": "79x", "792": "79x", "793": "79x",
    "794": "79x", "795": "79x", "796": "79x", "797": "79x", "799": "79x",
    "920": "900", "930": "900",
    "960": "900-light", "965": "900-light", "970": "900-light",
    "030": "1030",
    "040": "1040",
}
