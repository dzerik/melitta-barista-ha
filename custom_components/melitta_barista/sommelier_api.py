"""WebSocket API for AI Coffee Sommelier."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .const import (
    AROMA_MAP,
    DOMAIN,
    INTENSITY_MAP,
    PROCESS_MAP,
    SHOTS_MAP,
    TEMPERATURE_MAP,
    Blend,
)
from .panel_api import _check_llm_agent, _resolve_agent_id, _send_versioned
from .ui_contract import build_icon_spec

_LOGGER = logging.getLogger("melitta_barista")


def _find_client(hass: HomeAssistant):
    """Find the first available machine client via config entries."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if hasattr(entry, "runtime_data") and entry.runtime_data:
            return entry.runtime_data
    return None


class RecipeWritesUnsupportedError(RuntimeError):
    """Raised when the active machine cannot accept custom freestyle recipes.

    Nivona families set MachineCapabilities.supports_recipe_writes=False
    because their recipe protocol differs from Melitta's freestyle slot.
    The Sommelier UI shows the recipe as print-only for those machines.
    """

    def __init__(self, family_key: str) -> None:
        super().__init__(
            f"machine family {family_key!r} does not support custom recipe writes",
        )
        self.family_key = family_key


# Recipes store the LLM's blend semantics (1 = hopper 1, 0 = hopper 2, as
# defined in the generation prompt). The BLE blend byte uses a different
# encoding (Blend enum: BLEND_1=1, BLEND_2=2, BARISTA_T=0), so the value
# must be translated exactly once, at the BLE boundary.
_LLM_BLEND_TO_BLE: dict[int, int] = {
    1: int(Blend.BLEND_1),  # LLM hopper 1 → BLE byte 1
    0: int(Blend.BLEND_2),  # LLM hopper 2 → BLE byte 2
}


def _resolve_enum(mapping: dict[str, int], field: str, value: Any, default: str) -> int:
    """Map a recipe enum string to its BLE byte; unknown values are errors.

    A missing/None value keeps the historic default (schema allows omission),
    but a *present* unknown string means the row drifted from the const maps
    (legacy DB row, hand-edited SQLite, future vocab drift) — silently
    substituting a default would brew the wrong thing, so raise instead.
    """
    if value is None:
        value = default
    if value not in mapping:
        raise ValueError(
            f"unknown {field} value {value!r}; expected one of {sorted(mapping)}"
        )
    return mapping[value]


