"""UI Contract v1 builder (docs/UI_CONTRACT.md).

Pure, BLE-free derivation of the renderer-facing contract: token
vocabularies, capability blocks, live status tokens, bridge attributes,
procedural drink icon specs, and the full `ui_contract/get` document.
Everything here is computed from data the integration already holds after
handshake (MachineCapabilities, BrandProfile, const maps, the client-side
base-recipe cache) — building a contract never triggers BLE traffic.

Token vocabularies are generated from the real enums / const maps, never
hand-copied, so a new enum member automatically ships as a new token
(additive growth per spec §5.2.2).
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
from .const import (
    AROMA_MAP,
    BLEND_MAP,
    INTENSITY_MAP,
    MachineType,
    PROCESS_MAP,
    PROMPT_MANIPULATIONS,
    RECIPE_KEY_MAP,
    RECIPE_NAMES,
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

def compute_contract_fingerprint(client: Any) -> str | None:
    """Content revision for this machine's contract: 12 hex chars of sha256.

    Computed over (family_key, model_name, machine_type, the capability-
    relevant profile fields, recipe-cache generation counter, brand-logo
    presence flag) per §5.1. The logo flag is the cached setup-time §3.10
    file-check result — fixed for the life of the entry runtime, so it can
    only differ across entry reloads. Carries no semantics beyond equality
    comparison. Returns None while the client has no capabilities
    (pre-handshake — no contract exists).
    """
    caps = getattr(client, "capabilities", None)
    if caps is None:
        return None
    machine_type = getattr(client, "machine_type", None)
    brand = client.brand
    payload = {
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
    icons come from category defaults (§4.8).
    """
    return [
        {
            "recipe_id": descriptor.recipe_id,
            "name": descriptor.name,
            "category": descriptor.category,
            "icon": icon_spec_for_category(descriptor.category),
        }
        for descriptor in caps.recipes
    ]


# ---------------------------------------------------------------------------
# Full contract document (§3.3)
# ---------------------------------------------------------------------------

def build_ui_contract(entry: Any, client: Any) -> dict[str, Any]:
    """Build the full `ui_contract/get` response body for one entry (§3.3).

    Pure in-memory read of the client's post-handshake state — no BLE, no
    DB. The WS transport envelope (`schema_version`) is added by the
    caller via `_send_versioned`. Raises ContractNotReadyError when the
    client has no MachineCapabilities yet (mapped to `contract_not_ready`).
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

    return {
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
        "recipes": recipes,
        "status_attribute_entity": "state",
        "bridge_attribute_entity": "connection",
    }
