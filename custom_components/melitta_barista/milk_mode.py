"""Resolve logical milk preparation without changing legacy process tokens."""

from .const import SHOTS_MAP

MILK_MODE_SHOTS: dict[str, int] = {"warm_milk": 1, "milk_froth": 0}
MILK_MODES: tuple[str, ...] = tuple(MILK_MODE_SHOTS)


def milk_mode_from_shots(shots: str | int) -> str:
    """Describe a saved milk component; nonzero legacy values mean warm milk."""
    if shots == "none" or (type(shots) is int and shots == 0):
        return "milk_froth"
    return "warm_milk"


def component_shots(
    process: str, shots: str, milk_mode: str | None = None, *, default: int = 1,
) -> int:
    """Apply an explicit milk mode only to milk; preserve legacy shots otherwise."""
    if process == "milk" and isinstance(milk_mode, str) and milk_mode in MILK_MODE_SHOTS:
        return MILK_MODE_SHOTS[milk_mode]
    return SHOTS_MAP.get(shots, default)
