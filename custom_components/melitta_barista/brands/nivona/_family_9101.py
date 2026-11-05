"""NIVO 9101: independent Ef1109 recipes, settings and read-only counters."""

from ..base import (
    MachineCapabilities,
    RecipeDescriptor,
    RecipeFieldLayout,
    SettingDescriptor,
)
from ._stats_helpers import _count, _pct

_DRINKS = (
    ("Lungo", "coffee", "coffee"),
    ("Espresso", "espresso", "espresso"),
    ("Americano", "americano", "americano"),
    ("Caffè Latte", "milk_drink", "caffe_latte"),
    ("Cappuccino", "cappuccino", "cappuccino"),
    ("Espresso Macchiato", "milk_drink", "espresso_macchiato"),
    ("Flat White", "milk_drink", "flat_white"),
    ("Latte Macchiato", "milk_drink", "latte_macchiato"),
    ("Milk", "milk_drink", "hot_milk"),
    ("Hot Water", "water", "hot_water"),
    ("Chilled Lungo", "coffee", "chilled_lungo"),
    ("Chilled Espresso", "espresso", "chilled_espresso"),
    ("Chilled Americano", "americano", "chilled_americano"),
)
RECIPES = tuple(
    RecipeDescriptor(i, name, category, key)
    for i, (name, category, key) in enumerate(_DRINKS)
)
STANDARD_LAYOUT = RecipeFieldLayout(
    family_key="9101",
    strength_offset=3,
    profile_offset=4,
    coffee_temperature_offset=5,
    preparation_offset=6,
    milk_type_offset=7,
    froth_type_offset=8,
    milk_temp_control_offset=9,
    milk_temperature_offset=10,
    two_cups_offset=11,
    coffee_amount_offset=12,
    water_amount_offset=13,
    milk_amount_offset=14,
    milk_foam_amount_offset=15,
)
MYCOFFEE_LAYOUT = RecipeFieldLayout(
    family_key="9101",
    enabled_offset=0,
    name_offset=2,
    type_offset=3,
    strength_offset=4,
    profile_offset=5,
    coffee_temperature_offset=6,
    preparation_offset=7,
    milk_type_offset=8,
    froth_type_offset=9,
    milk_temp_control_offset=10,
    milk_temperature_offset=11,
    two_cups_offset=12,
    coffee_amount_offset=13,
    water_amount_offset=14,
    milk_amount_offset=15,
    milk_foam_amount_offset=16,
)
REMOTE_LAYOUT = RecipeFieldLayout(
    family_key="9101",
    type_offset=0,
    strength_offset=1,
    profile_offset=2,
    coffee_temperature_offset=3,
    preparation_offset=4,
    milk_type_offset=5,
    froth_type_offset=6,
    milk_temp_control_offset=7,
    milk_temperature_offset=8,
    two_cups_offset=9,
    coffee_amount_offset=10,
    water_amount_offset=11,
    milk_amount_offset=12,
    milk_foam_amount_offset=13,
)
REMOTE_DEFAULTS = (
    ("strength", 3),
    ("profile", 2),
    ("coffee_temperature", 1),
    ("preparation", 0),
    ("milk_type", 0),
    ("froth_type", 0),
    ("milk_temp_control", 1),
    ("milk_temperature", 2),
    ("two_cups", 0),
    ("coffee_amount", 0),
    ("water_amount", 0),
    ("milk_amount", 0),
    ("milk_foam_amount", 0),
)
STANDARD_BASES = {0: 10100, 1: 10000, **{i: 10000 + i * 100 for i in range(2, 13)}}
STANDARD_OFFSETS = {
    0: (3, 4, 5, 11, 12),
    1: (3, 4, 5, 11, 12),
    2: (3, 4, 5, 6, 11, 12, 13),
    3: (3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14),
    4: (3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 15),
    5: (3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 15),
    6: (3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14, 15),
    7: (3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14, 15),
    8: (7, 8, 9, 10, 11, 14, 15),
    9: (11, 13),
    10: (3, 4, 11, 12),
    11: (3, 4, 11, 12),
    12: (3, 4, 11, 12, 13),
}

