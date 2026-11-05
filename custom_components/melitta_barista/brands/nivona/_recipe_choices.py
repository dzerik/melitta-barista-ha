"""Allowed numeric choices by register context and semantic field."""

# Persistent keys bind an absolute address (standard) or slot offset (MyCoffee).
# Temporary keys bind the actual selected register, including temperature aliases.

_TEMPORARY_CHOICES = {
    "700": {
        9002: (0, 1, 2, 3, 4),
        9003: (0, 1, 2),
        9004: (0, 1, 2),
        9005: (0, 1),
    },
    "1030": {
        9002: (0, 1, 2, 3, 4),
        9003: (0, 1, 2),
        9004: (0, 1, 2),
        9005: (0, 1),
        9006: (0, 1, 2),
        9007: (0, 1, 2),
        9008: (0, 1),
        9009: (0,),
    },
    "1040": {
        9002: (0, 1, 2, 3, 4),
        9003: (0, 1, 2),
        9004: (0, 1, 2),
        9005: (0, 1, 3, 5, 7),
        9006: (0, 1, 2),
        9007: (0, 1, 2),
        9008: (0, 1, 2, 3),
        9009: (0, 1),
    },
    "8000": {
        9002: (0, 1, 2, 3, 4),
        9003: (0, 1, 2, 3, 4, 5),
        9004: (0, 1, 2, 99),
        9005: (0, 1),
        9010: (0, 1),
    },
    "79x": {
        9002: (0, 1, 2, 3, 4),
        9003: (0, 1, 2),
        9004: (0, 1, 2),
        9005: (0, 1),
    },
    "900-light": {
        9002: (0, 1, 2, 3, 4),
        9003: (0, 1, 2),
        9004: (0, 1),
        9005: (0, 1),
        9006: (0, 1, 2, 3),
        9007: (0, 1, 2, 3),
        9008: (0, 1, 2, 3),
        9009: (0, 1, 2, 3),
        9014: (0, 1, 2, 3, 4),
    },
    "8000-chilled": {
        9002: (0, 1, 2, 3, 4),
        9003: (0, 1, 2, 3, 4, 5),
        9004: (0, 1, 2, 99),
        9005: (0, 1),
        9010: (0, 1),
    },
}

_MYCOFFEE_CHOICES = {
    "700": {
        (0, "enabled"): (0, 1),
        (3, "type"): (0, 1, 2, 3, 4, 5, 6),
        (4, "strength"): (0, 1, 2, 3, 4),
        (5, "profile"): (0, 1, 2),
        (6, "coffee_temperature"): (0, 1, 2),
        (7, "two_cups"): (0, 1),
    },
    "1030": {
        (0, "enabled"): (0, 1),
        (1, "icon"): (1, 2, 3, 4, 5, 6, 7, 8, 9),
        (3, "type"): (0, 1, 2, 3, 4, 5, 6, 7),
        (4, "strength"): (0, 1, 2, 3, 4),
        (5, "profile"): (0, 1, 2),
        (6, "preparation"): (0, 1, 2),
        (7, "two_cups"): (0, 1, 3, 5, 7),
        (8, "coffee_temperature"): (0, 1, 2),
        (9, "water_temperature"): (0, 1, 2),
        (10, "milk_temperature"): (0, 1, 2, 3),
        (11, "milk_foam_temperature"): (0, 1),
    },
    "1040": {
        (0, "enabled"): (0, 1),
        (1, "icon"): (1, 2, 3, 4, 5, 6, 7, 8, 9),
        (3, "type"): (0, 1, 2, 3, 4, 5, 6, 7),
        (4, "strength"): (0, 1, 2, 3, 4),
        (5, "profile"): (0, 1, 2),
        (6, "preparation"): (0, 1, 2),
        (7, "two_cups"): (0, 1, 3, 5, 7),
        (8, "coffee_temperature"): (0, 1, 2),
        (9, "water_temperature"): (0, 1, 2),
        (10, "milk_temperature"): (0, 1, 2, 3),
        (11, "milk_foam_temperature"): (0, 1),
    },
    "8000": {
        (0, "enabled"): (0, 1),
        (3, "type"): (0, 1, 2, 3, 4, 5, 6, 7),
        (4, "strength"): (0, 1, 2, 3, 4),
        (5, "profile"): (0, 1, 2, 3, 4, 5),
        (6, "coffee_temperature"): (0, 1, 2, 99),
        (7, "two_cups"): (0, 1),
    },
    "79x": {
        (0, "enabled"): (0, 1),
        (3, "type"): (0, 1, 2, 3, 5, 6, 7),
        (4, "strength"): (0, 1, 2, 3, 4),
        (5, "profile"): (0, 1, 2),
        (6, "coffee_temperature"): (0, 1, 2),
        (7, "two_cups"): (0, 1),
    },
    "900-light": {
        (0, "enabled"): (0, 1, 2),
        (3, "type"): (0, 1, 2, 3, 4, 5, 6, 7),
        (4, "strength"): (0, 1, 2, 3, 4),
        (5, "profile"): (0, 1, 2),
        (6, "preparation"): (0, 1),
        (7, "two_cups"): (0, 1),
        (8, "coffee_temperature"): (0, 1, 2, 3),
        (9, "water_temperature"): (0, 1, 2, 3),
        (10, "milk_temperature"): (0, 1, 2, 3),
        (11, "milk_foam_temperature"): (0, 1, 2, 3),
        (16, "overall_temperature"): (0, 1, 2, 3),
    },
    "8000-chilled": {
        (0, "enabled"): (0, 1),
        (3, "type"): (0, 1, 2, 3, 4, 5, 6, 7),
        (4, "strength"): (0, 1, 2, 3, 4),
        (5, "profile"): (0, 1, 2, 3, 4, 5),
        (6, "coffee_temperature"): (0, 1, 2, 99),
        (7, "two_cups"): (0, 1),
    },
}