def _resolve_portion(raw_ml: Any) -> int:
    """Clamp portion_ml to 0..250 and round to the 5 ml grid; returns the byte.

    Brew-time re-validation: DB rows can bypass generation-time clamps, and
    an unclamped value would crash struct.pack deep in the protocol layer
    (or silently floor 42 ml to 40 where generation documents round-half).
    """
    try:
        ml = float(raw_ml if raw_ml is not None else 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid portion_ml value {raw_ml!r}") from exc
    ml = max(0.0, min(250.0, ml))
    return int(ml / 5.0 + 0.5)


# ── UI Contract §3.9: per-recipe IconSpec on sommelier payloads ───────

# Additive slots of a recipe's `extras` dict that carry a display name
# (the boolean `ice` and free-text `instruction` slots are not additives).
_ADDITIVE_SLOTS: tuple[str, ...] = ("syrup", "topping", "liqueur")


async def _additive_color_hints(db) -> dict[str, str]:
    """Map lowercased additive names to color hints from the panel DB.

    Reads the `attributes` JSON of the panel additive tables (syrups /
    toppings) and picks a `color_hint` (or legacy `color`) value when one
    is stored. Both tables are created lazily by the panel API, so they
    may not exist yet — that, or an unreadable row, degrades to "no hint"
    (build_icon_spec normalizes anything invalid to null anyway, §3.6).
    """
    hints: dict[str, str] = {}
    for table in ("syrups", "toppings"):
        try:
            # `table` is a hardcoded literal, never user input.
            cursor = await db.db.execute(
                f"SELECT name, attributes FROM {table}"  # nosec B608
            )
            rows = await cursor.fetchall()
        except Exception:  # noqa: BLE001 — table missing / DB unavailable
            continue
        for row in rows:
            name, raw_attributes = row[0], row[1]
            if not name or not raw_attributes:
                continue
            try:
                attributes = json.loads(raw_attributes)
            except (TypeError, ValueError):
                continue
            if not isinstance(attributes, dict):
                continue
            hint = attributes.get("color_hint") or attributes.get("color")
            if isinstance(hint, str) and hint:
                hints[str(name).lower()] = hint
    return hints


def _recipe_additive_slots(
    recipe: dict[str, Any], color_hints: dict[str, str]
) -> list[dict[str, Any]]:
    """Extract §3.9 additive slots (syrup/topping/liqueur) from `extras`.

    Each named slot becomes `{name, ml: None, color_hint}` in slot order;
    `ml: None` lets build_icon_spec apply the §4.6 default (10 ml). The
    hint lookup is by lowercased name — extras store lowercased strings,
    DB rows may be any case.
    """
    extras = recipe.get("extras") or {}
    if not isinstance(extras, dict):
        return []
    slots: list[dict[str, Any]] = []
    for slot in _ADDITIVE_SLOTS:
        name = extras.get(slot)
        if isinstance(name, str) and name.strip():
            slots.append({
                "name": name,
                "ml": None,
                "color_hint": color_hints.get(name.strip().lower()),
            })
    return slots


def _attach_recipe_icon(
    recipe: dict[str, Any], color_hints: dict[str, str]
) -> None:
    """Attach the §3.9 `icon` IconSpec (or None) to one recipe payload.

    Computed by the same builder as the UI-contract recipe catalog, from
    the recipe's `machine_phases` components plus its additive slots.
    Purely additive to the existing sommelier schemas — the icon is
    derived at read time and never persisted.
    """
    phases = recipe.get("machine_phases") or []
    components = [
        phase.get("component")
        for phase in phases[:2]
        if isinstance(phase, dict)
    ]
    recipe["icon"] = build_icon_spec(
        components, _recipe_additive_slots(recipe, color_hints)
    )


async def _attach_recipe_icons(db, recipes) -> None:
    """Attach icons to an iterable of recipe payloads (one DB hint lookup)."""
    color_hints = await _additive_color_hints(db)
    for recipe in recipes:
        if isinstance(recipe, dict):
            _attach_recipe_icon(recipe, color_hints)


async def _brew_recipe_components(
    client, name: str, blend: int, phases: list[dict]
) -> bool:
    """Execute a multi-phase brew; returns brew_freestyle's result.

    P2a: BLE layer still takes component1/2, so we unpack phases[0]/phases[1].
    `phases` is the list-of-dicts form ({"component": {...}, "user_action_before": [...]}).
    A single-phase brew is encoded by sending a "none"-process component2,
    which the BLE protocol naturally treats as "no second pour".

    `blend` uses the recipe/LLM semantics (1 = hopper 1, 0 = hopper 2) and is
    translated to the BLE Blend byte here; both phases pour from the SAME
    hopper. Component fields are re-validated at brew time (see
    ``_resolve_enum`` / ``_resolve_portion``); a ``ValueError`` means the
    stored row is unbrewable, and ``False`` means the machine refused to
    start (busy, disconnected, not ready, or a write failed).
    """
    from .protocol import RecipeComponent as ProtocolRC

    if not phases:
        raise ValueError("phases is empty; cannot brew")
    if len(phases) > 2:
        raise ValueError(f"phases length {len(phases)} > 2; cap to 2 in caller")

    ble_blend = _LLM_BLEND_TO_BLE.get(blend)
    if ble_blend is None:
        raise ValueError(
            f"unknown blend value {blend!r}; expected 1 (hopper 1) or 0 (hopper 2)"
        )

    def _to_proto(comp: dict) -> ProtocolRC:
        return ProtocolRC(
            process=_resolve_enum(PROCESS_MAP, "process", comp.get("process"), "none"),
            shots=_resolve_enum(SHOTS_MAP, "shots", comp.get("shots"), "none"),
            blend=ble_blend,
            intensity=_resolve_enum(
                INTENSITY_MAP, "intensity", comp.get("intensity"), "medium"
            ),
            aroma=_resolve_enum(AROMA_MAP, "aroma", comp.get("aroma"), "standard"),
            temperature=_resolve_enum(
                TEMPERATURE_MAP, "temperature", comp.get("temperature"), "normal"
            ),
            portion=_resolve_portion(comp.get("portion_ml")),
        )

    component1 = _to_proto(phases[0].get("component", {}))
    if len(phases) >= 2:
        component2 = _to_proto(phases[1].get("component", {}))
    else:
        # Single-phase: synthesize a "none"-process component2 — BLE protocol
        # treats this as "no second pour". Same blend byte as phase 1.
        component2 = ProtocolRC(
            process=PROCESS_MAP["none"],
            shots=SHOTS_MAP["none"],
            blend=ble_blend,
            intensity=INTENSITY_MAP["medium"],
            aroma=AROMA_MAP["standard"],
            temperature=TEMPERATURE_MAP["normal"],
            portion=0,
        )

    # P10 brand-honest gate: Nivona families set
    # MachineCapabilities.supports_recipe_writes=False because their recipe
    # protocol differs from Melitta's freestyle slot. Refusing here keeps
    # the failure mode explicit instead of failing silently inside the BLE
    # layer. WS handlers translate this into a "recipe_writes_unsupported"
    # send_error so the panel can show a clear print-only state.
    caps = getattr(client, "capabilities", None)
    if caps is not None and not caps.supports_recipe_writes:
        family_key = getattr(caps, "family_key", "unknown")
        raise RecipeWritesUnsupportedError(family_key)

    return await client.brew_freestyle(
        name=name,
        recipe_type=24,
        component1=component1,
        component2=component2,
    )


async def _brew_recipe_phase(
    client, name: str, blend: int, phases: list[dict], phase_index: int
) -> bool:
    """Brew exactly one machine phase of a multi-phase recipe.

    The step-machine wizard drives phases one at a time so the user can
    perform ``user_action_before`` steps between pours. Delegates to
    ``_brew_recipe_components`` with a single-element phase list, which
    already encodes "no second pour" via the synthesized none-process
    component2 — the translation/validation logic stays single-source.
    Raises ``ValueError`` for an out-of-range ``phase_index`` (WS callers
    pre-check the range to report a distinct ``invalid_phase`` error).
    """
    if not 0 <= phase_index < len(phases):
        raise ValueError(
            f"phase_index {phase_index} out of range; "
            f"recipe has {len(phases)} machine phase(s)"
        )
    return await _brew_recipe_components(
        client, name=name, blend=blend, phases=[phases[phase_index]]
    )


# ── Schemas ───────────────────────────────────────────────────────────

VALID_ROASTS = ["light", "medium", "medium_dark", "dark"]
VALID_BEAN_TYPES = ["arabica", "arabica_robusta", "robusta"]
VALID_ORIGINS = ["single_origin", "blend"]
VALID_FLAVOR_NOTES = [
    "chocolate", "nutty", "fruity", "floral", "caramel",
    "spicy", "earthy", "honey", "berry", "citrus",
]
VALID_MILK_TYPES = [
    "regular", "whole", "skim", "oat", "almond",
    "soy", "coconut", "cream",
]
VALID_MODES = ["surprise_me", "custom"]
VALID_EXTRAS_CATEGORIES = ["syrups", "toppings", "liqueurs"]

# User-writable keys for the shared `settings` table. The same table also
# stores `schema_version` (managed by the DB migration code) and is checked
# on every startup; if it were overwritten with garbage from a WS caller,
# future migrations would either skip or re-run incorrectly. The allowlist
# below is therefore not a UX gate — it is a hard schema guarantee.
VALID_SETTING_KEYS = ["llm_agent_id", "llm_timeout_s"]

# User-writable keys for the `user_preferences` table. There is no current
# WS caller, but the endpoint exists; restrict it the same way to prevent
# a future caller from polluting the table with arbitrary keys.
VALID_PREFERENCE_KEYS = [
    "default_cup_size",
    "default_temperature",
    "default_caffeine",
    "default_dietary",
    # Weather + presence integration. The values are read by ws_generate
    # (use_weather / use_presence as "true"/"false" strings;
    # weather_entity as the HA entity_id of the weather sensor). They
    # were live on the read side from the start but missing from the
    # write allowlist, so callers couldn't actually configure them via
    # the WS API. Added in 0.72.0 (closes §10 B6).
    "use_weather",
    "weather_entity",
    "use_presence",
]
VALID_CUP_SIZES = ["espresso_cup", "cup", "mug", "tall_glass", "travel"]
VALID_MOODS = ["energizing", "relaxing", "dessert", "classic"]
VALID_OCCASIONS = ["morning", "after_lunch", "guests", "romantic", "work"]
VALID_TEMP_PREFS = ["auto", "hot", "iced", "hot_only", "cold_ok", "prefer_cold"]
VALID_CAFFEINE_PREFS = ["regular", "low", "decaf_evening"]
VALID_DIETARY = ["no_sugar", "lactose_free", "low_calorie", "vegan"]

BEAN_SCHEMA = {
    vol.Required("brand"): cv.string,
    vol.Required("product"): cv.string,
    vol.Required("roast"): vol.In(VALID_ROASTS),
    vol.Required("bean_type"): vol.In(VALID_BEAN_TYPES),
    vol.Required("origin"): vol.In(VALID_ORIGINS),
    vol.Optional("origin_country"): cv.string,
    # flavor_notes is a free-form list of strings since the panel introduced
    # the dynamic-tag UI: users (and the LLM, when its output isn't pinned to
    # a hardcoded vocabulary) are free to coin any tag. The legacy
    # VALID_FLAVOR_NOTES whitelist is kept as a typo-safety hint via
    # cv.string only — anything that's a string passes.
    vol.Optional("flavor_notes", default=[]): vol.All(
        cv.ensure_list, [cv.string]
    ),
    vol.Optional("composition"): cv.string,
    vol.Optional("preset_id"): cv.string,
}


async def _async_get_db(hass: HomeAssistant):
    """Get or lazily initialize the SommelierDB instance."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    db = domain_data.get("sommelier_db")
    if db is not None:
        return db

    from .sommelier_db import SommelierDB

    db_path = hass.config.path("melitta_barista_sommelier.db")
    db = SommelierDB(db_path)
    await db.async_setup()
    domain_data["sommelier_db"] = db
    _LOGGER.info("Sommelier DB initialized lazily at %s", db_path)
    return db


def async_register_websocket_handlers(hass: HomeAssistant) -> None:
    """Register all Sommelier WebSocket command handlers."""
    websocket_api.async_register_command(hass, ws_beans_list)
    websocket_api.async_register_command(hass, ws_beans_add)
    websocket_api.async_register_command(hass, ws_beans_update)
    websocket_api.async_register_command(hass, ws_beans_delete)
    websocket_api.async_register_command(hass, ws_hoppers_get)
    websocket_api.async_register_command(hass, ws_hoppers_assign)
    websocket_api.async_register_command(hass, ws_capabilities_get)
    websocket_api.async_register_command(hass, ws_milk_get)
    websocket_api.async_register_command(hass, ws_milk_set)
    websocket_api.async_register_command(hass, ws_milk_list_full)
    websocket_api.async_register_command(hass, ws_milk_set_available)
    websocket_api.async_register_command(hass, ws_generate)
    websocket_api.async_register_command(hass, ws_brew)
    websocket_api.async_register_command(hass, ws_brew_phase)
    websocket_api.async_register_command(hass, ws_favorites_list)
    websocket_api.async_register_command(hass, ws_favorites_add)
    websocket_api.async_register_command(hass, ws_favorites_remove)
    websocket_api.async_register_command(hass, ws_favorites_update)
    websocket_api.async_register_command(hass, ws_favorites_brew)
    websocket_api.async_register_command(hass, ws_history_list)
    websocket_api.async_register_command(hass, ws_history_clear)
    websocket_api.async_register_command(hass, ws_bean_presets_list)
    websocket_api.async_register_command(hass, ws_presets_list)
    websocket_api.async_register_command(hass, ws_presets_add)
    websocket_api.async_register_command(hass, ws_presets_update)
    websocket_api.async_register_command(hass, ws_presets_delete)
    websocket_api.async_register_command(hass, ws_settings_get)
    websocket_api.async_register_command(hass, ws_settings_set)
    websocket_api.async_register_command(hass, ws_extras_get)
    websocket_api.async_register_command(hass, ws_extras_set)
    websocket_api.async_register_command(hass, ws_preferences_get)
    websocket_api.async_register_command(hass, ws_preferences_set)
    websocket_api.async_register_command(hass, ws_profiles_list)
    websocket_api.async_register_command(hass, ws_profiles_add)
    websocket_api.async_register_command(hass, ws_profiles_update)
    websocket_api.async_register_command(hass, ws_profiles_delete)
    websocket_api.async_register_command(hass, ws_profiles_activate)
    websocket_api.async_register_command(hass, ws_recipe_rate)
    websocket_api.async_register_command(hass, ws_recipe_unrate)


# ── Beans ─────────────────────────────────────────────────────────────

@websocket_api.websocket_command(
    {vol.Required("type"): "melitta_barista/sommelier/beans/list"}
)
@websocket_api.async_response
async def ws_beans_list(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """List all coffee beans."""
    db = await _async_get_db(hass)
    beans = await db.async_list_beans()
    _send_versioned(connection, msg["id"], {"beans": beans})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/beans/add",
        **BEAN_SCHEMA,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_beans_add(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Add a new coffee bean."""
    db = await _async_get_db(hass)
    data = {
        k: msg[k]
        for k in (
            "brand", "product", "roast", "bean_type", "origin",
            "origin_country", "flavor_notes", "composition", "preset_id",
        )
        if k in msg
    }
    bean = await db.async_add_bean(data)
    _send_versioned(connection, msg["id"], {"bean": bean})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/beans/update",
        vol.Required("bean_id"): cv.string,
        vol.Optional("brand"): cv.string,
        vol.Optional("product"): cv.string,
        vol.Optional("roast"): vol.In(VALID_ROASTS),
        vol.Optional("bean_type"): vol.In(VALID_BEAN_TYPES),
        vol.Optional("origin"): vol.In(VALID_ORIGINS),
        vol.Optional("origin_country"): cv.string,
        # See BEAN_SCHEMA — free-form tag list now.
        vol.Optional("flavor_notes"): vol.All(
            cv.ensure_list, [cv.string]
        ),
        vol.Optional("composition"): cv.string,
        vol.Optional("preset_id"): cv.string,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_beans_update(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Update an existing coffee bean."""
    db = await _async_get_db(hass)
    bean_id = msg["bean_id"]
    data = {
        k: msg[k]
        for k in (
            "brand", "product", "roast", "bean_type", "origin",
            "origin_country", "flavor_notes", "composition", "preset_id",
        )
        if k in msg
    }
    bean = await db.async_update_bean(bean_id, data)
    if bean is None:
        connection.send_error(msg["id"], "not_found", f"Bean {bean_id} not found")
        return
    _send_versioned(connection, msg["id"], {"bean": bean})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/beans/delete",
        vol.Required("bean_id"): cv.string,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_beans_delete(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Delete a coffee bean."""
    db = await _async_get_db(hass)
    deleted = await db.async_delete_bean(msg["bean_id"])
    if not deleted:
        connection.send_error(msg["id"], "not_found", "Bean not found")
        return
    _send_versioned(connection, msg["id"], {})


# ── Hoppers ───────────────────────────────────────────────────────────

@websocket_api.websocket_command(
    {vol.Required("type"): "melitta_barista/sommelier/hoppers/get"}
)
@websocket_api.async_response
async def ws_hoppers_get(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Get current hopper assignments."""
    db = await _async_get_db(hass)
    hoppers = await db.async_get_hoppers()
    _send_versioned(connection, msg["id"], hoppers)


# ── Capabilities ──────────────────────────────────────────────────────

@websocket_api.websocket_command({
    vol.Required("type"): "melitta_barista/capabilities/get",
    vol.Required("entry_id"): cv.string,
})
@websocket_api.async_response
async def ws_capabilities_get(hass, connection, msg) -> None:
    """Return the live capabilities for a config entry.

    Strategy: read the cached row from sommelier DB. If absent, fall back
    to deriving on-the-fly from runtime_data (no DB write, since this is
    a read endpoint — the on-connect callback handles persistence).
    """
    from .capabilities import LiveCapabilities, derive_capabilities

    entry_id = msg["entry_id"]
    db = hass.data.get(DOMAIN, {}).get("sommelier_db")

    # 1) Try DB cache.
    if db is not None:
        row = await db.async_get_capabilities(entry_id)
        if row is not None:
            try:
                cap = LiveCapabilities.from_json(row["json_payload"])
            except (ValueError, json.JSONDecodeError):
                # Corrupt DB row or future-schema payload — fall through
                # to the live-derive path so the user is never blocked by
                # a stale cache. The on-connect probe will eventually
                # rewrite the row on next handshake.
                _LOGGER.warning(
                    "stale or corrupt cached capabilities for entry %s; "
                    "falling back to live derive",
                    entry_id,
                )
            else:
                _send_versioned(connection, msg["id"], {
                    "schema_version": 1,
                    "entry_id": entry_id,
                    "source": "cache",
                    "probed_at": row["probed_at"],
                    "capabilities": {
                        "family_key": cap.family_key,
                        "model_name": cap.model_name,
                        "supported_processes": list(cap.supported_processes),
                        "supported_intensities": list(cap.supported_intensities),
                        "supported_aromas": list(cap.supported_aromas),
                        "supported_temperatures": list(cap.supported_temperatures),
                        "supported_shots": list(cap.supported_shots),
                        "portion_limits": cap.portion_limits,
                        "forbidden_combinations": list(cap.forbidden_combinations),
                        # Gating flag for the panel's print-only UX (Nivona
                        # families set this False).
                        "supports_recipe_writes": cap.supports_recipe_writes,
                    },
                })
                return

    # 2) Fallback: derive live from runtime_data.
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.runtime_data is None:
        connection.send_error(msg["id"], "entry_not_found",
                              f"no live client for entry_id={entry_id}")
        return

    try:
        cap = derive_capabilities(entry.runtime_data)
    except ValueError as exc:
        connection.send_error(msg["id"], "client_not_ready", str(exc))
        return

    _send_versioned(connection, msg["id"], {
        "schema_version": 1,
        "entry_id": entry_id,
        "source": "derive",
        "probed_at": None,
        "capabilities": {
            "family_key": cap.family_key,
            "model_name": cap.model_name,
            "supported_processes": list(cap.supported_processes),
            "supported_intensities": list(cap.supported_intensities),
            "supported_aromas": list(cap.supported_aromas),
            "supported_temperatures": list(cap.supported_temperatures),
            "supported_shots": list(cap.supported_shots),
            "portion_limits": cap.portion_limits,
            "forbidden_combinations": list(cap.forbidden_combinations),
            # Gating flag for the panel's print-only UX (Nivona families
            # set this False).
            "supports_recipe_writes": cap.supports_recipe_writes,
        },
    })


@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/hoppers/assign",
        vol.Required("hopper_id"): vol.In([1, 2]),
        vol.Optional("bean_id"): vol.Any(cv.string, None),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_hoppers_assign(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Assign a bean to a hopper."""
    db = await _async_get_db(hass)
    await db.async_assign_hopper(msg["hopper_id"], msg.get("bean_id"))
    _send_versioned(connection, msg["id"], {})


# ── Milk ──────────────────────────────────────────────────────────────

@websocket_api.websocket_command(
    {vol.Required("type"): "melitta_barista/sommelier/milk/get"}
)
@websocket_api.async_response
async def ws_milk_get(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Get available milk types."""
    db = await _async_get_db(hass)
    milk = await db.async_get_milk()
    _send_versioned(connection, msg["id"], {"milk_types": milk})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/milk/set",
        # Free-form list of milk type names. The legacy VALID_MILK_TYPES
        # whitelist (8 English-only values) was rejecting Russian / brand
        # names like "Ультрапастеризованное 3%"; the panel's milk manager
        # is intended to be a freeform vocabulary just like flavor tags.
        vol.Required("milk_types"): vol.All(
            cv.ensure_list, [cv.string]
        ),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_milk_set(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Set available milk types."""
    db = await _async_get_db(hass)
    await db.async_set_milk(msg["milk_types"])
    _send_versioned(connection, msg["id"], {})


@websocket_api.websocket_command(
    {vol.Required("type"): "melitta_barista/sommelier/milk/list_full"}
)
@websocket_api.async_response
async def ws_milk_list_full(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """List every configured milk with its availability flag.

    Used by the Additives panel which needs to surface the per-row
    in-stock / out-of-stock toggle. Sommelier's chip picker keeps
    using `/milk/get` so out-of-stock milks stay hidden from
    generation context.
    """
    db = await _async_get_db(hass)
    rows = await db.async_list_milk_full()
    _send_versioned(connection, msg["id"], {"milks": rows})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/milk/set_available",
        vol.Required("milk_type"): cv.string,
        vol.Required("available"): bool,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_milk_set_available(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Toggle a single milk type's availability flag."""
    db = await _async_get_db(hass)
    await db.async_set_milk_available(msg["milk_type"], bool(msg["available"]))
    _send_versioned(connection, msg["id"], {"updated": True})


# ── Generate ──────────────────────────────────────────────────────────

@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/generate",
        vol.Optional("mode", default="surprise_me"): vol.In(VALID_MODES),
        vol.Optional("preference"): cv.string,
        vol.Optional("count", default=3): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=5)
        ),
        # Single mood/occasion kept for backwards compat; the new multi-
        # selects (moods / dietary) override them when sent.
        vol.Optional("mood"): vol.In(VALID_MOODS),
        vol.Optional("moods"): [vol.In(VALID_MOODS)],
        vol.Optional("occasion"): vol.In(VALID_OCCASIONS),
        vol.Optional("temperature", default="auto"): vol.In(["auto", "hot", "iced"]),
        vol.Optional("servings", default=1): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=4)
        ),
        vol.Optional("dietary"): [vol.In(VALID_DIETARY)],
        vol.Optional("caffeine_pref"): vol.In(VALID_CAFFEINE_PREFS),
        vol.Optional("cup_size"): vol.In(VALID_CUP_SIZES),
        # Whitelist filters: when present, restrict the LLM to ONLY these
        # add-in / milk names (intersection with what's actually
        # configured). When absent, fall back to the DB defaults.
        vol.Optional("allow_syrups"): [cv.string],
        vol.Optional("allow_toppings"): [cv.string],
        vol.Optional("allow_milk"): [cv.string],
        # B7 — per-request override of the conversation agent. Wins over
        # settings.llm_agent_id (see `_resolve_agent_id` in panel_api).
        vol.Optional("agent_id"): cv.string,
        # R4/Task 5 — explicit config entry to scope LiveCapabilities lookup.
        # Defaults to the first config entry when omitted (single-machine
        # case). Multi-machine support will use this field.
        vol.Optional("entry_id"): cv.string,
        # P7a — bind this session (and its recipes) to the machine's
        # hardware profile slot. NULL/omitted means the session is
        # shared across profiles. The FE typically reads this from
        # `melitta_barista/status`'s `active_profile`.
        vol.Optional("machine_profile"): int,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_generate(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Generate AI freestyle recipes."""
    db = await _async_get_db(hass)
    # Fail fast with an actionable message when no usable LLM agent is
    # configured (issue #38) — otherwise the call would fall through to
    # the built-in Assist agent and die with an opaque parse error.
    agent_id = await _resolve_agent_id(hass, msg)
    agent_problem = _check_llm_agent(hass, agent_id)
    if agent_problem is not None:
        connection.send_error(msg["id"], agent_problem[0], agent_problem[1])
        return

    hoppers = await db.async_get_hoppers()
    milk_types = await db.async_get_milk()
    settings = await db.async_get_settings()

    hopper1_bean = hoppers.get("hopper1", {}).get("bean")
    hopper2_bean = hoppers.get("hopper2", {}).get("bean")

    # Load extras from DB then apply per-request whitelist filters.
    # Empty filter list (= user explicitly cleared the multiselect)
    # means "none of this category"; absent filter means "use DB
    # default = everything available".
    extras_db = await db.async_get_pantry_extras() or {}
    if "allow_syrups" in msg:
        extras_db["syrups"] = list(msg["allow_syrups"])
    if "allow_toppings" in msg:
        extras_db["toppings"] = list(msg["allow_toppings"])
    if "allow_milk" in msg:
        # `milk_types` is its own arg into the generator, but we keep
        # the constraint in the extras dict too so the prompt's
        # "Available extras" section reflects exactly what's allowed.
        milk_types = list(msg["allow_milk"])
        extras_db["milk"] = milk_types
    extras_context = extras_db if any(extras_db.values()) else None

    # Load active profile from DB
    profile_id: str | None = None
    active_profile: dict[str, Any] | None = None
    try:
        active_profile = await db.async_get_active_profile()
        if active_profile:
            profile_id = active_profile["id"]
    except Exception:
        _LOGGER.debug("No profiles available, using defaults")

    # Load user preferences from DB
    user_prefs = await db.async_get_preferences()

    # Merge profile preferences with user preferences (profile overrides)
    cup_size = "mug"
    temperature_pref = msg.get("temperature", "auto")
    dietary: list[str] = []
    caffeine_pref = "regular"
    # `extras_db` is the post-filter dict built above (DB defaults +
    # any allow_* whitelists from the request). Renamed from `extras`
    # but this line was missed — keep using the same source.
    ice_available = "ice" in extras_db.get("misc", []) if extras_db else False

    if active_profile:
        cup_size = active_profile.get("cup_size", "mug")
        if temperature_pref == "auto":
            temperature_pref = active_profile.get("temperature_pref", "auto")
            if temperature_pref in ("hot_only", "cold_ok", "prefer_cold"):
                temperature_pref = {"hot_only": "hot", "cold_ok": "auto", "prefer_cold": "iced"}.get(
                    temperature_pref, "auto"
                )
        dietary = active_profile.get("dietary", [])
        caffeine_pref = active_profile.get("caffeine_pref", "regular")

    # Per-request overrides win over the active profile. The user can
    # leave them out of the WS message to fall back to profile values.
    if "cup_size" in msg:
        cup_size = msg["cup_size"]
    if "dietary" in msg:
        dietary = list(msg["dietary"])
    if "caffeine_pref" in msg:
        caffeine_pref = msg["caffeine_pref"]
    # Resolve mood/moods: prefer the new multi-list, fall back to the
    # legacy single-mood field. We pass the union to the generator so
    # the prompt explicitly lists all selected moods.
    moods: list[str] | None = None
    if "moods" in msg and msg["moods"]:
        moods = list(msg["moods"])
    elif msg.get("mood"):
        moods = [msg["mood"]]

    # Get weather from HA if use_weather preference is set
    weather_context: dict[str, Any] | None = None
    if user_prefs.get("use_weather") == "true":
        weather_entity = user_prefs.get("weather_entity", "weather.home")
        weather_state = hass.states.get(weather_entity)
        if weather_state:
            weather_context = {
                "temperature": weather_state.attributes.get("temperature"),
                "condition": weather_state.state,
            }

    # Get cups today from sensor (if available)
    cups_today: int | None = None
    for entry in hass.config_entries.async_entries(DOMAIN):
        if hasattr(entry, "runtime_data") and entry.runtime_data:
            cups_today = getattr(entry.runtime_data, "total_cups", None)
            break

    # Get people home count
    people_home: int | None = None
    if user_prefs.get("use_presence") == "true":
        people_home = sum(
            1 for s in hass.states.async_all("person") if s.state == "home"
        )

    # Load user-overridable persona prompt for the sommelier (slot
    # `sommelier_intro` in the panel prompt store). Falls back to the bundled
    # default inside _build_prompt when None.
    try:
        from .panel_api import (  # noqa: PLC0415
            _resolve_prompt,
            _structured_call,
        )
        from .ai_recipes import _build_prompt, _validate_recipes  # noqa: PLC0415
        intro = await _resolve_prompt(hass, "sommelier_intro")
    except Exception:  # noqa: BLE001
        intro = None
        from .ai_recipes import _validate_recipes  # noqa: PLC0415

    # Fetch LiveCapabilities so the prompt enumerates only this machine's
    # supported processes/intensities/etc. Cache hit -> use it; cache
    # miss -> derive live from runtime_data; both-fail -> caps=None
    # (fallback to legacy universal block).
    caps = None
    target_entry_id = msg.get("entry_id")
    if target_entry_id is None:
        # B1+X2 deferred: take the first config entry as today.
        entries = hass.config_entries.async_entries(DOMAIN)
        if entries:
            target_entry_id = entries[0].entry_id
    if target_entry_id is not None:
        row = await db.async_get_capabilities(target_entry_id)
        if row is not None:
            try:
                from .capabilities import LiveCapabilities  # noqa: PLC0415
                caps = LiveCapabilities.from_json(row["json_payload"])
            except Exception:  # noqa: BLE001 — corrupt cache, fall through
                caps = None
        if caps is None:
            entry = hass.config_entries.async_get_entry(target_entry_id)
            if entry is not None and getattr(entry, "runtime_data", None) is not None:
                try:
                    from .capabilities import derive_capabilities  # noqa: PLC0415
                    caps = derive_capabilities(entry.runtime_data)
                except ValueError:
                    caps = None

    # Build intro+context (without the legacy ## Output Format text block —
    # the JSON Schema is auto-appended by _structured_call instead).
    prebuilt_prompt = _build_prompt(
        hopper1_bean=hopper1_bean,
        hopper2_bean=hopper2_bean,
        milk_types=milk_types,
        mode=msg["mode"],
        preference=msg.get("preference"),
        count=msg["count"],
        extras=extras_context,
        ice_available=ice_available,
        cup_size=cup_size,
        temperature_pref=temperature_pref,
        intro=intro,
        mood=msg.get("mood"),
        moods=moods,
        occasion=msg.get("occasion"),
        servings=msg.get("servings", 1),
        dietary=dietary,
        caffeine_pref=caffeine_pref,
        weather=weather_context,
        people_home=people_home,
        cups_today=cups_today,
        # Inject the HA UI locale so the recipe names / descriptions /
        # step instructions come back in the user's language. Falls
        # back to English if HA's language is unset for some reason.
        language=hass.config.language or "en",
        omit_output_format=True,
        caps=caps,
    )

    try:
        sc_result = await _structured_call(
            hass,
            slot="sommelier_intro",
            fmt_vars={"count": msg["count"], "mode": msg["mode"]},
            agent_id=await _resolve_agent_id(hass, msg),
            ctx=connection.context(msg),
            prebuilt_prompt=prebuilt_prompt,
        )
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Failed to generate recipes")
        connection.send_error(
            msg["id"], "generation_failed", "Recipe generation failed; see HA logs"
        )
        return

    parsed = sc_result.get("parsed") or {}
    raw_recipes = parsed.get("recipes") if isinstance(parsed, dict) else None
    if not raw_recipes:
        connection.send_error(
            msg["id"], "no_recipes",
            f"LLM returned no usable recipes (errors: {sc_result.get('validation_errors')})",
        )
        return

    # Pydantic already enforced the schema; _validate_recipes still applies
    # legacy clamps (portion_ml rounding to 5ml, extras vocabulary check)
    # so the brew payload stays inside what the machine accepts.
    recipes = _validate_recipes(raw_recipes)

    hopper1_bean_id = hopper1_bean["id"] if hopper1_bean else None
    hopper2_bean_id = hopper2_bean["id"] if hopper2_bean else None

    session = await db.async_create_session(
        mode=msg["mode"],
        preference=msg.get("preference"),
        hopper1_bean_id=hopper1_bean_id,
        hopper2_bean_id=hopper2_bean_id,
        milk_types=milk_types,
        llm_agent=settings.get("llm_agent_id"),
        recipes=recipes,
        profile_id=profile_id,
        mood=msg.get("mood"),
        occasion=msg.get("occasion"),
        temperature=msg.get("temperature", "auto"),
        servings=msg.get("servings", 1),
        extras_context=extras_context,
        weather_context=weather_context,
        machine_profile=msg.get("machine_profile"),
    )
    # UI Contract §3.9: generate results carry a per-recipe IconSpec.
    await _attach_recipe_icons(db, session.get("recipes") or [])
    _send_versioned(connection, msg["id"], {"session": session})


# ── Brew (from generated recipe) ─────────────────────────────────────

@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/brew",
        vol.Required("recipe_id"): cv.string,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_brew(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Brew a generated recipe via freestyle."""
    db = await _async_get_db(hass)
    recipe = await db.async_get_recipe(msg["recipe_id"])
    if recipe is None:
        connection.send_error(msg["id"], "not_found", "Recipe not found")
        return

    client = _find_client(hass)
    if client is None:
        connection.send_error(msg["id"], "no_device", "No coffee machine available")
        return

    try:
        brewed = await _brew_recipe_components(
            client,
            name=recipe.get("name", "Sommelier"),
            blend=recipe.get("blend", 1),
            phases=recipe.get("machine_phases") or [],
        )
    except RecipeWritesUnsupportedError as exc:
        connection.send_error(
            msg["id"],
            "recipe_writes_unsupported",
            f"This machine ({exc.family_key}) does not support custom "
            "recipe writes. The recipe is print-only here.",
        )
        return
    except ValueError as exc:
        _LOGGER.warning("Recipe %s failed brew-time validation: %s", msg["recipe_id"], exc)
        connection.send_error(msg["id"], "brew_failed", str(exc))
        return
    except Exception:
        _LOGGER.exception("Failed to brew recipe")
        connection.send_error(
            msg["id"], "brew_failed", "Brewing failed; see HA logs"
        )
        return

    if not brewed:
        connection.send_error(
            msg["id"],
            "brew_failed",
            "The machine refused to start brewing (busy, not ready, or "
            "disconnected). Check the machine and try again.",
        )
        return

    await db.async_mark_recipe_brewed(msg["recipe_id"])
    _send_versioned(connection, msg["id"], {})


# ── Brew a single phase (step-machine wizard) ────────────────────────

@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/brew_phase",
        vol.Exclusive("recipe_id", "target"): cv.string,
        vol.Exclusive("favorite_id", "target"): cv.string,
        vol.Required("phase_index"): vol.All(vol.Coerce(int), vol.Range(min=0)),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_brew_phase(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Brew a single machine phase of a recipe or favorite.

    Backend of the step-machine wizard: exactly one of ``recipe_id`` /
    ``favorite_id`` selects the source row, and ``machine_phases[phase_index]``
    is brewed alone (the BLE call still carries a none-process component2,
    i.e. "no second pour"). The reply carries ``phase_count`` and the NEXT
    phase's ``user_action_before`` list so the wizard can show inter-phase
    manual steps without refetching the recipe. The brewed marker /
    favorite counter only advances when the LAST phase starts successfully,
    so a wizard aborted mid-recipe doesn't count as a completed drink.
    """
    has_recipe = "recipe_id" in msg
    has_favorite = "favorite_id" in msg
    if has_recipe == has_favorite:
        connection.send_error(
            msg["id"],
            "invalid_target",
            "exactly one of recipe_id or favorite_id is required",
        )
        return

    db = await _async_get_db(hass)
    if has_recipe:
        row = await db.async_get_recipe(msg["recipe_id"])
    else:
        row = await db.async_get_favorite(msg["favorite_id"])
    if row is None:
        connection.send_error(
            msg["id"],
            "not_found",
            "Recipe not found" if has_recipe else "Favorite not found",
        )
        return

    client = _find_client(hass)
    if client is None:
        connection.send_error(msg["id"], "no_device", "No coffee machine available")
        return

    phases = row.get("machine_phases") or []
    phase_count = len(phases)
    phase_index = msg["phase_index"]
    if not 0 <= phase_index < phase_count:
        connection.send_error(
            msg["id"],
            "invalid_phase",
            f"phase_index {phase_index} out of range; "
            f"recipe has {phase_count} machine phase(s)",
        )
        return

    try:
        brewed = await _brew_recipe_phase(
            client,
            name=row.get("name", "Sommelier"),
            blend=row.get("blend", 1),
            phases=phases,
            phase_index=phase_index,
        )
    except RecipeWritesUnsupportedError as exc:
        connection.send_error(
            msg["id"],
            "recipe_writes_unsupported",
            f"This machine ({exc.family_key}) does not support custom "
            "recipe writes. The recipe is print-only here.",
        )
        return
    except ValueError as exc:
        _LOGGER.warning(
            "Phase %s of %s failed brew-time validation: %s",
            phase_index,
            row.get("id"),
            exc,
        )
        connection.send_error(msg["id"], "brew_failed", str(exc))
        return
    except Exception:
        _LOGGER.exception("Failed to brew phase %s of %s", phase_index, row.get("id"))
        connection.send_error(
            msg["id"], "brew_failed", "Brewing failed; see HA logs"
        )
        return

    if not brewed:
        connection.send_error(
            msg["id"],
            "brew_failed",
            "The machine refused to start brewing (busy, not ready, or "
            "disconnected). Check the machine and try again.",
        )
        return

    if phase_index == phase_count - 1:
        if has_recipe:
            await db.async_mark_recipe_brewed(msg["recipe_id"])
        else:
            await db.async_increment_favorite_brew(msg["favorite_id"])

    next_actions = (
        phases[phase_index + 1].get("user_action_before") or []
        if phase_index + 1 < phase_count
        else []
    )
    _send_versioned(
        connection,
        msg["id"],
        {
            "success": True,
            "phase_index": phase_index,
            "phase_count": phase_count,
            "manual_actions_next": next_actions,
        },
    )


# ── Favorites ─────────────────────────────────────────────────────────

@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/favorites/list",
        vol.Optional("machine_profile_filter"): int,
    }
)
@websocket_api.async_response
async def ws_favorites_list(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """List favorites, optionally filtered to a machine profile + shared.

    Each favorite carries a per-recipe `icon` IconSpec (UI Contract §3.9).
    """
    db = await _async_get_db(hass)
    favorites = await db.async_list_favorites(
        machine_profile_filter=msg.get("machine_profile_filter"),
    )
    await _attach_recipe_icons(db, favorites)
    _send_versioned(connection, msg["id"], {"favorites": favorites})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/favorites/add",
        vol.Required("recipe_id"): cv.string,
        vol.Optional("machine_profile"): int,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_favorites_add(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Add a generated recipe to favorites. NULL machine_profile = shared."""
    db = await _async_get_db(hass)
    recipe = await db.async_get_recipe(msg["recipe_id"])
    if recipe is None:
        connection.send_error(msg["id"], "not_found", "Recipe not found")
        return

    # Get current hopper bean for source tracking. Recipe blend uses the
    # LLM semantics (1 = hopper 1, 0 = hopper 2) — same translation as the
    # BLE boundary in _brew_recipe_components.
    hoppers = await db.async_get_hoppers()
    hopper_key = "hopper1" if recipe["blend"] == 1 else "hopper2"
    source_bean = hoppers.get(hopper_key, {}).get("bean")

    # Pass through machine_phases so async_add_favorite stores the v5
    # representation; legacy component1/component2 columns are synthesized
    # from phase[0]/phase[1] by the DB layer (mirrors async_create_session).
    fav = await db.async_add_favorite({
        "name": recipe["name"],
        "description": recipe["description"],
        "blend": recipe["blend"],
        "component1": recipe["component1"],
        "component2": recipe["component2"],
        "machine_phases": recipe.get("machine_phases"),
        "extras": recipe.get("extras"),
        "steps": recipe.get("steps"),
        "cup_type": recipe.get("cup_type"),
        "source_recipe_id": recipe["id"],
        "source_bean_id": source_bean["id"] if source_bean else None,
        "machine_profile": msg.get("machine_profile"),
    })
    # UI Contract §3.9: echo the stored favorite with its IconSpec.
    await _attach_recipe_icons(db, [fav])
    _send_versioned(connection, msg["id"], {"favorite": fav})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/favorites/remove",
        vol.Required("favorite_id"): cv.string,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_favorites_remove(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Remove a favorite."""
    db = await _async_get_db(hass)
    removed = await db.async_remove_favorite(msg["favorite_id"])
    if not removed:
        connection.send_error(msg["id"], "not_found", "Favorite not found")
        return
    _send_versioned(connection, msg["id"], {})


@websocket_api.websocket_command({
    vol.Required("type"): "melitta_barista/sommelier/favorites/update",
    vol.Required("favorite_id"): cv.string,
    vol.Optional("name"): cv.string,
    vol.Optional("description"): cv.string,
    vol.Optional("note"): vol.Any(cv.string, None),
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_favorites_update(hass, connection, msg) -> None:
    """Patch a favorite's name / description / note."""
    db = await _async_get_db(hass)
    patch = {k: msg[k] for k in ("name", "description", "note") if k in msg}
    if not patch:
        connection.send_error(msg["id"], "no_fields", "no fields to update")
        return
    try:
        changed = await db.async_update_favorite(msg["favorite_id"], **patch)
    except ValueError as exc:
        connection.send_error(msg["id"], "invalid_update", str(exc))
        return
    if not changed:
        connection.send_error(msg["id"], "not_found", f"favorite {msg['favorite_id']} not found")
        return
    _send_versioned(connection, msg["id"], {})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/favorites/brew",
        vol.Required("favorite_id"): cv.string,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_favorites_brew(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Brew a favorite recipe."""
    db = await _async_get_db(hass)
    fav = await db.async_get_favorite(msg["favorite_id"])
    if fav is None:
        connection.send_error(msg["id"], "not_found", "Favorite not found")
        return

    client = _find_client(hass)
    if client is None:
        connection.send_error(msg["id"], "no_device", "No coffee machine available")
        return

    try:
        brewed = await _brew_recipe_components(
            client,
            name=fav.get("name", "Sommelier"),
            blend=fav.get("blend", 1),
            phases=fav.get("machine_phases") or [],
        )
    except RecipeWritesUnsupportedError as exc:
        connection.send_error(
            msg["id"],
            "recipe_writes_unsupported",
            f"This machine ({exc.family_key}) does not support custom "
            "recipe writes. The recipe is print-only here.",
        )
        return
    except ValueError as exc:
        _LOGGER.warning(
            "Favorite %s failed brew-time validation: %s", msg["favorite_id"], exc
        )
        connection.send_error(msg["id"], "brew_failed", str(exc))
        return
    except Exception:
        _LOGGER.exception("Failed to brew favorite")
        connection.send_error(
            msg["id"], "brew_failed", "Brewing favorite failed; see HA logs"
        )
        return

    if not brewed:
        connection.send_error(
            msg["id"],
            "brew_failed",
            "The machine refused to start brewing (busy, not ready, or "
            "disconnected). Check the machine and try again.",
        )
        return

    await db.async_increment_favorite_brew(msg["favorite_id"])
    _send_versioned(connection, msg["id"], {})


# ── History ───────────────────────────────────────────────────────────

@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/history/list",
        vol.Optional("limit", default=20): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=100)
        ),
        vol.Optional("offset", default=0): vol.All(
            vol.Coerce(int), vol.Range(min=0)
        ),
        vol.Optional("machine_profile_filter"): int,
    }
)
@websocket_api.async_response
async def ws_history_list(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """List generation history, optionally filtered to a machine profile + shared.

    Every saved recipe in every session carries a per-recipe `icon`
    IconSpec (UI Contract §3.9).
    """
    db = await _async_get_db(hass)
    sessions = await db.async_list_history(
        limit=msg["limit"],
        offset=msg["offset"],
        machine_profile_filter=msg.get("machine_profile_filter"),
    )
    color_hints = await _additive_color_hints(db)
    for session in sessions:
        for recipe in session.get("recipes") or []:
            if isinstance(recipe, dict):
                _attach_recipe_icon(recipe, color_hints)
    _send_versioned(connection, msg["id"], {"sessions": sessions})


@websocket_api.websocket_command({
    vol.Required("type"): "melitta_barista/sommelier/history/clear",
    vol.Optional("keep_favorited", default=True): bool,
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_history_clear(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Delete history sessions. By default protects sessions linked to favorites."""
    db = await _async_get_db(hass)
    keep = msg.get("keep_favorited", True)
    cleared = await db.async_clear_history(keep_favorited=keep)
    _send_versioned(connection, msg["id"], {"cleared": cleared})


# ── Bean Presets (static catalogue from coffee_presets.json) ──────────

_BEAN_PRESETS_CACHE: list[dict[str, Any]] | None = None


def _load_bean_presets_sync() -> list[dict[str, Any]]:
    """Read and parse the bundled bean presets JSON (blocking I/O)."""
    presets_path = Path(__file__).parent / "coffee_presets.json"
    return json.loads(presets_path.read_text(encoding="utf-8"))


@websocket_api.websocket_command(
    {vol.Required("type"): "melitta_barista/sommelier/bean_presets/list"}
)
@websocket_api.async_response
async def ws_bean_presets_list(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """List built-in coffee bean presets (cached; loaded via executor on first call)."""
    global _BEAN_PRESETS_CACHE
    if _BEAN_PRESETS_CACHE is None:
        try:
            _BEAN_PRESETS_CACHE = await hass.async_add_executor_job(
                _load_bean_presets_sync
            )
        except Exception:
            _LOGGER.exception("Failed to load bean presets")
            connection.send_error(
                msg["id"], "load_failed", "Bean preset list failed to load"
            )
            return
    _send_versioned(connection, msg["id"], {"presets": _BEAN_PRESETS_CACHE})


# ── Sommelier Presets (R7 — user-defined preset templates) ────────────

@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/presets/list",
        vol.Optional("machine_profile_filter"): int,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_presets_list(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """List user-defined sommelier presets.

    When `machine_profile_filter` is set, the response includes presets
    bound to that machine profile plus shared (machine_profile IS NULL)
    presets. Without the filter the entire list is returned.
    """
    db = await _async_get_db(hass)
    presets = await db.async_list_presets(
        machine_profile_filter=msg.get("machine_profile_filter"),
    )
    _send_versioned(connection, msg["id"], {"presets": presets})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/presets/add",
        vol.Required("name"): vol.All(cv.string, vol.Length(min=1, max=80)),
        vol.Optional("description"): vol.All(cv.string, vol.Length(max=500)),
        vol.Required("payload"): dict,
        vol.Optional("machine_profile"): int,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_presets_add(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Create a new sommelier preset. NULL machine_profile = shared."""
    db = await _async_get_db(hass)
    preset_id = await db.async_add_preset(
        msg["name"],
        msg.get("description"),
        msg["payload"],
        machine_profile=msg.get("machine_profile"),
    )
    _send_versioned(connection, msg["id"], {"id": preset_id})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/presets/update",
        vol.Required("preset_id"): cv.string,
        vol.Optional("name"): vol.All(cv.string, vol.Length(min=1, max=80)),
        vol.Optional("description"): vol.All(cv.string, vol.Length(max=500)),
        vol.Optional("payload"): dict,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_presets_update(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Patch a sommelier preset's name / description / payload."""
    db = await _async_get_db(hass)
    patch = {k: msg[k] for k in ("name", "description", "payload") if k in msg}
    try:
        changed = await db.async_update_preset(msg["preset_id"], **patch)
    except ValueError as exc:
        code = exc.args[0] if exc.args else "invalid_update"
        if code == "no_fields":
            connection.send_error(
                msg["id"], "no_fields", "Provide at least one field to update"
            )
            return
        if code == "system_preset_readonly":
            connection.send_error(
                msg["id"],
                "system_preset_readonly",
                "Built-in presets cannot be modified",
            )
            return
        connection.send_error(msg["id"], "invalid_update", str(exc))
        return
    if not changed:
        connection.send_error(msg["id"], "not_found", "Preset not found")
        return
    _send_versioned(connection, msg["id"], {"updated": True})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/presets/delete",
        vol.Required("preset_id"): cv.string,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_presets_delete(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Delete a sommelier preset."""
    db = await _async_get_db(hass)
    try:
        deleted = await db.async_delete_preset(msg["preset_id"])
    except ValueError as exc:
        code = exc.args[0] if exc.args else "invalid_delete"
        if code == "system_preset_readonly":
            connection.send_error(
                msg["id"],
                "system_preset_readonly",
                "Built-in presets cannot be deleted",
            )
            return
        connection.send_error(msg["id"], "invalid_delete", str(exc))
        return
    if not deleted:
        connection.send_error(msg["id"], "not_found", "Preset not found")
        return
    _send_versioned(connection, msg["id"], {"deleted": True})


# ── Settings ──────────────────────────────────────────────────────────

@websocket_api.websocket_command(
    {vol.Required("type"): "melitta_barista/sommelier/settings/get"}
)
@websocket_api.async_response
async def ws_settings_get(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Get Sommelier settings."""
    db = await _async_get_db(hass)
    settings = await db.async_get_settings()
    _send_versioned(connection, msg["id"], {"settings": settings})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/settings/set",
        vol.Required("key"): vol.In(VALID_SETTING_KEYS),
        vol.Required("value"): cv.string,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_settings_set(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Set a Sommelier setting."""
    db = await _async_get_db(hass)
    await db.async_set_setting(msg["key"], msg["value"])
    _send_versioned(connection, msg["id"], {})


# ── Extras ───────────────────────────────────────────────────────────

@websocket_api.websocket_command(
    {vol.Required("type"): "melitta_barista/sommelier/extras/get"}
)
@websocket_api.async_response
async def ws_extras_get(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Get available extras (syrups, toppings, etc.)."""
    db = await _async_get_db(hass)
    extras = await db.async_get_pantry_extras()
    _send_versioned(connection, msg["id"], {"extras": extras})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/extras/set",
        vol.Required("category"): vol.In(VALID_EXTRAS_CATEGORIES),
        vol.Required("items"): vol.All(cv.ensure_list, [cv.string]),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_extras_set(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Set extras for a category."""
    db = await _async_get_db(hass)
    await db.async_set_extras(msg["category"], msg["items"])
    _send_versioned(connection, msg["id"], {})


# ── Recipe Ratings ────────────────────────────────────────────────────

@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/recipe/rate",
        vol.Required("target_id"): cv.string,
        vol.Required("target_type"): vol.In(["generated", "favorite"]),
        vol.Required("rating"): vol.All(int, vol.Range(min=1, max=5)),
        vol.Optional("note"): vol.Any(cv.string, None),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_recipe_rate(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Set/update a rating for a recipe (generated or favorite)."""
    db = await _async_get_db(hass)
    try:
        await db.async_set_rating(
            msg["target_id"],
            msg["target_type"],
            int(msg["rating"]),
            msg.get("note"),
        )
    except ValueError as exc:
        connection.send_error(msg["id"], "invalid_rating", str(exc))
        return
    _send_versioned(connection, msg["id"], {})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/recipe/unrate",
        vol.Required("target_id"): cv.string,
        vol.Required("target_type"): vol.In(["generated", "favorite"]),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_recipe_unrate(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Remove a recipe rating."""
    db = await _async_get_db(hass)
    await db.async_clear_rating(msg["target_id"], msg["target_type"])
    _send_versioned(connection, msg["id"], {})


# ── Preferences ──────────────────────────────────────────────────────

@websocket_api.websocket_command(
    {vol.Required("type"): "melitta_barista/sommelier/preferences/get"}
)
@websocket_api.async_response
async def ws_preferences_get(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Get user preferences."""
    db = await _async_get_db(hass)
    preferences = await db.async_get_preferences()
    _send_versioned(connection, msg["id"], {"preferences": preferences})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/preferences/set",
        vol.Required("key"): vol.In(VALID_PREFERENCE_KEYS),
        vol.Required("value"): cv.string,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_preferences_set(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Set a user preference."""
    db = await _async_get_db(hass)
    await db.async_set_preference(msg["key"], msg["value"])
    _send_versioned(connection, msg["id"], {})


# ── Profiles ─────────────────────────────────────────────────────────

@websocket_api.websocket_command(
    {vol.Required("type"): "melitta_barista/sommelier/profiles/list"}
)
@websocket_api.async_response
async def ws_profiles_list(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """List all profiles."""
    db = await _async_get_db(hass)
    profiles = await db.async_list_profiles()
    _send_versioned(connection, msg["id"], {"profiles": profiles})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/profiles/add",
        vol.Required("name"): cv.string,
        vol.Optional("preferences", default={}): dict,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_profiles_add(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Add a new profile."""
    db = await _async_get_db(hass)
    # async_add_profile accepts a single `data` dict shaped like a profile
    # row (name, cup_size, dietary, caffeine_pref, …). Earlier we passed
    # name=…, preferences=… as kwargs — that signature didn't exist and
    # the call raised TypeError, plus the nested `preferences` mapping
    # was silently dropped on the way to the DB.
    data: dict[str, Any] = {"name": msg["name"]}
    data.update(msg.get("preferences", {}))
    profile = await db.async_add_profile(data)
    _send_versioned(connection, msg["id"], {"profile": profile})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/profiles/update",
        vol.Required("profile_id"): cv.string,
        vol.Optional("name"): cv.string,
        vol.Optional("preferences"): dict,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_profiles_update(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Update an existing profile."""
    db = await _async_get_db(hass)
    data: dict[str, Any] = {}
    if "name" in msg:
        data["name"] = msg["name"]
    if "preferences" in msg:
        data["preferences"] = msg["preferences"]
    profile = await db.async_update_profile(msg["profile_id"], data)
    if profile is None:
        connection.send_error(
            msg["id"], "not_found", f"Profile {msg['profile_id']} not found"
        )
        return
    _send_versioned(connection, msg["id"], {"profile": profile})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/profiles/delete",
        vol.Required("profile_id"): cv.string,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_profiles_delete(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Delete a profile."""
    db = await _async_get_db(hass)
    deleted = await db.async_delete_profile(msg["profile_id"])
    if not deleted:
        connection.send_error(msg["id"], "not_found", "Profile not found")
        return
    _send_versioned(connection, msg["id"], {})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/profiles/activate",
        vol.Required("profile_id"): cv.string,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_profiles_activate(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Activate a profile (deactivates others)."""
    db = await _async_get_db(hass)
    activated = await db.async_set_active_profile(msg["profile_id"])
    if not activated:
        connection.send_error(msg["id"], "not_found", "Profile not found")
        return
    _send_versioned(connection, msg["id"], {})