_OFF_ON = ((0, "off"), (1, "on"))
SETTINGS = (
    SettingDescriptor(
        100,
        "language",
        "Language",
        tuple(
            enumerate(
                (
                    "EN",
                    "DE",
                    "FR",
                    "IT",
                    "ES",
                    "PT",
                    "PL",
                    "NL",
                    "RU",
                    "CS",
                    "SV",
                    "ET",
                    "HU",
                    "DA",
                    "LT",
                    "LV",
                    "SK",
                    "NO",
                    "FI",
                    "UK",
                    "KO",
                ),
                1,
            )
        ),
    ),
    SettingDescriptor(
        101,
        "easy_clean_after_minutes",
        "Easy clean after minutes",
        tuple((i, f"{i} min") for i in (5, 10, 20, 30, 40)),
    ),
    SettingDescriptor(
        102,
        "auto_off",
        "Auto-off",
        (
            (10, "10 min"),
            (30, "30 min"),
            (60, "1 h"),
            (120, "2 h"),
            (240, "4 h"),
            (360, "6 h"),
            (480, "8 h"),
            (600, "10 h"),
            (720, "12 h"),
        ),
    ),
    SettingDescriptor(
        119,
        "water_hardness",
        "Water hardness",
        ((0, "very soft"), (1, "soft"), (2, "medium"), (3, "hard")),
    ),
    SettingDescriptor(120, "extreme_mode", "Extreme mode", _OFF_ON),
    SettingDescriptor(
        121, "big_picture_mode", "Big picture mode", ((0, "small"), (1, "large"))
    ),
    SettingDescriptor(122, "quick_select", "Quick select", _OFF_ON),
    SettingDescriptor(124, "flying_picture", "Flying picture", _OFF_ON),
    SettingDescriptor(
        125,
        "brew_unit_wash_interval",
        "Brew unit wash interval",
        ((0, "off"), (1, "25"), (2, "50"), (3, "100"), (4, "150")),
    ),
    SettingDescriptor(
        126,
        "tank_light_color",
        "Accent color",
        (
            (14, "Aqua"),
            (12, "Blue"),
            (13, "Fuchsia"),
            (10, "Lime"),
            (16, "Orange"),
            (9, "Red"),
            (128, "Spectrum"),
            (15, "White"),
            (11, "Yellow"),
        ),
    ),
    SettingDescriptor(
        127,
        "tank_light_brightness",
        "Accent brightness",
        ((1, "low"), (2, "medium"), (3, "high")),
    ),
    SettingDescriptor(128, "tank_light", "Accent lighting", _OFF_ON),
    SettingDescriptor(129, "filter_inserted", "Filter inserted", _OFF_ON),
)
_STAT_KEYS = (
    "coffee",
    "espresso",
    "americano",
    "caffe_latte",
    "cappuccino",
    "espresso_macchiato",
    "flat_white",
    "latte_macchiato",
    "hot_milk",
    "hot_water",
    "chilled_coffee",
    "chilled_espresso",
    "chilled_americano",
    "total_beverages",
    "one_cup",
    "two_cups",
    "powder",
    "with_milk",
    "two_cups_with_milk",
    "with_coffee",
    "two_cups_with_coffee",
    "with_water",
    "two_cups_with_water",
    *(
        f"{profile}_{drink}"
        for profile in ("quick", "dynamic", "constant", "harmonic", "intense")
        for drink in ("coffee", "espresso", "cappuccino")
    ),
    "remote_starts",
)
STATS = (
    *(
        _count(200 + i, key, key.replace("_", " ").capitalize())
        for i, key in enumerate(_STAT_KEYS)
    ),
    *(
        _count(240 + i, key, key.replace("_", " ").capitalize())
        for i, key in enumerate(
            (
                "brewing_operations",
                "water_operations",
                "steam_operations",
                "powder_operations",
                "milling_operations",
            )
        )
    ),
    _pct(600, "descale_percent", "Descale progress"),
    _pct(610, "brew_unit_clean_percent", "Brew unit clean progress"),
    _pct(620, "frother_clean_percent", "Frother clean progress"),
    _pct(640, "filter_percent", "Filter progress"),
    _pct(650, "brew_unit_wash_percent", "Brew unit wash progress"),
)
CAPABILITIES = MachineCapabilities(
    family_key="9101",
    model_name="Nivona NIVO 9101",
    supports_stats=True,
    supports_brew_overrides=True,
    my_coffee_slots=16,
    first_mycoffee_selector=13,
    strength_levels=5,
    brew_command_mode=4,
    recipe_text_encoding="utf16_le",
    verified_maintenance_processes=(),
    recipes=RECIPES,
    settings=SETTINGS,
    stats=STATS,
    unsupported_generic_setting_ids=frozenset(range(100)),
    recipe_write_precondition=98,
    temporary_recipe_type_register=9000,
    temporary_recipe_selector=0,
    temporary_recipe_defaults=REMOTE_DEFAULTS,
    required_brew_overrides=tuple((i, "profile", 5) for i in (10, 11, 12)),
    unsupported_setting_keys=(
        "coffee_temperature",
        "auto_on_deactivated",
        "auto_on_hours",
        "auto_on_minutes",
        "off_rinse",
        "save_energy",
        "touch_lock",
    ),
)
EXPORTS = {
    "9101": dict(
        recipes=RECIPES,
        settings=SETTINGS,
        stats=STATS,
        standard_layout=STANDARD_LAYOUT,
        mycoffee_layout=MYCOFFEE_LAYOUT,
        capabilities=CAPABILITIES,
    )
}


