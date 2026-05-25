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

from .const import DOMAIN, PROCESS_MAP, SHOTS_MAP, INTENSITY_MAP, AROMA_MAP, TEMPERATURE_MAP

_LOGGER = logging.getLogger("melitta_barista")


def _find_client(hass: HomeAssistant):
    """Find the first available MelittaBleClient via config entries."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if hasattr(entry, "runtime_data") and entry.runtime_data:
            return entry.runtime_data
    return None


async def _brew_recipe_components(
    client, name: str, blend: int, phases: list[dict]
) -> None:
    """Execute a multi-phase brew.

    P2a: BLE layer still takes component1/2, so we unpack phases[0]/phases[1].
    `phases` is the list-of-dicts form ({"component": {...}, "user_action_before": [...]}).
    A single-phase brew is encoded by sending a "none"-process component2,
    which the BLE protocol naturally treats as "no second pour".
    """
    from .protocol import RecipeComponent as ProtocolRC

    if not phases:
        raise ValueError("phases is empty; cannot brew")
    if len(phases) > 2:
        raise ValueError(f"phases length {len(phases)} > 2; cap to 2 in caller")

    def _to_proto(comp: dict, *, blend_value: int) -> ProtocolRC:
        return ProtocolRC(
            process=PROCESS_MAP.get(comp.get("process", "none"), 0),
            shots=SHOTS_MAP.get(comp.get("shots", "none"), 0),
            blend=blend_value,
            intensity=INTENSITY_MAP.get(comp.get("intensity", "medium"), 2),
            aroma=AROMA_MAP.get(comp.get("aroma", "standard"), 0),
            temperature=TEMPERATURE_MAP.get(comp.get("temperature", "normal"), 1),
            portion=int(comp.get("portion_ml", 0)) // 5,
        )

    component1 = _to_proto(phases[0].get("component", {}), blend_value=blend)
    if len(phases) >= 2:
        component2 = _to_proto(
            phases[1].get("component", {}),
            blend_value=0 if blend == 1 else 1,
        )
    else:
        # Single-phase: synthesize a "none"-process component2 — BLE protocol
        # treats this as "no second pour".
        component2 = ProtocolRC(
            process=PROCESS_MAP.get("none", 0),
            shots=SHOTS_MAP.get("none", 0),
            blend=0 if blend == 1 else 1,
            intensity=INTENSITY_MAP.get("medium", 2),
            aroma=AROMA_MAP.get("standard", 0),
            temperature=TEMPERATURE_MAP.get("normal", 1),
            portion=0,
        )

    await client.brew_freestyle(
        name=name,
        recipe_type=24,
        component1=component1,
        component2=component2,
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
VALID_SETTING_KEYS = ["llm_agent_id"]

# User-writable keys for the `user_preferences` table. There is no current
# WS caller, but the endpoint exists; restrict it the same way to prevent
# a future caller from polluting the table with arbitrary keys.
VALID_PREFERENCE_KEYS = [
    "default_cup_size",
    "default_temperature",
    "default_caffeine",
    "default_dietary",
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
    websocket_api.async_register_command(hass, ws_generate)
    websocket_api.async_register_command(hass, ws_brew)
    websocket_api.async_register_command(hass, ws_favorites_list)
    websocket_api.async_register_command(hass, ws_favorites_add)
    websocket_api.async_register_command(hass, ws_favorites_remove)
    websocket_api.async_register_command(hass, ws_favorites_update)
    websocket_api.async_register_command(hass, ws_favorites_brew)
    websocket_api.async_register_command(hass, ws_history_list)
    websocket_api.async_register_command(hass, ws_history_clear)
    websocket_api.async_register_command(hass, ws_presets_list)
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
    connection.send_result(msg["id"], {"beans": beans})


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
    connection.send_result(msg["id"], {"bean": bean})


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
    connection.send_result(msg["id"], {"bean": bean})


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
    connection.send_result(msg["id"])


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
    connection.send_result(msg["id"], hoppers)


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
                connection.send_result(msg["id"], {
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

    connection.send_result(msg["id"], {
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
    connection.send_result(msg["id"])


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
    connection.send_result(msg["id"], {"milk_types": milk})


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
    connection.send_result(msg["id"])


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
    hoppers = await db.async_get_hoppers()
    milk_types = await db.async_get_milk()
    settings = await db.async_get_settings()

    hopper1_bean = hoppers.get("hopper1", {}).get("bean")
    hopper2_bean = hoppers.get("hopper2", {}).get("bean")

    # Load extras from DB then apply per-request whitelist filters.
    # Empty filter list (= user explicitly cleared the multiselect)
    # means "none of this category"; absent filter means "use DB
    # default = everything available".
    extras_db = await db.async_get_extras() or {}
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
            _resolve_agent_id,
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
    )
    connection.send_result(msg["id"], {"session": session})


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
        await _brew_recipe_components(
            client,
            name=recipe.get("name", "Sommelier"),
            blend=recipe.get("blend", 1),
            phases=recipe.get("machine_phases") or [],
        )
    except Exception:
        _LOGGER.exception("Failed to brew recipe")
        connection.send_error(
            msg["id"], "brew_failed", "Brewing failed; see HA logs"
        )
        return

    await db.async_mark_recipe_brewed(msg["recipe_id"])
    connection.send_result(msg["id"])


# ── Favorites ─────────────────────────────────────────────────────────

@websocket_api.websocket_command(
    {vol.Required("type"): "melitta_barista/sommelier/favorites/list"}
)
@websocket_api.async_response
async def ws_favorites_list(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """List favorites."""
    db = await _async_get_db(hass)
    favorites = await db.async_list_favorites()
    connection.send_result(msg["id"], {"favorites": favorites})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/favorites/add",
        vol.Required("recipe_id"): cv.string,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_favorites_add(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Add a generated recipe to favorites."""
    db = await _async_get_db(hass)
    recipe = await db.async_get_recipe(msg["recipe_id"])
    if recipe is None:
        connection.send_error(msg["id"], "not_found", "Recipe not found")
        return

    # Get current hopper bean for source tracking
    hoppers = await db.async_get_hoppers()
    hopper_key = f"hopper{recipe['blend'] + 1}"
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
    })
    connection.send_result(msg["id"], {"favorite": fav})


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
    connection.send_result(msg["id"])


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
    connection.send_result(msg["id"], {})


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
        await _brew_recipe_components(
            client,
            name=fav.get("name", "Sommelier"),
            blend=fav.get("blend", 1),
            phases=fav.get("machine_phases") or [],
        )
    except Exception:
        _LOGGER.exception("Failed to brew favorite")
        connection.send_error(
            msg["id"], "brew_failed", "Brewing favorite failed; see HA logs"
        )
        return

    await db.async_increment_favorite_brew(msg["favorite_id"])
    connection.send_result(msg["id"])


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
    }
)
@websocket_api.async_response
async def ws_history_list(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """List generation history."""
    db = await _async_get_db(hass)
    sessions = await db.async_list_history(
        limit=msg["limit"], offset=msg["offset"]
    )
    connection.send_result(msg["id"], {"sessions": sessions})


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
    connection.send_result(msg["id"], {"cleared": cleared})


# ── Presets ───────────────────────────────────────────────────────────

_PRESETS_CACHE: list[dict[str, Any]] | None = None


def _load_presets_sync() -> list[dict[str, Any]]:
    """Read and parse the bundled presets JSON (blocking I/O)."""
    presets_path = Path(__file__).parent / "coffee_presets.json"
    return json.loads(presets_path.read_text(encoding="utf-8"))


@websocket_api.websocket_command(
    {vol.Required("type"): "melitta_barista/sommelier/presets/list"}
)
@websocket_api.async_response
async def ws_presets_list(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """List built-in coffee presets (cached; loaded via executor on first call)."""
    global _PRESETS_CACHE
    if _PRESETS_CACHE is None:
        try:
            _PRESETS_CACHE = await hass.async_add_executor_job(_load_presets_sync)
        except Exception:
            _LOGGER.exception("Failed to load presets")
            connection.send_error(
                msg["id"], "load_failed", "Preset list failed to load"
            )
            return
    connection.send_result(msg["id"], {"presets": _PRESETS_CACHE})


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
    connection.send_result(msg["id"], {"settings": settings})


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
    connection.send_result(msg["id"])


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
    extras = await db.async_get_extras()
    connection.send_result(msg["id"], {"extras": extras})


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
    connection.send_result(msg["id"], {})


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
    connection.send_result(msg["id"], {})


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
    connection.send_result(msg["id"], {"preferences": preferences})


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
    connection.send_result(msg["id"])


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
    connection.send_result(msg["id"], {"profiles": profiles})


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
    connection.send_result(msg["id"], {"profile": profile})


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
    connection.send_result(msg["id"], {"profile": profile})


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
    connection.send_result(msg["id"])


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
    connection.send_result(msg["id"])
