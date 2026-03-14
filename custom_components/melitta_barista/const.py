"""Constants for the Melitta Barista Smart integration."""

from __future__ import annotations

from enum import IntEnum, IntFlag
from uuid import UUID

DOMAIN = "melitta_barista"


# ---------------------------------------------------------------------------
# Machine types
# ---------------------------------------------------------------------------

class MachineType(IntEnum):
    """Machine type IDs read via HR id=6."""
    BARISTA_T = 258
    BARISTA_TS = 259


MACHINE_TYPE_SETTING_ID = 6  # HR id to read machine type

# BLE device name prefixes per model
BLE_PREFIXES_T: set[str] = {"8301", "8311", "8401"}
BLE_PREFIXES_TS: set[str] = {"8501", "8601", "8604"}
BLE_PREFIXES_ALL: set[str] = BLE_PREFIXES_T | BLE_PREFIXES_TS

MACHINE_MODEL_NAMES: dict[MachineType, str] = {
    MachineType.BARISTA_T: "Barista T Smart",
    MachineType.BARISTA_TS: "Barista TS Smart",
}


def detect_machine_type_from_name(device_name: str) -> MachineType | None:
    """Determine machine type from BLE device name prefix."""
    for prefix in BLE_PREFIXES_T:
        if device_name.startswith(prefix):
            return MachineType.BARISTA_T
    for prefix in BLE_PREFIXES_TS:
        if device_name.startswith(prefix):
            return MachineType.BARISTA_TS
    return None

# BLE GATT UUIDs
CHAR_NOTIFY = UUID("0000ad02-b35c-11e4-9813-0002a5d5c51b")
CHAR_WRITE = UUID("0000ad03-b35c-11e4-9813-0002a5d5c51b")

# Frame markers
FRAME_START = 0x53  # 'S'
FRAME_END = 0x45  # 'E'
FRAME_TIMEOUT = 5  # seconds
BLE_MTU = 20

# Frame commands
CMD_ACK = "A"
CMD_NACK = "N"
CMD_READ_ALPHA = "HA"
CMD_WRITE_ALPHA = "HB"
CMD_READ_RECIPE = "HC"
CMD_START_PROCESS = "HE"
CMD_WRITE_RECIPE = "HJ"
CMD_READ_NUMERICAL = "HR"
CMD_READ_VERSION = "HV"
CMD_WRITE_NUMERICAL = "HW"
CMD_READ_STATUS = "HX"
CMD_CANCEL_PROCESS = "HZ"
CMD_HANDSHAKE = "HU"

# Protocol encryption keys
AES_KEY_PART_B = bytes([
    125, 57, 51, 41, 121, 78, -30 & 0xFF, 10, -62 & 0xFF, -22 & 0xFF,
    -27 & 0xFF, -19 & 0xFF, -89 & 0xFF, -85 & 0xFF, 3, 40, -12 & 0xFF,
])
AES_KEY_PART_A = bytes([
    99, -127 & 0xFF, 119, 125, 118, 101, -102 & 0xFF, -108 & 0xFF,
    -39 & 0xFF, 100, -61 & 0xFF, -117 & 0xFF, -95 & 0xFF, -65 & 0xFF,
    -14 & 0xFF,
])
AES_IV = bytes([
    -72 & 0xFF, -1 & 0xFF, -122 & 0xFF, -122 & 0xFF, 64, -10 & 0xFF,
    12, -118 & 0xFF, 25, 69, -117 & 0xFF, -123 & 0xFF, 58, -99 & 0xFF,
    93, -2 & 0xFF,
])
ENCRYPTED_RC4_KEY = bytes([
    -81 & 0xFF, -14 & 0xFF, 21, -30 & 0xFF, 26, 60, 54, -89 & 0xFF,
    11, -42 & 0xFF, 95, -65 & 0xFF, 125, -6 & 0xFF, -99 & 0xFF, -111 & 0xFF,
    65, -16 & 0xFF, 14, 36, -126 & 0xFF, -40 & 0xFF, 13, -28 & 0xFF,
    15, 114, -48 & 0xFF, 48, -28 & 0xFF, -9 & 0xFF, -87 & 0xFF, 63,
    72, 122, -75 & 0xFF, 57, -13 & 0xFF, 101, 23, -7 & 0xFF,
    123, -9 & 0xFF, -66 & 0xFF, -30 & 0xFF, -87 & 0xFF, 5, -113 & 0xFF, -47 & 0xFF,
])


