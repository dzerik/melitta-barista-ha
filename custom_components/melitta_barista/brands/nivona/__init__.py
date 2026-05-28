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
from . import _family_600


# ---------------------------------------------------------------------------
# Per-family recipe tables — selector byte → (key, display name).
# Per-family standard-recipe lists. Each entry is (selector, key, title).
# ---------------------------------------------------------------------------

_RECIPES_600: tuple[RecipeDescriptor, ...] = _family_600.RECIPES

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

# 900-light family reuses the 900 table.
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

# NICR 8107 additionally exposes chilled-brew selectors 8/9/10.
# These recipes are brewed with a distinct HE flags byte (byte[5]=0,
# not 0x01) — see `start_process_nivona` for the chilled-mode wiring.
_RECIPES_8000_CHILLED: tuple[RecipeDescriptor, ...] = _RECIPES_8000 + (
    RecipeDescriptor(8,  "Chilled Espresso",  "espresso"),
    RecipeDescriptor(9,  "Chilled Lungo",     "coffee"),
    RecipeDescriptor(10, "Chilled Americano", "americano"),
)
# Selectors that require the chilled-brew flag byte when sent as HE.
_CHILLED_SELECTORS: frozenset[int] = frozenset({8, 9, 10})

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

_SETTINGS_8000: tuple[SettingDescriptor, ...] = (
    SettingDescriptor(101, "water_hardness", "Water hardness", _HARDNESS_OPTIONS),
    SettingDescriptor(103, "off_rinse", "Off-rinse", _OFF_ON_OPTIONS),
    SettingDescriptor(104, "auto_off", "Auto-off", _AUTO_OFF_8000_OPTIONS),
    SettingDescriptor(105, "coffee_temperature", "Coffee temperature", _TEMP_ON_OFF),
)

# 600 and 700 families share settings; 79X differs (no 103 off-rinse).
_SETTINGS_600: tuple[SettingDescriptor, ...] = _family_600.SETTINGS

# 700-proper: identical to 600.
_SETTINGS_700: tuple[SettingDescriptor, ...] = _SETTINGS_600

# 79X: drops id 103 (off-rinse not exposed on 79X hardware).
_SETTINGS_79X: tuple[SettingDescriptor, ...] = tuple(
    s for s in _SETTINGS_700 if s.setting_id != 103
)

# Legacy alias kept only to avoid breaking imports in older tests /
# docstrings. Points at the 600/700 table.
_SETTINGS_600_700_BASE: tuple[SettingDescriptor, ...] = _SETTINGS_600

# 900 family — 8 settings including tank-lighting accents and
# AutoOn hours/minutes pair (111/112).
_SETTINGS_900: tuple[SettingDescriptor, ...] = (
    SettingDescriptor(102, "water_hardness", "Water hardness", _HARDNESS_OPTIONS),
    SettingDescriptor(103, "off_rinse", "Off-rinse", _OFF_ON_OPTIONS),
    SettingDescriptor(104, "save_energy", "Save energy", _OFF_ON_OPTIONS),
    SettingDescriptor(105, "tank_light", "Tank light", _OFF_ON_OPTIONS),
    SettingDescriptor(106, "tank_light_color", "Tank light color", _TANK_LIGHT_COLOR_900_OPTIONS),
    SettingDescriptor(107, "tank_light_brightness", "Tank light brightness", _TANK_LIGHT_BRIGHTNESS_900_OPTIONS),
    SettingDescriptor(108, "touch_lock", "Touch lock", _OFF_ON_OPTIONS),
    SettingDescriptor(109, "auto_off", "Auto-off", _AUTO_OFF_STANDARD_OPTIONS),
    SettingDescriptor(110, "auto_on_deactivated", "Auto-on deactivated", _OFF_ON_OPTIONS),
    # Hours / minutes for AutoOn time. No options list — numeric.
    SettingDescriptor(111, "auto_on_hours", "Auto-on hours"),
    SettingDescriptor(112, "auto_on_minutes", "Auto-on minutes"),
)