def validate_field(context: str, selector: int, field: str, value: object) -> int:
    """Validate exact context domains; unsupported standard fields fail closed."""
    if not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be an integer")
    try:
        number = int(value)
    except (ValueError, OverflowError) as err:
        raise ValueError(f"{field} must be a finite integer") from err
    if value != number:
        raise ValueError(f"{field} must be an integer")
    if context not in ("temporary", "standard", "mycoffee"):
        raise ValueError("Unknown recipe context")
    if context == "mycoffee":
        if not 0 <= selector < 16:
            raise ValueError("MyCoffee slot is unsupported")
        layout = MYCOFFEE_LAYOUT
    else:
        if selector not in STANDARD_BASES:
            raise ValueError("Recipe selector is unsupported")
        layout = STANDARD_LAYOUT if context == "standard" else REMOTE_LAYOUT
    if field == "temperature":
        field = (
            "coffee_temperature"
            if selector in range(8)
            else "milk_temperature"
            if selector == 8
            else "unsupported"
        )
    offset = getattr(layout, f"{field}_offset", None)
    if offset is None or (
        context == "standard" and offset not in STANDARD_OFFSETS[selector]
    ):
        raise ValueError(f"{field} is unsupported for recipe {selector}")
    domains = {
        "strength": range(5),
        "profile": range(6),
        "two_cups": range(2),
        "coffee_temperature": (0, 1, 2, 3, 99),
        "milk_temperature": range(4),
        "preparation": range(4),
        "milk_type": range(2),
        "froth_type": range(2),
        "milk_temp_control": range(2),
        "enabled": range(2),
        "type": range(13),
    }
    minimum, maximum = 0, 500 if field == "water_amount" else 240
    if context == "standard":
        maximum = 500 if selector == 9 and field == "water_amount" else 240
        domains["profile"] = (5,) if selector in (10, 11, 12) else range(5)
        domains["coffee_temperature"] = range(4)
        domains["milk_type"] = range(3)
        domains["preparation"] = (2, 3) if selector == 2 else (0, 1)
        if field.endswith("_amount"):
            minimum = (
                0
                if selector == 8
                else 50
                if selector == 9
                else 15
                if selector == 5 and field == "milk_foam_amount"
                else 20
            )
    if context == "temporary" and selector in (10, 11, 12) and field == "profile":
        domains["profile"] = (5,)
    if field in domains:
        if number not in domains[field]:
            raise ValueError(f"Invalid {field} value {number}")
    elif not field.endswith("_amount"):
        raise ValueError(f"{field} is not a numeric recipe parameter")
    elif not minimum <= number <= maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return number
