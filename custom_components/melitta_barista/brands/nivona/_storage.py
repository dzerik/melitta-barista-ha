"""Persistent storage bases, independent of the product selector on HE."""


_STORAGE_BASES = {
    "600": {0:10000, 1:10100, 2:10300, 3:10400, 4:10600, 5:10700},
    "700": {0:10000, 1:10100, 2:10200, 3:10300, 4:10400, 5:10500, 6:10600, 7:10700},
    "79x": {0:10000, 1:10100, 2:10200, 3:10300, 5:10500, 6:10600, 7:10700},
    "900": {0:10000, 1:10100, 2:10200, 3:10300, 4:10400, 5:10500, 6:10600, 7:10700},
    "900-light": {0:10000, 1:10100, 2:10200, 3:10300, 4:10400, 5:10500, 6:10600, 7:10700},
    "1030": {0:10000, 1:10100, 2:10200, 3:10300, 4:10400, 5:10500, 6:10600, 9:10900},
    "1040": {0:10000, 1:10100, 2:10200, 3:10300, 4:10400, 5:10500, 6:10600, 8:10900},
    "8000": {0:10000, 1:10100, 2:10200, 3:10300, 4:10400, 5:10500, 6:10600, 7:10700,
             8:10800, 9:10900, 10:11000},
}


def standard_recipe_register(family_key: str, selector: int, offset: int) -> int | None:
    """Resolve a verified storage base; unresolved recipes cannot be written."""
    base = _STORAGE_BASES.get(family_key, {}).get(selector)
    return None if base is None else base + offset