# 900-Light — strip the tank-lighting / touch-lock accents; keep
# save-energy + AutoOn.
_SETTINGS_900_LIGHT: tuple[SettingDescriptor, ...] = (
    SettingDescriptor(102, "water_hardness", "Water hardness", _HARDNESS_OPTIONS),
    SettingDescriptor(104, "save_energy", "Save energy", _OFF_ON_OPTIONS),
    SettingDescriptor(109, "auto_off", "Auto-off", _AUTO_OFF_STANDARD_OPTIONS),
    SettingDescriptor(110, "auto_on_deactivated", "Auto-on deactivated", _OFF_ON_OPTIONS),
    SettingDescriptor(111, "auto_on_hours", "Auto-on hours"),
    SettingDescriptor(112, "auto_on_minutes", "Auto-on minutes"),
)

# 1030 — adds cup-heater / milk-active / direct-start / touch-lock /
# AutoOn pair versus the previous skeleton.
_SETTINGS_1030: tuple[SettingDescriptor, ...] = (
    SettingDescriptor(102, "water_hardness", "Water hardness", _HARDNESS_OPTIONS),
    SettingDescriptor(103, "off_rinse", "Off-rinse", _OFF_ON_OPTIONS),
    SettingDescriptor(104, "cup_heater", "Cup heater", _OFF_ON_OPTIONS),
    SettingDescriptor(105, "milk_products_active", "Milk products active", _OFF_ON_OPTIONS),
    SettingDescriptor(106, "direct_start_deactivated", "Direct-start deactivated", _OFF_ON_OPTIONS),
    SettingDescriptor(107, "touch_lock", "Touch lock", _OFF_ON_OPTIONS),
    SettingDescriptor(109, "auto_off", "Auto-off", _AUTO_OFF_STANDARD_OPTIONS),
    SettingDescriptor(110, "auto_on_deactivated", "Auto-on deactivated", _OFF_ON_OPTIONS),
    SettingDescriptor(111, "auto_on_hours", "Auto-on hours"),
    SettingDescriptor(112, "auto_on_minutes", "Auto-on minutes"),
    SettingDescriptor(113, "profile", "Profile", _PROFILE_STANDARD_OPTIONS),
    SettingDescriptor(114, "coffee_temperature", "Coffee temperature", _TEMPERATURE_OPTIONS),
    SettingDescriptor(115, "water_temperature", "Water temperature", _TEMPERATURE_OPTIONS),
    SettingDescriptor(116, "milk_temperature", "Milk temperature", _MILK_TEMPERATURE_1030_OPTIONS),
)

