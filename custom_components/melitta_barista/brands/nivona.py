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

from dataclasses import replace

from .base import MachineCapabilities, RecipeDescriptor, SettingDescriptor, StatDescriptor


# ---------------------------------------------------------------------------
# Per-family recipe tables — selector byte → (key, display name).
# Upstream: src/nivona.cpp:183-258. Each entry is (selector, key, title).
# ---------------------------------------------------------------------------

_RECIPES_600: tuple[RecipeDescriptor, ...] = (
    RecipeDescriptor(0, "Espresso", "espresso"),
    RecipeDescriptor(1, "Coffee", "coffee"),
    RecipeDescriptor(2, "Americano", "americano"),
    RecipeDescriptor(3, "Cappuccino", "cappuccino"),
    RecipeDescriptor(4, "Frothy Milk", "milk_drink"),
    RecipeDescriptor(5, "Hot Water", "water"),
)

_RECIPES_700: tuple[RecipeDescriptor, ...] = (
    RecipeDescriptor(0, "Espresso", "espresso"),
    RecipeDescriptor(1, "Cream", "coffee"),
    RecipeDescriptor(2, "Lungo", "coffee"),
    RecipeDescriptor(3, "Americano", "americano"),
    RecipeDescriptor(4, "Cappuccino", "cappuccino"),
    RecipeDescriptor(5, "Latte Macchiato", "milk_drink"),
    RecipeDescriptor(6, "Milk", "milk_drink"),
    RecipeDescriptor(7, "Hot Water", "water"),
)

_RECIPES_79X: tuple[RecipeDescriptor, ...] = (
    RecipeDescriptor(0, "Espresso", "espresso"),
    RecipeDescriptor(1, "Coffee", "coffee"),
    RecipeDescriptor(2, "Americano", "americano"),
    RecipeDescriptor(3, "Cappuccino", "cappuccino"),
    RecipeDescriptor(5, "Latte Macchiato", "milk_drink"),
    RecipeDescriptor(6, "Milk", "milk_drink"),
    RecipeDescriptor(7, "Hot Water", "water"),
)

_RECIPES_900: tuple[RecipeDescriptor, ...] = (
    RecipeDescriptor(0, "Espresso", "espresso"),
    RecipeDescriptor(1, "Coffee", "coffee"),
    RecipeDescriptor(2, "Americano", "americano"),
    RecipeDescriptor(3, "Cappuccino", "cappuccino"),
    RecipeDescriptor(4, "Caffè Latte", "milk_drink"),
    RecipeDescriptor(5, "Latte Macchiato", "milk_drink"),
    RecipeDescriptor(6, "Hot Milk", "milk_drink"),
    RecipeDescriptor(7, "Hot Water", "water"),
)

# 900-light family reuses the 900 table upstream (src/nivona.cpp near line 947).
_RECIPES_900_LIGHT: tuple[RecipeDescriptor, ...] = _RECIPES_900

_RECIPES_1030: tuple[RecipeDescriptor, ...] = (
    RecipeDescriptor(0, "Espresso", "espresso"),
    RecipeDescriptor(1, "Coffee", "coffee"),
    RecipeDescriptor(2, "Americano", "americano"),
    RecipeDescriptor(3, "Cappuccino", "cappuccino"),
    RecipeDescriptor(4, "Caffè Latte", "milk_drink"),
    RecipeDescriptor(5, "Latte Macchiato", "milk_drink"),
    RecipeDescriptor(6, "Hot Water", "water"),
    RecipeDescriptor(7, "Warm Milk", "milk_drink"),
    RecipeDescriptor(8, "Hot Milk", "milk_drink"),
    RecipeDescriptor(9, "Frothy Milk", "milk_drink"),
)

_RECIPES_1040: tuple[RecipeDescriptor, ...] = (
    RecipeDescriptor(0, "Espresso", "espresso"),
    RecipeDescriptor(1, "Coffee", "coffee"),
    RecipeDescriptor(2, "Americano", "americano"),
    RecipeDescriptor(3, "Cappuccino", "cappuccino"),
    RecipeDescriptor(4, "Caffè Latte", "milk_drink"),
    RecipeDescriptor(5, "Latte Macchiato", "milk_drink"),
    RecipeDescriptor(6, "Hot Water", "water"),
    RecipeDescriptor(7, "Warm Milk", "milk_drink"),
    RecipeDescriptor(8, "Frothy Milk", "milk_drink"),
)

