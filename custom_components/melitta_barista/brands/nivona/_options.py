"""Setting-option enumerations shared across Nivona families.

Each tuple maps a 16-bit value code (as observed on the wire) to the
human-readable label exposed via the HA select entity. Tables are
defined here once and referenced from per-family settings modules, so
adding a new family doesn't duplicate the (value, label) pairs.
"""

from __future__ import annotations

_HARDNESS_OPTIONS = ((0x0000, "soft"), (0x0001, "medium"), (0x0002, "hard"), (0x0003, "very hard"))
_OFF_ON_OPTIONS = ((0x0000, "off"), (0x0001, "on"))
_AUTO_OFF_8000_OPTIONS = (
    (0x0000, "10 min"), (0x0001, "30 min"), (0x0002, "1 h"),
    (0x0003, "2 h"), (0x0004, "4 h"), (0x0005, "6 h"),
    (0x0006, "8 h"), (0x0007, "10 h"), (0x0008, "12 h"),
    (0x0009, "14 h"), (0x0010, "16 h"),
)
_AUTO_OFF_STANDARD_OPTIONS = (
    (0x0000, "10 min"), (0x0001, "30 min"), (0x0002, "1 h"),
    (0x0003, "2 h"), (0x0004, "4 h"), (0x0005, "6 h"),
    (0x0006, "8 h"), (0x0007, "10 h"), (0x0008, "12 h"),
    (0x0009, "off"),
)
_TEMP_ON_OFF = ((0x0000, "off"), (0x0001, "on"))
_TEMPERATURE_OPTIONS = ((0x0000, "normal"), (0x0001, "high"), (0x0002, "max"), (0x0003, "individual"))
_PROFILE_STANDARD_OPTIONS = (
    (0x0000, "dynamic"), (0x0001, "constant"),
    (0x0002, "intense"), (0x0003, "individual"),
)
_PROFILE_1040_OPTIONS = (
    (0x0000, "dynamic"), (0x0001, "constant"),
    (0x0002, "intense"), (0x0003, "quick"), (0x0004, "individual"),
)
_MILK_TEMPERATURE_1030_OPTIONS = (
    (0x0000, "high"), (0x0001, "max"), (0x0002, "individual"),
)
_MILK_TEMPERATURE_1040_OPTIONS = (
    (0x0000, "normal"), (0x0001, "high"), (0x0002, "hot"),
    (0x0003, "max"), (0x0004, "individual"),
)
_MILK_FOAM_TEMPERATURE_1040_OPTIONS = (
    (0x0000, "warm"), (0x0001, "max"), (0x0002, "individual"),
)
_POWER_ON_FROTHER_TIME_1040_OPTIONS = (
    (0x0000, "10 min"), (0x0001, "20 min"),
    (0x0002, "30 min"), (0x0003, "40 min"),
)

# 900 family — tank-lighting accent + save-energy add-ons that do not
# exist on other Nivona families.
_TANK_LIGHT_COLOR_900_OPTIONS = (
    (0x0000, "white"), (0x0001, "red"),    (0x0002, "orange"),
    (0x0003, "yellow"), (0x0004, "green"), (0x0005, "cyan"),
    (0x0006, "blue"),   (0x0007, "violet"),(0x0008, "rainbow"),
)
_TANK_LIGHT_BRIGHTNESS_900_OPTIONS = (
    (0x0000, "low"), (0x0001, "medium"), (0x0002, "high"),
)
# "Minutes since midnight" bucket for AutoOn hour/minute pair. Raw
# values go through unchanged (0..59 / 0..23); we surface them as a
# number entity rather than an options list.
