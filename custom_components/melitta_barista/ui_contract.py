"""UI Contract builder (docs/UI_CONTRACT.md; v2 additions per §6, v3 per §9).

Pure, BLE-free derivation of the renderer-facing contract: token
vocabularies, capability blocks, live status tokens, bridge attributes,
procedural drink icon specs, the v2 parameter/action catalogs (§6.1/§6.2),
the v3 settings/DirectKey blocks (§9.1/§9.3 — all additive within
contract_version 1), and the full `ui_contract/get` document.
Everything here is computed from data the integration already holds after
handshake (MachineCapabilities, BrandProfile, const maps, the client-side
base-recipe cache) — building a contract never triggers BLE traffic.

Token vocabularies are generated from the real enums / const maps, never
hand-copied, so a new enum member automatically ships as a new token
(additive growth per spec §5.2.2). The only intra-package imports are
`const.py` and `brands/nivona/_options.py` (shared pure data tables) —
no homeassistant modules.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from .coffee_platform.domain import (
    InfoMessage,
    MachineProcess,
    Manipulation,
    SubProcess,
)
from .brands.nivona._options import nivona_number_range, option_tokens
from .const import (
    AROMA_MAP,
    BLEND_MAP,
    DIRECTKEY_CATEGORY_ICONS,
    DIRECTKEY_NO_BUTTON_CATEGORIES,
    DirectKeyCategory,
    INTENSITY_MAP,
    MELITTA_SETTING_TABLES,
    MachineType,
    PROCESS_MAP,
    PROMPT_MANIPULATIONS,
    RECIPE_KEY_MAP,
    RECIPE_NAMES,
    SETTING_LEVEL_TOKENS,
    SHOTS_MAP,
    TEMPERATURE_MAP,
    get_available_recipes,
)

_LOGGER = logging.getLogger("melitta_barista")

# ---------------------------------------------------------------------------
# Version constants (spec §5.1)
# ---------------------------------------------------------------------------

CONTRACT_VERSION: int = 1
"""The contract compatibility gate. Bumped ONLY on a breaking change."""

ICON_SPEC_VERSION: int = 1
"""Versions the IconSpec sub-schema independently of the contract."""


class ContractNotReadyError(Exception):
    """No contract can be built yet: the client has no MachineCapabilities.

    Raised before the first successful handshake (fresh install with the
    machine off, HA restart before reconnect). The WS layer maps this to
    the `contract_not_ready` error code (spec §2.2); clients treat it as
    transient. The server never invents placeholder capability values.
    """


# ---------------------------------------------------------------------------
# Token vocabularies — generated from the real enums / const maps (§3.1/§3.2)
# ---------------------------------------------------------------------------

STATUS_PROCESS_TOKENS: tuple[str, ...] = tuple(m.name for m in MachineProcess)
"""UPPER_SNAKE status.process tokens, byte-equal to MachineProcess names."""

STATUS_SUB_PROCESS_TOKENS: tuple[str, ...] = tuple(m.name for m in SubProcess)
"""UPPER_SNAKE status.sub_process tokens, byte-equal to SubProcess names."""

STATUS_MANIPULATION_TOKENS: tuple[str, ...] = tuple(m.name for m in Manipulation)
"""UPPER_SNAKE status.manipulation tokens, byte-equal to Manipulation names."""

STATUS_INFO_MESSAGE_TOKENS: tuple[str, ...] = tuple(m.name for m in InfoMessage)
"""UPPER_SNAKE status.info_message tokens, byte-equal to InfoMessage names."""

MACHINE_TYPE_TOKENS: tuple[str, ...] = tuple(m.name for m in MachineType)
"""Known machine_type tokens (BARISTA_T / BARISTA_TS)."""


def _map_tokens(table: Mapping[str, int]) -> tuple[str, ...]:
    """Const-map keys ordered by their wire value — the frozen value tokens."""
    return tuple(sorted(table, key=lambda key: table[key]))


FREESTYLE_PROCESS_TOKENS: tuple[str, ...] = _map_tokens(PROCESS_MAP)
"""lower_snake freestyle process tokens (PROCESS_MAP keys)."""

FREESTYLE_INTENSITY_TOKENS: tuple[str, ...] = _map_tokens(INTENSITY_MAP)
"""lower_snake freestyle intensity tokens (INTENSITY_MAP keys)."""

FREESTYLE_AROMA_TOKENS: tuple[str, ...] = _map_tokens(AROMA_MAP)
"""lower_snake freestyle aroma tokens (AROMA_MAP keys)."""

FREESTYLE_TEMPERATURE_TOKENS: tuple[str, ...] = _map_tokens(TEMPERATURE_MAP)
"""lower_snake freestyle temperature tokens (TEMPERATURE_MAP keys)."""

FREESTYLE_SHOTS_TOKENS: tuple[str, ...] = _map_tokens(SHOTS_MAP)
"""lower_snake freestyle shots tokens (SHOTS_MAP keys)."""

FREESTYLE_BLEND_TOKENS: tuple[str, ...] = _map_tokens(BLEND_MAP)
"""lower_snake blend tokens (BLEND_MAP keys); wire byte 0 has no token."""


# Reverse const maps: wire byte -> token (used by component_to_tokens).
_PROCESS_NAMES = {value: key for key, value in PROCESS_MAP.items()}
_INTENSITY_NAMES = {value: key for key, value in INTENSITY_MAP.items()}
_AROMA_NAMES = {value: key for key, value in AROMA_MAP.items()}
_TEMPERATURE_NAMES = {value: key for key, value in TEMPERATURE_MAP.items()}
_SHOTS_NAMES = {value: key for key, value in SHOTS_MAP.items()}
_BLEND_NAMES = {int(value): key for key, value in BLEND_MAP.items()}

# §4.3 darkness index. The normative token for level 2 is the const-map
# key "medium" (spec Appendix A.1 amendment 1); "normal" — the pre-amendment
# draft spelling — is accepted defensively as an input alias, never emitted.
_INTENSITY_INDEX: dict[str, int] = {**INTENSITY_MAP, "normal": 2}

# ---------------------------------------------------------------------------
# IconSpec constants (§3.6, normative for spec_version 1)
# ---------------------------------------------------------------------------

NOMINAL_GLASS_VOLUMES: dict[str, int] = {
    "espresso_cup": 60,
    "cup": 220,
    "tall_glass": 320,
}
"""Nominal glass volumes in ml, normative for `spec_version: 1`."""

_COLOR_HINT_RE = re.compile(r"#[0-9a-fA-F]{6}\Z")

# Recipe categories for the Melitta catalog, derived from the const
# RecipeKey table (0 espresso / 1 coffee / 2-5 milk drinks / 6 water).
_RECIPE_KEY_TO_CATEGORY: dict[int, str] = {
    0: "espresso",
    1: "coffee",
    2: "milk_drink",
    3: "milk_drink",
    4: "milk_drink",
    5: "milk_drink",
    6: "water",
}
MELITTA_RECIPE_CATEGORIES: dict[int, str] = {
    int(recipe_id): _RECIPE_KEY_TO_CATEGORY.get(key, "")
    for recipe_id, key in RECIPE_KEY_MAP.items()
}
"""RecipeId -> contract category token, generated from RECIPE_KEY_MAP."""

MELITTA_RECIPE_NAME_KEYS: dict[int, str] = {
    200: "espresso",
    201: "ristretto",
    202: "lungo",
    203: "espresso_doppio",
    204: "ristretto_doppio",
    205: "cafe_creme",
    206: "cafe_creme_doppio",
    207: "americano",
    208: "americano_extra",
    209: "long_black",
    210: "red_eye",
    211: "black_eye",
    212: "dead_eye",
    213: "cappuccino",
    214: "espresso_macchiato",
    215: "caffe_latte",
    216: "cafe_au_lait",
    217: "flat_white",
    218: "latte_macchiato",
    219: "latte_macchiato_extra",
    220: "latte_macchiato_triple",
    221: "milk",
    222: "milk_froth",
    223: "hot_water",
}
"""RecipeId -> stable ASCII lower_snake i18n `name_key` (spec §6.3.6).