# 1040 — 1030 superset plus the frothing / power-on extras.
_SETTINGS_1040: tuple[SettingDescriptor, ...] = (
    SettingDescriptor(102, "water_hardness", "Water hardness", _HARDNESS_OPTIONS),
    SettingDescriptor(103, "off_rinse", "Off-rinse", _OFF_ON_OPTIONS),
    SettingDescriptor(104, "cup_heater", "Cup heater", _OFF_ON_OPTIONS),
    SettingDescriptor(105, "milk_products_active", "Milk products active", _OFF_ON_OPTIONS),
    SettingDescriptor(106, "direct_start_deactivated", "Direct-start deactivated", _OFF_ON_OPTIONS),
    SettingDescriptor(107, "touch_lock", "Touch lock", _OFF_ON_OPTIONS),
    SettingDescriptor(109, "auto_off", "Auto-off", _AUTO_OFF_STANDARD_OPTIONS),
    SettingDescriptor(110, "auto_on_deactivated", "Auto-on deactivated", _OFF_ON_OPTIONS),
    SettingDescriptor(111, "auto_on_hours", "Auto-on hours"),
    SettingDescriptor(112, "auto_on_minutes", "Auto-on minutes"),
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
# Each family exposes a different set of HR IDs — stat IDs overlap across
# families but describe different counters (e.g. id 213 is
# "total beverages" on 8000/900 but "single-cup brews" on 1000-family).
# Maintenance gauges 600/601/610/611/620/621/640/641 are universal; only
# the "filter dependency" id varies (642 on 8000, 101 on 900/1000,
# 105 on 700/79X/600).
# ---------------------------------------------------------------------------

_STATS_8000: tuple[StatDescriptor, ...] = (
    _count(200, "espresso", "Espresso"),
    _count(201, "coffee", "Coffee"),
    _count(202, "americano", "Americano"),
    _count(203, "cappuccino", "Cappuccino"),
    _count(204, "caffe_latte", "Caffè latte"),
    _count(205, "macchiato", "Latte macchiato"),
    # id 206 = "Heisse Milch" (Hot milk, not Warm). Slug changed from
    # `warm_milk` in v0.77.0 to align with vendor terminology; entity
    # registry migration in async_migrate_entry v2 → v3 renames
    # existing user entries.
    _count(206, "hot_milk", "Hot milk"),
    _count(207, "hot_water", "Hot water"),
    _count(208, "my_coffee", "My coffee"),
    _count(209, "steam_drinks", "Steam drinks"),
    _count(210, "powder_coffee", "Powder coffee"),
    # Added in v0.77.0 from extended vendor register set:
    _count(211, "grinding_count", "Grinding count", "maintenance"),
    _count(212, "reserve_count", "Reserve count", "maintenance"),
    _count(213, "total_beverages", "Total beverages"),
    _count(214, "clean_coffee_system", "Clean coffee system", "maintenance"),
    _count(215, "clean_frother", "Clean frother", "maintenance"),
    _count(216, "rinse_cycles", "Rinse cycles", "maintenance"),
    _count(219, "filter_changes", "Filter changes", "maintenance"),
    _count(220, "descaling", "Descaling", "maintenance"),
    _count(221, "beverages_via_app", "Beverages via app", "maintenance"),
    _pct(600, "descale_percent", "Descale progress"),
    _flag(601, "descale_warning", "Descale warning"),
    # Added in v0.77.0: "Entkalken_Status" — descale state machine.
    _flag(602, "descale_status", "Descale status"),
    _pct(610, "brew_unit_clean_percent", "Brew unit clean progress"),
    _flag(611, "brew_unit_clean_warning", "Brew unit clean warning"),
    _pct(620, "frother_clean_percent", "Frother clean progress"),
    _flag(621, "frother_clean_warning", "Frother clean warning"),
    # Added in v0.77.0: "SpuelenAufsch_Notwendig" — frother-rinse needed.
    _flag(630, "frother_rinse_needed", "Frother rinse needed"),
    _pct(640, "filter_percent", "Filter progress"),
    _flag(641, "filter_warning", "Filter warning"),
    _flag(642, "filter_dependency", "Filter dependency"),
)

# 700 family — recipe counters 200..208 only; no 213-221 cumulative
# counters on this hardware. Maintenance gauges universal; 105 is the
# filter dependency.
_STATS_700: tuple[StatDescriptor, ...] = (
    _count(200, "espresso", "Espresso"),
    _count(201, "cream", "Cream"),                          # "Creme" (700-proper)
    _count(202, "lungo", "Lungo"),
    _count(203, "americano", "Americano"),
    _count(204, "cappuccino", "Cappuccino"),
    _count(205, "latte_macchiato", "Latte macchiato"),
    _count(206, "milk", "Milk"),
    _count(207, "hot_water", "Hot water"),
    _count(208, "my_coffee", "My coffee"),
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

# 79X family — like 700 but id 201 is "Kaffee" (not "Creme"),
# selector 204 (Cappuccino) is absent, and there are no cumulative
# counters (213-221) — recipe counters only.
_STATS_79X: tuple[StatDescriptor, ...] = (
    _count(200, "espresso", "Espresso"),
    _count(201, "coffee", "Coffee"),                        # "Kaffee" on 79X
    _count(202, "lungo", "Lungo"),
    _count(203, "americano", "Americano"),
    # selector 204 absent on 79X hardware
    _count(205, "latte_macchiato", "Latte macchiato"),
    _count(206, "milk", "Milk"),
    _count(207, "hot_water", "Hot water"),
    _count(208, "my_coffee", "My coffee"),
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

_STATS_600: tuple[StatDescriptor, ...] = _family_600.STATS

# 900 / 900-Light family — 21 counters (recipe 200..208 + cumulative
# 209..221), maintenance gauges, 101 dependency.
_STATS_900: tuple[StatDescriptor, ...] = (
    _count(200, "espresso", "Espresso"),
    _count(201, "coffee", "Coffee"),
    _count(202, "americano", "Americano"),
    _count(203, "cappuccino", "Cappuccino"),
    _count(204, "caffe_latte", "Caffè latte"),
    _count(205, "macchiato", "Latte macchiato"),
    _count(206, "milk", "Milk"),
    _count(207, "hot_water", "Hot water"),
    _count(208, "my_coffee", "My coffee"),
    _count(209, "steam_drinks", "Steam drinks"),
    _count(210, "powder_coffee", "Powder coffee"),
    _count(211, "single_cup", "Single cup brews"),
    _count(212, "double_cup", "Double cup brews"),
    _count(213, "total_beverages", "Total beverages"),
    _count(214, "clean_coffee_system", "Clean coffee system", "maintenance"),
    _count(215, "clean_frother", "Clean frother", "maintenance"),
    _count(216, "rinse_cycles", "Rinse cycles", "maintenance"),
    _count(217, "rinse_frother", "Rinse frother", "maintenance"),
    _count(218, "rinse_filter", "Rinse filter", "maintenance"),
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
    _flag(101, "filter_dependency", "Filter dependency"),
)

# 900-Light shares the same stats table as 900 (same hardware counter
# set on both variants).
_STATS_900_LIGHT: tuple[StatDescriptor, ...] = _STATS_900

# 1030 family — 24 counters, distinctly numbered from 8000/900:
# id 213 is "single cup" (not Total), 215 is Total, 222 is Descaling.
# Filter dependency is 101.
_STATS_1030: tuple[StatDescriptor, ...] = (
    _count(200, "espresso", "Espresso"),
    # id 201 = "Coffee" counter, not Lungo. We used to label it `lungo`
    # — corrected in v0.77.0; entity registry migration renames
    # existing user entries.
    _count(201, "coffee", "Coffee"),
    _count(202, "americano", "Americano"),
    _count(203, "cappuccino", "Cappuccino"),
    _count(204, "caffe_latte", "Caffè latte"),
    _count(205, "macchiato", "Latte macchiato"),
    _count(206, "warm_milk", "Warm milk"),
    _count(207, "hot_milk", "Hot milk"),
    _count(208, "milk_foam", "Milk foam"),
    _count(209, "hot_water", "Hot water"),
    # Added in v0.77.0: id 210 is the MyCoffee counter — was missing
    # entirely on 1030/1040.
    _count(210, "my_coffee", "My coffee"),
    _count(211, "steam_drinks", "Steam drinks"),
    _count(212, "powder_coffee", "Powder coffee"),
    _count(213, "single_cup", "Single cup brews"),
    _count(214, "double_cup", "Double cup brews"),
    _count(215, "total_beverages", "Total beverages"),
    _count(216, "clean_coffee_system", "Clean coffee system", "maintenance"),
    _count(217, "clean_frother", "Clean frother", "maintenance"),
    _count(218, "rinse_cycles", "Rinse cycles", "maintenance"),
    _count(219, "rinse_frother", "Rinse frother", "maintenance"),
    _count(220, "rinse_filter", "Rinse filter", "maintenance"),
    _count(221, "filter_changes", "Filter changes", "maintenance"),
    _count(222, "descaling", "Descaling", "maintenance"),
    _count(223, "beverages_via_app", "Beverages via app", "maintenance"),
    # id 224 hasn't been seen in any vendor reference data we trust —
    # origin unclear. Kept enabled until we get a field-confirmed read
    # from a real NICR 1030/1040; "(experimental)" in the title flags
    # the uncertainty.
    _count(224, "beverages_via_kanne", "Beverages via Kanne (experimental)", "maintenance"),
    _pct(600, "descale_percent", "Descale progress"),
    _flag(601, "descale_warning", "Descale warning"),
    _pct(610, "brew_unit_clean_percent", "Brew unit clean progress"),
    _flag(611, "brew_unit_clean_warning", "Brew unit clean warning"),
    _pct(620, "frother_clean_percent", "Frother clean progress"),
    _flag(621, "frother_clean_warning", "Frother clean warning"),
    _pct(640, "filter_percent", "Filter progress"),
    _flag(641, "filter_warning", "Filter warning"),
    _flag(101, "filter_dependency", "Filter dependency"),
)

# 1040 family — identical to 1030 minus selector 207 (HeisseMilch):
# 1040 doesn't expose a separate hot-milk counter.
_STATS_1040: tuple[StatDescriptor, ...] = tuple(
    s for s in _STATS_1030 if s.stat_id != 207
)


# ---------------------------------------------------------------------------
# Per-family standard-recipe layouts (resolveStandardRecipeLayout upstream).
# Maps family_key → RecipeFieldLayout with byte-offsets inside
# `RECIPE_BASE_REGISTER + selector*RECIPE_SLOT_STRIDE`.
# ---------------------------------------------------------------------------

_STANDARD_RECIPE_LAYOUTS: dict[str, RecipeFieldLayout] = {
    "600": _family_600.STANDARD_LAYOUT,
    "700": RecipeFieldLayout(
        family_key="700",
        strength_offset=1, profile_offset=2, temperature_offset=3,
        two_cups_offset=4, coffee_amount_offset=5, water_amount_offset=6,
        milk_amount_offset=7, milk_foam_amount_offset=8,
    ),
    "79x": RecipeFieldLayout(
        family_key="79x",
        strength_offset=1, profile_offset=2, temperature_offset=3,
        two_cups_offset=4, coffee_amount_offset=5, water_amount_offset=6,
        milk_amount_offset=7, milk_foam_amount_offset=8,
    ),
    "900": RecipeFieldLayout(
        family_key="900",
        strength_offset=1, profile_offset=2, preparation_offset=3,
        two_cups_offset=4,
        coffee_temperature_offset=5, water_temperature_offset=6,
        milk_temperature_offset=7, milk_foam_temperature_offset=8,
        coffee_amount_offset=9, water_amount_offset=10,
        milk_amount_offset=11, milk_foam_amount_offset=12,
        overall_temperature_offset=13,
        # fluid_write_scale_10: previously set True here; the upstream
        # RE flagged it but the observed machine behaviour does not
        # apply a ×10 scaling to fluid amounts in HW writes. Reverted
        # to the default of False until a live trace confirms a
        # family that actually needs it.
        fluid_write_scale_10=False,
    ),
    "900-light": RecipeFieldLayout(
        family_key="900-light",
        strength_offset=1, profile_offset=2, preparation_offset=3,
        two_cups_offset=4,
        coffee_temperature_offset=5, water_temperature_offset=6,
        milk_temperature_offset=7, milk_foam_temperature_offset=8,
        coffee_amount_offset=9, water_amount_offset=10,
        milk_amount_offset=11, milk_foam_amount_offset=12,
        overall_temperature_offset=13,
    ),
    "1030": RecipeFieldLayout(
        family_key="1030",
        strength_offset=1, profile_offset=2, preparation_offset=3,
        two_cups_offset=4,
        coffee_temperature_offset=5, water_temperature_offset=6,
        milk_temperature_offset=7, milk_foam_temperature_offset=8,
        coffee_amount_offset=9, water_amount_offset=10,
        milk_amount_offset=11, milk_foam_amount_offset=12,
    ),
    "1040": RecipeFieldLayout(
        family_key="1040",
        strength_offset=1, profile_offset=2, preparation_offset=3,
        two_cups_offset=4,
        coffee_temperature_offset=5, water_temperature_offset=6,
        milk_temperature_offset=7, milk_foam_temperature_offset=8,
        coffee_amount_offset=9, water_amount_offset=10,
        milk_amount_offset=11, milk_foam_amount_offset=12,
    ),
    "8000": RecipeFieldLayout(
        family_key="8000",
        strength_offset=1, profile_offset=2, temperature_offset=3,
        two_cups_offset=4, coffee_amount_offset=5, water_amount_offset=6,
        milk_amount_offset=7, milk_foam_amount_offset=8,
    ),
}


# ---------------------------------------------------------------------------
# Per-family MyCoffee slot layouts (resolveMyCoffeeLayout upstream).
# Offsets inside `MY_COFFEE_BASE_REGISTER + slot*MY_COFFEE_SLOT_STRIDE`.
# ---------------------------------------------------------------------------

_MYCOFFEE_LAYOUTS: dict[str, RecipeFieldLayout] = {
    "600": _family_600.MYCOFFEE_LAYOUT,
    "700": RecipeFieldLayout(
        family_key="700",
        enabled_offset=0, icon_offset=1, name_offset=2, type_offset=3,
        strength_offset=4, profile_offset=5, temperature_offset=6,
        two_cups_offset=7, coffee_amount_offset=8, water_amount_offset=9,
        milk_amount_offset=10, milk_foam_amount_offset=11,
    ),
    "79x": RecipeFieldLayout(
        family_key="79x",
        enabled_offset=0, icon_offset=1, name_offset=2, type_offset=3,
        strength_offset=4, profile_offset=5, temperature_offset=6,
        two_cups_offset=7, coffee_amount_offset=8, water_amount_offset=9,
        milk_amount_offset=10, milk_foam_amount_offset=11,
    ),
    "900": RecipeFieldLayout(
        family_key="900",
        enabled_offset=0, icon_offset=1, name_offset=2, type_offset=3,
        strength_offset=4, profile_offset=5, preparation_offset=6,
        two_cups_offset=7,
        coffee_temperature_offset=8, water_temperature_offset=9,
        milk_temperature_offset=10, milk_foam_temperature_offset=11,
        coffee_amount_offset=12, water_amount_offset=13,
        milk_amount_offset=14, milk_foam_amount_offset=15,
        overall_temperature_offset=16,
        # fluid_write_scale_10: previously set True here; the upstream
        # RE flagged it but the observed machine behaviour does not
        # apply a ×10 scaling to fluid amounts in HW writes. Reverted
        # to the default of False until a live trace confirms a
        # family that actually needs it.
        fluid_write_scale_10=False,
    ),
    "900-light": RecipeFieldLayout(
        family_key="900-light",
        enabled_offset=0, icon_offset=1, name_offset=2, type_offset=3,
        strength_offset=4, profile_offset=5, preparation_offset=6,
        two_cups_offset=7,
        coffee_temperature_offset=8, water_temperature_offset=9,
        milk_temperature_offset=10, milk_foam_temperature_offset=11,
        coffee_amount_offset=12, water_amount_offset=13,
        milk_amount_offset=14, milk_foam_amount_offset=15,
        overall_temperature_offset=16,
    ),
    "1030": RecipeFieldLayout(
        family_key="1030",
        enabled_offset=0, icon_offset=1, name_offset=2, type_offset=3,
        strength_offset=4, profile_offset=5, preparation_offset=6,
        two_cups_offset=7,
        coffee_temperature_offset=8, water_temperature_offset=9,
        milk_temperature_offset=10, milk_foam_temperature_offset=11,
        coffee_amount_offset=12, water_amount_offset=13,
        milk_amount_offset=14, milk_foam_amount_offset=15,
    ),
    "1040": RecipeFieldLayout(
        family_key="1040",
        enabled_offset=0, icon_offset=1, name_offset=2, type_offset=3,
        strength_offset=4, profile_offset=5, preparation_offset=6,
        two_cups_offset=7,
        coffee_temperature_offset=8, water_temperature_offset=9,
        milk_temperature_offset=10, milk_foam_temperature_offset=11,
        coffee_amount_offset=12, water_amount_offset=13,
        milk_amount_offset=14, milk_foam_amount_offset=15,
    ),
    "8000": RecipeFieldLayout(
        family_key="8000",
        enabled_offset=0, name_offset=2, icon_offset=3, type_offset=3,
        strength_offset=4, profile_offset=5, temperature_offset=6,
        two_cups_offset=7, coffee_amount_offset=8, water_amount_offset=9,
        milk_amount_offset=10, milk_foam_amount_offset=11,
    ),
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
        supports_stats=True,
        my_coffee_slots=4,
        strength_levels=5,
        has_aroma_balance=False,
        brew_command_mode=0x0B,
        fluid_scale_factor=10,
        # NICR 930 leaves MOVE_CUP_TO_FROTHER (11) set after a brew
        # until the next status frame; tolerate so subsequent brews
        # are not falsely blocked.
        tolerated_brew_manipulations=(11,),
        recipes=_RECIPES_900,
        settings=_FAMILY_SETTINGS['900'],
        stats=_FAMILY_STATS['900'],
    ),
    "900-light": MachineCapabilities(
        family_key="900-light",
        model_name="Nivona NICR 9xx Light",
        supports_recipe_writes=False,
        supports_stats=True,
        my_coffee_slots=4,
        strength_levels=3,
        brew_command_mode=0x0B,
        tolerated_brew_manipulations=(11,),
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
