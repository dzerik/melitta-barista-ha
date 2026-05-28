"""NICR 7xx + 79x families — selectors, settings, stats, layouts, capabilities.

The 700 family and 79x are protocol-siblings: identical recipe-layout,
identical setting register set apart from the 79X drop of off-rinse
(id 103), and the same 105 filter-dependency. Stat tables differ in
recipe-counter labels — 700 has "Creme" (id 201) and a Cappuccino slot;
79X has "Kaffee" and lacks selector 204.

700-proper inherits its setting list from 600 by importing
``_family_600.SETTINGS`` to keep the family relationship explicit.
"""

from __future__ import annotations

from ..base import (
    MachineCapabilities,
    RecipeDescriptor,
    RecipeFieldLayout,
    SettingDescriptor,
    StatDescriptor,
)
from . import _family_600
from ._stats_helpers import _count, _flag, _pct

RECIPES_700: tuple[RecipeDescriptor, ...] = (
    RecipeDescriptor(0, "Espresso", "espresso"),
    RecipeDescriptor(1, "Cream", "coffee"),
    RecipeDescriptor(2, "Lungo", "coffee"),
    RecipeDescriptor(3, "Americano", "americano"),
    RecipeDescriptor(4, "Cappuccino", "cappuccino"),
    RecipeDescriptor(5, "Latte Macchiato", "milk_drink"),
    RecipeDescriptor(6, "Milk", "milk_drink"),
    RecipeDescriptor(7, "Hot Water", "water"),
)

RECIPES_79X: tuple[RecipeDescriptor, ...] = (
    RecipeDescriptor(0, "Espresso", "espresso"),
    RecipeDescriptor(1, "Coffee", "coffee"),
    RecipeDescriptor(2, "Americano", "americano"),
    RecipeDescriptor(3, "Cappuccino", "cappuccino"),
    RecipeDescriptor(5, "Latte Macchiato", "milk_drink"),
    RecipeDescriptor(6, "Milk", "milk_drink"),
    RecipeDescriptor(7, "Hot Water", "water"),
)


# 700-proper: identical to 600 settings.
SETTINGS_700: tuple[SettingDescriptor, ...] = _family_600.SETTINGS

# 79X: drops id 103 (off-rinse not exposed on 79X hardware).
SETTINGS_79X: tuple[SettingDescriptor, ...] = tuple(
    s for s in SETTINGS_700 if s.setting_id != 103
)


# 700 family — recipe counters 200..208 only; no 213-221 cumulative
# counters on this hardware. Maintenance gauges universal; 105 is the
# filter dependency.
STATS_700: tuple[StatDescriptor, ...] = (
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
STATS_79X: tuple[StatDescriptor, ...] = (
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


STANDARD_LAYOUT_700 = RecipeFieldLayout(
    family_key="700",
    strength_offset=1, profile_offset=2, temperature_offset=3,
    two_cups_offset=4, coffee_amount_offset=5, water_amount_offset=6,
    milk_amount_offset=7, milk_foam_amount_offset=8,
)

STANDARD_LAYOUT_79X = RecipeFieldLayout(
    family_key="79x",
    strength_offset=1, profile_offset=2, temperature_offset=3,
    two_cups_offset=4, coffee_amount_offset=5, water_amount_offset=6,
    milk_amount_offset=7, milk_foam_amount_offset=8,
)


MYCOFFEE_LAYOUT_700 = RecipeFieldLayout(
    family_key="700",
    enabled_offset=0, icon_offset=1, name_offset=2, type_offset=3,
    strength_offset=4, profile_offset=5, temperature_offset=6,
    two_cups_offset=7, coffee_amount_offset=8, water_amount_offset=9,
    milk_amount_offset=10, milk_foam_amount_offset=11,
)

MYCOFFEE_LAYOUT_79X = RecipeFieldLayout(
    family_key="79x",
    enabled_offset=0, icon_offset=1, name_offset=2, type_offset=3,
    strength_offset=4, profile_offset=5, temperature_offset=6,
    two_cups_offset=7, coffee_amount_offset=8, water_amount_offset=9,
    milk_amount_offset=10, milk_foam_amount_offset=11,
)


CAPABILITIES_700 = MachineCapabilities(
    family_key="700",
    model_name="Nivona NICR 7xx",
    supports_recipe_writes=False,
    supports_stats=True,
    my_coffee_slots=4,
    strength_levels=3,
    has_aroma_balance=True,
    brew_command_mode=0x0B,
    recipes=RECIPES_700,
    settings=SETTINGS_700,
    stats=STATS_700,
)

CAPABILITIES_79X = MachineCapabilities(
    family_key="79x",
    model_name="Nivona NICR 79x",
    supports_recipe_writes=False,
    supports_stats=True,
    my_coffee_slots=4,
    strength_levels=5,
    has_aroma_balance=True,
    brew_command_mode=0x0B,
    recipes=RECIPES_79X,
    settings=SETTINGS_79X,
    stats=STATS_79X,
)
