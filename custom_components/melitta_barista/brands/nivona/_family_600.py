"""NICR 6xx family — selectors, settings, stats, layouts, capabilities.

The 6xx hardware exposes a 6-recipe palette (no separate macchiato /
caffè latte slots), the smallest setting set (5 entries, same as 700),
and gaps at counter ids 202 / 205 because those recipes don't exist.
Brews use ``brew_command_mode = 0x0B`` like every non-8000 family.
"""

from __future__ import annotations

from ..base import (
    MachineCapabilities,
    RecipeDescriptor,
    RecipeFieldLayout,
    SettingDescriptor,
    StatDescriptor,
)
from ._options import (
    _AUTO_OFF_STANDARD_OPTIONS,
    _HARDNESS_OPTIONS,
    _OFF_ON_OPTIONS,
    _PROFILE_STANDARD_OPTIONS,
    _TEMPERATURE_OPTIONS,
)
from ._stats_helpers import _count, _flag, _pct

RECIPES: tuple[RecipeDescriptor, ...] = (
    RecipeDescriptor(0, "Espresso", "espresso"),
    RecipeDescriptor(1, "Coffee", "coffee"),
    RecipeDescriptor(2, "Americano", "americano"),
    RecipeDescriptor(3, "Cappuccino", "cappuccino"),
    RecipeDescriptor(4, "Frothy Milk", "milk_drink"),
    RecipeDescriptor(5, "Hot Water", "water"),
)


# 600 and 700 families share most settings — 600 carries the same 5
# entries the 700 family does. NICR758 additionally has no 106 profile
# (handled per-model in _prefixes._MODEL_SETTINGS_EXCLUDE).
SETTINGS: tuple[SettingDescriptor, ...] = (
    SettingDescriptor(101, "water_hardness", "Water hardness", _HARDNESS_OPTIONS),
    SettingDescriptor(102, "temperature", "Temperature", _TEMPERATURE_OPTIONS),
    SettingDescriptor(103, "off_rinse", "Off-rinse", _OFF_ON_OPTIONS),
    SettingDescriptor(104, "auto_off", "Auto-off", _AUTO_OFF_STANDARD_OPTIONS),
    SettingDescriptor(106, "profile", "Profile", _PROFILE_STANDARD_OPTIONS),
)


# 7 recipe counters (gaps at 202 / 205 — those recipes don't exist on
# 600 hardware). Maintenance gauges 600/601/610/611/620/621/640/641 are
# universal; filter dependency is 105 on 600/700/79X.
STATS: tuple[StatDescriptor, ...] = (
    _count(200, "espresso", "Espresso"),
    _count(201, "coffee", "Coffee"),
    _count(203, "americano", "Americano"),
    _count(204, "cappuccino", "Cappuccino"),
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


STANDARD_LAYOUT = RecipeFieldLayout(
    family_key="600",
    strength_offset=1, profile_offset=2, temperature_offset=3,
    two_cups_offset=4, coffee_amount_offset=5, water_amount_offset=6,
    milk_foam_amount_offset=8, preparation_offset=9,
)


MYCOFFEE_LAYOUT = RecipeFieldLayout(
    family_key="600",
    enabled_offset=0, icon_offset=1, name_offset=2, type_offset=3,
    strength_offset=4, profile_offset=5, temperature_offset=6,
    two_cups_offset=7, coffee_amount_offset=8, water_amount_offset=9,
    milk_foam_amount_offset=11, preparation_offset=12,
)


CAPABILITIES = MachineCapabilities(
    family_key="600",
    model_name="Nivona NICR 6xx",
    supports_recipe_writes=False,
    supports_stats=False,
    my_coffee_slots=1,
    strength_levels=3,
    has_aroma_balance=False,
    brew_command_mode=0x0B,
    recipes=RECIPES,
    settings=SETTINGS,
    stats=STATS,
)
