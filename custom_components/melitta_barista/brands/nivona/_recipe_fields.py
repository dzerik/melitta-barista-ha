"""Numeric recipe constraints, kept separate for each register context."""

from __future__ import annotations

from ...coffee_platform.domain import MachineCapabilities
from ._recipe_choices import choice_values


_AMOUNT_FIELDS = ("coffee_amount", "water_amount", "milk_amount", "milk_foam_amount")
_VERIFIED_PROFILES = frozenset(("ef573", "ef253", "ef698", "ef568", "ef1040"))
_CHILLED_STANDARD_AMOUNTS = {
    (10805, "coffee_amount"): (20, 240),
    (10905, "coffee_amount"): (20, 240),
    (11005, "coffee_amount"): (20, 240),
    (11006, "water_amount"): (20, 240),
}
# Bind persistent limits by absolute address AND field, not by drink labels.
# Unresolved addresses retain the integer wire domain instead of borrowing
# a different recipe's minimum or total-volume range.
_STANDARD_AMOUNTS = {
    "700": {
        (10005, "coffee_amount"): (20, 240),
        (10105, "coffee_amount"): (20, 240),
        (10305, "coffee_amount"): (20, 240),
        (10306, "water_amount"): (20, 240),
        (10405, "coffee_amount"): (20, 240),
        (10407, "milk_amount"): (40, 240),
        (10408, "milk_foam_amount"): (20, 240),
        (10505, "coffee_amount"): (20, 240),
        (10507, "milk_amount"): (20, 240),
        (10508, "milk_foam_amount"): (20, 240),
        (10607, "milk_amount"): (20, 240),
        (10608, "milk_foam_amount"): (20, 240),
        (10706, "water_amount"): (20, 500),
    },
    "1030": {
        (10009, "coffee_amount"): (20, 240),
        (10109, "coffee_amount"): (20, 240),
        (10209, "coffee_amount"): (20, 240),
        (10210, "water_amount"): (20, 240),
        (10309, "coffee_amount"): (20, 240),
        (10312, "milk_foam_amount"): (40, 240),
        (10409, "coffee_amount"): (20, 240),
        (10411, "milk_amount"): (40, 240),
        (10509, "coffee_amount"): (20, 240),
        (10511, "milk_amount"): (20, 240),
        (10512, "milk_foam_amount"): (20, 240),
        (10610, "water_amount"): (50, 500),
        (11411, "milk_amount"): (40, 240),
        (11511, "milk_amount"): (40, 240),
    },
    "1040": {
        (10009, "coffee_amount"): (20, 240),
        (10109, "coffee_amount"): (20, 240),
        (10209, "coffee_amount"): (20, 240),
        (10210, "water_amount"): (20, 240),
        (10309, "coffee_amount"): (20, 240),
        (10312, "milk_foam_amount"): (40, 240),
        (10409, "coffee_amount"): (20, 240),
        (10411, "milk_amount"): (40, 240),
        (10509, "coffee_amount"): (20, 240),
        (10511, "milk_amount"): (20, 240),
        (10512, "milk_foam_amount"): (20, 240),
        (10610, "water_amount"): (50, 500),
        (11411, "milk_amount"): (40, 240),
    },
    "8000": {
        (10005, "coffee_amount"): (20, 240),
        (10105, "coffee_amount"): (20, 240),
        (10205, "coffee_amount"): (20, 240),
        (10206, "water_amount"): (20, 240),
        (10305, "coffee_amount"): (20, 240),
        (10308, "milk_foam_amount"): (20, 240),
        (10405, "coffee_amount"): (20, 240),
        (10407, "milk_amount"): (20, 240),
        (10505, "coffee_amount"): (20, 240),
        (10507, "milk_amount"): (20, 240),
        (10508, "milk_foam_amount"): (20, 240),
        (10607, "milk_amount"): (20, 240),
        (10608, "milk_foam_amount"): (20, 240),
        (10706, "water_amount"): (20, 240),
    },
    "79x": {
        (10005, "coffee_amount"): (20, 240),
        (10105, "coffee_amount"): (20, 240),
        (10205, "coffee_amount"): (20, 240),
        (10206, "water_amount"): (20, 240),
        (10305, "coffee_amount"): (20, 240),
        (10308, "milk_foam_amount"): (20, 240),
        (10505, "coffee_amount"): (20, 240),
        (10507, "milk_amount"): (20, 240),
        (10508, "milk_foam_amount"): (20, 240),
        (10607, "milk_amount"): (20, 240),
        (10608, "milk_foam_amount"): (20, 240),
        (10706, "water_amount"): (30, 500),
    },
    "900-light": {
        (10009, "coffee_amount"): (20, 240),
        (10109, "coffee_amount"): (20, 240),
        (10209, "coffee_amount"): (20, 240),
        (10210, "water_amount"): (20, 240),
        (10309, "coffee_amount"): (20, 240),
        (10312, "milk_foam_amount"): (40, 240),
        (10409, "coffee_amount"): (20, 240),
        (10411, "milk_amount"): (40, 240),
        (10509, "coffee_amount"): (20, 240),
        (10511, "milk_amount"): (20, 240),
        (10512, "milk_foam_amount"): (20, 240),
        (10612, "milk_foam_amount"): (20, 240),
    },
}