class MachineProcess(IntEnum):
    """Machine process states."""
    READY = 2
    PRODUCT = 4
    CLEANING = 9
    DESCALING = 10
    FILTER_INSERT = 11
    FILTER_REPLACE = 12
    FILTER_REMOVE = 13
    SWITCH_OFF = 16
    EASY_CLEAN = 17
    INTENSIVE_CLEAN = 19
    EVAPORATING = 20
    BUSY = 99


class SubProcess(IntEnum):
    """Sub-process states during preparation."""
    GRINDING = 1
    COFFEE = 2
    STEAM = 3
    WATER = 4
    PREPARE = 5


class InfoMessage(IntFlag):
    """Info message bitfield."""
    FILL_BEANS_1 = 1 << 0
    FILL_BEANS_2 = 1 << 1
    EASY_CLEAN = 1 << 2
    POWDER_FILLED = 1 << 3
    PREPARATION_CANCELLED = 1 << 4


class Manipulation(IntEnum):
    """Manipulation states requiring user action."""
    NONE = 0
    BU_REMOVED = 1
    TRAYS_MISSING = 2
    EMPTY_TRAYS = 3
    FILL_WATER = 4
    CLOSE_POWDER_LID = 5
    FILL_POWDER = 6


class RecipeId(IntEnum):
    """Built-in recipe IDs."""
    ESPRESSO = 200
    RISTRETTO = 201
    LUNGO = 202
    ESPRESSO_DOPIO = 203
    RISETTO_DOPIO = 204
    CAFE_CREME = 205
    CAFE_CREME_DOPIO = 206
    AMERICANO = 207
    AMERICANO_EXTRA = 208
    LONG_BLACK = 209
    RED_EYE = 210
    BLACK_EYE = 211
    DEAD_EYE = 212
    CAPPUCCINO = 213
    ESPR_MACCHIATO = 214
    CAFFE_LATTE = 215
    CAFE_AU_LAIT = 216
    FLAT_WHITE = 217
    LATTE_MACCHIATO = 218
    LATTE_MACCHIATO_EXTRA = 219
    LATTE_MACCHIATO_TRIPLE = 220
    MILK = 221
    MILK_FROTH = 222
    WATER = 223


RECIPE_NAMES: dict[int, str] = {
    RecipeId.ESPRESSO: "Espresso",
    RecipeId.RISTRETTO: "Ristretto",
    RecipeId.LUNGO: "Lungo",
    RecipeId.ESPRESSO_DOPIO: "Espresso Doppio",
    RecipeId.RISETTO_DOPIO: "Ristretto Doppio",
    RecipeId.CAFE_CREME: "Café Crème",
    RecipeId.CAFE_CREME_DOPIO: "Café Crème Doppio",
    RecipeId.AMERICANO: "Americano",
    RecipeId.AMERICANO_EXTRA: "Americano Extra",
    RecipeId.LONG_BLACK: "Long Black",
    RecipeId.RED_EYE: "Red Eye",
    RecipeId.BLACK_EYE: "Black Eye",
    RecipeId.DEAD_EYE: "Dead Eye",
    RecipeId.CAPPUCCINO: "Cappuccino",
    RecipeId.ESPR_MACCHIATO: "Espresso Macchiato",
    RecipeId.CAFFE_LATTE: "Caffè Latte",
    RecipeId.CAFE_AU_LAIT: "Café au Lait",
    RecipeId.FLAT_WHITE: "Flat White",
    RecipeId.LATTE_MACCHIATO: "Latte Macchiato",
    RecipeId.LATTE_MACCHIATO_EXTRA: "Latte Macchiato Extra",
    RecipeId.LATTE_MACCHIATO_TRIPLE: "Latte Macchiato Triple",
    RecipeId.MILK: "Milk",
    RecipeId.MILK_FROTH: "Milk Froth",
    RecipeId.WATER: "Hot Water",
}


# Freestyle / temp recipe constants
TEMP_RECIPE_ID = 400
FREESTYLE_NAME_ID = 401
FREESTYLE_RECIPE_TYPE = 24  # RecipeType.FREESTYLE byte value


class ComponentProcess(IntEnum):
    """Process type within a recipe component (coffee, steam, water)."""
    NONE = 0
    COFFEE = 1
    STEAM = 2
    WATER = 3


class DirectKeyCategory(IntEnum):
    """DirectKey recipe categories per profile."""
    ESPRESSO = 0
    CAFE_CREME = 1
    CAPPUCCINO = 2
    LATTE_MACCHIATO = 3
    MILK_FROTH = 4
    MILK = 5
    WATER = 6


# DirectKey offset and multiplier
DIRECTKEY_OFFSET = 302
DIRECTKEY_PROFILE_MULTIPLIER = 10