_RECIPES_8000: tuple[RecipeDescriptor, ...] = (
    RecipeDescriptor(0, "Espresso", "espresso"),
    RecipeDescriptor(1, "Coffee", "coffee"),
    RecipeDescriptor(2, "Americano", "americano"),
    RecipeDescriptor(3, "Cappuccino", "cappuccino"),
    RecipeDescriptor(4, "Caffè Latte", "milk_drink"),
    RecipeDescriptor(5, "Latte Macchiato", "milk_drink"),
    RecipeDescriptor(6, "Milk", "milk_drink"),
    RecipeDescriptor(7, "Hot Water", "water"),
)

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

# ---------------------------------------------------------------------------
# Setting option enums (value_code → human label). Ported verbatim from
# upstream src/nivona.cpp lines 35-128.
# ---------------------------------------------------------------------------

_HARDNESS_OPTIONS = ((0x0000, "soft"), (0x0001, "medium"), (0x0002, "hard"), (0x0003, "very hard"))
_OFF_ON_OPTIONS = ((0x0000, "off"), (0x0001, "on"))
_AUTO_OFF_8000_OPTIONS = (
    (0x0000, "10 min"), (0x0001, "30 min"), (0x0002, "1 h"),
    (0x0003, "2 h"), (0x0004, "4 h"), (0x0005, "6 h"),
    (0x0006, "8 h"), (0x0007, "10 h"), (0x0008, "12 h"),
    (0x0009, "14 h"), (0x0010, "16 h"),
)
_AUTO_OFF_STANDARD_OPTIONS = (
    (0x0000, "10 min"), (0x0001, "30 min"), (0x0002, "1 h"),
    (0x0003, "2 h"), (0x0004, "4 h"), (0x0005, "6 h"),
    (0x0006, "8 h"), (0x0007, "10 h"), (0x0008, "12 h"),
    (0x0009, "off"),
)
_TEMP_ON_OFF = ((0x0000, "off"), (0x0001, "on"))
_TEMPERATURE_OPTIONS = ((0x0000, "normal"), (0x0001, "high"), (0x0002, "max"), (0x0003, "individual"))
_PROFILE_STANDARD_OPTIONS = (
    (0x0000, "dynamic"), (0x0001, "constant"),
    (0x0002, "intense"), (0x0003, "individual"),
)
_PROFILE_1040_OPTIONS = (
    (0x0000, "dynamic"), (0x0001, "constant"),
    (0x0002, "intense"), (0x0003, "quick"), (0x0004, "individual"),
)
_MILK_TEMPERATURE_1030_OPTIONS = (
    (0x0000, "high"), (0x0001, "max"), (0x0002, "individual"),
)
_MILK_TEMPERATURE_1040_OPTIONS = (
    (0x0000, "normal"), (0x0001, "high"), (0x0002, "hot"),
    (0x0003, "max"), (0x0004, "individual"),
)
_MILK_FOAM_TEMPERATURE_1040_OPTIONS = (
    (0x0000, "warm"), (0x0001, "max"), (0x0002, "individual"),
)
_POWER_ON_FROTHER_TIME_1040_OPTIONS = (
    (0x0000, "10 min"), (0x0001, "20 min"),
    (0x0002, "30 min"), (0x0003, "40 min"),
)


# ---------------------------------------------------------------------------
# Per-family settings register tables (HR-readable, HW-writable).
# Ported from SETTINGS_*_PROBES in upstream src/nivona.cpp:128-177.
# IDs are Nivona-specific and do NOT overlap with Melitta's setting IDs.
# ---------------------------------------------------------------------------

_SETTINGS_8000: tuple[SettingDescriptor, ...] = (
    SettingDescriptor(101, "water_hardness", "Water hardness", _HARDNESS_OPTIONS),
    SettingDescriptor(103, "off_rinse", "Off-rinse", _OFF_ON_OPTIONS),
    SettingDescriptor(104, "auto_off", "Auto-off", _AUTO_OFF_8000_OPTIONS),
    SettingDescriptor(105, "coffee_temperature", "Coffee temperature", _TEMP_ON_OFF),
)

_SETTINGS_600_700_BASE: tuple[SettingDescriptor, ...] = (
    SettingDescriptor(101, "water_hardness", "Water hardness", _HARDNESS_OPTIONS),
    SettingDescriptor(102, "temperature", "Temperature", _TEMPERATURE_OPTIONS),
    SettingDescriptor(103, "off_rinse", "Off-rinse", _OFF_ON_OPTIONS),
    SettingDescriptor(104, "auto_off", "Auto-off", _AUTO_OFF_STANDARD_OPTIONS),
    SettingDescriptor(106, "profile", "Profile", _PROFILE_STANDARD_OPTIONS),
)

