"""NICR 9xx + 9xx-light families — selectors, settings, stats, layouts, capabilities.

900 / 900-light are sibling families. 900 adds the tank-lighting and
touch-lock accents (settings 105-108); 900-light strips them. Both
share recipes, the maintenance stat block, and the ``fluid_scale_factor=10``
quirk noted on NICR 920/930.

NICR 930 leaves MOVE_CUP_TO_FROTHER (Manipulation 11) set after a brew
until the next status frame; both variants tolerate it so subsequent
brews aren't falsely blocked.
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
    _TANK_LIGHT_BRIGHTNESS_900_OPTIONS,
    _TANK_LIGHT_COLOR_900_OPTIONS,
)
from ._stats_helpers import _count, _flag, _pct

RECIPES_900: tuple[RecipeDescriptor, ...] = (
    RecipeDescriptor(0, "Espresso", "espresso"),
    RecipeDescriptor(1, "Coffee", "coffee"),
    RecipeDescriptor(2, "Americano", "americano"),
    RecipeDescriptor(3, "Cappuccino", "cappuccino"),
    RecipeDescriptor(4, "Caffè Latte", "milk_drink"),
    RecipeDescriptor(5, "Latte Macchiato", "milk_drink"),
    RecipeDescriptor(6, "Hot Milk", "milk_drink"),
    RecipeDescriptor(7, "Hot Water", "water"),
)

# 900-light family reuses the 900 recipe table.
RECIPES_900_LIGHT: tuple[RecipeDescriptor, ...] = RECIPES_900


# 900 family — 8 settings including tank-lighting accents and
# AutoOn hours/minutes pair (111/112).
SETTINGS_900: tuple[SettingDescriptor, ...] = (
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
SETTINGS_900_LIGHT: tuple[SettingDescriptor, ...] = (
    SettingDescriptor(102, "water_hardness", "Water hardness", _HARDNESS_OPTIONS),
    SettingDescriptor(104, "save_energy", "Save energy", _OFF_ON_OPTIONS),
    SettingDescriptor(109, "auto_off", "Auto-off", _AUTO_OFF_STANDARD_OPTIONS),
    SettingDescriptor(110, "auto_on_deactivated", "Auto-on deactivated", _OFF_ON_OPTIONS),
    SettingDescriptor(111, "auto_on_hours", "Auto-on hours"),
    SettingDescriptor(112, "auto_on_minutes", "Auto-on minutes"),
)


# 900 / 900-Light family — 21 counters (recipe 200..208 + cumulative
# 209..221), maintenance gauges, 101 dependency.
STATS_900: tuple[StatDescriptor, ...] = (
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
STATS_900_LIGHT: tuple[StatDescriptor, ...] = STATS_900


# fluid_write_scale_10: previously set True here; the upstream RE flagged
# it but the observed machine behaviour does not apply a ×10 scaling to
# fluid amounts in HW writes. Reverted to default False until a live
# trace confirms a family that actually needs it.
STANDARD_LAYOUT_900 = RecipeFieldLayout(
    family_key="900",
    strength_offset=1, profile_offset=2, preparation_offset=3,
    two_cups_offset=4,
    coffee_temperature_offset=5, water_temperature_offset=6,
    milk_temperature_offset=7, milk_foam_temperature_offset=8,
    coffee_amount_offset=9, water_amount_offset=10,
    milk_amount_offset=11, milk_foam_amount_offset=12,
    overall_temperature_offset=13,
    fluid_write_scale_10=False,
)

STANDARD_LAYOUT_900_LIGHT = RecipeFieldLayout(
    family_key="900-light",
    strength_offset=1, profile_offset=2, preparation_offset=3,
    two_cups_offset=4,
    coffee_temperature_offset=5, water_temperature_offset=6,
    milk_temperature_offset=7, milk_foam_temperature_offset=8,
    coffee_amount_offset=9, water_amount_offset=10,
    milk_amount_offset=11, milk_foam_amount_offset=12,
    overall_temperature_offset=13,
)


MYCOFFEE_LAYOUT_900 = RecipeFieldLayout(
    family_key="900",
    enabled_offset=0, icon_offset=1, name_offset=2, type_offset=3,
    strength_offset=4, profile_offset=5, preparation_offset=6,
    two_cups_offset=7,
    coffee_temperature_offset=8, water_temperature_offset=9,
    milk_temperature_offset=10, milk_foam_temperature_offset=11,
    coffee_amount_offset=12, water_amount_offset=13,
    milk_amount_offset=14, milk_foam_amount_offset=15,
    overall_temperature_offset=16,
    fluid_write_scale_10=False,
)

MYCOFFEE_LAYOUT_900_LIGHT = RecipeFieldLayout(
    family_key="900-light",
    enabled_offset=0, icon_offset=1, name_offset=2, type_offset=3,
    strength_offset=4, profile_offset=5, preparation_offset=6,
    two_cups_offset=7,
    coffee_temperature_offset=8, water_temperature_offset=9,
    milk_temperature_offset=10, milk_foam_temperature_offset=11,
    coffee_amount_offset=12, water_amount_offset=13,
    milk_amount_offset=14, milk_foam_amount_offset=15,
    overall_temperature_offset=16,
)


CAPABILITIES_900 = MachineCapabilities(
    family_key="900",
    model_name="Nivona NICR 9xx",
    supports_recipe_writes=False,
    supports_stats=True,
    my_coffee_slots=4,
    strength_levels=5,
    has_aroma_balance=False,
    brew_command_mode=0x0B,
    fluid_scale_factor=10,
    tolerated_brew_manipulations=(11,),
    recipes=RECIPES_900,
    settings=SETTINGS_900,
    stats=STATS_900,
)

CAPABILITIES_900_LIGHT = MachineCapabilities(
    family_key="900-light",
    model_name="Nivona NICR 9xx Light",
    supports_recipe_writes=False,
    supports_stats=True,
    my_coffee_slots=4,
    strength_levels=3,
    brew_command_mode=0x0B,
    tolerated_brew_manipulations=(11,),
    recipes=RECIPES_900_LIGHT,
    settings=SETTINGS_900_LIGHT,
    stats=STATS_900_LIGHT,
)


EXPORTS: dict[str, dict] = {
    "900": {
        "recipes": RECIPES_900,
        "settings": SETTINGS_900,
        "stats": STATS_900,
        "standard_layout": STANDARD_LAYOUT_900,
        "mycoffee_layout": MYCOFFEE_LAYOUT_900,
        "capabilities": CAPABILITIES_900,
    },
    "900-light": {
        "recipes": RECIPES_900_LIGHT,
        "settings": SETTINGS_900_LIGHT,
        "stats": STATS_900_LIGHT,
        "standard_layout": STANDARD_LAYOUT_900_LIGHT,
        "mycoffee_layout": MYCOFFEE_LAYOUT_900_LIGHT,
        "capabilities": CAPABILITIES_900_LIGHT,
    },
}