def get_directkey_id(profile_id: int, category: DirectKeyCategory) -> int:
    """Calculate DirectKey recipe ID for a profile and category."""
    return DIRECTKEY_OFFSET + profile_id * DIRECTKEY_PROFILE_MULTIPLIER + category


# Default recipe_type for each DirectKey category (fallback when read fails)
DIRECTKEY_DEFAULT_RECIPE_TYPE: dict[DirectKeyCategory, int] = {
    DirectKeyCategory.ESPRESSO: 0,
    DirectKeyCategory.CAFE_CREME: 5,
    DirectKeyCategory.CAPPUCCINO: 13,
    DirectKeyCategory.LATTE_MACCHIATO: 18,
    DirectKeyCategory.MILK_FROTH: 22,
    DirectKeyCategory.MILK: 21,
    DirectKeyCategory.WATER: 23,
}


# Map RecipeId -> DirectKeyCategory for profile-based brewing
RECIPE_TO_DIRECTKEY: dict[int, DirectKeyCategory] = {
    RecipeId.ESPRESSO: DirectKeyCategory.ESPRESSO,
    RecipeId.RISTRETTO: DirectKeyCategory.ESPRESSO,
    RecipeId.LUNGO: DirectKeyCategory.ESPRESSO,
    RecipeId.ESPRESSO_DOPIO: DirectKeyCategory.ESPRESSO,
    RecipeId.RISETTO_DOPIO: DirectKeyCategory.ESPRESSO,
    RecipeId.CAFE_CREME: DirectKeyCategory.CAFE_CREME,
    RecipeId.CAFE_CREME_DOPIO: DirectKeyCategory.CAFE_CREME,
    RecipeId.AMERICANO: DirectKeyCategory.CAFE_CREME,
    RecipeId.AMERICANO_EXTRA: DirectKeyCategory.CAFE_CREME,
    RecipeId.LONG_BLACK: DirectKeyCategory.CAFE_CREME,
    RecipeId.RED_EYE: DirectKeyCategory.CAFE_CREME,
    RecipeId.BLACK_EYE: DirectKeyCategory.CAFE_CREME,
    RecipeId.DEAD_EYE: DirectKeyCategory.CAFE_CREME,
    RecipeId.CAPPUCCINO: DirectKeyCategory.CAPPUCCINO,
    RecipeId.ESPR_MACCHIATO: DirectKeyCategory.LATTE_MACCHIATO,
    RecipeId.CAFFE_LATTE: DirectKeyCategory.CAPPUCCINO,
    RecipeId.CAFE_AU_LAIT: DirectKeyCategory.CAPPUCCINO,
    RecipeId.FLAT_WHITE: DirectKeyCategory.CAPPUCCINO,
    RecipeId.LATTE_MACCHIATO: DirectKeyCategory.LATTE_MACCHIATO,
    RecipeId.LATTE_MACCHIATO_EXTRA: DirectKeyCategory.LATTE_MACCHIATO,
    RecipeId.LATTE_MACCHIATO_TRIPLE: DirectKeyCategory.LATTE_MACCHIATO,
    RecipeId.MILK: DirectKeyCategory.MILK,
    RecipeId.MILK_FROTH: DirectKeyCategory.MILK_FROTH,
    RecipeId.WATER: DirectKeyCategory.WATER,
}

# Profile names for select entity
PROFILE_NAMES = {0: "My Coffee"}  # Profile 0 is default


# User profile name IDs: 310, 320, ..., 380
USER_NAME_IDS = {i: 310 + (i - 1) * 10 for i in range(1, 9)}
# User activity IDs: 311, 321, ..., 381
USER_ACTIVITY_IDS = {i: 311 + (i - 1) * 10 for i in range(1, 9)}

# Profile count per model
# T: profiles 0-4 (5 total), TS: profiles 0-8 (9 total)
# Profile 0 is "My Coffee" (default), profiles 1-N are user-named
PROFILE_COUNTS: dict[MachineType, int] = {
    MachineType.BARISTA_T: 5,
    MachineType.BARISTA_TS: 9,
}


def get_user_profile_count(machine_type: MachineType | None) -> int:
    """Return number of user-configurable profiles (excluding default profile 0)."""
    if machine_type is None:
        return 8  # max
    total = PROFILE_COUNTS.get(machine_type, 5)
    return total - 1  # exclude profile 0 (default)