_SETTINGS_900: tuple[SettingDescriptor, ...] = (
    SettingDescriptor(102, "water_hardness", "Water hardness", _HARDNESS_OPTIONS),
    SettingDescriptor(103, "off_rinse", "Off-rinse", _OFF_ON_OPTIONS),
    SettingDescriptor(109, "auto_off", "Auto-off", _AUTO_OFF_STANDARD_OPTIONS),
)

_SETTINGS_900_LIGHT: tuple[SettingDescriptor, ...] = (
    SettingDescriptor(102, "water_hardness", "Water hardness", _HARDNESS_OPTIONS),
    SettingDescriptor(109, "auto_off", "Auto-off", _AUTO_OFF_STANDARD_OPTIONS),
)

_SETTINGS_1030: tuple[SettingDescriptor, ...] = (
    SettingDescriptor(102, "water_hardness", "Water hardness", _HARDNESS_OPTIONS),
    SettingDescriptor(103, "off_rinse", "Off-rinse", _OFF_ON_OPTIONS),
    SettingDescriptor(109, "auto_off", "Auto-off", _AUTO_OFF_STANDARD_OPTIONS),
    SettingDescriptor(113, "profile", "Profile", _PROFILE_STANDARD_OPTIONS),
    SettingDescriptor(114, "coffee_temperature", "Coffee temperature", _TEMPERATURE_OPTIONS),
    SettingDescriptor(115, "water_temperature", "Water temperature", _TEMPERATURE_OPTIONS),
    SettingDescriptor(116, "milk_temperature", "Milk temperature", _MILK_TEMPERATURE_1030_OPTIONS),
)

_SETTINGS_1040: tuple[SettingDescriptor, ...] = (
    SettingDescriptor(102, "water_hardness", "Water hardness", _HARDNESS_OPTIONS),
    SettingDescriptor(103, "off_rinse", "Off-rinse", _OFF_ON_OPTIONS),
    SettingDescriptor(109, "auto_off", "Auto-off", _AUTO_OFF_STANDARD_OPTIONS),
    SettingDescriptor(113, "profile", "Profile", _PROFILE_1040_OPTIONS),
    SettingDescriptor(114, "coffee_temperature", "Coffee temperature", _TEMPERATURE_OPTIONS),
    SettingDescriptor(115, "water_temperature", "Water temperature", _TEMPERATURE_OPTIONS),
    SettingDescriptor(116, "milk_temperature", "Milk temperature", _MILK_TEMPERATURE_1040_OPTIONS),
    SettingDescriptor(117, "milk_foam_temperature", "Milk foam temperature", _MILK_FOAM_TEMPERATURE_1040_OPTIONS),
    SettingDescriptor(118, "power_on_rinse", "Power-on rinse", _OFF_ON_OPTIONS),
    SettingDescriptor(119, "power_on_frother_time", "Power-on frother time", _POWER_ON_FROTHER_TIME_1040_OPTIONS),
)


# ---------------------------------------------------------------------------
# Per-family stats register tables (HR-readable counters / percentages).
# Ported from STATS_*_PROBES in upstream src/nivona.cpp:429-500.
# Only families with supportsStats=true expose stats (600/900/900-light
# /1030/1040 have empty tables upstream).
# ---------------------------------------------------------------------------

def _count(stat_id: int, key: str, title: str, section: str = "beverages") -> StatDescriptor:
    return StatDescriptor(
        stat_id=stat_id, key=key, title=title,
        unit="count", is_diagnostic=(section == "maintenance"),
    )


def _pct(stat_id: int, key: str, title: str) -> StatDescriptor:
    return StatDescriptor(
        stat_id=stat_id, key=key, title=title,
        unit="%", is_diagnostic=True,
    )


def _flag(stat_id: int, key: str, title: str) -> StatDescriptor:
    return StatDescriptor(
        stat_id=stat_id, key=key, title=title,
        unit=None, is_diagnostic=True,
    )