Authored once, matching the existing 24-entry recipe translation block;
never derived from display names at runtime (a rename would silently
orphan translations).
"""

# §4.8 fixed synthetic compositions for composition-less recipes.
_CATEGORY_COMPOSITIONS: dict[str, tuple[dict[str, Any], ...]] = {
    "espresso": (
        {"process": "coffee", "intensity": "strong", "aroma": "standard",
         "temperature": "normal", "shots": "one", "portion_ml": 40},
    ),
    "coffee": (
        {"process": "coffee", "intensity": "medium", "aroma": "standard",
         "temperature": "normal", "shots": "one", "portion_ml": 120},
    ),
    "milk_drink": (
        {"process": "coffee", "intensity": "strong", "aroma": "standard",
         "temperature": "normal", "shots": "one", "portion_ml": 40},
        {"process": "milk", "intensity": "medium", "aroma": "standard",
         "temperature": "normal", "shots": "none", "portion_ml": 140},
    ),
    "water": (
        {"process": "water", "intensity": "medium", "aroma": "standard",
         "temperature": "high", "shots": "none", "portion_ml": 200},
    ),
}

# v1 portion limits (spec §3.3): c1 is the mandatory first component.
_PORTION_LIMITS: dict[str, dict[str, int]] = {
    "c1": {"min": 5, "max": 250, "step": 5},
    "c2": {"min": 0, "max": 250, "step": 5},
}


# ---------------------------------------------------------------------------
# Deterministic rounding helpers (§4.9)
# ---------------------------------------------------------------------------

def _ratio_round2(numerator: int, denominator: int) -> float:
    """Round numerator/denominator to 2 decimals, half-up, float-safe.

    Computed in exact integer arithmetic so 0.625 rounds to 0.63 (not the
    banker's 0.62) and no binary-float noise can flip a worked example.
    """
    return ((numerator * 200 + denominator) // (2 * denominator)) / 100


def _round5(value: float) -> int:
    """Round a millilitre value to the nearest multiple of 5."""
    return int(round(value / 5.0)) * 5


def _normalize_color_hint(value: Any) -> str | None:
    """Normalize an additive color hint to lowercase '#rrggbb', else None.

    §3.6: `color_hint` is `#RRGGBB` only — the server validates and emits
    `null` for anything else (never markup).
    """
    if isinstance(value, str) and _COLOR_HINT_RE.fullmatch(value):
        return value.lower()
    return None


# ---------------------------------------------------------------------------
# brand_theme (§3.10) — brand badge DATA only, never a logo asset
# ---------------------------------------------------------------------------

# Per-brand normative badge values (spec §3.10, server-owned — clients MUST
# NOT hardcode them). Legal constraint: the integration never ships or
# distributes brand logos; this table is a slug, a wordmark display string
# rendered as text, and accent colors.
_BRAND_THEMES: dict[str, dict[str, str]] = {
    "melitta": {
        "wordmark": "MELITTA", "accent": "#c8102e", "accent_soft": "#f6e3e6",
    },
    "nivona": {
        "wordmark": "NIVONA", "accent": "#00646b", "accent_soft": "#e0eeef",
    },
}

# Defensive non-normative fallback for a brand slug the table predates:
# neutral grey accents; clients render such badges unbranded anyway (§3.10).
_NEUTRAL_ACCENT = "#607d8b"
_NEUTRAL_ACCENT_SOFT = "#eceff1"


def build_brand_theme(client: Any, logo_url: str | None) -> dict[str, Any]:
    """Build the top-level `brand_theme` badge block (§3.10).

    Data only — slug, wordmark text, accent colors — never a logo asset.
    `logo_url` is the setup-time cached result of the user-supplied-file
    check (`/local/melitta_barista/<brand>.png` or None), passed through
    verbatim: this function does no I/O. A slug missing from the normative
    table gets a neutral fallback (wordmark from the profile's brand name).
    """
    brand = client.brand
    slug = brand.brand_slug
    theme = _BRAND_THEMES.get(slug)
    if theme is None:
        theme = {
            "wordmark": str(getattr(brand, "brand_name", slug) or slug).upper(),
            "accent": _NEUTRAL_ACCENT,
            "accent_soft": _NEUTRAL_ACCENT_SOFT,
        }
    return {
        "brand": slug,
        "wordmark": theme["wordmark"],
        "accent": theme["accent"],
        "accent_soft": theme["accent_soft"],
        "logo_url": logo_url,
    }


# ---------------------------------------------------------------------------
# Live status tokens (§3.4 block B)
# ---------------------------------------------------------------------------

def build_status_tokens(status: Any, connected: bool) -> dict[str, Any]:
    """Build the state sensor's additive token attributes (§3.4 block B).

    `status` is a parsed MachineStatus (or None before the first status).
    With no status — or while disconnected, which strips the entity's
    attributes anyway (the §3.4 server-side clarification) — every token
    is null and the booleans are False. On the wire `manipulation_token`
    is null iff status is None; "NONE" covers both the no-manipulation
    case and parsed-unknown manipulation codes.
    """
    if status is None or not connected:
        return {
            "process_token": None,
            "sub_process_token": None,
            "manipulation_token": None,
            "is_brewing": False,
            "awaiting_confirmation": False,
        }

    process = status.process
    sub_process = status.sub_process
    try:
        manipulation = Manipulation(status.manipulation)
    except ValueError:
        manipulation = Manipulation.NONE

    return {
        "process_token": process.name if process is not None else None,
        "sub_process_token": sub_process.name if sub_process is not None else None,
        "manipulation_token": manipulation.name,
        "is_brewing": process == MachineProcess.PRODUCT,
        "awaiting_confirmation": manipulation in PROMPT_MANIPULATIONS,
    }


# ---------------------------------------------------------------------------
# Bridge attributes + fingerprint (§3.4 block A, §5.1)
# ---------------------------------------------------------------------------

def _integration_version(client: Any) -> str | None:
    """The setup-time stashed integration version string, or None.

    §5.1 single-source rule: `async_setup_entry` resolves the manifest
    version once (async, via `async_get_integration`) and stashes it as
    `client.integration_version`; both sync fingerprint call sites read
    it back here so they are byte-identical by construction. A non-str
    value (absent attribute, test double) collapses to None so the
    fingerprint stays deterministic and JSON-serializable.
    """
    version = getattr(client, "integration_version", None)
    return version if isinstance(version, str) else None


def compute_contract_fingerprint(client: Any) -> str | None:
    """Content revision for this machine's contract: 12 hex chars of sha256.

    Computed over (family_key, model_name, machine_type, the capability-
    relevant profile fields, recipe-cache generation counter, brand-logo
    presence flag) per §5.1. The logo flag is the cached setup-time §3.10
    file-check result — fixed for the life of the entry runtime, so it can
    only differ across entry reloads. Carries no semantics beyond equality
    comparison. Returns None while the client has no capabilities
    (pre-handshake — no contract exists).

    0.92 delta (§5.1 amendment): the integration version string joins the
    inputs — read from `client.integration_version`, stashed once by
    `async_setup_entry` (single-source rule: both sync call sites, the
    connection sensor and the WS document, see the identical value) — so
    catalog-content changes shipped in a release refresh long-lived
    client sessions. `verified_maintenance_processes` (§6.2.6) joins too,
    because it feeds the served action catalog's `available` flags.
    """
    caps = getattr(client, "capabilities", None)
    if caps is None:
        return None
    machine_type = getattr(client, "machine_type", None)
    brand = client.brand
    verified = getattr(caps, "verified_maintenance_processes", None)
    payload = {
        "integration_version": _integration_version(client),
        "brand": brand.brand_slug,
        "family_key": caps.family_key,
        "model_name": caps.model_name,
        "machine_type": machine_type.name if machine_type is not None else None,
        "supported_extensions": sorted(brand.supported_extensions),
        "supports_recipe_writes": caps.supports_recipe_writes,
        "supports_stats": caps.supports_stats,
        "supports_factory_reset": caps.supports_factory_reset,
        "supports_brew_overrides": caps.supports_brew_overrides,
        "my_coffee_slots": caps.my_coffee_slots,
        "strength_levels": caps.strength_levels,
        "has_aroma_balance": caps.has_aroma_balance,
        "tolerated_brew_manipulations": list(caps.tolerated_brew_manipulations),
        "recipe_cache_generation": getattr(client, "recipe_cache_generation", 0),
        "brand_logo": bool(getattr(client, "brand_logo_url", None)),
        "verified_maintenance_processes": (
            None if verified is None else sorted(int(v) for v in verified)
        ),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def build_bridge_attributes(entry: Any, client: Any) -> dict[str, Any]:
    """Build the always-present connection sensor bridge block (§3.4 A).

    `entry_id`, `contract_version` and `connected` are always present so
    token-mode detection never flickers; `contract_fingerprint` is omitted
    only on a pre-handshake entry where no contract exists yet (matching
    the `contract_not_ready` WS error).
    """
    attrs: dict[str, Any] = {
        "entry_id": entry.entry_id,
        "contract_version": CONTRACT_VERSION,
    }
    fingerprint = compute_contract_fingerprint(client)
    if fingerprint is not None:
        attrs["contract_fingerprint"] = fingerprint
    attrs["connected"] = bool(getattr(client, "connected", False))
    return attrs


# ---------------------------------------------------------------------------
# Capabilities block (§3.3, derivation rules §3.5)
# ---------------------------------------------------------------------------

def _derive_hopper_count(brand: Any, machine_type: Any) -> int:
    """§3.5: Melitta is single-hopper ONLY on a confirmed BARISTA_T.

    Unknown/unrefined Melitta machine_type is treated as dual-hopper —
    mirroring select.py's blend gating and get_available_recipes. All
    currently supported Nivona families are single-hopper.
    """
    if brand.brand_slug == "melitta":
        return 1 if machine_type == MachineType.BARISTA_T else 2
    return 1


def _derive_has_milk_system(brand: Any, caps: Any) -> bool:
    """§3.5: milk system iff family layout has milk params, the catalog has
    a milk_drink, or the brand is Melitta (all Baristas have milk systems)."""
    if brand.brand_slug == "melitta":
        return True
    for getter_name in ("standard_recipe_layout", "mycoffee_layout"):
        getter = getattr(brand, getter_name, None)
        if getter is None:
            continue
        try:
            layout = getter(caps.family_key)
        except Exception:  # defensive: layout tables may not know the family
            layout = None
        if layout is not None and getattr(layout, "milk_amount_offset", None) is not None:
            return True
    return any(recipe.category == "milk_drink" for recipe in caps.recipes)


def _serialize_tolerated_manipulations(values: Iterable[int]) -> list[str]:
    """§3.5: map each tolerated int via Manipulation(value).name; ints
    without an enum member are omitted from the client-facing list."""
    tokens: list[str] = []
    for value in values:
        try:
            tokens.append(Manipulation(value).name)
        except ValueError:
            continue
    return tokens


def build_capabilities_block(client: Any) -> dict[str, Any]:
    """Build the contract `capabilities` block (§3.3) from the client.

    Raises ContractNotReadyError when the client has no MachineCapabilities
    (no successful handshake yet).
    """
    caps = getattr(client, "capabilities", None)
    if caps is None:
        raise ContractNotReadyError(
            "client has no MachineCapabilities yet (no handshake)"
        )
    brand = client.brand
    return {
        "supports_recipe_writes": caps.supports_recipe_writes,
        "supports_stats": caps.supports_stats,
        "supports_factory_reset": caps.supports_factory_reset,
        "supports_brew_overrides": caps.supports_brew_overrides,
        "supports_freestyle": "HJ" in brand.supported_extensions,
        "my_coffee_slots": caps.my_coffee_slots,
        "strength_levels": caps.strength_levels,
        "has_aroma_balance": caps.has_aroma_balance,
        "hopper_count": _derive_hopper_count(brand, getattr(client, "machine_type", None)),
        "has_milk_system": _derive_has_milk_system(brand, caps),
        "tolerated_brew_manipulations": _serialize_tolerated_manipulations(
            caps.tolerated_brew_manipulations
        ),
    }


# ---------------------------------------------------------------------------
# Vocabularies block (§3.3)
# ---------------------------------------------------------------------------

def build_vocabularies(caps: Mapping[str, Any]) -> dict[str, Any]:
    """Build the contract `vocabularies` block for one machine.

    `caps` is the capabilities block produced by build_capabilities_block;
    its `strength_levels`, `has_aroma_balance` and `hopper_count` fields
    select the machine-filtered freestyle subsets (§3.2). The status
    vocabularies are always the full enum-derived lists.
    """
    intensity = list(FREESTYLE_INTENSITY_TOKENS)
    if caps.get("strength_levels") == 3:
        # Center three steps for 3-level machines.
        intensity = intensity[1:4]

    if caps.get("has_aroma_balance"):
        aroma = list(FREESTYLE_AROMA_TOKENS)
    else:
        aroma = [FREESTYLE_AROMA_TOKENS[0]]

    if caps.get("hopper_count") == 2:
        blend = list(FREESTYLE_BLEND_TOKENS)
    else:
        blend = [FREESTYLE_BLEND_TOKENS[0]]

    return {
        "status": {
            "process": list(STATUS_PROCESS_TOKENS),
            "sub_process": list(STATUS_SUB_PROCESS_TOKENS),
            "manipulation": list(STATUS_MANIPULATION_TOKENS),
            "info_message": list(STATUS_INFO_MESSAGE_TOKENS),
        },
        "freestyle": {
            "process": list(FREESTYLE_PROCESS_TOKENS),
            "intensity": intensity,
            "aroma": aroma,
            "temperature": list(FREESTYLE_TEMPERATURE_TOKENS),
            "shots": list(FREESTYLE_SHOTS_TOKENS),
            "blend": blend,
        },
    }


# ---------------------------------------------------------------------------
# Parameter catalog (§6.1) — v2, additive within contract_version 1
# ---------------------------------------------------------------------------

# §6.1.3 family table: (family, potential scopes, applies_to). A scope
# survives only when the matching capability gate is on (`freestyle` iff
# supports_freestyle, `brew_override` iff supports_brew_overrides); a
# family whose scope list empties out is omitted entirely.
_PARAMETER_FAMILIES: tuple[tuple[str, tuple[str, ...], tuple[str, ...] | None], ...] = (
    ("process", ("freestyle",), None),
    ("intensity", ("freestyle", "brew_override"), ("coffee",)),
    ("aroma", ("freestyle", "brew_override"), ("coffee",)),
    ("temperature", ("freestyle",), None),
    ("shots", ("freestyle",), ("coffee",)),
    ("blend", ("freestyle",), ("coffee",)),
)


def build_parameters(capabilities_block: Mapping[str, Any]) -> dict[str, Any]:
    """Build the v2 `parameters` catalog (§6.1) for one machine.

    `capabilities_block` is the §3.3 block from build_capabilities_block.
    Enum token lists are taken from build_vocabularies, which guarantees
    the §6.1.2 mirror-and-freeze invariant by construction:
    `parameters.<family>.tokens` is byte-equal to
    `vocabularies.freestyle.<family>` and `parameters.portion_ml.c1/.c2`
    to `limits.portion_ml.c1/.c2`. Scope gating per §6.1.3:
    `freestyle`-scoped descriptors are emitted iff supports_freestyle,
    `brew_override`-scoped iff supports_brew_overrides; a family left
    with no scope is omitted entirely.
    """
    freestyle_vocab = build_vocabularies(capabilities_block)["freestyle"]
    gates = {
        "freestyle": bool(capabilities_block.get("supports_freestyle")),
        "brew_override": bool(capabilities_block.get("supports_brew_overrides")),
    }

    parameters: dict[str, Any] = {}
    for family, potential_scopes, applies_to in _PARAMETER_FAMILIES:
        scope = [s for s in potential_scopes if gates[s]]
        if not scope:
            continue
        descriptor: dict[str, Any] = {"kind": "enum", "scope": scope}
        if applies_to is not None:
            descriptor["applies_to"] = list(applies_to)
        descriptor["tokens"] = list(freestyle_vocab[family])
        parameters[family] = descriptor

    portion_scope = [s for s in ("freestyle", "brew_override") if gates[s]]
    if portion_scope:
        parameters["portion_ml"] = {
            "kind": "range",
            "scope": portion_scope,
            "unit": "ml",
            "per_component": True,
            "c1": dict(_PORTION_LIMITS["c1"]),
            "c2": dict(_PORTION_LIMITS["c2"]),
        }
    return parameters


# ---------------------------------------------------------------------------
# Settings descriptors (§9.1) — v3, additive within contract_version 1
# ---------------------------------------------------------------------------

# §9.1.3 group render order; unknown groups follow in served order.
_SETTING_GROUP_ORDER: tuple[str, ...] = ("brew", "water", "power", "system")

# §9.1.3 Nivona descriptor-key → group table; everything else → system.
_NIVONA_SETTING_GROUPS: dict[str, str] = {
    "temperature": "brew",
    "coffee_temperature": "brew",
    "milk_temperature": "brew",
    "milk_foam_temperature": "brew",
    "profile": "brew",
    "cup_heater": "brew",
    "water_hardness": "water",
    "off_rinse": "water",
    "power_on_rinse": "water",
    "auto_off": "power",
    "save_energy": "power",
    "auto_on_deactivated": "power",
}


def _group_sorted(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stable-sort setting entries into the §9.1.3 group order.

    Known groups in `brew, water, power, system` order; unknown groups
    after them in served order; original order preserved within a group.
    """
    order = {group: index for index, group in enumerate(_SETTING_GROUP_ORDER)}
    return sorted(entries, key=lambda entry: order.get(entry["group"], len(order)))


def _melitta_setting_entries(caps: Any, machine_type: Any) -> list[dict[str, Any]]:
    """Melitta settings entries from the shared MELITTA_SETTING_TABLES.

    Evaluates the same gating data entity registration consumes (§9.1.2.5
    predicate equality): the table's TS-only flags against the machine
    type (unknown type follows the assume-TS precedent — the flag drops a
    row only on a confirmed BARISTA_T) and the family's
    `unsupported_generic_setting_ids`.
    """
    excluded = getattr(caps, "unsupported_generic_setting_ids", frozenset())
    entries: list[dict[str, Any]] = []
    for row in MELITTA_SETTING_TABLES:
        if row["ts_only"] and machine_type == MachineType.BARISTA_T:
            continue
        if int(row["id"]) in excluded:
            continue
        entry: dict[str, Any] = {
            "setting": row["setting"],
            "control": row["control"],
            "group": row["group"],
            "icon": row["icon"],
            "entity": {
                "domain": row["control"],
                "entity_suffix": row["setting"],
            },
            "writable": True,
        }
        if row["control"] == "number":
            entry["min"] = row["min"]
            entry["max"] = row["max"]
            entry["step"] = row["step"]
            if "unit" in row:
                entry["unit"] = row["unit"]
            entry["display"] = row["display"]
            levels = SETTING_LEVEL_TOKENS.get(row["setting"])
            if levels:
                entry["levels"] = [
                    {"value": value, "token": token} for value, token in levels
                ]
        entries.append(entry)
    return entries


def _descriptor_setting_entries(caps: Any) -> list[dict[str, Any]]:
    """Nivona settings entries from the post-exclusion SettingDescriptors.

    One `select` entry per options-bearing descriptor (labels derived at
    build time from the descriptor tables — single source, §9.1.1;
    tokens via the shared `_options.py` annotations) and one `number`
    entry per options-less descriptor (range/unit from the shared
    `nivona_number_range` helper, group `power`). Icons are `mdi:tune`
    (§9.1.2.7); `writable` mirrors `descriptor.is_writable`.
    """
    entries: list[dict[str, Any]] = []
    for descriptor in getattr(caps, "settings", ()) or ():
        if descriptor.options:
            tokens = option_tokens(descriptor.options)
            entry: dict[str, Any] = {
                "setting": descriptor.key,
                "control": "select",
                "group": _NIVONA_SETTING_GROUPS.get(descriptor.key, "system"),
                "icon": "mdi:tune",
                "entity": {"domain": "select", "entity_suffix": descriptor.key},
                "writable": descriptor.is_writable,
                "options": [
                    {"value": value, "token": token, "label": label}
                    for (value, label), token in zip(descriptor.options, tokens)
                ],
            }
        else:
            min_value, max_value, unit = nivona_number_range(descriptor)
            entry = {
                "setting": descriptor.key,
                "control": "number",
                "group": "power",
                "icon": "mdi:tune",
                "entity": {"domain": "number", "entity_suffix": descriptor.key},
                "writable": descriptor.is_writable,
                "min": min_value,
                "max": max_value,
                "step": 1,
            }
            unit = descriptor.unit or unit
            if unit:
                entry["unit"] = unit
        entries.append(entry)
    return entries


def build_settings_block(
    caps: Any, machine_type: Any, brand: Any,
) -> list[dict[str, Any]]:
    """Build the v3 `settings` block (§9.1) for one machine.

    Melitta serves the shared `MELITTA_SETTING_TABLES` rows (TS-only and
    `unsupported_generic_setting_ids` gating, §9.1.2.5); other brands
    serve their post-exclusion `SettingDescriptor` tables. Served order
    is the normative render order: grouped per §9.1.3, original order
    preserved within each group. The block *describes* the entity
    surface — writes still go through the bound entities (§9.1.6.4).
    """
    if brand.brand_slug == "melitta":
        entries = _melitta_setting_entries(caps, machine_type)
    else:
        entries = _descriptor_setting_entries(caps)
    return _group_sorted(entries)


# ---------------------------------------------------------------------------
# DirectKey / profile model (§9.3) — v3, additive within contract_version 1
# ---------------------------------------------------------------------------

def build_directkey_block(
    caps: Any, machine_type: Any, brand: Any,
) -> dict[str, Any] | None:
    """Build the v3 `directkey` model block (§9.3.2), or None without HC.

    Present iff the brand supports the HC DirectKey extension (Melitta
    only — absence means feature absence, §9.0.1). Categories are always
    all 7, in DirectKeyCategory enum order (the normative render order),
    with `machine_button` truth from `DIRECTKEY_NO_BUTTON_CATEGORIES`
    (§9.3.1: unknown machine type follows the TS row; a confirmed
    BARISTA_T has no exclusions). Profile slots are
    `capabilities.my_coffee_slots + 1` entries: the fixed slot 0
    ("My Coffee") plus one entry per user slot with its name/activity
    entity bindings. `active_profile` remains client-side selector state
    on the integration's BLE client — the machine is never told about it.
    """
    if "HC" not in brand.supported_extensions:
        return None
    effective_type = (
        MachineType.BARISTA_TS if machine_type is None else machine_type
    )
    no_button = DIRECTKEY_NO_BUTTON_CATEGORIES.get(effective_type, frozenset())

    categories = [
        {
            "category": category.name.lower(),
            "id": int(category),
            "machine_button": category not in no_button,
            "icon": DIRECTKEY_CATEGORY_ICONS.get(category, "mdi:cup"),
        }
        for category in DirectKeyCategory
    ]

    profiles: list[dict[str, Any]] = [
        {"slot": 0, "fixed": True, "name_key": "my_coffee"},
    ]
    for slot in range(1, caps.my_coffee_slots + 1):
        profiles.append({
            "slot": slot,
            "name_entity_suffix": f"profile_{slot}_name",
            "active_entity_suffix": f"profile_{slot}_active",
        })

    return {
        "categories": categories,
        "profiles": profiles,
        "profile_select_entity_suffix": "profile",
        "active_profile_attribute": "active_profile",
    }


# ---------------------------------------------------------------------------
# Action catalog (§6.2) — v2, additive within contract_version 1
# ---------------------------------------------------------------------------

def _schema_entry(schema: Any, name: str) -> tuple[Any, Any]:
    """Return (marker, validator) for one key of a voluptuous Schema."""
    for marker, validator in schema.schema.items():
        if str(marker) == name:
            return marker, validator
    raise KeyError(name)


def _marker_required(marker: Any) -> bool:
    """True for a vol.Required marker (vol imported lazily by the caller)."""
    import voluptuous as vol  # noqa: PLC0415

    return isinstance(marker, vol.Required)


def _marker_default(marker: Any) -> Any:
    """The marker's declared default value, or None when undefined."""
    import voluptuous as vol  # noqa: PLC0415

    default = getattr(marker, "default", vol.UNDEFINED)
    if default is vol.UNDEFINED:
        return None
    return default() if callable(default) else default


def _extract_int_ranges(validator: Any) -> list[list[int]]:
    """Flatten vol.All/vol.Any/vol.Range trees into [[min, max], ...]."""
    import voluptuous as vol  # noqa: PLC0415

    if isinstance(validator, vol.Range):
        return [[int(validator.min), int(validator.max)]]
    ranges: list[list[int]] = []
    for child in getattr(validator, "validators", ()):
        ranges.extend(_extract_int_ranges(child))
    return ranges


def _introspect_params(schema: Any) -> list[dict[str, Any]]:
    """ActionParams introspected from a voluptuous service schema (§9.3.5).

    Byte-exact mirror of the live schema, marker asymmetries included
    (a `vol.Required(..., default=...)` field emits `required: true`
    PLUS a `default` — the BREW_FREESTYLE precedent): `vol.In` fields
    become `enum` params with the container's tokens in declaration
    order, int/Range fields become `int` params with their flattened
    ranges; `default` appears only where the marker declares one. The
    `entity_id` targeting field is skipped — clients supply it from the
    invocation's `entity_suffix` anchor (§6.2.1).
    """
    import voluptuous as vol  # noqa: PLC0415

    params: list[dict[str, Any]] = []
    for marker, validator in schema.schema.items():
        name = str(marker)
        if name == "entity_id":
            continue
        param: dict[str, Any] = {
            "name": name,
            "required": _marker_required(marker),
        }
        if isinstance(validator, vol.In):
            param["kind"] = "enum"
            param["tokens"] = list(validator.container)
        else:
            ranges = _extract_int_ranges(validator)
            if not ranges:
                raise ValueError(
                    f"unsupported validator for schema field {name!r}"
                )
            param["kind"] = "int"
            param["ranges"] = ranges
        default = getattr(marker, "default", vol.UNDEFINED)
        if default is not vol.UNDEFINED:
            param["default"] = default() if callable(default) else default
        params.append(param)
    return params


# §6.2.6 gating: catalog entries whose invocation starts a MachineProcess
# via the start-process path (the 8 unconditionally-registered buttons of
# issue #36); the `available` flag for these follows the per-family
# `verified_maintenance_processes` audit field. `brew` also carries a
# process token (PRODUCT) but uses the brew command path, not
# start_process, so it is not gated here.
_PROCESS_START_ACTIONS: frozenset[str] = frozenset({
    "easy_clean", "intensive_clean", "descaling",
    "filter_insert", "filter_replace", "filter_remove",
    "evaporating", "switch_off",
})

# Suffix of the anchor button entity used for service-kind invocations:
# every melitta_barista service resolves its target machine from the
# passed entity_id (§6.2.1 multi-machine targeting), and the brew button
# exists for every brand — today's `button.<prefix>_brew` anchor.
_SERVICE_ANCHOR_SUFFIX = "brew"


def _process_available(
    process: MachineProcess, verified: Sequence[int] | None,
) -> bool:
    """§6.2.6: a start-process action is available iff its process id is
    hardware-verified for the family (None = everything verified)."""
    return verified is None or int(process) in verified


def build_action_catalog(
    client: Any, capabilities_block: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Build the v2+v3 `actions` catalog (§6.2.2 + §9.3.5) for one machine.

    Seventeen entries (the 0.93 amendment adds `save_directkey`), always
    all served, with per-family truth encoded in
    `available`: HJ gates `brew_freestyle`, HC gates `brew_directkey`
    and `save_directkey`, `supports_recipe_writes` gates `reset_recipe`,
    `supports_factory_reset` gates the factory resets, and the §6.2.6
    `verified_maintenance_processes` audit gates every start-process
    entry (all Nivona families ship `()` — unavailable — until the #36
    button matrix lands). `switch_off.requires == ["connected"]` encodes
    the PR #42 precedent (usable while connected-not-ready) as data.
    Service-kind `ActionParam`s are introspected from the live voluptuous
    service schemas in `__init__` — never hand-copied — so they cannot
    drift.
    """
    from . import (  # noqa: PLC0415 — lazy: avoids a circular module import
        BREW_DIRECTKEY_SCHEMA,
        RESET_RECIPE_SCHEMA,
        SAVE_DIRECTKEY_SCHEMA,
    )

    caps = getattr(client, "capabilities", None)
    if caps is None:
        raise ContractNotReadyError(
            "client has no MachineCapabilities yet (no handshake)"
        )
    brand = client.brand
    verified = getattr(caps, "verified_maintenance_processes", None)
    supports_freestyle = bool(capabilities_block.get("supports_freestyle"))
    has_directkey = "HC" in brand.supported_extensions

    # ActionParams from the live schemas (§6.2.2).
    category_marker, category_validator = _schema_entry(
        BREW_DIRECTKEY_SCHEMA, "category",
    )
    two_cups_marker, _ = _schema_entry(BREW_DIRECTKEY_SCHEMA, "two_cups")
    directkey_params = [
        {
            "name": "category",
            "kind": "enum",
            "required": _marker_required(category_marker),
            "tokens": list(category_validator.container),
        },
        {
            "name": "two_cups",
            "kind": "bool",
            "required": _marker_required(two_cups_marker),
            "default": _marker_default(two_cups_marker),
        },
    ]
    recipe_id_marker, recipe_id_validator = _schema_entry(
        RESET_RECIPE_SCHEMA, "recipe_id",
    )
    reset_recipe_params = [
        {
            "name": "recipe_id",
            "kind": "int",
            "required": _marker_required(recipe_id_marker),
            "ranges": _extract_int_ranges(recipe_id_validator),
        },
    ]
    freestyle_params = [
        {"name": "params", "kind": "params_ref", "required": True,
         "ref": "freestyle"},
    ]
    # §9.3.5: all save_directkey params introspected from the live
    # schema, exact required/default flags included (the schema has no
    # blend and no two_cups fields, so no params_ref is needed).
    save_directkey_params = _introspect_params(SAVE_DIRECTKEY_SCHEMA)

    def button(suffix: str) -> dict[str, Any]:
        return {"kind": "button", "entity_suffix": suffix}

    def service(name: str, params: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "kind": "service",
            "service": name,
            "entity_suffix": _SERVICE_ANCHOR_SUFFIX,
            "params": params,
        }

    def entry(
        action: str,
        group: str,
        process: MachineProcess | None,
        invocation: dict[str, Any],
        *,
        icon: str,
        confirm: bool,
        requires: list[str],
        available: bool,
        destructive: bool = False,
    ) -> dict[str, Any]:
        built: dict[str, Any] = {
            "action": action,
            "group": group,
            "process": process.name if process is not None else None,
            "icon": icon,
            "confirm": confirm,
        }
        if destructive:
            built["destructive"] = True
        built["requires"] = requires
        built["available"] = available
        built["invocation"] = invocation
        return built

    def maintenance(
        action: str, group: str, process: MachineProcess,
        *, icon: str, confirm: bool, requires: list[str] | None = None,
    ) -> dict[str, Any]:
        return entry(
            action, group, process, button(action),
            icon=icon, confirm=confirm,
            requires=requires if requires is not None else ["ready"],
            available=_process_available(process, verified),
        )

    return [
        entry(
            "brew", "brew", MachineProcess.PRODUCT, button("brew"),
            icon="mdi:coffee", confirm=False, requires=["ready"],
            available=True,
        ),
        entry(
            "brew_freestyle", "brew", MachineProcess.PRODUCT,
            service("brew_freestyle", freestyle_params),
            icon="mdi:coffee-maker", confirm=False, requires=["ready"],
            available=supports_freestyle,
        ),
        entry(
            "brew_directkey", "brew", MachineProcess.PRODUCT,
            service("brew_directkey", directkey_params),
            icon="mdi:gesture-tap-button", confirm=False, requires=["ready"],
            available=has_directkey,
        ),
        entry(
            "cancel", "control", None, button("cancel"),
            icon="mdi:stop", confirm=False, requires=["connected"],
            available=True,
        ),
        entry(
            "confirm_prompt", "control", None, button("confirm_prompt"),
            icon="mdi:check-circle", confirm=False,
            requires=["awaiting_confirmation"], available=True,
        ),
        entry(
            "reset_recipe", "control", None,
            service("reset_recipe", reset_recipe_params),
            icon="mdi:restore", confirm=True, requires=["ready"],
            available=bool(capabilities_block.get("supports_recipe_writes")),
        ),
        # §9.3.5: 17th entry (0.93). Group `control` is deliberate —
        # card 2.7 renders `control` with bespoke UI, so the new entry
        # is informational there (§6.2.5.2). Confirm: overwrites a slot.
        entry(
            "save_directkey", "control", None,
            service("save_directkey", save_directkey_params),
            icon="mdi:content-save", confirm=True, requires=["ready"],
            available=has_directkey,
        ),
        maintenance(
            "easy_clean", "cleaning", MachineProcess.EASY_CLEAN,
            icon="mdi:shimmer", confirm=True,
        ),
        maintenance(
            "intensive_clean", "cleaning", MachineProcess.INTENSIVE_CLEAN,
            icon="mdi:dishwasher", confirm=True,
        ),
        maintenance(
            "descaling", "cleaning", MachineProcess.DESCALING,
            icon="mdi:water-sync", confirm=True,
        ),
        maintenance(
            "filter_insert", "filter", MachineProcess.FILTER_INSERT,
            icon="mdi:filter-plus", confirm=False,
        ),
        maintenance(
            "filter_replace", "filter", MachineProcess.FILTER_REPLACE,
            icon="mdi:filter-cog", confirm=False,
        ),
        maintenance(
            "filter_remove", "filter", MachineProcess.FILTER_REMOVE,
            icon="mdi:filter-remove", confirm=False,
        ),
        maintenance(
            "evaporating", "power", MachineProcess.EVAPORATING,
            icon="mdi:air-humidifier", confirm=True,
        ),
        # PR #42 precedent as data: Switch Off stays usable while
        # connected-not-ready, hence requires=["connected"], not "ready".
        maintenance(
            "switch_off", "power", MachineProcess.SWITCH_OFF,
            icon="mdi:power", confirm=True, requires=["connected"],
        ),
        entry(
            "factory_reset_settings", "danger", None,
            button("factory_reset_settings"),
            icon="mdi:cog-refresh", confirm=True, destructive=True,
            requires=["ready"],
            available=bool(capabilities_block.get("supports_factory_reset")),
        ),
        entry(
            "factory_reset_recipes", "danger", None,
            button("factory_reset_recipes"),
            icon="mdi:book-refresh", confirm=True, destructive=True,
            requires=["ready"],
            available=bool(capabilities_block.get("supports_factory_reset")),
        ),
    ]


# ---------------------------------------------------------------------------
# component_to_tokens (§3.3 RecipeComponentData)
# ---------------------------------------------------------------------------

def component_to_tokens(recipe_component: Any) -> dict[str, Any]:
    """Translate a protocol RecipeComponent into a token-level dict.

    Wire bytes map through the reverse const maps; unmapped bytes fall back
    to the component's wire defaults. The `blend` key is OMITTED for wire
    byte 0 (Blend.BARISTA_T, machine-default hopper) and for any unknown
    byte — clients treat an absent blend as "machine default hopper".
    """
    tokens: dict[str, Any] = {
        "process": _PROCESS_NAMES.get(recipe_component.process, "none"),
        "intensity": _INTENSITY_NAMES.get(recipe_component.intensity, "medium"),
        "aroma": _AROMA_NAMES.get(recipe_component.aroma, "standard"),
        "temperature": _TEMPERATURE_NAMES.get(recipe_component.temperature, "normal"),
        "shots": _SHOTS_NAMES.get(recipe_component.shots, "none"),
        "portion_ml": recipe_component.portion_ml,
    }
    blend = _BLEND_NAMES.get(recipe_component.blend)
    if blend is not None:
        tokens["blend"] = blend
    return tokens


# ---------------------------------------------------------------------------
# IconSpec derivation (§4) — one deterministic pure function
# ---------------------------------------------------------------------------

def _coffee_darkness(component: Mapping[str, Any]) -> float:
    """§4.3 coffee-layer darkness from intensity + shots (+ intense aroma).

    Computed in integer thousandths so 0.675 rounds half-up to 0.68
    deterministically (binary floats would banker's-round it to 0.67).
    """
    idx = _INTENSITY_INDEX.get(component.get("intensity"), 2)
    shot_count = SHOTS_MAP.get(component.get("shots"), 1)
    millis = 300 + 125 * idx + 100 * max(shot_count - 1, 0)
    if component.get("aroma") == "intense":
        millis += 50
    millis = min(max(millis, 300), 1000)
    return ((millis + 5) // 10) / 100


def build_icon_spec(
    components: Sequence[Mapping[str, Any] | None],
    additives: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any] | None:
    """Derive a procedural drink IconSpec from token-level components (§4).

    Pure and deterministic: same inputs yield a dict-equal output. Each
    component dict carries token fields (`process`, `intensity`, `aroma`,
    `temperature`, `shots`) plus integer `portion_ml`; each additive is
    `{name, ml | None, color_hint | None}`. Returns None for an empty
    composition (client renders its generic default icon).
    """
    survivors: list[tuple[str, int, Mapping[str, Any]]] = []
    for component in components:
        if not component:
            continue
        process = component.get("process")
        try:
            portion = int(component.get("portion_ml") or 0)
        except (TypeError, ValueError):
            portion = 0
        if process is None or process == "none" or portion <= 0:
            continue
        survivors.append((process, portion, component))

    additive_list = [additive for additive in additives if additive]
    if not survivors and not additive_list:
        return None

    # §4.2 glass inputs — computed from component ml only (§4.6).
    component_total = sum(portion for _, portion, _ in survivors)
    has_coffee = any(process == "coffee" for process, _, _ in survivors)
    milk_first = False
    for process, _, _ in survivors:
        if process == "milk":
            milk_first = True
            break
        if process == "coffee":
            break

    # §4.1/§4.3-§4.5: layers in dispense order; §4.4 milk foam split.
    raw_layers: list[dict[str, Any]] = []
    foam_ml = 0
    sole_component = len(survivors) == 1
    for process, portion, component in survivors:
        if process == "coffee":
            raw_layers.append({
                "role": "coffee", "ml": portion,
                "intensity": _coffee_darkness(component),
            })
        elif process == "milk":
            if sole_component and component.get("temperature") == "high":
                foam_ratio = 0.50  # froth-dominant drink
            else:
                foam_ratio = 0.20
            foam = max(_round5(portion * foam_ratio), 10)
            foam = min(foam, portion)
            foam_ml += foam
            body = portion - foam
            if body > 0:
                raw_layers.append({"role": "milk", "ml": body, "intensity": 0.0})
        elif process == "water":
            raw_layers.append({"role": "water", "ml": portion, "intensity": 0.0})
        else:
            # Unknown process token: emit it as an open-vocabulary layer
            # role — clients render unknown roles as neutral grey (§5.3.2).
            raw_layers.append({"role": process, "ml": portion, "intensity": 0.5})

    # §4.6 additive layers: above components, below foam.
    for additive in additive_list:
        ml = additive.get("ml")
        ml = 10 if ml is None else max(int(ml), 0)
        raw_layers.append({
            "role": "additive", "ml": ml, "intensity": 0.5,
            "color_hint": _normalize_color_hint(additive.get("color_hint")),
            "label": additive.get("name"),
        })

    total_ml = sum(layer["ml"] for layer in raw_layers) + foam_ml
    if total_ml <= 0:
        return None

    # §4.3 crema: only on a coffee layer that is topmost overall.
    if foam_ml == 0 and raw_layers and raw_layers[-1]["role"] == "coffee":
        raw_layers[-1]["crema"] = True

    # §4.2 glass — first matching rule wins.
    if component_total > 200 or (has_coffee and milk_first):
        glass = "tall_glass"
    elif component_total <= 60:
        glass = "espresso_cup"
    else:
        glass = "cup"

    nominal = NOMINAL_GLASS_VOLUMES[glass]
    if total_ml >= nominal:
        fill_level = 1.0
    else:
        fill_level = max(_ratio_round2(total_ml, nominal), 0.01)

    layers: list[dict[str, Any]] = []
    for raw in raw_layers:
        layer: dict[str, Any] = {
            "role": raw["role"],
            "ml": raw["ml"],
            "fraction": _ratio_round2(raw["ml"], total_ml),
            "intensity": raw["intensity"],
        }
        if raw.get("crema"):
            layer["crema"] = True
        if raw["role"] == "additive":
            layer["color_hint"] = raw["color_hint"]
            layer["label"] = raw["label"]
        layers.append(layer)

    foam: dict[str, Any] | None = None
    if foam_ml > 0:
        foam = {
            "role": "milk_foam",
            "ml": foam_ml,
            "fraction": _ratio_round2(foam_ml, total_ml),
        }

    # §4.7: steam iff at least one coffee component is not cold.
    steam = any(
        process == "coffee" and component.get("temperature") != "cold"
        for process, _, component in survivors
    )

    return {
        "spec_version": ICON_SPEC_VERSION,
        "glass": glass,
        "total_ml": total_ml,
        "fill_level": fill_level,
        "layers": layers,
        "foam": foam,
        "steam": steam,
    }


def icon_spec_for_category(category: str) -> dict[str, Any] | None:
    """IconSpec for a composition-less recipe from its category (§4.8).

    Runs the fixed synthetic composition for the category through the
    normal §4.1-§4.7 pipeline. `my_coffee`, empty and unknown categories
    return None (client renders its default icon).
    """
    composition = _CATEGORY_COMPOSITIONS.get(category)
    if composition is None:
        return None
    return build_icon_spec(list(composition))


# ---------------------------------------------------------------------------
# Recipe catalogs
# ---------------------------------------------------------------------------

def _melitta_recipe_catalog(client: Any) -> list[dict[str, Any]]:
    """Melitta catalog: RecipeId list (TS-gated) + base-recipe cache.

    Recipes present in the client-side base-recipe cache carry token-level
    `components` and a composition-derived icon; before preload completes
    the rest fall back to category-default icons (spec §3.3 notes).
    """
    base_recipes = getattr(client, "base_recipes", None) or {}
    catalog: list[dict[str, Any]] = []
    for recipe_id in get_available_recipes(getattr(client, "machine_type", None)):
        rid = int(recipe_id)
        category = MELITTA_RECIPE_CATEGORIES.get(rid, "")
        recipe: dict[str, Any] = {
            "recipe_id": rid,
            "name": RECIPE_NAMES.get(rid, str(rid)),
            "category": category,
        }
        name_key = MELITTA_RECIPE_NAME_KEYS.get(rid)
        if name_key:
            recipe["name_key"] = name_key
        cached = base_recipes.get(rid)
        if cached is not None:
            c1_raw = getattr(cached, "component1", None)
            c2_raw = getattr(cached, "component2", None)
            c1 = component_to_tokens(c1_raw) if c1_raw is not None else None
            c2 = component_to_tokens(c2_raw) if c2_raw is not None else None
            recipe["icon"] = build_icon_spec([c1, c2])
            recipe["components"] = {
                "c1": c1 if c1 is not None and c1["process"] != "none" else None,
                "c2": c2 if c2 is not None and c2["process"] != "none" else None,
            }
        else:
            recipe["icon"] = icon_spec_for_category(category)
        catalog.append(recipe)
    return catalog


def _descriptor_recipe_catalog(caps: Any) -> list[dict[str, Any]]:
    """Nivona catalog: MachineCapabilities.recipes descriptor tables.

    Composition is not exposed per-recipe, so no `components` blocks;
    icons come from category defaults (§4.8). Each entry carries the
    descriptor's authored `name_key` (§6.3.6) when one is seeded.
    """
    catalog: list[dict[str, Any]] = []
    for descriptor in caps.recipes:
        recipe: dict[str, Any] = {
            "recipe_id": descriptor.recipe_id,
            "name": descriptor.name,
            "category": descriptor.category,
        }
        name_key = getattr(descriptor, "name_key", "")
        if name_key:
            recipe["name_key"] = name_key
        recipe["icon"] = icon_spec_for_category(descriptor.category)
        catalog.append(recipe)
    return catalog


# ---------------------------------------------------------------------------
# Full contract document (§3.3)
# ---------------------------------------------------------------------------

def build_ui_contract(entry: Any, client: Any) -> dict[str, Any]:
    """Build the full `ui_contract/get` response body for one entry (§3.3).

    Pure in-memory read of the client's post-handshake state — no BLE, no
    DB. The WS transport envelope (`schema_version`) is added by the
    caller via `_send_versioned`. Raises ContractNotReadyError when the
    client has no MachineCapabilities yet (mapped to `contract_not_ready`).

    0.92 (§6.0): the document additionally carries the additive v2 blocks
    `parameters` (§6.1), `actions` (§6.2), `forbidden_combinations`
    (§6.1.6, always `[]` today) and `strings_version` (§6.3.2), and
    recipe entries gain the additive `name_key` (§6.3.6) — all within
    `contract_version: 1`.

    0.93 (§9.0): the additive v3 blocks join — `settings` (§9.1, both
    brands) and `directkey` (§9.3, present iff the brand supports the HC
    extension; absent = feature absent). Zero new fingerprint inputs
    (§9.4): every value they derive from is already a §5.1 input.
    """
    caps = getattr(client, "capabilities", None)
    if caps is None:
        raise ContractNotReadyError(
            "client has no MachineCapabilities yet (no handshake)"
        )
    brand = client.brand
    machine_type = getattr(client, "machine_type", None)
    capabilities_block = build_capabilities_block(client)

    if brand.brand_slug == "melitta":
        recipes = _melitta_recipe_catalog(client)
    else:
        recipes = _descriptor_recipe_catalog(caps)

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    directkey = build_directkey_block(caps, machine_type, brand)

    document: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "contract_fingerprint": compute_contract_fingerprint(client),
        "entry_id": entry.entry_id,
        "generated_at": generated_at,
        "source": "live",
        "machine": {
            "brand": brand.brand_slug,
            "brand_name": brand.brand_name,
            "model_name": caps.model_name,
            "family_key": caps.family_key,
            "machine_type": machine_type.name if machine_type is not None else None,
            "connected": bool(getattr(client, "connected", False)),
        },
        "brand_theme": build_brand_theme(
            client, getattr(client, "brand_logo_url", None)
        ),
        "capabilities": capabilities_block,
        "vocabularies": build_vocabularies(capabilities_block),
        "limits": {
            "portion_ml": {
                component: dict(limits)
                for component, limits in _PORTION_LIMITS.items()
            },
        },
        # v2 blocks (§6.0): additive within contract_version 1.
        "parameters": build_parameters(capabilities_block),
        "actions": build_action_catalog(client, capabilities_block),
        # v3 block (§9.1): additive within contract_version 1.
        "settings": build_settings_block(caps, machine_type, brand),
        # §6.1.6: defined shape, empty content in 0.92 — always emitted
        # so clients need no presence special-case.
        "forbidden_combinations": [],
        # §6.3.2: cache axis for server-served display strings; equals
        # the integration manifest version (single-source stash, §5.1).
        "strings_version": _integration_version(client) or "unknown",
        "recipes": recipes,
        "status_attribute_entity": "state",
        "bridge_attribute_entity": "connection",
    }
    # v3 block (§9.3.2): present iff "HC" in supported_extensions —
    # per-feature presence gating (§9.0.1), never an explicit null.
    if directkey is not None:
        document["directkey"] = directkey
    return document