# RecipeType values
RECIPE_TYPE_MAP: dict[int, int] = {
    RecipeId.ESPRESSO: 0, RecipeId.RISTRETTO: 1, RecipeId.LUNGO: 2,
    RecipeId.ESPRESSO_DOPIO: 3, RecipeId.RISETTO_DOPIO: 4,
    RecipeId.CAFE_CREME: 5, RecipeId.CAFE_CREME_DOPIO: 6,
    RecipeId.AMERICANO: 7, RecipeId.AMERICANO_EXTRA: 8,
    RecipeId.LONG_BLACK: 9, RecipeId.RED_EYE: 10,
    RecipeId.BLACK_EYE: 11, RecipeId.DEAD_EYE: 12,
    RecipeId.CAPPUCCINO: 13, RecipeId.ESPR_MACCHIATO: 14,
    RecipeId.CAFFE_LATTE: 15, RecipeId.CAFE_AU_LAIT: 16,
    RecipeId.FLAT_WHITE: 17, RecipeId.LATTE_MACCHIATO: 18,
    RecipeId.LATTE_MACCHIATO_EXTRA: 19, RecipeId.LATTE_MACCHIATO_TRIPLE: 20,
    RecipeId.MILK: 21, RecipeId.MILK_FROTH: 22, RecipeId.WATER: 23,
}

# RecipeKey values
RECIPE_KEY_MAP: dict[int, int] = {
    RecipeId.ESPRESSO: 0, RecipeId.RISTRETTO: 0, RecipeId.LUNGO: 0,
    RecipeId.ESPRESSO_DOPIO: 0, RecipeId.RISETTO_DOPIO: 0,
    RecipeId.CAFE_CREME: 1, RecipeId.CAFE_CREME_DOPIO: 1,
    RecipeId.AMERICANO: 1, RecipeId.AMERICANO_EXTRA: 1,
    RecipeId.LONG_BLACK: 1, RecipeId.RED_EYE: 1,
    RecipeId.BLACK_EYE: 1, RecipeId.DEAD_EYE: 1,
    RecipeId.CAPPUCCINO: 2, RecipeId.ESPR_MACCHIATO: 2,
    RecipeId.CAFFE_LATTE: 2, RecipeId.CAFE_AU_LAIT: 2,
    RecipeId.FLAT_WHITE: 2, RecipeId.LATTE_MACCHIATO: 3,
    RecipeId.LATTE_MACCHIATO_EXTRA: 3, RecipeId.LATTE_MACCHIATO_TRIPLE: 3,
    RecipeId.MILK: 5, RecipeId.MILK_FROTH: 4, RecipeId.WATER: 6,
}

# RecipeType byte → RecipeKey byte (for HJ write payload)
# From decompiled E3/Z.java: each RecipeType maps to a RecipeKey category
RECIPE_TYPE_TO_KEY: dict[int, int] = {
    0: 0, 1: 0, 2: 0, 3: 0, 4: 0,          # Espresso family → ESPRESSO(0)
    5: 1, 6: 1, 7: 1, 8: 1, 9: 1,          # Café Crème family → COFFEE(1)
    10: 1, 11: 1, 12: 1,                     # Red/Black/Dead Eye → COFFEE(1)
    13: 2, 14: 2, 15: 2, 16: 2, 17: 2,      # Cappuccino family → CAPPUCCINO(2)
    18: 3, 19: 3, 20: 3,                     # Latte Macchiato → MACCHIATO(3)
    21: 5,                                    # Milk → MILK(5)
    22: 4,                                    # Milk Froth → MILK_FROTH(4)
    23: 6,                                    # Water → WATER(6)
    24: 7,                                    # Freestyle → MENU(7)
}


def get_recipe_key(recipe_type: int) -> int:
    """Get RecipeKey byte value for a given RecipeType byte value."""
    return RECIPE_TYPE_TO_KEY.get(recipe_type, 7)  # default MENU(7) for unknown


# TS-only recipes
TS_ONLY_RECIPES: set[int] = {
    RecipeId.RED_EYE,
    RecipeId.BLACK_EYE,
    RecipeId.DEAD_EYE,
}

# Base recipes available on all models
BASE_RECIPES: list[int] = [
    r for r in RecipeId if r not in TS_ONLY_RECIPES
]


def get_available_recipes(machine_type: MachineType | None) -> list[int]:
    """Return recipe IDs available for the given machine type."""
    if machine_type == MachineType.BARISTA_TS or machine_type is None:
        return list(RecipeId)
    return BASE_RECIPES


class Intensity(IntEnum):
    """Coffee intensity levels."""
    VERY_MILD = 0
    MILD = 1
    MEDIUM = 2
    STRONG = 3
    VERY_STRONG = 4