_STANDARD_CHOICES = {
    "700": {
        ("strength", (10001, 10101, 10301, 10401, 10501)): (0, 1, 2, 3, 4),
        ("profile", (10002, 10102, 10302, 10402, 10502)): (0, 1, 2),
        ("coffee_temperature", (10003, 10103, 10303, 10403, 10503, 10703)): (0, 1, 2),
        ("two_cups", (10004, 10104, 10404)): (0, 1),
    },
    "1030": {
        ("strength", (10001, 10101, 10201, 10301, 10401, 10501)): (0, 1, 2, 3, 4),
        ("profile", (10002, 10102, 10202, 10302, 10402, 10502)): (0, 1, 2),
        ("two_cups", (10004, 10104, 10304, 10404, 10504, 11404, 11504)): (0, 1),
        ("coffee_temperature", (10005, 10105, 10205, 10305, 10405, 10505)): (0, 1, 2),
        ("water_temperature", (10206, 10606)): (0, 1, 2),
        ("preparation", (10303, 10403)): (0, 1, 2),
        ("milk_foam_temperature", (10308, 10508)): (0,),
        ("milk_temperature", (10407, 10507, 11507)): (0, 1),
        ("milk_temperature", (11407,)): (0,),
    },
    "1040": {
        ("strength", (10001, 10101, 10201, 10301, 10401, 10501)): (0, 1, 2, 3, 4),
        ("profile", (10002, 10102, 10202, 10302, 10402, 10502)): (0, 1, 2),
        ("two_cups", (10004, 10304, 10404, 10504, 11404)): (0, 1),
        ("coffee_temperature", (10005, 10105, 10205, 10305, 10405, 10505)): (0, 1, 2),
        ("two_cups", (10104,)): (0, 1, 3, 5, 7),
        ("water_temperature", (10206, 10606)): (0, 1, 2),
        ("preparation", (10303, 10403)): (0, 1, 2),
        ("milk_foam_temperature", (10308, 10508)): (0, 1),
        ("milk_temperature", (10407, 10507, 11407)): (0, 1, 2, 3),
    },
    "8000": {
        ("strength", (10001, 10101, 10201, 10301, 10401, 10501)): (0, 1, 2, 3, 4),
        ("profile", (10002, 10102, 10202, 10302, 10402, 10502)): (0, 1, 2, 3, 4),
        ("coffee_temperature", (10003, 10103, 10203, 10303, 10403, 10503)): (0, 1, 2),
        ("two_cups", (10004, 10104, 10204, 10304, 10404)): (0, 1),
        ("preparation", (10309,)): (0, 1),
        ("water_temperature", (10703,)): (0, 1, 2),
    },
    "79x": {
        ("strength", (10001, 10101, 10201, 10301, 10501)): (0, 1, 2, 3, 4),
        ("profile", (10002, 10102, 10202, 10302, 10502)): (0, 1, 2),
        ("coffee_temperature", (10003, 10103, 10203, 10303, 10503, 10703)): (0, 1, 2),
        ("two_cups", (10004, 10104)): (0, 1),
    },
    "900-light": {
        ("strength", (10001, 10101, 10201, 10301, 10401, 10501)): (0, 1, 2, 3, 4),
        ("profile", (10002, 10102, 10202, 10302, 10402, 10502)): (0, 1, 2),
        ("two_cups", (10004, 10104, 10304, 10404, 10504, 10604)): (0, 1),
        ("coffee_temperature", (10005, 10105, 10205, 10305, 10405, 10505)): (0, 1, 2, 3),
        ("water_temperature", (10206, 10706)): (0, 1, 2, 3),
        ("preparation", (10303, 10403)): (0, 1),
        ("milk_foam_temperature", (10308, 10508, 10608)): (0, 1, 2, 3),
        ("milk_temperature", (10407, 10507, 10607)): (0, 1, 2, 3),
    },
    "8000-chilled": {
        ("strength", (10001, 10101, 10201, 10301, 10401, 10501, 10801, 10901, 11001)): (0, 1, 2, 3, 4),
        ("profile", (10002, 10102, 10202, 10302, 10402, 10502)): (0, 1, 2, 3, 4),
        ("coffee_temperature", (10003, 10103, 10203, 10303, 10403, 10503)): (0, 1, 2),
        ("two_cups", (10004, 10104, 10204, 10304, 10404, 10804, 10904, 11004)): (0, 1),
        ("preparation", (10309,)): (0, 1),
        ("water_temperature", (10703,)): (0, 1, 2),
        ("profile", (10802, 10902, 11002)): (5,),
        ("coffee_temperature", (10803, 10903, 11003)): (99,),
    },
}

def choice_values(
    family: str, context: str, register: int, field: str,
) -> tuple[int, ...] | None:
    """Find a verified choice domain without borrowing another context."""
    if context == "temporary":
        return _TEMPORARY_CHOICES.get(family, {}).get(register)
    if field == "temperature":
        field = "coffee_temperature"
    if context == "mycoffee":
        return _MYCOFFEE_CHOICES.get(family, {}).get((register % 100, field))
    for (semantic, addresses), values in _STANDARD_CHOICES.get(family, {}).items():
        if semantic == field and register in addresses:
            return values
    return None

