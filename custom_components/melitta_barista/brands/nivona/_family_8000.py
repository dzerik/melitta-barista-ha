"""NIVO 8xxx family — selectors, settings, stats, layouts, capabilities.

The 8000 family is the only Nivona family that uses
``brew_command_mode = 0x04`` (every other family uses 0x0B). It also
exposes the smallest setting set (4 entries — no Auto-On pair, no
profile select), but the richest stat block: 8000 firmware tracks
extended counters 211 (grinding count), 212 (reserve count), 602
(descale state machine), 630 (frother-rinse needed) on top of the
universal beverage/maintenance set.

NICR 8107 additionally exposes chilled-brew selectors 8/9/10. These
recipes are sent with a distinct HE flags byte (byte[5]=0, not 0x01) —
see ``start_process_nivona``. The chilled-brew selector set is
exposed via ``CHILLED_SELECTORS`` and applied by
``NivonaProfile.capabilities_for_model`` when the prefix matches 8107.
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
    _AUTO_OFF_8000_OPTIONS,
    _HARDNESS_OPTIONS,
    _OFF_ON_OPTIONS,
    _TEMP_ON_OFF,
)
from ._stats_helpers import _count, _flag, _pct

RECIPES_8000: tuple[RecipeDescriptor, ...] = (
    RecipeDescriptor(0, "Espresso", "espresso"),
    RecipeDescriptor(1, "Coffee", "coffee"),
    RecipeDescriptor(2, "Americano", "americano"),
    RecipeDescriptor(3, "Cappuccino", "cappuccino"),
    RecipeDescriptor(4, "Caffè Latte", "milk_drink"),
    RecipeDescriptor(5, "Latte Macchiato", "milk_drink"),
    RecipeDescriptor(6, "Milk", "milk_drink"),
    RecipeDescriptor(7, "Hot Water", "water"),
)

RECIPES_8000_CHILLED: tuple[RecipeDescriptor, ...] = RECIPES_8000 + (
    RecipeDescriptor(8,  "Chilled Espresso",  "espresso"),
    RecipeDescriptor(9,  "Chilled Lungo",     "coffee"),
    RecipeDescriptor(10, "Chilled Americano", "americano"),
)

# Selectors that require the chilled-brew flag byte when sent as HE.
CHILLED_SELECTORS: frozenset[int] = frozenset({8, 9, 10})


SETTINGS_8000: tuple[SettingDescriptor, ...] = (
    SettingDescriptor(101, "water_hardness", "Water hardness", _HARDNESS_OPTIONS),
    SettingDescriptor(103, "off_rinse", "Off-rinse", _OFF_ON_OPTIONS),
    SettingDescriptor(104, "auto_off", "Auto-off", _AUTO_OFF_8000_OPTIONS),
    SettingDescriptor(105, "coffee_temperature", "Coffee temperature", _TEMP_ON_OFF),
)


# id 206 = "Heisse Milch" (Hot milk, not Warm). Slug changed from
# `warm_milk` in v0.77.0 to align with vendor terminology; entity
# registry migration in async_migrate_entry v2 → v3 renames existing
# user entries.
STATS_8000: tuple[StatDescriptor, ...] = (
    _count(200, "espresso", "Espresso"),
    _count(201, "coffee", "Coffee"),
    _count(202, "americano", "Americano"),
    _count(203, "cappuccino", "Cappuccino"),
    _count(204, "caffe_latte", "Caffè latte"),
    _count(205, "macchiato", "Latte macchiato"),
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


STANDARD_LAYOUT_8000 = RecipeFieldLayout(
    family_key="8000",
    strength_offset=1, profile_offset=2, temperature_offset=3,
    two_cups_offset=4, coffee_amount_offset=5, water_amount_offset=6,
    milk_amount_offset=7, milk_foam_amount_offset=8,
)


MYCOFFEE_LAYOUT_8000 = RecipeFieldLayout(
    family_key="8000",
    enabled_offset=0, name_offset=2, icon_offset=3, type_offset=3,
    strength_offset=4, profile_offset=5, temperature_offset=6,
    two_cups_offset=7, coffee_amount_offset=8, water_amount_offset=9,
    milk_amount_offset=10, milk_foam_amount_offset=11,
)


CAPABILITIES_8000 = MachineCapabilities(
    family_key="8000",
    model_name="Nivona NIVO 8xxx",
    supports_recipe_writes=False,
    supports_stats=True,
    my_coffee_slots=4,
    strength_levels=5,
    brew_command_mode=0x04,    # NIVO8000 uses different brew opcode byte
    recipes=RECIPES_8000,
    settings=SETTINGS_8000,
    stats=STATS_8000,
)