_STATS_8000: tuple[StatDescriptor, ...] = (
    _count(200, "espresso", "Espresso"),
    _count(201, "coffee", "Coffee"),
    _count(202, "americano", "Americano"),
    _count(203, "cappuccino", "Cappuccino"),
    _count(204, "caffe_latte", "Caffè latte"),
    _count(205, "macchiato", "Latte macchiato"),
    _count(206, "warm_milk", "Warm milk"),
    _count(207, "hot_water", "Hot water"),
    _count(208, "my_coffee", "My coffee"),
    _count(209, "steam_drinks", "Steam drinks"),
    _count(210, "powder_coffee", "Powder coffee"),
    _count(213, "total_beverages", "Total beverages"),
    _count(214, "clean_coffee_system", "Clean coffee system", "maintenance"),
    _count(215, "clean_frother", "Clean frother", "maintenance"),
    _count(216, "rinse_cycles", "Rinse cycles", "maintenance"),
    _count(219, "filter_changes", "Filter changes", "maintenance"),
    _count(220, "descaling", "Descaling", "maintenance"),
    _count(221, "beverages_via_app", "Beverages via app", "maintenance"),
    _pct(600, "descale_percent", "Descale progress"),
    _flag(601, "descale_warning", "Descale warning"),
    _pct(610, "brew_unit_clean_percent", "Brew unit clean progress"),
    _flag(611, "brew_unit_clean_warning", "Brew unit clean warning"),
    _pct(620, "frother_clean_percent", "Frother clean progress"),
    _flag(621, "frother_clean_warning", "Frother clean warning"),
    _pct(640, "filter_percent", "Filter progress"),
    _flag(641, "filter_warning", "Filter warning"),
    _flag(642, "filter_dependency", "Filter dependency"),
)

_STATS_700: tuple[StatDescriptor, ...] = (
    _count(200, "espresso", "Espresso"),
    _count(201, "cream", "Cream"),
    _count(202, "lungo", "Lungo"),
    _count(203, "americano", "Americano"),
    _count(204, "cappuccino", "Cappuccino"),
    _count(205, "latte_macchiato", "Latte macchiato"),
    _count(206, "milk", "Milk"),
    _count(207, "hot_water", "Hot water"),
    _count(208, "my_coffee", "My coffee"),
    _count(213, "total_beverages", "Total beverages"),
    _count(214, "clean_brewing_unit", "Cleaning brewing unit", "maintenance"),
    _count(215, "clean_frother", "Cleaning frother", "maintenance"),
    _count(216, "rinse_cycles", "Rinse cycles", "maintenance"),
    _count(219, "filter_changes", "Filter changes", "maintenance"),
    _count(220, "descaling", "Descaling", "maintenance"),
    _count(221, "beverages_via_app", "Beverages via app", "maintenance"),
    _pct(600, "descale_percent", "Descaling progress"),
    _flag(601, "descale_warning", "Descaling warning"),
    _pct(610, "brew_unit_clean_percent", "Brewing unit cleaning progress"),
    _flag(611, "brew_unit_clean_warning", "Brewing unit cleaning warning"),
    _pct(620, "frother_clean_percent", "Frother cleaning progress"),
    _flag(621, "frother_clean_warning", "Frother cleaning warning"),
    _pct(640, "filter_percent", "Filter progress"),
    _flag(641, "filter_warning", "Filter warning"),
    _flag(105, "filter_dependency", "Filter dependency"),
)

_STATS_79X: tuple[StatDescriptor, ...] = (
    _count(200, "espresso", "Espresso"),
    _count(201, "coffee", "Coffee"),
    _count(202, "americano", "Americano"),
    _count(203, "cappuccino", "Cappuccino"),
    _count(205, "latte_macchiato", "Latte macchiato"),
    _count(206, "milk", "Milk"),
    _count(207, "hot_water", "Hot water"),
    _count(208, "my_coffee", "My coffee"),
    _count(213, "total_beverages", "Total beverages"),
    _flag(105, "filter_dependency", "Filter dependency"),
)


# ---------------------------------------------------------------------------
# Per-model overrides (MODEL_RULES from upstream src/nivona.cpp:278-311).
# Key: 3- or 4-char serial prefix; value: (my_coffee_slots, strength_levels).
# Other fields are taken from the family default.
# ---------------------------------------------------------------------------