def validate_recipe_param(
    caps: MachineCapabilities, context: str, selector: int, field: str, value: object,
    *, register: int | None,
) -> int:
    """Validate one explicit input before any write; quantities are in mL.

    Choice values remain zero-based protocol values. A zero remote quantity
    may disable a component even when the active range has a nonzero minimum.
    Unknown persistent domains retain their integer wire bounds.
    """
    if register is None:
        raise ValueError(f"{field} is unsupported for recipe {selector} on {caps.family_key}")
    if not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be an integer")
    try:
        number = int(value)
    except (ValueError, OverflowError) as err:
        raise ValueError(f"{field} must be a finite integer") from err
    if number != value:
        raise ValueError(f"{field} must be an integer")
    minimum, maximum = -2147483648, 2147483647
    allowed = None
    verified = caps.status_profile_key in _VERIFIED_PROFILES
    if field in _AMOUNT_FIELDS:
        minimum = 0
        scale = caps.fluid_scale_factor
        maximum //= scale
        if verified and context in ("temporary", "mycoffee"):
            maximum = 500 if field == "water_amount" else 240
            if context == "temporary":
                minima = {
                    "1030": (20, 20, 40, 40), "1040": (20, 30, 20, 20),
                }.get(caps.family_key, (0, 0, 0, 0))
                minimum = minima[_AMOUNT_FIELDS.index(field)] if number != 0 else 0
        elif verified and context == "standard":
            minimum, maximum = _STANDARD_AMOUNTS.get(caps.family_key, {}).get(
                (register, field), (minimum, maximum),
            )
            if caps.family_key == "8000" and selector in caps.saved_recipe_only_selectors:
                minimum, maximum = _CHILLED_STANDARD_AMOUNTS.get(
                    (register, field), (minimum, maximum),
                )
    else:
        if field == "strength":
            minimum, maximum = 0, 4
        elif field == "two_cups":
            allowed = (0, 1)
        elif field == "temperature":
            minimum, maximum = 0, 2
        if verified:
            profile = caps.family_key
            if profile == "8000" and caps.saved_recipe_only_selectors:
                profile = "8000-chilled"
            exact = choice_values(profile, context, register, field)
            if exact is not None:
                allowed = exact
                minimum, maximum = -2147483648, 2147483647
            if caps.family_key == "1040" and field == "profile" and allowed is not None:
                allowed = tuple(sorted(set(allowed) | {3}))
    if allowed is not None and number not in allowed:
        raise ValueError(f"{field} must be one of {allowed}")
    if not minimum <= number <= maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return number

