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
    client, name: str, blend: int, comp1: dict, comp2: dict
) -> None:
    """Build RecipeComponents and call brew_freestyle on the client."""
    from .protocol import RecipeComponent

    component1 = RecipeComponent(
        process=PROCESS_MAP.get(comp1["process"], 1),
        shots=SHOTS_MAP.get(comp1["shots"], 0),
        blend=blend,
        intensity=INTENSITY_MAP.get(comp1["intensity"], 2),
        aroma=AROMA_MAP.get(comp1["aroma"], 0),
        temperature=TEMPERATURE_MAP.get(comp1["temperature"], 1),
        portion=comp1["portion_ml"] // 5,
    )
    component2 = RecipeComponent(
        process=PROCESS_MAP.get(comp2["process"], 0),
        shots=SHOTS_MAP.get(comp2["shots"], 0),
        blend=0 if blend == 1 else 1,
        intensity=INTENSITY_MAP.get(comp2["intensity"], 2),
        aroma=AROMA_MAP.get(comp2["aroma"], 0),
        temperature=TEMPERATURE_MAP.get(comp2["temperature"], 1),
        portion=comp2["portion_ml"] // 5,
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
    websocket_api.async_register_command(hass, ws_milk_get)
    websocket_api.async_register_command(hass, ws_milk_set)
    websocket_api.async_register_command(hass, ws_generate)
    websocket_api.async_register_command(hass, ws_brew)
    websocket_api.async_register_command(hass, ws_favorites_list)
    websocket_api.async_register_command(hass, ws_favorites_add)
    websocket_api.async_register_command(hass, ws_favorites_remove)
    websocket_api.async_register_command(hass, ws_favorites_brew)
    websocket_api.async_register_command(hass, ws_history_list)
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


@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/hoppers/assign",
        vol.Required("hopper_id"): vol.In([1, 2]),
        vol.Optional("bean_id"): vol.Any(cv.string, None),
    }
)
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
        vol.Optional("mood"): vol.In(VALID_MOODS),
        vol.Optional("occasion"): vol.In(VALID_OCCASIONS),
        vol.Optional("temperature", default="auto"): vol.In(["auto", "hot", "iced"]),
        vol.Optional("servings", default=1): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=4)
        ),
    }
)
@websocket_api.async_response
async def ws_generate(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Generate AI freestyle recipes."""
    from .ai_recipes import async_generate_recipes

    db = await _async_get_db(hass)
    hoppers = await db.async_get_hoppers()
    milk_types = await db.async_get_milk()
    settings = await db.async_get_settings()

    hopper1_bean = hoppers.get("hopper1", {}).get("bean")
    hopper2_bean = hoppers.get("hopper2", {}).get("bean")

    # Load extras from DB
    extras = await db.async_get_extras()
    extras_context = extras if extras else None

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
    ice_available = "ice" in extras.get("misc", []) if extras else False

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
        from .panel_api import _resolve_prompt, _structured_call  # noqa: PLC0415
        from .ai_recipes import _build_prompt, _validate_recipes  # noqa: PLC0415
        intro = await _resolve_prompt(hass, "sommelier_intro")
    except Exception:  # noqa: BLE001
        intro = None
        from .ai_recipes import _validate_recipes  # noqa: PLC0415

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
    )

    try:
        sc_result = await _structured_call(
            hass,
            slot="sommelier_intro",
            fmt_vars={"count": msg["count"], "mode": msg["mode"]},
            agent_id=settings.get("llm_agent_id") or None,
            ctx=connection.context(msg),
            prebuilt_prompt=prebuilt_prompt,
        )
    except Exception as err:  # noqa: BLE001
        _LOGGER.error("Failed to generate recipes: %s", err)
        connection.send_error(msg["id"], "generation_failed", str(err))
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
            client, recipe["name"], recipe["blend"],
            recipe["component1"], recipe["component2"],
        )
    except Exception as err:
        _LOGGER.error("Failed to brew recipe: %s", err)
        connection.send_error(msg["id"], "brew_failed", str(err))
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

    fav = await db.async_add_favorite({
        "name": recipe["name"],
        "description": recipe["description"],
        "blend": recipe["blend"],
        "component1": recipe["component1"],
        "component2": recipe["component2"],
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


@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/favorites/brew",
        vol.Required("favorite_id"): cv.string,
    }
)
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
            client, fav["name"], fav["blend"],
            fav["component1"], fav["component2"],
        )
    except Exception as err:
        _LOGGER.error("Failed to brew favorite: %s", err)
        connection.send_error(msg["id"], "brew_failed", str(err))
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
        except Exception as err:
            _LOGGER.error("Failed to load presets: %s", err)
            connection.send_error(msg["id"], "load_failed", str(err))
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
        vol.Required("key"): cv.string,
        vol.Required("value"): cv.string,
    }
)
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
@websocket_api.async_response
async def ws_extras_set(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Set extras for a category."""
    db = await _async_get_db(hass)
    await db.async_set_extras(msg["category"], msg["items"])
    connection.send_result(msg["id"])


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
        vol.Required("key"): cv.string,
        vol.Required("value"): cv.string,
    }
)
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
@websocket_api.async_response
async def ws_profiles_add(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Add a new profile."""
    db = await _async_get_db(hass)
    profile = await db.async_add_profile(
        name=msg["name"],
        preferences=msg.get("preferences", {}),
    )
    connection.send_result(msg["id"], {"profile": profile})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "melitta_barista/sommelier/profiles/update",
        vol.Required("profile_id"): cv.string,
        vol.Optional("name"): cv.string,
        vol.Optional("preferences"): dict,
    }
)
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
@websocket_api.async_response
async def ws_profiles_activate(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Activate a profile (deactivates others)."""
    db = await _async_get_db(hass)
    activated = await db.async_activate_profile(msg["profile_id"])
    if not activated:
        connection.send_error(msg["id"], "not_found", "Profile not found")
        return
    connection.send_result(msg["id"])
