"""NICR 1030 + 1040 families — selectors, settings, stats, layouts, capabilities.

1030 and 1040 are sibling families with the largest setting set
(13 / 16 settings, MyCoffee slots up to 18, full 5-band strength).
1040 is a superset of 1030 — adds milk-foam-temperature (id 117),
power-on-rinse (118), power-on-frother-time (119), and uses a
different ``profile`` option enum (``_PROFILE_1040_OPTIONS``) plus a
distinct milk-temperature scale.

Stat tables share a single STATS_1030 source — 1040 is derived by
dropping selector 207 (1040 has no separate hot-milk counter).
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
    _MILK_FOAM_TEMPERATURE_1040_OPTIONS,
    _MILK_TEMPERATURE_1030_OPTIONS,
    _MILK_TEMPERATURE_1040_OPTIONS,
    _OFF_ON_OPTIONS,
    _POWER_ON_FROTHER_TIME_1040_OPTIONS,
    _PROFILE_1040_OPTIONS,
    _PROFILE_STANDARD_OPTIONS,
    _TEMPERATURE_OPTIONS,
)
from ._stats_helpers import _count, _flag, _pct

RECIPES_1030: tuple[RecipeDescriptor, ...] = (
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

RECIPES_1040: tuple[RecipeDescriptor, ...] = (
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


# 1030 — adds cup-heater / milk-active / direct-start / touch-lock /
# AutoOn pair versus the 700/900 skeleton.
SETTINGS_1030: tuple[SettingDescriptor, ...] = (
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
SETTINGS_1040: tuple[SettingDescriptor, ...] = (
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


# 1030 family — 24 counters, distinctly numbered from 8000/900:
# id 213 is "single cup" (not Total), 215 is Total, 222 is Descaling.
# Filter dependency is 101.
STATS_1030: tuple[StatDescriptor, ...] = (
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
STATS_1040: tuple[StatDescriptor, ...] = tuple(
    s for s in STATS_1030 if s.stat_id != 207
)


STANDARD_LAYOUT_1030 = RecipeFieldLayout(
    family_key="1030",
    strength_offset=1, profile_offset=2, preparation_offset=3,
    two_cups_offset=4,
    coffee_temperature_offset=5, water_temperature_offset=6,
    milk_temperature_offset=7, milk_foam_temperature_offset=8,
    coffee_amount_offset=9, water_amount_offset=10,
    milk_amount_offset=11, milk_foam_amount_offset=12,
)

STANDARD_LAYOUT_1040 = RecipeFieldLayout(
    family_key="1040",
    strength_offset=1, profile_offset=2, preparation_offset=3,
    two_cups_offset=4,
    coffee_temperature_offset=5, water_temperature_offset=6,
    milk_temperature_offset=7, milk_foam_temperature_offset=8,
    coffee_amount_offset=9, water_amount_offset=10,
    milk_amount_offset=11, milk_foam_amount_offset=12,
)


MYCOFFEE_LAYOUT_1030 = RecipeFieldLayout(
    family_key="1030",
    enabled_offset=0, icon_offset=1, name_offset=2, type_offset=3,
    strength_offset=4, profile_offset=5, preparation_offset=6,
    two_cups_offset=7,
    coffee_temperature_offset=8, water_temperature_offset=9,
    milk_temperature_offset=10, milk_foam_temperature_offset=11,
    coffee_amount_offset=12, water_amount_offset=13,
    milk_amount_offset=14, milk_foam_amount_offset=15,
)

MYCOFFEE_LAYOUT_1040 = RecipeFieldLayout(
    family_key="1040",
    enabled_offset=0, icon_offset=1, name_offset=2, type_offset=3,
    strength_offset=4, profile_offset=5, preparation_offset=6,
    two_cups_offset=7,
    coffee_temperature_offset=8, water_temperature_offset=9,
    milk_temperature_offset=10, milk_foam_temperature_offset=11,
    coffee_amount_offset=12, water_amount_offset=13,
    milk_amount_offset=14, milk_foam_amount_offset=15,
)


CAPABILITIES_1030 = MachineCapabilities(
    family_key="1030",
    model_name="Nivona NICR 1030",
    supports_recipe_writes=False,
    supports_stats=False,
    my_coffee_slots=4,
    strength_levels=5,
    brew_command_mode=0x0B,
    recipes=RECIPES_1030,
    settings=SETTINGS_1030,
    stats=STATS_1030,
)

CAPABILITIES_1040 = MachineCapabilities(
    family_key="1040",
    model_name="Nivona NICR 1040",
    supports_recipe_writes=False,
    supports_stats=False,
    my_coffee_slots=4,
    strength_levels=5,
    brew_command_mode=0x0B,
    recipes=RECIPES_1040,
    settings=SETTINGS_1040,
    stats=STATS_1040,
)


EXPORTS: dict[str, dict] = {
    "1030": {
        "recipes": RECIPES_1030,
        "settings": SETTINGS_1030,
        "stats": STATS_1030,
        "standard_layout": STANDARD_LAYOUT_1030,
        "mycoffee_layout": MYCOFFEE_LAYOUT_1030,
        "capabilities": CAPABILITIES_1030,
    },
    "1040": {
        "recipes": RECIPES_1040,
        "settings": SETTINGS_1040,
        "stats": STATS_1040,
        "standard_layout": STANDARD_LAYOUT_1040,
        "mycoffee_layout": MYCOFFEE_LAYOUT_1040,
        "capabilities": CAPABILITIES_1040,
    },
}