class Aroma(IntEnum):
    """Aroma settings."""
    STANDARD = 0
    INTENSE = 1


class Temperature(IntEnum):
    """Temperature settings."""
    COLD = 0
    NORMAL = 1
    HIGH = 2


class Blend(IntEnum):
    """Bean blend selection."""
    BARISTA_T = 0
    BLEND_1 = 1
    BLEND_2 = 2


class Shots(IntEnum):
    """Number of shots."""
    NONE = 0
    ONE = 1
    TWO = 2
    THREE = 3


class MachineSettingId(IntEnum):
    """Machine setting IDs for numerical read/write."""
    WATER_HARDNESS = 11
    ENERGY_SAVING = 12
    AUTO_OFF_AFTER = 13
    AUTO_OFF_WHEN = 14
    LANGUAGE = 15
    AUTO_BEAN_SELECT = 16
    RINSING_OFF = 18
    CLOCK = 20
    CLOCK_SEND = 21
    TEMPERATURE = 22
    FILTER = 91


# TS-only settings (Barista T has one bean hopper)
TS_ONLY_SETTINGS: set[int] = {
    MachineSettingId.AUTO_BEAN_SELECT,
}


# ---------------------------------------------------------------------------
# Cup counters (discovered via BLE ID scan)
# ---------------------------------------------------------------------------

# Per-recipe cup counter: HR ID = CUP_COUNTER_BASE + RecipeType
# e.g. Espresso (type 0) → ID 100, Cappuccino (type 13) → ID 113
CUP_COUNTER_BASE_ID = 100
TOTAL_CUPS_ID = 150

# Map RecipeType offset → recipe name for cup counters
CUP_COUNTER_RECIPES: dict[int, str] = {
    0: "Espresso",
    1: "Ristretto",
    2: "Lungo",
    3: "Espresso Doppio",
    4: "Ristretto Doppio",
    5: "Café Crème",
    6: "Café Crème Doppio",
    7: "Americano",
    8: "Americano Extra",
    9: "Long Black",
    10: "Red Eye",
    11: "Black Eye",
    12: "Dead Eye",
    13: "Cappuccino",
    14: "Espresso Macchiato",
    15: "Caffè Latte",
    16: "Café au Lait",
    17: "Flat White",
    18: "Latte Macchiato",
    19: "Latte Macchiato Extra",
    20: "Latte Macchiato Triple",
    21: "Milk",
    22: "Milk Froth",
    23: "Hot Water",
}

# ---------------------------------------------------------------------------
# Freestyle / service parameter mappings (name → protocol value)
# ---------------------------------------------------------------------------

DEFAULT_POLL_INTERVAL: float = 5.0

# ---------------------------------------------------------------------------
# Options Flow keys and defaults
# ---------------------------------------------------------------------------

# Option keys
CONF_POLL_INTERVAL = "poll_interval"
CONF_RECONNECT_DELAY = "reconnect_initial_delay"
CONF_RECONNECT_MAX_DELAY = "reconnect_max_delay"
CONF_MAX_CONSECUTIVE_ERRORS = "max_consecutive_errors"
CONF_FRAME_TIMEOUT = "frame_timeout"
CONF_BLE_CONNECT_TIMEOUT = "ble_connect_timeout"
CONF_PAIR_TIMEOUT = "pair_timeout"
CONF_RECIPE_RETRIES = "recipe_retries"
CONF_INITIAL_CONNECT_DELAY = "initial_connect_delay"

# Defaults
DEFAULT_RECONNECT_DELAY: float = 5.0
DEFAULT_RECONNECT_MAX_DELAY: float = 300.0
DEFAULT_MAX_CONSECUTIVE_ERRORS: int = 3
DEFAULT_FRAME_TIMEOUT: int = 5
DEFAULT_BLE_CONNECT_TIMEOUT: float = 15.0
DEFAULT_PAIR_TIMEOUT: float = 30.0
DEFAULT_RECIPE_RETRIES: int = 3
DEFAULT_INITIAL_CONNECT_DELAY: float = 3.0

PROCESS_MAP: dict[str, int] = {"none": 0, "coffee": 1, "milk": 2, "water": 3}
INTENSITY_MAP: dict[str, int] = {
    "very_mild": 0, "mild": 1, "medium": 2, "strong": 3, "very_strong": 4,
}
AROMA_MAP: dict[str, int] = {"standard": 0, "intense": 1}
TEMPERATURE_MAP: dict[str, int] = {"cold": 0, "normal": 1, "high": 2}
SHOTS_MAP: dict[str, int] = {"none": 0, "one": 1, "two": 2, "three": 3}