_MODEL_OVERRIDES: dict[str, dict] = {
    # 4-char NIVO 8xxx
    "8101": {"my_coffee_slots": 9, "strength_levels": 5},
    "8103": {"my_coffee_slots": 9, "strength_levels": 5},
    "8107": {"my_coffee_slots": 9, "strength_levels": 5},
    # NICR 600
    "660": {"my_coffee_slots": 1, "strength_levels": 3},
    "670": {"my_coffee_slots": 5, "strength_levels": 3},
    "675": {"my_coffee_slots": 5, "strength_levels": 3},
    "680": {"my_coffee_slots": 5, "strength_levels": 3},
    # NICR 700 (single-slot variants)
    "756": {"my_coffee_slots": 1, "strength_levels": 3},
    "758": {"my_coffee_slots": 1, "strength_levels": 3},
    "759": {"my_coffee_slots": 1, "strength_levels": 3},
    "768": {"my_coffee_slots": 1, "strength_levels": 3},
    "769": {"my_coffee_slots": 1, "strength_levels": 3},
    "778": {"my_coffee_slots": 1, "strength_levels": 3},
    "779": {"my_coffee_slots": 1, "strength_levels": 3},
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


# ---------------------------------------------------------------------------
# Register bases (for future read/write recipe support — not exposed yet).
# Upstream: standard recipes at 10000 + selector*100; MyCoffee at 20000 + slot*100.
# ---------------------------------------------------------------------------

RECIPE_BASE_REGISTER = 10000
RECIPE_SLOT_STRIDE = 100
MY_COFFEE_BASE_REGISTER = 20000
MY_COFFEE_SLOT_STRIDE = 100


# ---------------------------------------------------------------------------
# Per-family settings + stats dispatch
# ---------------------------------------------------------------------------

_FAMILY_SETTINGS: dict[str, tuple[SettingDescriptor, ...]] = {
    "600": _SETTINGS_600_700_BASE,
    "700": _SETTINGS_600_700_BASE,
    "79x": _SETTINGS_600_700_BASE,
    "900": _SETTINGS_900,
    "900-light": _SETTINGS_900_LIGHT,
    "1030": _SETTINGS_1030,
    "1040": _SETTINGS_1040,
    "8000": _SETTINGS_8000,
}

_FAMILY_STATS: dict[str, tuple[StatDescriptor, ...]] = {
    "600": (),
    "700": _STATS_700,
    "79x": _STATS_79X,
    "900": (),
    "900-light": (),
    "1030": (),
    "1040": (),
    "8000": _STATS_8000,
}


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
        recipes=_RECIPES_600,
        settings=_FAMILY_SETTINGS['600'],
        stats=_FAMILY_STATS['600'],
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
        recipes=_RECIPES_700,
        settings=_FAMILY_SETTINGS['700'],
        stats=_FAMILY_STATS['700'],
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
        recipes=_RECIPES_79X,
        settings=_FAMILY_SETTINGS['79x'],
        stats=_FAMILY_STATS['79x'],
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
        recipes=_RECIPES_900,
        settings=_FAMILY_SETTINGS['900'],
        stats=_FAMILY_STATS['900'],
    ),
    "900-light": MachineCapabilities(
        family_key="900-light",
        model_name="Nivona NICR 9xx Light",
        supports_recipe_writes=False,
        supports_stats=False,
        my_coffee_slots=4,
        strength_levels=3,
        brew_command_mode=0x0B,
        recipes=_RECIPES_900_LIGHT,
        settings=_FAMILY_SETTINGS['900-light'],
        stats=_FAMILY_STATS['900-light'],
    ),
    "1030": MachineCapabilities(
        family_key="1030",
        model_name="Nivona NICR 1030",
        supports_recipe_writes=False,
        supports_stats=False,
        my_coffee_slots=4,
        strength_levels=5,
        brew_command_mode=0x0B,
        recipes=_RECIPES_1030,
        settings=_FAMILY_SETTINGS['1030'],
        stats=_FAMILY_STATS['1030'],
    ),
    "1040": MachineCapabilities(
        family_key="1040",
        model_name="Nivona NICR 1040",
        supports_recipe_writes=False,
        supports_stats=False,
        my_coffee_slots=4,
        strength_levels=5,
        brew_command_mode=0x0B,
        recipes=_RECIPES_1040,
        settings=_FAMILY_SETTINGS['1040'],
        stats=_FAMILY_STATS['1040'],
    ),
    "8000": MachineCapabilities(
        family_key="8000",
        model_name="Nivona NIVO 8xxx",
        supports_recipe_writes=False,
        supports_stats=True,
        my_coffee_slots=4,
        strength_levels=5,
        brew_command_mode=0x04,    # NIVO8000 uses different brew opcode byte
        recipes=_RECIPES_8000,
        settings=_FAMILY_SETTINGS['8000'],
        stats=_FAMILY_STATS['8000'],
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
    "030": "1030",
    "040": "1040",
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
        override: dict | None = None
        if len(serial) >= 4:
            override = _MODEL_OVERRIDES.get(serial[:4])
        if override is None and len(serial) >= 3:
            override = _MODEL_OVERRIDES.get(serial[:3])
        if override is None:
            return caps
        return replace(caps, **override)
